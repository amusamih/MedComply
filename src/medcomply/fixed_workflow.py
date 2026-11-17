"""National and global assessment workflows with source validation and bounded repairs.

The prescribed graph combines read-only evidence retrieval, quota checks and
LLM interpretation, while regulatory actions require separate human authority.
"""
from __future__ import annotations
import copy
import json
import time
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from tools import provider_preflight as transport
from .agents import VerificationWorkflow as HistoricalWorkflow, SYSTEM
from .chain import Chain
from .config import ROOT
from .db import Assessment, session, audit
from .providers import Inference
from .quota import check_quota

VERSION = "fixed-workflow-20261024-v2"
DEFAULT_PROVIDER = json.loads((ROOT/"policies/application-release-v1.json").read_text(encoding="utf-8"))["default_provider"]
RUN_PREFIX = "release-20261024/"
RELEASE_LIMIT = 1_500_000_000


class ReleaseBudget(transport.Budget):
    def reserve(self, provider, stage, amount):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            subtotal = self.db.execute("SELECT COALESCE(SUM(accounted_nano),0) FROM api_spend WHERE substr(run_id,1,?)=?", (len(RUN_PREFIX), RUN_PREFIX)).fetchone()[0]
            if self.total() + amount > transport.TOTAL_LIMIT_NANO or self.total(True) + amount > self.run_limit or subtotal + amount > RELEASE_LIMIT:
                raise transport.BudgetError("Release or shared inference ceiling would be exceeded")
            key = uuid.uuid4().hex
            self.db.execute("INSERT INTO api_spend VALUES (?,?,?,?,?,?,?,?,?)", (key,self.run_id,provider,stage,transport.utc_now(),amount,amount,"reserved_or_uncertain",None))
            self.db.commit()
            return key
        except BaseException:
            self.db.rollback()
            raise


class ReleaseInference(Inference):
    def __init__(self, provider, run_id):
        super().__init__(provider, RUN_PREFIX + run_id, run_limit_usd="0.12")
        self.budget.db.close()
        self.budget = ReleaseBudget(ROOT/".local/api-spend.sqlite3",self.run_id,120_000_000)
        self.client.budget = self.budget
        self.started = time.monotonic()
        self.dispatches = 0

    def invoke(self, system, evidence, output_model, stage="assessment", output_limit=1536):
        remaining = 180 - (time.monotonic() - self.started)
        if self.dispatches >= 8 or remaining <= 0:
            raise RuntimeError("Assessment dispatch/time ceiling reached")
        self.dispatches += 1
        self.client.timeout_seconds = min(60, max(1, int(remaining)))
        result = super().invoke(system,evidence,output_model,stage,output_limit)
        response = self.records[-1]["response"]
        if self.provider == "gemini":
            complete = response["candidates"][0].get("finishReason") == "STOP"
        elif self.provider == "claude":
            complete = response.get("stop_reason") == "end_turn"
        else:
            complete = response["choices"][0].get("finish_reason") == "stop"
        if not complete:
            raise ValueError("Incomplete provider response")
        return result


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    path: str
    value: str | int | bool | None


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "reject", "review"]
    summary: str
    findings: list[str] = Field(max_length=3)
    evidence_cids: list[str] = Field(max_length=12)
    facts: list[SourceFact] = Field(max_length=12)
    policy_requirements: list[int]


class QuotaOpinion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "reject", "review"]
    summary: str


def pointer(source, path):
    if not path.startswith("/"):
        raise ValueError("JSON pointer required")
    value = source
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value,list) else value[part]
    return value


def source_package(state):
    sources = {d["cid"]:d["content"] for d in state["documents"].values() if isinstance(d.get("content"),dict)}
    required = []
    for name,doc in state["documents"].items():
        if isinstance(doc.get("content"),dict):
            field = "/quantity" if name == "shipment" else "/account" if name.endswith("_license") else "/shipment_id"
            required.append({"source_id":doc["cid"],"path":field})
    quotas = []
    for q in state["quota_assessments"]:
        item = {k:v for k,v in q.items() if k not in ("raw_model","model_attempts","ledger")}
        source_id = "quota:" + q["mode"] + ":" + q["scope"]
        sources[source_id] = item
        if "remaining" in q:
            required.append({"source_id":source_id,"path":"/remaining"})
        quotas.append(item)
    return {"sources":sources,"required_approval_facts":required,"quota_assessments":quotas}


def contract_issues(raw, state, package):
    issues = []
    allowed = {d["cid"] for d in state["documents"].values()} | {q["document_cid"] for q in state["quota_assessments"] if q.get("document_cid")}
    for cid in raw["evidence_cids"]:
        if cid not in allowed:
            issues.append({"code":"UNRETRIEVED_CID","cid":cid})
    for f in raw["facts"]:
        try:
            if f["path"].split("/")[1] in ("decision","findings","raw_model"):
                raise ValueError("Cite evidence rather than an overall verdict")
            expected = pointer(package["sources"][f["source_id"]],f["path"])
            if type(expected) is not type(f["value"]) or expected != f["value"]:
                raise ValueError("Value differs from source")
        except (KeyError,ValueError,IndexError,TypeError):
            issues.append({"code":"UNSUPPORTED_SOURCE_FACT","fact":f})
    count = len(state["evidence"]["policy"]["requirements"])
    if any(type(i) is not int or not 1 <= i <= count for i in raw["policy_requirements"]):
        issues.append({"code":"UNKNOWN_POLICY_REQUIREMENT"})
    if raw["decision"] == "approve":
        if state["findings"]:
            issues.append({"code":"UNRESOLVED_STRUCTURAL_FINDINGS","findings":state["findings"]})
        for q in state["quota_assessments"]:
            if q["decision"] != "approve":
                issues.append({"code":"QUOTA_REQUIRES_"+q["decision"].upper(),"mode":q["mode"],"findings":q["findings"]})
        supplied = {(f["source_id"],f["path"]) for f in raw["facts"]}
        for f in package["required_approval_facts"]:
            if (f["source_id"],f["path"]) not in supplied:
                issues.append({"code":"APPROVAL_SOURCE_FACT_MISSING",**f})
        missing_cids = allowed - set(raw["evidence_cids"])
        if missing_cids:
            issues.append({"code":"APPROVAL_CITATIONS_MISSING","cids":sorted(missing_cids)})
        if not raw["policy_requirements"]:
            issues.append({"code":"POLICY_REFERENCES_MISSING"})
    return issues


class QuotaValidationAgent:
    def __init__(self, chain, inference):
        self.chain,self.inference = chain,inference

    def invoke(self, shipment, mode, scope):
        q = check_quota(self.chain,shipment,mode,scope)
        data = {k:v for k,v in q.items() if k != "ledger"}
        data["ledger"] = {k:v for k,v in q.get("ledger",{}).items() if k != "events"}
        attempts = []
        for index in range(2):
            try:
                answer = self.inference.invoke(SYSTEM+" Summarize the supplied quota evidence. A stale cache, missing record or inconsistent cap requires review; an exceedance requires reject. Respect the supplied verified decision. Return a brief summary without repeating numbers.",data,QuotaOpinion,"quota_"+mode+"_"+scope+("_repair" if index else ""))
                attempts.append(answer)
                if answer["output"]["decision"] == q["decision"]:
                    q["raw_model"] = answer
                    break
                data["validation_feedback"] = "The opinion disagrees with the verified quota status. Recheck the supplied findings."
            except (ValueError,KeyError,TypeError) as exc:
                attempts.append({"error":type(exc).__name__})
                if not getattr(self.inference,"records",[]) or not self.inference.records[-1].get("response"):
                    break
            except Exception as exc:
                attempts.append({"error":type(exc).__name__})
                break
        else:
            q["findings"].append("MODEL_QUOTA_DISAGREEMENT")
        if "raw_model" not in q:
            q["findings"].append("QUOTA_MODEL_UNRESOLVED")
            if q["decision"] == "approve": q["decision"] = "review"
        q["model_attempts"] = attempts
        return q


class VerificationWorkflow(HistoricalWorkflow):
    def __init__(self, chain, inference, kind, mode="export"):
        super().__init__(chain,inference,kind,mode)
        self.quota_agent = QuotaValidationAgent(chain,inference)

    def gather(self, state):
        result = super().gather(state)
        content = result["documents"].get("shipment", {}).get("content")
        if isinstance(content, dict):
            observed = content.get("shipment_id")
            if type(observed) is not int or observed != state["shipment"]["id"]:
                result["findings"].append("SHIPMENT_DOCUMENT_CONFLICT:shipment_id")
        return result

    def interpret(self,state):
        package = source_package(state)
        evidence = dict(state["evidence"]) | package
        evidence["source_semantics"] = "CID sources are retrieved structured documents; quota:<mode>:<scope> sources are calculated read-only evidence, not document contents. Facts use exact source ids, JSON pointers and scalar values. Cite policy requirements by their one-based list indices."
        instructions = SYSTEM + " You are the fixed verification role. Return concise JSON. For approval include every required_approval_facts entry, all retrieved evidence CIDs and applicable policy requirement indices. Use /quantity from the shipment document, /account from each license, /shipment_id from permits, and /remaining from each quota source. Missing or stale evidence cannot be treated as valid."
        attempts = []
        final = None
        for index in range(3):
            try:
                answer = self.inference.invoke(instructions,evidence,Judgement,self.kind+"_verification"+("_repair" if index else ""))
                issues = contract_issues(answer["output"],state,package)
                attempts.append({"response":copy.deepcopy(answer),"validation_issues":issues})
                final = answer
                if not issues: break
                evidence["previous_draft"] = answer["output"]
                evidence["validation_feedback"] = issues
            except (ValueError,KeyError,TypeError) as exc:
                attempts.append({"error":type(exc).__name__})
                if not getattr(self.inference,"records",[]) or not self.inference.records[-1].get("response"): break
                evidence["validation_feedback"] = "Previous response was invalid or incomplete; return a compact valid object."
            except Exception as exc:
                attempts.append({"error":type(exc).__name__})
                break
        if final is None:
            final = {"error":"MODEL_UNAVAILABLE","output":{"decision":"review","summary":"Model response unavailable; human review required.","findings":["MODEL_UNAVAILABLE"],"evidence_cids":[],"facts":[],"policy_requirements":[]}}
        final["draft_attempts"] = attempts
        final["validation_issues"] = attempts[-1].get("validation_issues",[{"code":"MODEL_RESPONSE_UNRESOLVED"}]) if attempts else [{"code":"MODEL_RESPONSE_UNRESOLVED"}]
        return {"raw":final}

    def finalize(self,state):
        revised = dict(state)
        revised["findings"] = list(state["findings"])
        if state["raw"].get("validation_issues"):
            revised["findings"].append("MODEL_EVIDENCE_CONTRACT_UNRESOLVED")
        result = super().finalize(revised)["result"]
        result["workflow_version"] = VERSION
        result["model_attempts"] = state["raw"].get("draft_attempts",[])
        return {"result":result}


def run_assessment(assessment_id):
    with session() as db:
        row = db.get(Assessment,assessment_id)
        network,kind,provider,sid,account = row.network,row.agent,row.provider,row.shipment_id,row.account
        mode = row.payload.get("mode","export")
    started = time.monotonic()
    inference = None
    try:
        chain = Chain(network)
        inference = ReleaseInference(provider,assessment_id)
        result = VerificationWorkflow(chain,inference,kind,mode).invoke(sid)
        if kind == "national" and result["decision"] == "approve":
            from .permits import prepare_draft
            result["draft_id"] = prepare_draft(assessment_id,account,network,mode,result,chain)
        result["elapsed_seconds"] = round(time.monotonic()-started,3)
        status = "completed"
    except Exception as exc:
        result = {"decision":"review","findings":["ASSESSMENT_INCOMPLETE"],"error_type":type(exc).__name__,"requires_human_decision":True,"workflow_version":VERSION}
        status = "failed"
    finally:
        artifact = inference.close() if inference else None
    with session() as db:
        row = db.get(Assessment,assessment_id)
        row.status = status
        row.payload = {"mode":mode,"result":result,"run_artifact":artifact}
        audit(db,account,"assessment_finished",{"assessment_id":assessment_id,"status":status,"decision":result["decision"]})
        db.commit()
