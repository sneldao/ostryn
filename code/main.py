"""Entry point for Ostryn — Ghost Data Still Exists deletion-audit agent.

Usage:
    python3 code/main.py                # full run -> output.csv (518 rows)
    python3 code/main.py --offline      # deterministic only, no LLM calls
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from audit import Auditor, CaseResult, load_cases, steps_json
from intent import classify_texts
from vision import extract_all

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

    # 2. vision: structured records from PNG consoles (cached)
    vision = None if offline else extract_all()

    auditor = Auditor(all_cases, vision=vision, not_applicable=na)
    target = all_cases if include_samples else eval_cases
    return [auditor.audit(c) for c in target]


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
    args = ap.parse_args()
    results = run(include_samples=False, offline=args.offline)
    write_output(results, Path(args.out))
    print(f"Wrote {len(results)} rows to {args.out}")


if __name__ == "__main__":
    main()
