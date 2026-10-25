"""Evaluation harness: score predictions against labelled cases.

Usage:
    python code/evaluation/main.py                    # sample_output.csv vs fresh run
    python code/evaluation/main.py --pred output.csv --gold dataset/sample_output.csv
    python code/evaluation/main.py --verbose          # per-case diffs

Metrics
- status accuracy      : deletion_status exact match per case
- step precision/recall: steps matched as full 7-field tuples (order-insensitive)
- field accuracy       : per gold step aligned by (phase, system, resource, operation)
                         key, fraction of the 7 fields that match
- sequence accuracy    : steps_json identical as an ordered list
- evidence accuracy    : evidence_id match on aligned steps
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FIELDS = [
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
]


def _tuple(s: dict) -> tuple:
    return tuple(s.get(k) for k in FIELDS)


def _key(s: dict) -> tuple:
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"])


def score(
    pred: dict[str, tuple[str, list]],
    gold: dict[str, tuple[str, list]],
    verbose: bool = False,
) -> dict:
    n = len(gold)
    status_ok = seq_ok = 0
    tp = n_pred = n_gold = 0
    field_hits = field_total = 0
    ev_hits = ev_total = 0
    failures = []
    for cid, (gstat, gsteps) in sorted(gold.items()):
        pstat, psteps = pred.get(cid, ("MISSING", []))
        status_ok += pstat == gstat
        seq_ok += [_tuple(s) for s in psteps] == [_tuple(s) for s in gsteps]
        ps = {_tuple(s) for s in psteps}
        gs = {_tuple(s) for s in gsteps}
        tp += len(ps & gs)
        n_pred += len(ps)
        n_gold += len(gs)
        pk = {_key(s): s for s in psteps}
        for g in gsteps:
            p = pk.get(_key(g))
            field_total += len(FIELDS)
            ev_total += 1
            if p:
                field_hits += sum(p[k] == g[k] for k in FIELDS)
                ev_hits += p["evidence_id"] == g["evidence_id"]
        if (
            pstat != gstat
            or ps != gs
            or [_tuple(s) for s in psteps] != [_tuple(s) for s in gsteps]
        ):
            failures.append(cid)
            if verbose:
                print(f"--- {cid}: pred={pstat} gold={gstat}")
                for s in sorted(gs - ps, key=lambda t: t[0]):
                    print("   missing", s)
                for s in sorted(ps - gs, key=lambda t: t[0]):
                    print("   extra  ", s)
    prec = tp / n_pred if n_pred else 0.0
    rec = tp / n_gold if n_gold else 0.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "sequence_accuracy": seq_ok / n,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "field_accuracy": field_hits / field_total if field_total else 0.0,
        "evidence_accuracy": ev_hits / ev_total if ev_total else 0.0,
        "failures": failures,
    }


def read_csv(path: Path) -> dict[str, tuple[str, list]]:
    with open(path, newline="") as f:
        return {
            r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
            for r in csv.DictReader(f)
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    root = HERE.parent.parent
    ap.add_argument("--gold", default=str(root / "dataset" / "sample_output.csv"))
    ap.add_argument(
        "--pred", default=None, help="CSV of predictions (default: run agent)"
    )
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    gold = read_csv(Path(a.gold))
    if a.pred:
        pred = read_csv(Path(a.pred))
    else:
        from main import run

        results = run(include_samples=True, offline=True)
        pred = {r.case_id: (r.status, r.steps) for r in results}
    m = score(pred, gold, a.verbose)
    for k, v in m.items():
        print(f"{k:>18}: {v if isinstance(v, (int, list)) else round(v, 4)}")


if __name__ == "__main__":
    main()
