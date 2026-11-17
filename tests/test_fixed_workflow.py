"""Final application checks; scripted answers test controls, not LLM accuracy."""
import copy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from medcomply.fixed_workflow import VerificationWorkflow,source_package,contract_issues,ReleaseBudget,RUN_PREFIX
from tools import provider_preflight as p


def fixture():
    state={"documents":{"shipment":{"cid":"shipment-cid","content":{"quantity":5000}},"exporter_license":{"cid":"exp-cid","content":{"account":"EXP"}},"importer_license":{"cid":"imp-cid","content":{"account":"IMP"}}},
           "findings":[],"quota_assessments":[{"mode":"import","scope":"national","decision":"approve","findings":[],"remaining":10000,"document_cid":"quota-cid"}],
           "evidence":{"policy":{"requirements":["Require appropriate licensing and quota."]}},"shipment":{"id":1},"initial_fingerprint":"same"}
    package=source_package(state)
    raw={"decision":"approve","summary":"Supported synthetic recommendation.","findings":[],"evidence_cids":["shipment-cid","exp-cid","imp-cid","quota-cid"],"facts":[{"source_id":f["source_id"],"path":f["path"],"value":package["sources"][f["source_id"]][f["path"][1:]]} for f in package["required_approval_facts"]],"policy_requirements":[1]}
    return state,raw


def test_wrong_source_value_and_missing_citation_are_detected():
    state,raw=fixture()
    assert contract_issues(raw,state,source_package(state))==[]
    raw["facts"][0]["value"]=5001
    raw["evidence_cids"].remove("exp-cid")
    codes={i["code"] for i in contract_issues(raw,state,source_package(state))}
    assert {"UNSUPPORTED_SOURCE_FACT","APPROVAL_CITATIONS_MISSING"} <= codes


@pytest.mark.parametrize("quota_decision",["review","reject"])
def test_quota_unresolved_or_exceeded_cannot_be_approved(quota_decision):
    state,raw=fixture();state["quota_assessments"][0]["decision"]=quota_decision
    assert any(i["code"]=="QUOTA_REQUIRES_"+quota_decision.upper() for i in contract_issues(raw,state,source_package(state)))


def test_repair_preserves_original_and_cannot_exceed_three_drafts():
    state,correct=fixture();wrong=copy.deepcopy(correct);wrong["evidence_cids"]=[]
    class Fake:
        def __init__(self):self.calls=0
        def invoke(self,*a):
            self.calls+=1
            return {"output":copy.deepcopy(wrong if self.calls==1 else correct)}
    fake=Fake();workflow=VerificationWorkflow(SimpleNamespace(fingerprint=lambda n:"same"),fake,"national","import")
    raw=workflow.interpret(state)["raw"]
    assert len(raw["draft_attempts"])==2 and raw["draft_attempts"][0]["response"]["output"]["evidence_cids"]==[]
    assert raw["validation_issues"]==[]
    fake=SimpleNamespace(invoke=lambda *a:{"output":copy.deepcopy(wrong)})
    workflow=VerificationWorkflow(SimpleNamespace(fingerprint=lambda n:"same"),fake,"national","import")
    state["raw"]=workflow.interpret(state)["raw"]
    assert len(state["raw"]["draft_attempts"])==3
    assert workflow.finalize(state)["result"]["decision"]=="review"


def test_uncertain_transport_is_not_automatically_retried():
    state,_=fixture()
    fake=SimpleNamespace(invoke=lambda *a:(_ for _ in ()).throw(p.ProbeError("Uncertain")))
    workflow=VerificationWorkflow(SimpleNamespace(),fake,"national","import")
    raw=workflow.interpret(state)["raw"]
    assert raw["output"]["decision"]=="review" and len(raw["draft_attempts"])==1


def test_release_cap_includes_uncertain_calls_and_preserves_shared_ledger(tmp_path):
    budget=ReleaseBudget(tmp_path/'spend.sqlite3',RUN_PREFIX+'first',120_000_000)
    for index in range(15):
        b=ReleaseBudget(tmp_path/'spend.sqlite3',RUN_PREFIX+str(index),120_000_000)
        b.reserve('openai','offline',100_000_000);b.db.close()
    with pytest.raises(p.BudgetError):budget.reserve('openai','over-release-cap',1)
    assert budget.total()==1_500_000_000
    budget.db.close()
