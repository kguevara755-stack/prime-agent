"""LLM Council: 3-stage multi-model deliberation over OpenRouter.

Stage 1 asks every council model the question independently. Stage 2 shows each
model all the answers, anonymized as "Response A/B/C", and asks for a ranking --
anonymization is what keeps models from favoring their own or a vendor sibling's
answer. Stage 3 has a chairman model synthesize a final answer from stages 1-2.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import httpx

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_COUNCIL_MODELS = (
    "openai/gpt-5.4",
    "anthropic/claude-opus-5",
    "google/gemini-3.1-pro-preview",
    "x-ai/grok-4.5",
)
DEFAULT_CHAIRMAN_MODEL = "anthropic/claude-opus-5"
DEFAULT_TIMEOUT = 180.0

NO_KEY_MESSAGE = (
    "The LLM Council is not set up yet: no OpenRouter API key is configured.\n"
    "Tell the user how to enable it:\n"
    "  1. Get an API key at https://openrouter.ai/keys (the council spends OpenRouter credits).\n"
    '  2. In Prime Agent, run /login and choose OpenRouter, or export OPENROUTER_API_KEY.\n'
    "Once the key is saved, the council works automatically."
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _agent_dir() -> Path:
    """Resolve the Prime Agent config dir the same way the runtime does."""
    raw = (
        os.environ.get("PRIME_AGENT_CODING_AGENT_DIR")
        or os.environ.get("PI_CODING_AGENT_DIR")
        or str(Path.home() / ".prime" / "agent")
    )
    return Path(raw).expanduser()


def _resolve_config_value(value: str) -> str:
    # Stored keys may be a literal or an env-var name; "!command" refs can't be run
    # safely here, so skip them.
    value = value.strip()
    if not value or value.startswith("!"):
        return ""
    return (os.environ.get(value) or value).strip()


def _resolve_api_key() -> str:
    """Read the OpenRouter key from the env, else from auth.json.

    auth.json is read on each call (not cached) so a key added via /login after
    the kernel started is still picked up. The env var wins.
    """
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key

    try:
        auth = json.loads((_agent_dir() / "auth.json").read_text())
        cred = auth.get("openrouter") if isinstance(auth, dict) else None
        if isinstance(cred, dict) and cred.get("type") == "api_key":
            return _resolve_config_value(str(cred.get("key") or ""))
    except (OSError, ValueError):
        pass
    return ""


def _split_models(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_models(models: str | Sequence[str] | None) -> list[str]:
    if isinstance(models, str):
        resolved = _split_models(models)
    elif models is not None:
        resolved = [str(m).strip() for m in models if str(m).strip()]
    else:
        resolved = _split_models(os.environ.get("PRIME_AGENT_COUNCIL_MODELS", "")) or list(
            DEFAULT_COUNCIL_MODELS
        )

    seen: set[str] = set()
    deduped = []
    for model in resolved:
        if model not in seen:
            seen.add(model)
            deduped.append(model)
    if not deduped:
        raise ValueError("No council models configured.")
    return deduped


def _resolve_chairman(chairman: str | None, models: Sequence[str]) -> str:
    return (
        (chairman or "").strip()
        or os.environ.get("PRIME_AGENT_COUNCIL_CHAIRMAN", "").strip()
        or DEFAULT_CHAIRMAN_MODEL
    )


async def _query_model(
    client: httpx.AsyncClient, model: str, prompt: str
) -> tuple[str | None, str | None]:
    """Query one model. Returns (content, error); exactly one is non-None."""
    try:
        response = await client.post(
            OPENROUTER_API_URL,
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        response.raise_for_status()
        data = response.json()
        content = (data["choices"][0]["message"].get("content") or "").strip()
    except httpx.HTTPStatusError as e:
        body = (e.response.text or "")[:400] if e.response is not None else ""
        return None, f"HTTP {e.response.status_code}: {body}"
    except (KeyError, IndexError, ValueError) as e:
        return None, f"Unexpected OpenRouter response shape: {e}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    if not content:
        return None, "Empty response"
    return content, None


async def _query_parallel(
    client: httpx.AsyncClient, models: Sequence[str], prompt: str
) -> list[tuple[str, str | None, str | None]]:
    results = await asyncio.gather(*(_query_model(client, model, prompt) for model in models))
    return [(model, content, error) for model, (content, error) in zip(models, results)]


def _ranking_prompt(question: str, stage1: Sequence[dict[str, Any]], labels: Sequence[str]) -> str:
    responses_text = "\n\n".join(
        f"Response {label}:\n{result['response']}" for label, result in zip(labels, stage1)
    )
    return f"""You are evaluating different responses to the following question:

Question: {question}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""


def _chairman_prompt(
    question: str, stage1: Sequence[dict[str, Any]], stage2: Sequence[dict[str, Any]]
) -> str:
    stage1_text = "\n\n".join(
        f"Model: {result['model']}\nResponse: {result['response']}" for result in stage1
    )
    stage2_text = (
        "\n\n".join(
            f"Model: {result['model']}\nRanking: {result['evaluation']}" for result in stage2
        )
        or "(no peer review was collected)"
    )
    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {question}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Where the council disagreed on a matter of fact, say so explicitly rather than averaging the disagreement away.

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""


def parse_ranking(text: str) -> list[str]:
    """Extract the ordered response labels from a stage 2 evaluation."""
    if "FINAL RANKING:" in text:
        section = text.split("FINAL RANKING:")[-1]
        numbered = re.findall(r"\d+\.\s*(Response [A-Z])", section)
        if numbered:
            return numbered
        labelled = re.findall(r"Response [A-Z]", section)
        if labelled:
            return labelled
    # Models that ignored the format still tend to mention labels in preference order.
    return re.findall(r"Response [A-Z]", text)


def aggregate_rankings(
    stage2: Sequence[dict[str, Any]], label_to_model: dict[str, str]
) -> list[dict[str, Any]]:
    """Average each model's rank position across all peer evaluations, best first."""
    positions: dict[str, list[int]] = defaultdict(list)
    for evaluation in stage2:
        seen: set[str] = set()
        for position, label in enumerate(evaluation["parsed_ranking"], start=1):
            # A malformed ranking can repeat a label; only the first mention counts.
            if label in seen or label not in label_to_model:
                continue
            seen.add(label)
            positions[label_to_model[label]].append(position)

    aggregate = [
        {
            "model": model,
            "average_rank": round(sum(ranks) / len(ranks), 2),
            "votes": len(ranks),
        }
        for model, ranks in positions.items()
        if ranks
    ]
    aggregate.sort(key=lambda entry: (entry["average_rank"], entry["model"]))
    return aggregate


async def convene(
    question: str,
    *,
    models: str | Sequence[str] | None = None,
    chairman: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run the full 3-stage council and return the structured result.

    Args:
        question: The question to put to the council.
        models: Council members as OpenRouter model ids (list, or comma-separated
            string). Defaults to PRIME_AGENT_COUNCIL_MODELS or a built-in set.
        chairman: Model that writes the final synthesis. Defaults to
            PRIME_AGENT_COUNCIL_CHAIRMAN or a built-in default.
        timeout: Per-request HTTP timeout in seconds.

    Returns:
        A dict with keys: question, models, chairman, stage1, stage2, stage3,
        aggregate_rankings, label_to_model, failures, notes. `stage3["response"]`
        is the final answer. `failures` lists per-model errors; the council
        degrades gracefully rather than failing outright when a member is down.

    Raises:
        RuntimeError: No OpenRouter API key is configured.
        ValueError: No council models are configured.
    """
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(NO_KEY_MESSAGE)

    council = _resolve_models(models)
    chairman_model = _resolve_chairman(chairman, council)
    if timeout is None:
        timeout = _env_float("PRIME_AGENT_COUNCIL_TIMEOUT", DEFAULT_TIMEOUT)

    result: dict[str, Any] = {
        "question": question,
        "models": council,
        "chairman": chairman_model,
        "stage1": [],
        "stage2": [],
        "stage3": {"model": chairman_model, "response": ""},
        "aggregate_rankings": [],
        "label_to_model": {},
        "failures": [],
        "notes": [],
    }
    stage1: list[dict[str, Any]] = result["stage1"]
    stage2: list[dict[str, Any]] = result["stage2"]
    failures: list[dict[str, str]] = result["failures"]
    notes: list[str] = result["notes"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Prime Agent LLM Council",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for model, content, error in await _query_parallel(client, council, question):
            if content is None:
                failures.append({"stage": "stage1", "model": model, "error": error or "unknown"})
            else:
                stage1.append({"model": model, "response": content})

        if not stage1:
            raise RuntimeError(
                "Every council model failed in stage 1:\n"
                + "\n".join(f"  {f['model']}: {f['error']}" for f in failures)
            )

        if len(stage1) > 1:
            labels = [chr(65 + i) for i in range(len(stage1))]
            label_to_model = {
                f"Response {label}": entry["model"] for label, entry in zip(labels, stage1)
            }
            result["label_to_model"] = label_to_model

            prompt = _ranking_prompt(question, stage1, labels)
            # Only models that answered in stage 1 get to review; a model that is
            # down stays down rather than being retried mid-deliberation.
            reviewers = [entry["model"] for entry in stage1]
            for model, content, error in await _query_parallel(client, reviewers, prompt):
                if content is None:
                    failures.append(
                        {"stage": "stage2", "model": model, "error": error or "unknown"}
                    )
                else:
                    stage2.append(
                        {
                            "model": model,
                            "evaluation": content,
                            "parsed_ranking": parse_ranking(content),
                        }
                    )
            result["aggregate_rankings"] = aggregate_rankings(stage2, label_to_model)
            if not stage2:
                notes.append("No peer review survived stage 2; the ranking is unavailable.")
        else:
            notes.append(
                f"Only {stage1[0]['model']} answered, so peer review was skipped "
                "and the chairman had a single response to work from."
            )

        content, error = await _query_model(
            client, chairman_model, _chairman_prompt(question, stage1, stage2)
        )

    if content is None:
        failures.append({"stage": "stage3", "model": chairman_model, "error": error or "unknown"})
        best = result["aggregate_rankings"][0]["model"] if result["aggregate_rankings"] else None
        fallback = next((e for e in stage1 if e["model"] == best), stage1[0])
        notes.append(
            f"Chairman {chairman_model} failed ({error}); falling back to the "
            f"top-ranked council response from {fallback['model']}."
        )
        result["stage3"] = {"model": fallback["model"], "response": fallback["response"]}
    else:
        result["stage3"] = {"model": chairman_model, "response": content}

    return result


def _truncate(text: str, max_output: int) -> str:
    if max_output <= 0 or len(text) <= max_output:
        return text
    marker = f"\n\n... [output truncated, {len(text)} chars total] ...\n\n"
    if len(marker) >= max_output:
        return text[:max_output]
    head = max_output - len(marker)
    return text[:head] + marker


def format_result(result: dict[str, Any], detail: str = "summary") -> str:
    """Render a convene() result as markdown."""
    lines = [f"# Council answer to: {result['question']}", ""]
    lines.append(result["stage3"]["response"] or "(the chairman returned nothing)")
    lines.append("")
    lines.append(f"-- synthesized by {result['stage3']['model']}")

    if result["aggregate_rankings"]:
        lines += ["", "## Peer ranking (anonymized, best first)", ""]
        for position, entry in enumerate(result["aggregate_rankings"], start=1):
            lines.append(
                f"{position}. {entry['model']} - average rank "
                f"{entry['average_rank']} across {entry['votes']} votes"
            )
    elif result["stage1"]:
        lines += ["", f"## Council: {', '.join(e['model'] for e in result['stage1'])}"]

    if result["notes"]:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in result["notes"]]

    if result["failures"]:
        lines += ["", "## Failures", ""]
        lines += [f"- {f['stage']} {f['model']}: {f['error']}" for f in result["failures"]]

    if detail == "full":
        lines += ["", "## Stage 1 - individual responses"]
        for entry in result["stage1"]:
            lines += ["", f"### {entry['model']}", "", entry["response"]]
        if result["stage2"]:
            lines += ["", "## Stage 2 - peer review"]
            for entry in result["stage2"]:
                ranked = [
                    result["label_to_model"].get(label, label) for label in entry["parsed_ranking"]
                ]
                lines += ["", f"### {entry['model']}", "", entry["evaluation"]]
                if ranked:
                    lines += ["", f"Parsed ranking: {' > '.join(ranked)}"]

    return "\n".join(lines)


async def run(
    question: str,
    *,
    models: str | None = None,
    chairman: str | None = None,
    detail: str = "summary",
    timeout: float | None = None,
    max_output: int = 16384,
) -> str:
    """Put a question to a council of LLMs and return their synthesized answer.

    Several models answer independently, peer-review each other's answers
    anonymously, and a chairman model synthesizes the result. Costs one
    OpenRouter call per member for the answer, one per member for the review,
    and one for the chairman, so reserve it for high-stakes questions.

    Args:
        question: The question to put to the council.
        models: Comma-separated OpenRouter model ids for the council members.
        chairman: Model id that writes the final synthesis.
        detail: "summary" for the answer plus ranking, "full" to also include
            every individual response and peer review.
        timeout: Per-request HTTP timeout in seconds.
        max_output: Truncate the rendered output to this many chars.

    Returns:
        Markdown: the chairman's answer, the aggregate peer ranking, and any
        failures or caveats. A missing API key or a council where every member
        failed comes back as an explanatory message rather than an exception.
    """
    if detail not in ("summary", "full"):
        raise ValueError(f'detail must be "summary" or "full", got {detail!r}')
    try:
        result = await convene(question, models=models, chairman=chairman, timeout=timeout)
    except RuntimeError as e:
        return str(e)
    return _truncate(format_result(result, detail=detail), max_output)
