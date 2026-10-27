"""Opt-in real-model replay against existing libraries; no ingestion or index writes.

Captures are user-authorized evaluation artifacts, never routine telemetry. The output
must be outside this repository. Review/sanitize before publishing. Supply the same
model, budget, frozen document generations and question order for comparisons.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from fli_demo.agent import azure_chat, run_turn
from fli_demo.client import IndexClient
from fli_demo.registry import Registry
from fli_demo.telemetry import metrics


def capture(value):
    """Native attachment credentials do not belong in even an opt-in replay capture."""
    if isinstance(value, list):
        return [capture(v) for v in value]
    if isinstance(value, dict):
        return {k: capture(v) for k, v in value.items() if k not in {"url", "expiresAt"}}
    return value


def replay(index, chat, agent, cases, output, budget=48000):
    output = Path(output).expanduser().resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("evaluation output must be outside the repository")
    output.mkdir(parents=True, exist_ok=True)
    history, ledger, results = [], [], []
    before = {lib: index.list_documents(lib) for lib in agent["libraryIds"]}
    for case in cases:
        for number, question in enumerate(case.get("questions") or [case["question"]], 1):
            turn = run_turn(chat, index, agent, history, question, context_token_budget=budget, evidence_ledger=ledger)
            row = {
                "case": case["case"],
                "turn": number,
                "question": question,
                "answer": turn.answer,
                "metrics": metrics(turn),
                "traces": capture([asdict(t) for t in turn.searches]),
                "toolReplies": [json.loads(m["content"]) for m in turn.messages if m["role"] == "tool"],
            }
            results.append(row)
            (output / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": turn.answer}])
            print(f"{case['case']} turn {number}: captured", flush=True)
    after = {lib: index.list_documents(lib) for lib in agent["libraryIds"]}

    def versions(rows):
        return sorted(
            (
                lib,
                d["id"],
                d.get("activeGeneration"),
                d.get("contentRevision"),
                (d.get("fullText") or {}).get("sha256"),
                (d.get("imageAssets") or {}).get("sha256"),
            )
            for lib, docs in rows.items()
            for d in docs
        )

    unchanged = versions(before) == versions(after)
    (output / "snapshot-check.json").write_text(
        json.dumps({"unchanged": unchanged, "before": versions(before), "after": versions(after)}, indent=2) + "\n"
    )
    if not unchanged:
        raise RuntimeError("source versions changed during replay; comparison is confounded")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--cases", type=Path, default=Path("docs/testing/2026-10-27/casual-cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-tokens", type=int, default=48000)
    args = parser.parse_args()
    replay(
        IndexClient.from_env(),
        azure_chat(os.environ["AOAI_ENDPOINT"], os.environ["AOAI_CHAT_DEPLOYMENT"]),
        Registry().get(args.agent_id),
        json.loads(args.cases.read_text()),
        args.output,
        args.context_tokens,
    )


if __name__ == "__main__":
    main()
