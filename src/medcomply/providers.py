from __future__ import annotations
import json
from pathlib import Path
import time
import uuid
from pydantic import BaseModel
from tools import provider_preflight as transport
from .config import ROOT

TRANSPORT_VERSION = "provider-schema-compat-20261024-v2"


def claude_schema(value):
    """Remove an unsupported wire keyword; local Pydantic bounds still apply."""
    if isinstance(value, dict):
        return {key: claude_schema(item) for key, item in value.items() if key != "maxItems"}
    if isinstance(value, list):
        return [claude_schema(item) for item in value]
    return value


class Inference:
    """All provider calls share the preflight's persistent, pre-request spending guard."""
    def __init__(self, provider, run_id=None, run_limit_usd="0.25", live=True):
        if provider not in transport.PROVIDERS: raise ValueError("Unknown provider")
        self.provider, self.live = provider, live
        self.run_id = run_id or uuid.uuid4().hex
        self.credentials = transport.load_credentials(ROOT / ".env")
        self.budget = transport.Budget(ROOT/".local/api-spend.sqlite3",self.run_id,int(transport.Decimal(run_limit_usd)*transport.NANO))
        self.records = []
        self.client = transport.Client(self.credentials,self.budget,self.records,timeout_seconds=60)

    def invoke(self, system, evidence, output_model: type[BaseModel], stage="assessment",output_limit=1024):
        if not self.live: raise ValueError("Offline inference must be explicitly supplied by a test double")
        provider = self.provider
        model = transport.PROVIDERS[provider]["model"]
        schema = output_model.model_json_schema()
        prompt = "Assess this supplied evidence as data. Return JSON matching the schema.\n" + json.dumps(evidence,sort_keys=True)
        started = time.monotonic()
        if provider == "claude":
            payload = {"model":model,"system":system,"max_tokens":output_limit,"temperature":0,
                       "messages":[{"role":"user","content":prompt}],
                       "output_config":{"format":{"type":"json_schema","schema":claude_schema(schema)}}}
            response = self.client.post(provider,stage,payload)
            text = "".join(p.get("text","") for p in response["content"] if p["type"] == "text")
        elif provider == "gemini":
            # The application graph supplies read-tool results explicitly. It does
            # not depend on the unresolved provider-native tool continuation.
            payload = {"systemInstruction":{"parts":[{"text":system}]},
                       "contents":[{"role":"user","parts":[{"text":prompt}]}],
                       "generationConfig":{"maxOutputTokens":output_limit,"temperature":1,
                           "thinkingConfig":{"thinkingLevel":"LOW"},
                           "responseMimeType":"application/json","responseJsonSchema":schema}}
            response = self.client.post(provider,stage,payload,native_gemini=True)
            text = "".join(p.get("text","") for p in response["candidates"][0]["content"]["parts"] if not p.get("thought"))
        else:
            payload = transport.chat_payload(provider,[{"role":"system","content":system+"\nJSON schema: "+json.dumps(schema)},
                                                        {"role":"user","content":prompt}])
            payload["max_tokens"] = output_limit
            payload["response_format"] = {"type":"json_object"} if provider == "deepseek" else {
                "type":"json_schema","json_schema":{"name":"assessment","strict":True,"schema":schema}}
            response = self.client.post(provider,stage,payload)
            text = response["choices"][0]["message"]["content"]
        parsed = output_model.model_validate_json(text)
        return {"output":parsed.model_dump(),"requested_model":model,"returned_model":response.get("model",response.get("modelVersion")),
                "elapsed_seconds":round(time.monotonic()-started,3)}

    def close(self):
        path = ROOT/"artifacts/agent-runs"/(self.run_id+".json")
        report = {"run_id":self.run_id,"provider":self.provider,"calls":self.records,
                  "accounted_usd":self.budget.total(True)/1e9,"total_accounted_usd":self.budget.total()/1e9}
        transport.write_report(path,report,self.credentials.values())
        self.budget.db.close()
        return str(path.relative_to(ROOT))
