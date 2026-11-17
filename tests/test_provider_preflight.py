"""Offline checks for credential handling, cost guards, and fixture validation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "provider_preflight.py"
spec = importlib.util.spec_from_file_location("provider_preflight", MODULE_PATH)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PreflightTests(unittest.TestCase):
    def test_budget_persists_uncertain_calls_and_serializes_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spend.sqlite3"
            first = p.Budget(path, "run", 100)
            second = p.Budget(path, "run", 100)
            first.reserve("openai", "first", 60)
            with self.assertRaises(p.BudgetError):
                second.reserve("openai", "second", 60)
            self.assertEqual(second.total(), 60)
            first.db.close()
            second.db.close()

    def test_total_ceiling_covers_distinct_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = p.Budget(Path(directory) / "spend.sqlite3", "old", p.TOTAL_LIMIT_NANO)
            budget.reserve("openai", "old", p.TOTAL_LIMIT_NANO - 1)
            budget.run_id = "new"
            with self.assertRaises(p.BudgetError):
                budget.reserve("openai", "new", 2)
            budget.db.close()

    def test_settlement_does_not_count_cached_or_thinking_tokens_twice(self):
        self.assertEqual(p.usage_tokens("deepseek", {"prompt_tokens": 100, "completion_tokens": 50,
                                                   "prompt_cache_hit_tokens": 80,
                                                   "completion_tokens_details": {"reasoning_tokens": 30}}), (100, 50))
        self.assertEqual(p.usage_tokens("gemini", {"promptTokenCount": 100, "candidatesTokenCount": 20,
                                                  "thoughtsTokenCount": 30, "totalTokenCount": 150}), (100, 50))
        self.assertEqual(p.usage_tokens("claude", {"input_tokens": 20, "cache_read_input_tokens": 80,
                                                  "cache_creation_input_tokens": 10, "output_tokens": 50}), (110, 50))

    def test_missing_usage_retains_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = p.Budget(Path(directory) / "spend.sqlite3", "run", 1_000_000)
            call_id = budget.reserve("openai", "stage", 100_000)
            self.assertIsNone(budget.settle(call_id, "openai", {}))
            self.assertEqual(budget.total(), 100_000)
            budget.settle(call_id, "openai", {"prompt_tokens": 1, "completion_tokens": 1})
            self.assertEqual(budget.total(), 10_000)
            budget.db.close()

    def test_dotenv_is_literal_and_secrets_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(p.os.environ, {}, clear=True):
            path = Path(directory) / ".env"
            secret = "literal-$name-$(noexecution)-value"
            path.write_text('OPENAI_API_KEY="' + secret + '"\n', encoding="utf-8")
            credentials = p.load_credentials(path)
            self.assertEqual(credentials["OPENAI_API_KEY"], secret)
            report = {"nested": ["error: " + secret]}
            self.assertNotIn(secret, json.dumps(p.scrub(report, credentials.values())))

    def test_unexpected_tool_and_bad_assessment_fail_closed(self):
        p.validate_tool(p.TOOL_NAME, p.TOOL_ARGS)
        with self.assertRaises(p.ProbeError):
            p.validate_tool("update_quota_usage", p.TOOL_ARGS)
        for value in ({"decision": "WITHIN_QUOTA", "remaining": True},
                      {"decision": "WITHIN_QUOTA", "remaining": 250, "approved": True},
                      {"decision": "WITHIN_QUOTA", "remaining": 400}):
            with self.assertRaises(p.ProbeError):
                p.parse_assessment(json.dumps(value))

    def test_redirect_does_not_forward_credentials(self):
        self.assertIsNone(p.NoRedirect().redirect_request(None, None, 307, "", {}, "https://untrusted.invalid"))

    def test_budget_rejection_prevents_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = p.Budget(Path(directory) / "spend.sqlite3", "run", 1)
            client = p.Client({"OPENAI_API_KEY": "test-only"}, budget, [])
            with patch.object(client.opener, "open") as network:
                with self.assertRaises(p.BudgetError):
                    client.post("openai", "stage", {"model": "test"})
                network.assert_not_called()
            budget.db.close()

    def test_expired_gemini_pricing_prevents_network_request(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = p.Budget(Path(directory) / "spend.sqlite3", "run", 1_000_000_000)
            client = p.Client({"GEMINI_API_KEY": "test-only"}, budget, [])
            with patch.object(p, "utc_now", return_value="2027-01-01T00:00:00+00:00"), patch.object(client.opener, "open") as network:
                with self.assertRaises(p.ProbeError):
                    client.post("gemini", "stage", {"model": "test"})
                network.assert_not_called()
            self.assertEqual(budget.total(), 0)
            budget.db.close()


if __name__ == "__main__":
    unittest.main()
