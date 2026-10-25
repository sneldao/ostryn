"""Schema + consistency validator for output.csv (no labels needed).

Usage:
    python code/evaluation/validate_output.py [--out output.csv]

Checks the problem-statement contract and internal consistency rules:
- exact header, one row per case_id in deletion_requests.csv, sorted, 518 rows
- valid status enum; steps_json parses as a list of 7-key objects
- phase/operation/state triples come from the allowed-operations table
- step_no is 1..n; order = phase, operation table order, system_id, resource_id
- COMPLETE  -> only VERIFICATION steps, each citing an evidence id
- INCOMPLETE-> at least one FINDING; findings cite evidence; remediation and
               verification use empty evidence_id; every remediated resource has
               a matching verification and every finding has a remediation
- NOT_APPLICABLE -> []
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OPS = [  # (phase, operation, state) in problem-statement table order
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
OP_RANK = {op: i for i, (_, op, _) in enumerate(OPS)}
OP_META = {op: (ph, st) for ph, op, st in OPS}
PHASE_RANK = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
KEYS = {
    "step_no",
    "phase",
    "system_id",
    "resource_id",
    "operation",
    "state",
    "evidence_id",
}
REMEDIATE_FOR = {
    "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"},
    "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"},
    "REPORT_REMAINING_RECORD": {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"},
    "REPORT_EXPOSED_PII": {"DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"},
}
VERIFY_FOR = {
    "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
    "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
    "DELETE_RECORD": "VERIFY_RECORD_ABSENT",
    "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED",
}


def check(out: Path, requests: Path, expected_rows: int | None) -> list[str]:
    errs: list[str] = []
    with requests.open(newline="", encoding="utf-8") as f:
        want = [r["case_id"] for r in csv.DictReader(f)]
    with out.open(newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = next(rd)
        rows = list(rd)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errs.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if expected_rows is not None and len(rows) != expected_rows:
        errs.append(f"row count {len(rows)} != {expected_rows}")
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        errs.append(f"duplicate case_ids {dup[:5]}")
    if set(ids) != set(want):
        errs.append(
            f"case_id mismatch: missing={sorted(set(want) - set(ids))[:5]} "
            f"extra={sorted(set(ids) - set(want))[:5]}"
        )
    if ids != sorted(ids):
        errs.append("rows not sorted by case_id")
    for r in rows:
        if len(r) != 3 or any(x == "" for x in r):
            errs.append(f"{r[:1]} malformed/empty field")
            continue
        cid, status, raw = r
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as e:
            errs.append(f"{cid} bad json {e}")
            continue
        if status not in {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}:
            errs.append(f"{cid} bad status {status}")
        if not isinstance(steps, list):
            errs.append(f"{cid} steps_json not a list")
            continue
        if status == "NOT_APPLICABLE":
            if steps:
                errs.append(f"{cid} NOT_APPLICABLE with steps")
            continue
        if not steps:
            errs.append(f"{cid} {status} with no steps")
            continue
        prev = None
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict) or set(s) != KEYS:
                errs.append(f"{cid} step {i} bad keys")
                break
            if s["step_no"] != i:
                errs.append(f"{cid} step_no {s['step_no']} != {i}")
            meta = OP_META.get(s["operation"])
            if meta is None or meta != (s["phase"], s["state"]):
                errs.append(f"{cid} step {i} invalid triple")
            for k in ("system_id", "resource_id"):
                if not isinstance(s[k], str) or not s[k]:
                    errs.append(f"{cid} step {i} empty {k}")
            if not isinstance(s["evidence_id"], str):
                errs.append(f"{cid} step {i} evidence_id not str")
            key = (
                PHASE_RANK.get(s["phase"], 9),
                OP_RANK.get(s["operation"], 99),
                s["system_id"],
                s["resource_id"],
            )
            if prev is not None and key < prev:
                errs.append(f"{cid} step {i} out of order")
            prev = key
            if status == "COMPLETE":
                if s["phase"] != "VERIFICATION":
                    errs.append(f"{cid} COMPLETE has {s['phase']} step")
                if not s["evidence_id"]:
                    errs.append(f"{cid} COMPLETE step {i} lacks evidence")
            else:
                if s["phase"] == "FINDING" and not s["evidence_id"]:
                    errs.append(f"{cid} finding {i} lacks evidence")
                if s["phase"] != "FINDING" and s["evidence_id"]:
                    errs.append(f"{cid} future step {i} cites evidence")
        if status == "INCOMPLETE":
            fnd = [s for s in steps if s["phase"] == "FINDING"]
            rem = {
                (s["system_id"], s["resource_id"], s["operation"])
                for s in steps
                if s["phase"] == "REMEDIATION"
            }
            ver = {
                (s["system_id"], s["resource_id"], s["operation"])
                for s in steps
                if s["phase"] == "VERIFICATION"
            }
            if not fnd:
                errs.append(f"{cid} INCOMPLETE without findings")
            for s in fnd:
                ok = any(
                    (s["system_id"], s["resource_id"], op) in rem
                    for op in REMEDIATE_FOR[s["operation"]]
                )
                if not ok:
                    errs.append(f"{cid} finding {s['resource_id']} has no remediation")
            for sysid, res, op in rem:
                if (sysid, res, VERIFY_FOR[op]) not in ver:
                    errs.append(f"{cid} remediation {res} {op} has no verification")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output.csv"))
    ap.add_argument(
        "--requests", default=str(ROOT / "dataset" / "deletion_requests.csv")
    )
    ap.add_argument("--rows", type=int, default=518)
    a = ap.parse_args()
    errs = check(Path(a.out), Path(a.requests), a.rows or None)
    with Path(a.out).open(newline="", encoding="utf-8") as f:
        c = Counter(r["deletion_status"] for r in csv.DictReader(f))
    print(f"status counts: {dict(sorted(c.items()))}")
    for e in errs[:50]:
        print("ERROR", e)
    print(f"{len(errs)} problems")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
