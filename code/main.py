"""Entry point for Ostryn — Ghost Data Still Exists deletion-audit agent.

Usage:
    python3 code/main.py                # full run -> output.csv (518 rows)
    python3 code/main.py --offline      # no LLM calls: cached intent/vision results only
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import datasource
from audit import Auditor, CaseResult, load_cases, steps_json
from datasource import src
from intent import classify_texts
from vision import extract_all, load_cache

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(*, include_samples: bool = False, offline: bool = False) -> list[CaseResult]:
    sample_cases = load_cases("sample_deletion_requests.csv")
    eval_cases = load_cases("deletion_requests.csv")
    all_cases = sample_cases + eval_cases

    # 1. intent: which requests authorize deletion at all
    intent = classify_texts(
        sorted({c.request_text for c in all_cases}), use_llm=not offline
    )
    na = {c.case_id for c in all_cases if intent.get(c.request_text, True) is False}

    # 2. vision: structured records from PNG consoles (cached per file_id;
    #    offline runs reuse the cache and never call a model)
    vision = load_cache() if offline else extract_all()

    auditor = Auditor(all_cases, vision=vision, not_applicable=na)
    target = all_cases if include_samples else eval_cases
    return [auditor.audit(c) for c in target]


def run_agent(case_ids: list[str], offline: bool = False) -> int:
    """Run the agentic investigator on the given cases and report agreement
    with the deterministic policy engine. Tool calls and verdicts are logged
    to runs/agent_transcripts.jsonl for review. With offline=True the
    ScriptedInvestigator replays the canonical tool sequence instead — a
    parity check of the whole tool layer that costs no model calls."""
    import json

    from agent import AuditAgent, ScriptedInvestigator
    from tools import AuditToolbox

    eval_cases = load_cases("deletion_requests.csv")
    all_cases = load_cases("sample_deletion_requests.csv") + eval_cases
    intent = classify_texts(
        sorted({c.request_text for c in all_cases}), use_llm=not offline
    )
    na = {c.case_id for c in all_cases if intent.get(c.request_text, True) is False}
    vision = load_cache() if offline else extract_all()
    auditor = Auditor(all_cases, vision=vision, not_applicable=na)
    toolbox = AuditToolbox(auditor, intent)
    agent = ScriptedInvestigator(toolbox) if offline else AuditAgent(toolbox)

    by_id = {c.case_id: c for c in all_cases}
    out = REPO_ROOT / "runs" / "agent_transcripts.jsonl"
    out.parent.mkdir(exist_ok=True)
    agree = 0
    with out.open("a") as f:
        for cid in case_ids:
            c = by_id[cid]
            inv = agent.investigate(c)
            engine = auditor.audit(c).status
            agree += inv.verdict == engine
            f.write(
                json.dumps(
                    {
                        "case_id": cid,
                        "verdict": inv.verdict,
                        "engine": engine,
                        "divergent": inv.divergent,
                        "rationale": inv.rationale,
                        "tool_calls": len(inv.transcript),
                    }
                )
                + "\n"
            )
            print(
                f"{cid}: agent={inv.verdict} engine={engine} divergent={inv.divergent}"
            )
    print(f"agreement: {agree}/{len(case_ids)}")
    return 0


def write_output(results: list[CaseResult], out_path: Path) -> None:
    rows = sorted(results, key=lambda r: r.case_id)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for r in rows:
            w.writerow([r.case_id, r.status, steps_json(r.steps)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default=str(REPO_ROOT / "output.csv"))
    ap.add_argument(
        "--source",
        choices=["csv", "appwrite"],
        help="data backend: local CSVs or Appwrite TablesDB "
        "(default: $OSTRYN_SOURCE or csv)",
    )
    ap.add_argument(
        "--push",
        action="store_true",
        help="upsert results into the Appwrite audit_results table",
    )
    ap.add_argument(
        "--agent",
        nargs="*",
        metavar="CASE_ID",
        help="run the agentic investigator on these cases instead of the "
        "batch pipeline (default: first 8 eval cases)",
    )
    args = ap.parse_args()
    datasource.init(args.source)
    if args.agent is not None:
        ids = args.agent or [c.case_id for c in load_cases("deletion_requests.csv")][:8]
        raise SystemExit(run_agent(ids, offline=args.offline))
    results = run(include_samples=False, offline=args.offline)
    write_output(results, Path(args.out))
    print(f"Wrote {len(results)} rows to {args.out}")
    if args.push:
        from datetime import UTC, datetime

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        pushed = src.push_results(
            [
                {
                    "case_id": r.case_id,
                    "deletion_status": r.status,
                    "steps_json": steps_json(r.steps),
                    "audited_at": now,
                }
                for r in results
            ]
        )
        print(f"Pushed {pushed} rows to Appwrite audit_results")


if __name__ == "__main__":
    main()
