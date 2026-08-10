---
name: llm-council
description: Put one question to several frontier LLMs at once via OpenRouter, have them peer-review each other's answers anonymously, and get a chairman model's synthesis plus the ranking. Use for high-stakes calls where a second opinion is worth real cost - architecture decisions, reviewing a risky plan, contested factual questions, or when the user asks for a council, a panel, or multiple models' opinions. Requires an OpenRouter key via /login.
---

# LLM Council

Three stages, run for you by one call:

1. Every council model answers the question independently.
2. Each model sees all the answers labeled "Response A/B/C" and ranks them. The
   labels are anonymous, so models cannot favor their own or a vendor sibling's
   answer. Rankings are averaged into an aggregate.
3. A chairman model synthesizes a final answer from stages 1 and 2.

## Cost

One OpenRouter call per member for stage 1, one per member for stage 2, and one
for the chairman: nine calls on the default four-member council, each with the
full transcript in context. This is expensive and slow (a minute or more). Answer
from your own knowledge unless the question is genuinely high-stakes or the user
asked for a council.

## Setup

Requires an OpenRouter API key with credits. In Prime Agent, run `/login` and
choose OpenRouter, or set `OPENROUTER_API_KEY`. If the skill reports a missing
key, walk the user through `/login`.

## Usage

```python
print(await llm_council("Should this service use Postgres or SQLite for a write-heavy audit log?"))
```

Pick the council and chairman per call:

```python
print(await llm_council(
    "Review this migration plan for data-loss risk: ...",
    models="openai/gpt-5.4,anthropic/claude-opus-5,google/gemini-3.1-pro-preview",
    chairman="anthropic/claude-opus-5",
))
```

`detail="full"` also returns every individual response and peer review. Use it
when the disagreement between models is the interesting part; the default
`"summary"` returns the synthesis, the ranking, and any failures.

For programmatic use, `convene()` returns the structured result instead of
markdown - `stage1`, `stage2` (with `parsed_ranking`), `stage3`,
`aggregate_rankings`, `label_to_model`, `failures`, `notes`:

```python
from llm_council import convene

result = await convene("Which of these two API shapes ages better? ...")
best = result["aggregate_rankings"][0]
print(best["model"], best["average_rank"], best["votes"])
```

From a shell cell:

```bash
!llm_council "Is this benchmark methodology sound?" --detail full
```

## Defaults

Council: `openai/gpt-5.4`, `anthropic/claude-opus-5`,
`google/gemini-3.1-pro-preview`, `x-ai/grok-4.5`. Chairman:
`anthropic/claude-opus-5`. Override per call, or set defaults in the
environment:

- `PRIME_AGENT_COUNCIL_MODELS` - comma-separated council members.
- `PRIME_AGENT_COUNCIL_CHAIRMAN` - chairman model id.
- `PRIME_AGENT_COUNCIL_TIMEOUT` - per-request timeout in seconds (default 180).

A council of models that all share a vendor mostly measures agreement within
that vendor. Keep members from different vendors.

## Failure behavior

Members that fail are dropped and reported under `failures`; the council
continues with whoever answered. Only models that answered in stage 1 are asked
to review in stage 2. If a single member answers, peer review is skipped. If the
chairman fails, the top-ranked council response is returned instead, with a note.
Report these caveats to the user rather than presenting a degraded council as a
full one.

## Reporting results

Give the user the chairman's answer as the substance, then the ranking as
provenance. When the council disagreed on a matter of fact, surface the
disagreement instead of presenting the synthesis as settled - a synthesized
answer is not a verified one, and models agreeing does not make them right.
