#!/usr/bin/env python3
"""Run the LLM Council from a plain shell, without Prime Agent.

    pip install httpx
    export OPENROUTER_API_KEY=sk-or-...
    ./ask_council.py "your question"
    ./ask_council.py --detail full --models "openai/gpt-5.4,anthropic/claude-opus-5" "your question"

Inside Prime Agent this script is unnecessary: call `await llm_council(...)` in
the kernel instead.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from llm_council import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask a council of LLMs one question.")
    parser.add_argument("question", help="The question to put to the council.")
    parser.add_argument("--models", default=None, help="Comma-separated OpenRouter model ids.")
    parser.add_argument("--chairman", default=None, help="Model that writes the synthesis.")
    parser.add_argument(
        "--detail",
        default="summary",
        choices=["summary", "full"],
        help="'full' also prints every response and peer review.",
    )
    parser.add_argument("--timeout", type=float, default=None, help="Per-request timeout, seconds.")
    parser.add_argument(
        "--max-output", type=int, default=0, help="Truncate output (0 = no truncation)."
    )
    args = parser.parse_args()

    print(
        asyncio.run(
            run(
                args.question,
                models=args.models,
                chairman=args.chairman,
                detail=args.detail,
                timeout=args.timeout,
                max_output=args.max_output,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
