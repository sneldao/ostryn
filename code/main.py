"""Entry point for Ostryn — Ghost Data Still Exists deletion-audit agent.

Usage:
    python3 code/main.py
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from audit import Auditor, Case, CaseResult, load_cases, steps_json

REPO_ROOT = Path(__file__).resolve().parent.parent


def _case_sort_key(cid: str) -> tuple[int, str]:
    parts = cid.split("_")
    if len(parts) == 2 and parts[1].isdigit():
        return (0, cid)
    return (1, cid)


def run(
    *,
    include_samples: bool = False,
    offline: bool = False,
) -> list[CaseResult]:
    sample_cases = load_cases("sample_deletion_requests.csv")
    eval_cases = load_cases("deletion_requests.csv")
    all_cases: list[Case] = sample_cases + eval_cases
    auditor = Auditor(all_cases)
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
