"""Small direct-provider connectivity test; no SDKs or application services needed.

Default: check key presence without network access. --live sends synthetic prompts.
This is an integration smoke test, not a compliance evaluation or an agent build.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import threading
from functools import wraps
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from urllib import error, request
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOTAL_LIMIT_NANO = 20_000_000_000
MAX_OUTPUT = 512
NANO = Decimal(1_000_000_000)
PROVIDERS = {
    "openai": {
        "key": "OPENAI_API_KEY", "model": "gpt-4.1-2025-04-14",
        "url": "https://api.openai.com/v1/chat/completions",
        "input_usd_million": "2", "output_usd_million": "8",
        "pricing_source": "https://developers.openai.com/api/docs/models/gpt-4.1",
    },
    "gemini": {
        "key": "GEMINI_API_KEY", "model": "gemini-3.8-flash",
        "url": "https://generativelanguage.googleapis.com/v1beta/interactions",
        "input_usd_million": "0.75", "output_usd_million": "3.75",
        "pricing_source": "https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash",
        "pricing_valid_through": "2026-12-31",
    },
    "claude": {
        "key": "ANTHROPIC_API_KEY", "model": "claude-haiku-4-5-20251001",
        "url": "https://api.anthropic.com/v1/messages",
        "input_usd_million": "1", "output_usd_million": "5",
        "pricing_source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "mistral": {
        "key": "MISTRAL_API_KEY", "model": "mistral-small-2603",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "input_usd_million": "0.15", "output_usd_million": "0.60",
        "pricing_source": "https://docs.mistral.ai/models/mistral-small-4-0-26-03",
    },
    "deepseek": {
        "key": "DEEPSEEK_API_KEY", "model": "deepseek-flash",
        "url": "https://api.deepseek.com/chat/completions",
        # Peak rates conservatively bound off-peak and cache discounts.
        "input_usd_million": "0.30", "output_usd_million": "1.20",
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
    },
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["WITHIN_QUOTA", "EXCEEDS_QUOTA"]},
        "remaining": {"type": "integer"},
    },
    "required": ["decision", "remaining"],
    "additionalProperties": False,
}
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "country": {"type": "string"},
        "substance": {"type": "string"},
        "year": {"type": "integer"},
        "mode": {"type": "string", "enum": ["export", "import"]},
    },
    "required": ["country", "substance", "year", "mode"],
    "additionalProperties": False,
}
TOOL_NAME = "get_quota_snapshot"
TOOL_ARGS = {"country": "USA", "substance": "morphine", "year": 2025, "mode": "export"}
SNAPSHOT = {"limit": 1000, "used": 600, "reserved": 150, "unit": "g"}
EXPECTED = {"decision": "WITHIN_QUOTA", "remaining": 250}
SYSTEM = (
    "Synthetic integration test. Return only the requested JSON object with decision "
    "and integer remaining. remaining = limit - used - reserved. WITHIN_QUOTA means "
    "shipment quantity <= remaining; otherwise EXCEEDS_QUOTA. Do not authorize any action."
)
DIRECT_PROMPT = "Assess a 200 g shipment using this synthetic snapshot: " + json.dumps(SNAPSHOT)
TOOL_PROMPT = (
    "First call get_quota_snapshot for country USA, substance morphine, year 2025, "
    "mode export. Then assess a 200 g shipment using the returned snapshot."
)


class BudgetError(RuntimeError):
    pass


class ProbeError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_credentials(path):
    """Read simple literal dotenv values; never execute or interpolate file contents."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            name, sep, value = line.partition("=")
            name, value = name.strip(), value.strip()
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Invalid dotenv assignment; values have not been displayed")
            if name in values:
                raise ValueError("Duplicate dotenv variable: " + name)
            if value[:1] in ("'", '"'):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("Unclosed dotenv quote for " + name)
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].rstrip()
            values[name] = value
    if not values.get("GEMINI_API_KEY"):
        values["GEMINI_API_KEY"] = values.get("GOOGLE_API_KEY", "")
    return {
        p["key"]: os.environ.get(p["key"]) or values.get(p["key"], "")
        or (os.environ.get("GOOGLE_API_KEY", "") if name == "gemini" else "")
        for name, p in PROVIDERS.items()
    }


def scrub(value, secrets):
    if isinstance(value, dict):
        return {k: scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return re.sub(r"\bAIza[A-Za-z0-9_-]+", "[REDACTED]", value)
    return value


def cost_nano(provider, inputs, outputs):
    p = PROVIDERS[provider]
    dollars = (Decimal(inputs) * Decimal(p["input_usd_million"])
               + Decimal(outputs) * Decimal(p["output_usd_million"])) / 1_000_000
    return int((dollars * NANO).to_integral_value(rounding=ROUND_CEILING))


def synchronized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return guarded


class Budget:
    """Atomic reservation before every POST; uncertain calls retain their reservation."""
    def __init__(self, path, run_id, run_limit_nano):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.run_id, self.run_limit = run_id, run_limit_nano
        self.db.execute("""CREATE TABLE IF NOT EXISTS api_spend (
            id TEXT PRIMARY KEY, run_id TEXT, provider TEXT, stage TEXT,
            created_utc TEXT, reserved_nano INTEGER, accounted_nano INTEGER,
            status TEXT, usage_json TEXT)""")
        self.db.commit()

    @synchronized
    def total(self, run_only=False):
        query = "SELECT COALESCE(SUM(accounted_nano), 0) FROM api_spend"
        params = ()
        if run_only:
            query += " WHERE run_id = ?"
            params = (self.run_id,)
        return self.db.execute(query, params).fetchone()[0]

    @synchronized
    def reserve(self, provider, stage, amount):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.total() + amount > TOTAL_LIMIT_NANO:
                raise BudgetError("Total $20 API budget would be exceeded")
            if self.total(True) + amount > self.run_limit:
                raise BudgetError("Preflight run budget would be exceeded")
            call_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO api_spend VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (call_id, self.run_id, provider, stage, utc_now(), amount,
                             amount, "reserved_or_uncertain", None))
            self.db.commit()
            return call_id
        except BaseException:
            self.db.rollback()
            raise

    @synchronized
    def settle(self, call_id, provider, usage):
        tokens = usage_tokens(provider, usage)
        if tokens is None:
            return None
        amount = cost_nano(provider, *tokens)
        self.db.execute("UPDATE api_spend SET accounted_nano=?, status=?, usage_json=? WHERE id=?",
                        (amount, "usage_priced_at_conservative_rates", json.dumps(usage), call_id))
        self.db.commit()
        if self.total() > TOTAL_LIMIT_NANO or self.total(True) > self.run_limit:
            raise BudgetError("Reported usage exceeded a budget reservation; stop further calls")
        return amount


def usage_tokens(provider, usage):
    if not isinstance(usage, dict):
        return None
    if provider == "gemini" and "total_input_tokens" in usage:
        inputs = usage.get("total_input_tokens")
        outputs = max(usage.get("total_output_tokens", 0) + usage.get("total_thought_tokens", 0), usage.get("total_tokens", 0) - (inputs or 0))
    elif provider == "gemini":
        inputs = usage.get("promptTokenCount")
        outputs = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        if "totalTokenCount" not in usage:
            return None
        outputs = max(outputs, usage["totalTokenCount"] - (inputs or 0))
    elif provider == "claude":
        inputs = usage.get("input_tokens")
        if inputs is not None:
            inputs += usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        outputs = usage.get("output_tokens")
    else:
        inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(v) is not int or v < 0 for v in (inputs, outputs)):
        return None
    return inputs, outputs


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, credentials, budget, records, timeout_seconds=30):
        self.credentials, self.budget, self.records = credentials, budget, records
        self.opener = request.build_opener(NoRedirect())
        if timeout_seconds not in (30, 60, 120):
            raise ValueError("Preflight timeout must be 30, 60, or 120 seconds")
        self.timeout_seconds = timeout_seconds

    def post(self, provider, stage, payload, *, native_gemini=False):
        p = PROVIDERS[provider]
        if native_gemini and provider != "gemini":
            raise ProbeError("Native Gemini endpoint is only valid for Gemini")
        url = ("https://generativelanguage.googleapis.com/v1beta/models/" + p["model"] + ":generateContent") if native_gemini else p["url"]
        if utc_now()[:10] > p.get("pricing_valid_through", "9999-12-31"):
            raise ProbeError("Recorded pricing has expired; refresh rates before sending requests")
        body = json.dumps(payload, ensure_ascii=True).encode()
        # Byte-count bound plus allowance for provider-injected prompts/tool framing.
        output_limit = payload.get("max_tokens", payload.get("generation_config", {}).get("max_output_tokens", payload.get("generationConfig", {}).get("maxOutputTokens", MAX_OUTPUT)))
        if type(output_limit) is not int or not 1 <= output_limit <= 4096:
            raise ProbeError("Explicit output limit of 1 to 4096 tokens required")
        reservation = cost_nano(provider, len(body) + 4096, output_limit)
        call_id = self.budget.reserve(provider, stage, reservation)
        record = {"call_id": call_id, "provider": provider, "stage": stage,
                  "started_utc": utc_now(), "url": url, "request": copy.deepcopy(payload),
                  "timeout_seconds": self.timeout_seconds,
                  "reservation_usd": reservation / 1e9}
        self.records.append(record)
        headers = {"Content-Type": "application/json", "User-Agent": "MedComply-preflight/1"}
        key = self.credentials[p["key"]]
        if provider == "gemini":
            headers["x-goog-api-key"] = key
        elif provider == "claude":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = "Bearer " + key
        started = time.monotonic()
        print(f"{provider}: {stage}", flush=True)
        try:
            req = request.Request(url, body, headers, method="POST")
            with self.opener.open(req, timeout=self.timeout_seconds) as response:
                raw = response.read(2_000_000)
                record["http_status"] = response.status
                record["request_id"] = response.headers.get("x-request-id")
            data = json.loads(raw)
            record["response"] = data
            usage = data.get("usageMetadata", data.get("usage")) if provider == "gemini" else data.get("usage")
            amount = self.budget.settle(call_id, provider, usage)
            record["conservative_usage_cost_usd"] = None if amount is None else amount / 1e9
            record["usage_complete"] = amount is not None
            return data
        except error.HTTPError as exc:
            record["http_status"] = exc.code
            record["error_body"] = exc.read(32_000).decode("utf-8", errors="replace")
            raise ProbeError(f"HTTP {exc.code}; inspect the redacted run report") from None
        except (error.URLError, TimeoutError, OSError) as exc:
            record["transport_error_type"] = type(exc).__name__
            raise ProbeError("Network request failed; reservation retained, no automatic retry") from None
        except (ValueError, KeyError, TypeError) as exc:
            record["parse_error_type"] = type(exc).__name__
            raise ProbeError("Invalid response or usage; inspect the redacted run report") from None
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)


def structured_format(provider):
    if provider == "deepseek":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {
        "name": "quota_assessment", "strict": True, "schema": RESULT_SCHEMA}}


def chat_payload(provider, messages):
    result = {"model": PROVIDERS[provider]["model"], "messages": messages,
              "max_tokens": MAX_OUTPUT, "temperature": 0}
    if provider == "deepseek":
        result["thinking"] = {"type": "disabled"}
    if provider == "mistral":
        result["reasoning_effort"] = "none"
    return result


def parse_assessment(text):
    obj = json.loads(text)
    if not isinstance(obj, dict) or set(obj) != set(EXPECTED):
        raise ProbeError("Assessment has invalid fields")
    if type(obj["remaining"]) is not int or obj != EXPECTED:
        raise ProbeError("Assessment differs from the synthetic expected answer")
    return obj


def validate_tool(name, args):
    if name != TOOL_NAME or not isinstance(args, dict) or args != TOOL_ARGS or type(args.get("year")) is not int:
        raise ProbeError("Unexpected tool or arguments; no tool was executed")


def chat_probe(client, provider):
    messages = [{"role": "system", "content": SYSTEM + " JSON example: " + json.dumps(EXPECTED)},
                {"role": "user", "content": DIRECT_PROMPT}]
    payload = chat_payload(provider, messages)
    payload["response_format"] = structured_format(provider)
    response = client.post(provider, "structured_response", payload)
    parse_assessment(response["choices"][0]["message"]["content"])
    results = {"structured_response": "passed", "tool_call": "not_run", "tool_round_trip": "not_run"}
    messages = [{"role": "system", "content": SYSTEM + " JSON example: " + json.dumps(EXPECTED)},
                {"role": "user", "content": TOOL_PROMPT}]
    payload = chat_payload(provider, messages)
    payload["tools"] = [{"type": "function", "function": {
        "name": TOOL_NAME, "description": "Read a synthetic, read-only quota snapshot.",
        "parameters": TOOL_SCHEMA}}]
    payload["tool_choice"] = {"type": "function", "function": {"name": TOOL_NAME}}
    response = client.post(provider, "tool_call", payload)
    message = response["choices"][0]["message"]
    calls = message.get("tool_calls", [])
    if len(calls) != 1:
        raise ProbeError("Expected exactly one tool call")
    call = calls[0]
    validate_tool(call["function"]["name"], json.loads(call["function"]["arguments"]))
    results["tool_call"] = "passed"
    # Preserve only protocol fields, not provider metadata or reasoning content.
    messages += [{"role": "assistant", "content": message.get("content"), "tool_calls": calls},
                 {"role": "tool", "tool_call_id": call["id"], "name": TOOL_NAME,
                  "content": json.dumps(SNAPSHOT)}]
    payload = chat_payload(provider, messages)
    payload["response_format"] = structured_format(provider)
    response = client.post(provider, "tool_round_trip", payload)
    parse_assessment(response["choices"][0]["message"]["content"])
    results["tool_round_trip"] = "passed"
    return results


def gemini_probe(client):
    def base(history):
        return {"model": PROVIDERS["gemini"]["model"], "input": history,
                "system_instruction": SYSTEM, "store": False,
                "generation_config": {"max_output_tokens": MAX_OUTPUT, "temperature": 1,
                                      "thinking_level": "low"}}

    def structured(payload):
        payload["response_format"] = {"type": "text", "mime_type": "application/json", "schema": RESULT_SCHEMA}

    def assessment(data):
        if data.get("status") != "completed":
            raise ProbeError("Gemini interaction did not complete")
        text = "".join(part.get("text", "") for step in data["steps"]
                       if step["type"] == "model_output" for part in step.get("content", []))
        parse_assessment(text)

    payload = base(DIRECT_PROMPT)
    structured(payload)
    assessment(client.post("gemini", "structured_response", payload))
    history = [{"type": "user_input", "content": [{"type": "text", "text": TOOL_PROMPT}]}]
    declarations = [{"type": "function", "name": TOOL_NAME,
                     "description": "Read a synthetic, read-only quota snapshot.", "parameters": TOOL_SCHEMA}]
    payload = base(history)
    payload["tools"] = declarations
    payload["generation_config"]["tool_choice"] = {"allowed_tools": {"mode": "any", "tools": [TOOL_NAME]}}
    response = client.post("gemini", "tool_call", payload)
    calls = [step for step in response["steps"] if step["type"] == "function_call"]
    if len(calls) != 1:
        raise ProbeError("Expected exactly one tool call")
    validate_tool(calls[0]["name"], calls[0]["arguments"])
    history += response["steps"]
    history += [{"type": "function_result", "name": TOOL_NAME, "call_id": calls[0]["id"],
                 "result": [{"type": "text", "text": json.dumps(SNAPSHOT)}]}]
    payload = base(history)
    payload["tools"] = declarations
    structured(payload)
    assessment(client.post("gemini", "tool_round_trip", payload))
    return {"structured_response": "passed", "tool_call": "passed", "tool_round_trip": "passed"}


def claude_probe(client):
    def base(messages):
        return {"model": PROVIDERS["claude"]["model"], "max_tokens": MAX_OUTPUT,
                "temperature": 0, "system": SYSTEM, "messages": messages}

    def structured(payload):
        payload["output_config"] = {"format": {"type": "json_schema", "schema": RESULT_SCHEMA}}

    def assessment(data):
        parse_assessment("".join(p.get("text", "") for p in data["content"] if p["type"] == "text"))

    payload = base([{"role": "user", "content": DIRECT_PROMPT}])
    structured(payload)
    assessment(client.post("claude", "structured_response", payload))
    messages = [{"role": "user", "content": TOOL_PROMPT}]
    payload = base(messages)
    payload["tools"] = [{"name": TOOL_NAME, "description": "Read a synthetic, read-only quota snapshot.",
                         "input_schema": TOOL_SCHEMA, "strict": True}]
    payload["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
    response = client.post("claude", "tool_call", payload)
    calls = [p for p in response["content"] if p["type"] == "tool_use"]
    if len(calls) != 1:
        raise ProbeError("Expected exactly one tool call")
    validate_tool(calls[0]["name"], calls[0]["input"])
    messages += [{"role": "assistant", "content": response["content"]},
                 {"role": "user", "content": [{"type": "tool_result", "tool_use_id": calls[0]["id"],
                                                "content": json.dumps(SNAPSHOT)}]}]
    payload = base(messages)
    structured(payload)
    assessment(client.post("claude", "tool_round_trip", payload))
    return {"structured_response": "passed", "tool_call": "passed", "tool_round_trip": "passed"}


def write_report(path, data, secrets):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(scrub(data, secrets), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send metered synthetic API requests")
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    parser.add_argument("--run-budget-usd", default="0.25", help="Preflight subset of the $20 total budget (max $0.50)")
    parser.add_argument("--request-timeout-seconds", type=int, choices=(30, 60, 120), default=30)
    args = parser.parse_args()
    run_limit = Decimal(args.run_budget_usd)
    if not run_limit.is_finite() or not 0 < run_limit <= Decimal("0.50"):
        parser.error("Preflight budget must be positive and no greater than $0.50")
    credentials = load_credentials(ROOT / ".env")
    missing = []
    for provider in args.providers:
        present = bool(credentials[PROVIDERS[provider]["key"]])
        print(f"{provider}: key {'present' if present else 'missing'}", flush=True)
        if not present:
            missing.append(provider)
    if missing or not args.live:
        return int(bool(missing))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    budget = Budget(ROOT / ".local" / "api-spend.sqlite3", run_id, int(run_limit * NANO))
    report_path = ROOT / "artifacts" / "provider-preflight" / (run_id + ".json")
    report = {"run_id": run_id, "started_utc": utc_now(), "purpose": "synthetic_provider_integration_smoke_test",
              "scope": "Not a compliance benchmark; no contracts or regulatory records changed",
              "pricing_checked_date": "2026-10-23", "run_limit_usd": str(run_limit), "total_limit_usd": 20,
              "cost_basis": "Conservative standard list rates, Gemini paid-tier assumption, DeepSeek peak rates; discounts/free allowances not assumed. Not an invoice.",
              "max_output_tokens_per_request": MAX_OUTPUT, "automatic_retries": 0,
              "provider_config": {k: v for k, v in PROVIDERS.items() if k in args.providers},
              "providers": {}, "calls": []}
    client = Client(credentials, budget, report["calls"], timeout_seconds=args.request_timeout_seconds)
    secrets = list(credentials.values())
    try:
        for provider in args.providers:
            result = {"requested_model": PROVIDERS[provider]["model"]}
            report["providers"][provider] = result
            try:
                if provider == "gemini":
                    result.update(gemini_probe(client))
                elif provider == "claude":
                    result.update(claude_probe(client))
                else:
                    result.update(chat_probe(client, provider))
                result["status"] = "passed"
            except (ProbeError, KeyError, IndexError, TypeError, ValueError) as exc:
                result["status"] = "failed"
                result["error"] = str(exc) if isinstance(exc, ProbeError) else "Unexpected response shape: " + type(exc).__name__
            print(f"{provider}: {result['status']}", flush=True)
            write_report(report_path, report, secrets)
    finally:
        report["finished_utc"] = utc_now()
        report["run_accounted_usd"] = budget.total(True) / 1e9
        report["total_accounted_usd"] = budget.total() / 1e9
        write_report(report_path, report, secrets)
        print("Report: " + str(report_path), flush=True)
        print(f"Conservative usage/reservation accounting: ${budget.total(True) / 1e9:.6f} this run; ${budget.total() / 1e9:.6f} total", flush=True)
        budget.db.close()
    return int(any(p["status"] != "passed" for p in report["providers"].values()))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BudgetError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
