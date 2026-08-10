from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import httpx

SKILL = (
    Path(__file__).parents[2]
    / "packages/coding-agent/skills/llm-council/src/llm_council/council.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("llm_council_under_test", SKILL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ok(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://openrouter.ai"),
    )


def fail(status: int = 500, text: str = "boom") -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("POST", "https://openrouter.ai"))


class FakeClient:
    """Stands in for httpx.AsyncClient; no network, records every call."""

    def __init__(self, handler: Callable[[str, str], httpx.Response], **kwargs) -> None:
        self.handler = handler
        self.kwargs = kwargs
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def post(self, _url: str, json: dict) -> httpx.Response:
        model = json["model"]
        prompt = json["messages"][0]["content"]
        self.calls.append((model, prompt))
        return self.handler(model, prompt)

    def prompts_for_stage(self, stage: str) -> list[str]:
        return [prompt for _model, prompt in self.calls if _classify(prompt) == stage]

    def models_for_stage(self, stage: str) -> list[str]:
        return [model for model, prompt in self.calls if _classify(prompt) == stage]


def _classify(prompt: str) -> str:
    if prompt.startswith("You are the Chairman"):
        return "stage3"
    if "FINAL RANKING:" in prompt:
        return "stage2"
    return "stage1"


THREE = ["vendor-a/one", "vendor-b/two", "vendor-c/three"]

# Every reviewer puts Response B first and Response A last, so the aggregate is
# unambiguous: two/B best, three/C middle, one/A worst.
RANKINGS = {
    "vendor-a/one": "A is thin. B is best.\n\nFINAL RANKING:\n1. Response B\n2. Response C\n3. Response A",
    "vendor-b/two": "Notes.\n\nFINAL RANKING:\n1. Response B\n2. Response C\n3. Response A",
    "vendor-c/three": "Notes.\n\nFINAL RANKING:\n1. Response B\n2. Response C\n3. Response A",
}


def happy_handler(model: str, prompt: str) -> httpx.Response:
    stage = _classify(prompt)
    if stage == "stage1":
        return ok(f"answer from {model}")
    if stage == "stage2":
        return ok(RANKINGS[model])
    return ok("the synthesized council answer")


def run_convene(module, handler, **kwargs):
    client_holder: list[FakeClient] = []

    def factory(**client_kwargs):
        client = FakeClient(handler, **client_kwargs)
        client_holder.append(client)
        return client

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=False):
        with patch.object(module.httpx, "AsyncClient", factory):
            result = asyncio.run(module.convene("the question", **kwargs))
    return result, client_holder[0]


class ParseRankingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_numbered_final_ranking(self) -> None:
        text = "chatter about Response A\n\nFINAL RANKING:\n1. Response C\n2. Response A\n3. Response B"
        self.assertEqual(
            self.module.parse_ranking(text), ["Response C", "Response A", "Response B"]
        )

    def test_unnumbered_final_ranking(self) -> None:
        text = "FINAL RANKING:\nResponse B\nResponse A"
        self.assertEqual(self.module.parse_ranking(text), ["Response B", "Response A"])

    def test_falls_back_to_label_order_without_header(self) -> None:
        text = "Response C is strongest, then Response A, then Response B."
        self.assertEqual(
            self.module.parse_ranking(text), ["Response C", "Response A", "Response B"]
        )

    def test_ignores_preamble_labels_when_header_present(self) -> None:
        text = "Response A looks great at first glance.\n\nFINAL RANKING:\n1. Response B\n2. Response A"
        self.assertEqual(self.module.parse_ranking(text), ["Response B", "Response A"])

    def test_no_labels_yields_empty(self) -> None:
        self.assertEqual(self.module.parse_ranking("I decline to rank these."), [])


class AggregateRankingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.label_to_model = {"Response A": "m/a", "Response B": "m/b"}

    def test_averages_positions_and_sorts_best_first(self) -> None:
        stage2 = [
            {"model": "m/a", "parsed_ranking": ["Response B", "Response A"]},
            {"model": "m/b", "parsed_ranking": ["Response A", "Response B"]},
            {"model": "m/c", "parsed_ranking": ["Response B", "Response A"]},
        ]
        aggregate = self.module.aggregate_rankings(stage2, self.label_to_model)
        self.assertEqual([e["model"] for e in aggregate], ["m/b", "m/a"])
        self.assertEqual(aggregate[0]["average_rank"], round(4 / 3, 2))
        self.assertEqual(aggregate[0]["votes"], 3)

    def test_duplicate_label_counts_once_at_first_position(self) -> None:
        stage2 = [{"model": "m/a", "parsed_ranking": ["Response B", "Response B", "Response A"]}]
        aggregate = self.module.aggregate_rankings(stage2, self.label_to_model)
        self.assertEqual(
            aggregate,
            [
                {"model": "m/b", "average_rank": 1.0, "votes": 1},
                {"model": "m/a", "average_rank": 3.0, "votes": 1},
            ],
        )

    def test_unknown_labels_are_dropped(self) -> None:
        stage2 = [{"model": "m/a", "parsed_ranking": ["Response Z", "Response A"]}]
        aggregate = self.module.aggregate_rankings(stage2, self.label_to_model)
        self.assertEqual(aggregate, [{"model": "m/a", "average_rank": 2.0, "votes": 1}])


class ConveneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_three_stages_and_aggregate(self) -> None:
        result, client = run_convene(self.module, happy_handler, models=THREE)

        self.assertEqual([e["model"] for e in result["stage1"]], THREE)
        self.assertEqual([e["model"] for e in result["stage2"]], THREE)
        self.assertEqual(result["stage3"]["response"], "the synthesized council answer")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["notes"], [])
        self.assertEqual(
            [e["model"] for e in result["aggregate_rankings"]],
            ["vendor-b/two", "vendor-c/three", "vendor-a/one"],
        )
        self.assertEqual(result["aggregate_rankings"][0]["average_rank"], 1.0)
        self.assertEqual(result["aggregate_rankings"][0]["votes"], 3)
        self.assertEqual(len(client.calls), 7)

    def test_stage2_prompt_is_anonymized(self) -> None:
        # Neutral answer bodies, so any model id in the prompt is real leakage
        # rather than an artifact of the stub's answer text.
        def handler(model: str, prompt: str) -> httpx.Response:
            if _classify(prompt) == "stage1":
                return ok(f"neutral answer {THREE.index(model)}")
            return happy_handler(model, prompt)

        _result, client = run_convene(self.module, handler, models=THREE)
        prompt = client.prompts_for_stage("stage2")[0]
        for model in THREE:
            self.assertNotIn(model, prompt)
        for label in ("Response A:", "Response B:", "Response C:"):
            self.assertIn(label, prompt)

    def test_label_mapping_follows_stage1_order(self) -> None:
        result, _client = run_convene(self.module, happy_handler, models=THREE)
        self.assertEqual(
            result["label_to_model"],
            {
                "Response A": "vendor-a/one",
                "Response B": "vendor-b/two",
                "Response C": "vendor-c/three",
            },
        )

    def test_chairman_sees_deanonymized_stage1_and_stage2(self) -> None:
        _result, client = run_convene(self.module, happy_handler, models=THREE)
        prompt = client.prompts_for_stage("stage3")[0]
        for model in THREE:
            self.assertIn(model, prompt)
        self.assertIn("answer from vendor-a/one", prompt)

    def test_failed_member_is_reported_and_excluded_from_review(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if model == "vendor-c/three" and _classify(prompt) == "stage1":
                return fail(502, "upstream down")
            return happy_handler(model, prompt)

        result, client = run_convene(self.module, handler, models=THREE)

        self.assertEqual([e["model"] for e in result["stage1"]], THREE[:2])
        self.assertEqual(client.models_for_stage("stage2"), THREE[:2])
        self.assertEqual(len(result["failures"]), 1)
        failure = result["failures"][0]
        self.assertEqual(failure["stage"], "stage1")
        self.assertEqual(failure["model"], "vendor-c/three")
        self.assertIn("502", failure["error"])
        self.assertIn("upstream down", failure["error"])
        self.assertEqual(result["stage3"]["response"], "the synthesized council answer")

    def test_empty_content_counts_as_failure(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if model == "vendor-a/one" and _classify(prompt) == "stage1":
                return ok("   ")
            return happy_handler(model, prompt)

        result, _client = run_convene(self.module, handler, models=THREE)
        self.assertEqual([e["model"] for e in result["stage1"]], THREE[1:])
        self.assertEqual(result["failures"][0]["error"], "Empty response")

    def test_all_members_failing_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Every council model failed"):
            run_convene(self.module, lambda *_: fail(500, "nope"), models=THREE)

    def test_single_responder_skips_peer_review(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if model != "vendor-a/one" and _classify(prompt) == "stage1":
                return fail(429, "rate limited")
            return happy_handler(model, prompt)

        result, client = run_convene(self.module, handler, models=THREE)

        self.assertEqual(result["stage2"], [])
        self.assertEqual(result["aggregate_rankings"], [])
        self.assertEqual(client.models_for_stage("stage2"), [])
        self.assertIn("peer review was skipped", result["notes"][0])
        self.assertEqual(result["stage3"]["response"], "the synthesized council answer")

    def test_stage2_failure_leaves_ranking_unavailable(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if _classify(prompt) == "stage2":
                return fail(500, "reviewer down")
            return happy_handler(model, prompt)

        result, _client = run_convene(self.module, handler, models=THREE)

        self.assertEqual(result["aggregate_rankings"], [])
        self.assertEqual(len(result["failures"]), 3)
        self.assertIn("No peer review survived", result["notes"][0])
        self.assertEqual(result["stage3"]["response"], "the synthesized council answer")

    def test_chairman_failure_falls_back_to_top_ranked_response(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if _classify(prompt) == "stage3":
                return fail(503, "chairman unavailable")
            return happy_handler(model, prompt)

        result, _client = run_convene(
            self.module, handler, models=THREE, chairman="vendor-a/one"
        )

        self.assertEqual(result["stage3"]["model"], "vendor-b/two")
        self.assertEqual(result["stage3"]["response"], "answer from vendor-b/two")
        self.assertEqual(result["failures"][0]["stage"], "stage3")
        self.assertIn("falling back", result["notes"][0])

    def test_malformed_payload_is_a_failure_not_a_crash(self) -> None:
        def handler(model: str, prompt: str) -> httpx.Response:
            if model == "vendor-a/one" and _classify(prompt) == "stage1":
                return httpx.Response(
                    200,
                    json={"error": {"message": "no choices"}},
                    request=httpx.Request("POST", "https://openrouter.ai"),
                )
            return happy_handler(model, prompt)

        result, _client = run_convene(self.module, handler, models=THREE)
        self.assertEqual([e["model"] for e in result["stage1"]], THREE[1:])
        self.assertIn("Unexpected OpenRouter response shape", result["failures"][0]["error"])

    def test_chairman_defaults_and_override(self) -> None:
        _result, client = run_convene(self.module, happy_handler, models=THREE)
        self.assertEqual(
            client.models_for_stage("stage3"), [self.module.DEFAULT_CHAIRMAN_MODEL]
        )

        _result, client = run_convene(
            self.module, happy_handler, models=THREE, chairman="vendor-c/three"
        )
        self.assertEqual(client.models_for_stage("stage3"), ["vendor-c/three"])

    def test_auth_header_and_timeout_reach_the_client(self) -> None:
        _result, client = run_convene(self.module, happy_handler, models=THREE, timeout=42.0)
        self.assertEqual(client.kwargs["timeout"], 42.0)
        self.assertEqual(client.kwargs["headers"]["Authorization"], "Bearer test-key")


class ModelResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_defaults_when_unset(self) -> None:
        with patch.dict("os.environ", {"PRIME_AGENT_COUNCIL_MODELS": ""}, clear=False):
            self.assertEqual(
                self.module._resolve_models(None), list(self.module.DEFAULT_COUNCIL_MODELS)
            )

    def test_env_override(self) -> None:
        with patch.dict(
            "os.environ", {"PRIME_AGENT_COUNCIL_MODELS": "a/one, b/two "}, clear=False
        ):
            self.assertEqual(self.module._resolve_models(None), ["a/one", "b/two"])

    def test_argument_beats_env_and_dedupes(self) -> None:
        with patch.dict("os.environ", {"PRIME_AGENT_COUNCIL_MODELS": "a/one"}, clear=False):
            self.assertEqual(self.module._resolve_models("b/two, b/two,c/three"), ["b/two", "c/three"])
            self.assertEqual(self.module._resolve_models(["b/two", "b/two"]), ["b/two"])

    def test_empty_selection_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "No council models"):
            self.module._resolve_models(" , ")


class ApiKeyResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_env_wins(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "from-env"}, clear=False):
            self.assertEqual(self.module._resolve_api_key(), "from-env")

    def test_reads_auth_json(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "auth.json").write_text(
                json.dumps({"openrouter": {"type": "api_key", "key": "from-auth"}})
            )
            env = {"OPENROUTER_API_KEY": "", "PRIME_AGENT_CODING_AGENT_DIR": tmp}
            with patch.dict("os.environ", env, clear=False):
                self.assertEqual(self.module._resolve_api_key(), "from-auth")

    def test_missing_key_returns_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {"OPENROUTER_API_KEY": "", "PRIME_AGENT_CODING_AGENT_DIR": tmp}
            with patch.dict("os.environ", env, clear=False):
                self.assertEqual(self.module._resolve_api_key(), "")

    def test_run_reports_setup_instead_of_raising(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {"OPENROUTER_API_KEY": "", "PRIME_AGENT_CODING_AGENT_DIR": tmp}
            with patch.dict("os.environ", env, clear=False):
                output = asyncio.run(self.module.run("anything"))
        self.assertIn("no OpenRouter API key is configured", output)
        self.assertIn("/login", output)


class FormatResultTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.result, _client = run_convene(self.module, happy_handler, models=THREE)

    def test_summary_omits_transcripts(self) -> None:
        text = self.module.format_result(self.result, detail="summary")
        self.assertIn("the synthesized council answer", text)
        self.assertIn("Peer ranking", text)
        self.assertIn("1. vendor-b/two - average rank 1.0 across 3 votes", text)
        self.assertNotIn("answer from vendor-a/one", text)

    def test_full_includes_transcripts_and_deanonymized_ranking(self) -> None:
        text = self.module.format_result(self.result, detail="full")
        self.assertIn("answer from vendor-a/one", text)
        self.assertIn("Stage 2 - peer review", text)
        self.assertIn("Parsed ranking: vendor-b/two > vendor-c/three > vendor-a/one", text)

    def test_failures_and_notes_are_surfaced(self) -> None:
        result = dict(self.result)
        result["failures"] = [{"stage": "stage1", "model": "m/x", "error": "HTTP 500"}]
        result["notes"] = ["something degraded"]
        text = self.module.format_result(result)
        self.assertIn("- stage1 m/x: HTTP 500", text)
        self.assertIn("- something degraded", text)

    def test_truncation_marks_the_cut(self) -> None:
        self.assertEqual(self.module._truncate("abcdef", 0), "abcdef")
        truncated = self.module._truncate("x" * 5000, 200)
        self.assertEqual(len(truncated), 200)
        self.assertIn("output truncated, 5000 chars total", truncated)

    def test_run_rejects_bad_detail(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            with self.assertRaisesRegex(ValueError, "summary"):
                asyncio.run(self.module.run("q", detail="verbose"))


if __name__ == "__main__":
    unittest.main()
