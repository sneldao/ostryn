"""Evidence layer: parse every participant-facing evidence file into observations.

An *observation* is one sighting of one resource in one evidence item:

    Obs(evidence_id, avail_at, obs_at, resource_id, system_id, fields, tokens)

- evidence_id : file_id from evidence_files.csv (or activity_id for activity rows)
- avail_at    : when the evidence item became available (manifest captured_at);
                an observation may only be used for a case if avail_at <= audit_at
- obs_at      : when the resource was actually observed (row/line/snapshot time)
- fields      : flattened lower-cased key -> value mapping of what was seen
- tokens      : lower-cased identifier strings seen together with the resource
                (emails, phones, hashes, user ids, names) used for identity linking

Nothing here decides case membership; that happens in audit.py by matching tokens
against the subject's resolved identifiers.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset"

RESOURCE_RE = re.compile(
    r"\b(account|search|analytics|support|export|data_flow|backup|audit_record|"
    r"tombstone|legal_hold|marketing|cache|email_delivery|object)_\d+\b"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
TRACE_RE = re.compile(r"\b((?:tr|rb)-[A-Za-z0-9-]+)")
KV_RE = re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|([^\s"|]+))')

PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "cache": "cache_service",
    "email_delivery": "email_delivery_service",
    "object": "object_storage",
}

# keys whose values identify a subject (used for linking, never for state)
IDENTITY_KEYS = {
    "email",
    "phone",
    "email_hash",
    "user_id",
    "customer_reference",
    "subject_reference",
    "subject_ref",
    "subject_selector",
    "lookup_key",
    "subject_key",
    "match_key",
    "scan_key",
    "owner_key",
    "correlation_key",
    "matched_value",
    "owner_email",
    "owner_phone",
    "customer_email",
    "requester_email",
    "requester_phone",
    "requester_name",
    "full_name",
    "display_name",
    "name",
    "uploaded_by",
    "account",
    "selector",
    "owner",
}


def prefix_of(resource_id: str) -> str:
    m = RESOURCE_RE.match(resource_id)
    return m.group(1) if m else resource_id.rsplit("_", 1)[0]


def system_of(resource_id: str) -> str:
    return PREFIX_SYSTEM.get(prefix_of(resource_id), "unknown")


@dataclass
class Obs:
    evidence_id: str
    avail_at: str
    obs_at: str
    resource_id: str
    system_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    tokens: set[str] = field(default_factory=set)
    source: str = ""  # file family, for debugging / prompts


def _norm(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in {"null", "none", "—", "-"}:
        return None
    return s.lower()


def _tokens_from(fields: dict[str, Any]) -> set[str]:
    toks: set[str] = set()
    for k, v in fields.items():
        if isinstance(v, (dict, list)):
            continue
        n = _norm(v)
        if n is None:
            continue
        if k in IDENTITY_KEYS:
            toks.add(n)
        for e in EMAIL_RE.findall(n):
            toks.add(e.lower())
        for u in USER_RE.findall(n):
            toks.add(u)
    return toks


def _flatten(d: dict, out: dict | None = None) -> dict:
    """Flatten nested dicts to leaf keys (first occurrence wins)."""
    out = {} if out is None else out
    for k, v in d.items():
        k = k.lower()
        if isinstance(v, dict):
            if k not in out:
                out[f"has_{k}"] = True
            _flatten(v, out)
        elif isinstance(v, list):
            if all(not isinstance(x, (dict, list)) for x in v):
                out.setdefault(k, v)
        else:
            out.setdefault(k, v)
    return out


# --------------------------------------------------------------------------- SQL


def _parse_sql(path: Path, fid: str, avail: str) -> list[Obs]:
    text = path.read_text()
    m = re.search(r"Captured at:\s*(\S+)", text) or re.search(
        r"snapshot_completed_at=(\S+)", text
    )
    cap = m.group(1) if m else avail
    con = sqlite3.connect(":memory:")
    con.executescript(text)
    out: list[Obs] = []
    tables = [
        r[0] for r in con.execute("select name from sqlite_master where type='table'")
    ]
    for t in tables:
        cur = con.execute(f'select * from "{t}"')
        cols = [c[0].lower() for c in cur.description]
        if "resource_id" not in cols:
            continue
        for row in cur.fetchall():
            rec = dict(zip(cols, row))
            if "record_json" in rec:
                inner = json.loads(rec.pop("record_json"))
                rec = {**_flatten(inner), **{k: v for k, v in rec.items()}}
            rec["_table"] = t
            rid = rec["resource_id"]
            out.append(
                Obs(
                    fid,
                    avail,
                    cap,
                    rid,
                    system_of(rid),
                    rec,
                    _tokens_from(rec),
                    "sql",
                )
            )
    # table coverage, used to prove absence by omission
    coverage: dict[str, set[str]] = {}
    for o in out:
        coverage.setdefault(o.fields["_table"], set()).add(o.system_id)
    if "snapshot_scope" in tables:
        scope = {r[0] for r in con.execute("select origin_system from snapshot_scope")}
        coverage["snapshot_scope"] = scope
    SQL_COVERAGE[fid] = (cap, set().union(*coverage.values()) if coverage else set())
    prof_tables = [t for t in tables if t.startswith("customer_profiles")]
    if prof_tables:
        non_empty = any(
            con.execute(f'select count(*) from "{t}"').fetchone()[0] > 0
            for t in prof_tables
        )
        if "account_service" in SQL_COVERAGE[fid][1] or prof_tables:
            PROFILES_COVERAGE[fid] = (cap, non_empty)
    return out


SQL_COVERAGE: dict[str, tuple[str, set[str]]] = {}

# files whose snapshot covers the account_service profile store:
#   fid -> (captured_at, non_empty)  — used for absence-by-omission proofs
PROFILES_COVERAGE: dict[str, tuple[str, bool]] = {}

# -------------------------------------------------------------------------- JSON


def _walk_json(node: Any, t: str | None, store: str | None, acc: list):
    if isinstance(node, dict):
        t = (
            node.get("observed_at")
            or node.get("recorded_at")
            or node.get("captured_at")
            or t
        )
        store = node.get("data_store") or node.get("system") or store
        if "resource_id" in node:
            acc.append((t, store, node))
            # do not descend: nested docs repeat the same resource
            return
        for v in node.values():
            _walk_json(v, t, store, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_json(v, t, store, acc)


def _parse_json(path: Path, fid: str, avail: str) -> list[Obs]:
    data = json.loads(path.read_text())
    top_t = data.get("exported_at") or data.get("captured_at") or avail
    acc: list = []
    _walk_json(data, top_t, None, acc)
    # register account-service profile coverage from snapshot bundles
    for snap in data.get("snapshots", []) or []:
        if (
            isinstance(snap, dict)
            and snap.get("data_store") == "account_service"
            and snap.get("collection") == "customer_profiles"
        ):
            cap = snap.get("captured_at") or top_t
            non_empty = bool(snap.get("records"))
            if fid not in PROFILES_COVERAGE or cap > PROFILES_COVERAGE[fid][0]:
                PROFILES_COVERAGE[fid] = (cap, non_empty)
    out = []
    for t, _store, node in acc:
        rec = _flatten(node)
        # search query results: documents list carries the identity
        hits = node.get("hits")
        if isinstance(hits, dict):
            rec["total_hits"] = hits.get("total")
            for doc in hits.get("documents", []) or []:
                for k, v in _flatten(doc).items():
                    rec.setdefault(k, v)
        if node.get("profile") is None and "profile" in node:
            rec["profile_null"] = True
        rid = rec["resource_id"]
        out.append(
            Obs(
                fid,
                avail,
                t or top_t,
                rid,
                system_of(rid),
                rec,
                _tokens_from(rec),
                "json",
            )
        )
    return out


# --------------------------------------------------------------------------- TXT


def _parse_txt(path: Path, fid: str, avail: str) -> list[Obs]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        parts = [p.strip() for p in line[2:].split("|")]
        if len(parts) < 6:
            continue
        ts, system, rid, rtype, result, subject = parts[:6]
        rec = {
            "system": system,
            "resource_id": rid,
            "record_type": rtype,
            "result": result,
            "subject_reference": subject,
        }
        out.append(Obs(fid, avail, ts, rid, system, rec, _tokens_from(rec), "txt"))
    return out


# -------------------------------------------------------------------------- LOGS


def _kv_line(line: str) -> dict[str, str]:
    body = line
    j = line.find("{")
    if j >= 0 and line.rstrip().endswith("}"):
        try:
            return {k.lower(): v for k, v in json.loads(line[j:]).items()}
        except json.JSONDecodeError:
            pass
    body = body.replace('\\"', '"')
    kv: dict[str, str] = {}
    for k, qv, bv in KV_RE.findall(body):
        kv.setdefault(k.lower(), qv if qv or not bv else bv)
    return kv


def _parse_log(path: Path, fid: str, avail: str) -> list[Obs]:
    traces: dict[str, list[tuple[str, dict]]] = {}
    standalone: list[tuple[str, dict]] = []
    for line in path.read_text().splitlines():
        m = TS_RE.match(line)
        if not m:
            continue
        ts = m.group(1)
        kv = _kv_line(line)
        if "event" not in kv:
            # bracket / pipe formats carry the event name positionally
            em = re.search(r"\]\s+(\w+):", line) or re.search(
                r"\|\s*(\w+)\s*\|\s*instance=", line
            )
            if em:
                kv["event"] = em.group(1)
        tm = TRACE_RE.search(line)
        trace = (
            kv.get("trace_id") or kv.get("requestid") or (tm.group(1) if tm else None)
        )
        if trace and trace.startswith(("tr-", "rb-")):
            traces.setdefault(trace, []).append((ts, kv))
        else:
            standalone.append((ts, kv))
    out: list[Obs] = []
    groups = [[x] for x in standalone] + list(traces.values())
    for grp in groups:
        merged: dict[str, str] = {}
        rid_ts: dict[str, tuple[str, dict]] = {}
        for ts, kv in grp:
            for k, v in kv.items():
                if k != "resource_id":
                    merged.setdefault(k, v)
            rid = kv.get("resource_id")
            if rid and RESOURCE_RE.fullmatch(rid):
                # later lines for the same resource overwrite earlier ones
                rid_ts[rid] = (ts, {**rid_ts.get(rid, ("", {}))[1], **kv})
        for rid, (ts, kv) in rid_ts.items():
            rec = {**merged, **kv}
            out.append(
                Obs(
                    fid,
                    avail,
                    ts,
                    rid,
                    kv.get("system") or system_of(rid),
                    rec,
                    _tokens_from(rec),
                    "log",
                )
            )
    return out


# --------------------------------------------------------------------------- PDF


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def _parse_pdf(path: Path, fid: str, avail: str, aliases: dict[str, str]) -> list[Obs]:
    text = pdf_text(path)
    low = text.lower()
    emails = {e.lower() for e in EMAIL_RE.findall(text)}
    rids = set(
        RESOURCE_RE.findall(text) and [m.group(0) for m in RESOURCE_RE.finditer(text)]
    )
    for alias, rid in aliases.items():
        # only distinctive aliases (document numbers), never bare user ids
        if not USER_RE.fullmatch(alias) and alias != rid and alias.lower() in low:
            rids.add(rid)
    out: list[Obs] = []
    users = set(USER_RE.findall(text))
    for rid in rids:
        rec: dict[str, Any] = {"pdf_text": text[:2000], "resource_id": rid}
        if "retain until" in low:
            m = re.search(r"retain until ([^\n]+)", low)
            rec["retention_until"] = m.group(1) if m else None
        out.append(
            Obs(fid, avail, avail, rid, system_of(rid), rec, emails | users, "pdf")
        )
    return out


# ------------------------------------------------------------------------ IMAGES


def _parse_vision(fid: str, avail: str, extraction: dict) -> list[Obs]:
    out = []
    for rec in extraction.get("records", []):
        rid = rec.get("resource_id")
        if not rid or not RESOURCE_RE.fullmatch(str(rid)):
            continue
        rec = {k.lower(): v for k, v in rec.items()}
        out.append(
            Obs(
                fid,
                avail,
                rec.get("observed_at") or avail,
                rid,
                rec.get("system") or system_of(rid),
                rec,
                _tokens_from(rec),
                "image",
            )
        )
    return out


# ---------------------------------------------------------------------- ACTIVITY


def load_activity() -> list[dict]:
    with open(DATASET / "system_activity.csv", newline="") as f:
        return list(csv.DictReader(f))


def activity_aliases(activity: Iterable[dict]) -> dict[str, str]:
    """data_catalog LINK_RESOURCE_IDENTITY rows: 'alias' -> resource_id."""
    out = {}
    for a in activity:
        if a["event_type"] == "LINK_RESOURCE_IDENTITY":
            m = re.search(r"linked (\S+) \(([^)]+)\)", a["details"]) or re.search(
                r"confirmed (\S+) \(([^)]+)\)", a["details"]
            )
            if m:
                out[m.group(2)] = m.group(1)
    return out


def activity_obs(activity: Iterable[dict]) -> list[Obs]:
    """Activity rows that *establish* state (checks, catalog links, restores).

    Plain workflow outcomes (DELETE_RECORD ... SUCCESS) are claims and are kept
    out of the observation set; audit.py uses them only for linking/category.
    """
    out = []
    for a in activity:
        et, rid, d = a["event_type"], a["resource_id"], a["details"]
        rec: dict[str, Any] = {
            "event_type": et,
            "details": d,
            "reported_result": a["reported_result"],
        }
        if et == "CHECK_RESTORE_ACCESS":
            rec["restore_allowed"] = "can still be restored" in d
        elif et == "CHECK_DATA_FLOW":
            rec["enabled"] = "remains enabled" in d
        elif et == "LINK_RESOURCE_IDENTITY":
            rec["link_only"] = True
            if "belongs only to" in d:
                rec["sole_owner"] = True
        elif et == "RESTORE_BACKUP":
            m = RESOURCE_RE.search(d.split("resource", 1)[-1])
            if m:
                rec2 = {
                    "event_type": et,
                    "details": d,
                    "restored_from": rid,
                    "recreated": True,
                }
                out.append(
                    Obs(
                        a["activity_id"],
                        a["event_time"],
                        a["event_time"],
                        m.group(0),
                        system_of(m.group(0)),
                        rec2,
                        set(),
                        "activity",
                    )
                )
            continue
        else:
            continue
        toks = {e.lower() for e in EMAIL_RE.findall(d)} | set(USER_RE.findall(d))
        out.append(
            Obs(
                a["activity_id"],
                a["event_time"],
                a["event_time"],
                rid,
                a["target_system"],
                rec,
                toks,
                "activity",
            )
        )
    return out


# ------------------------------------------------------------------------- LOAD


def load_manifest() -> list[dict]:
    with open(DATASET / "evidence_files.csv", newline="") as f:
        return list(csv.DictReader(f))


def load_all(vision: dict[str, dict] | None = None) -> tuple[list[Obs], list[dict]]:
    activity = load_activity()
    aliases = activity_aliases(activity)
    obs: list[Obs] = []
    for row in load_manifest():
        fid, rel, avail = row["file_id"], row["file_path"], row["captured_at"]
        p = DATASET / rel
        suf = p.suffix.lower()
        if suf == ".sql":
            obs += _parse_sql(p, fid, avail)
        elif suf == ".json":
            obs += _parse_json(p, fid, avail)
        elif suf == ".txt":
            obs += _parse_txt(p, fid, avail)
        elif suf == ".log":
            obs += _parse_log(p, fid, avail)
        elif suf == ".pdf":
            obs += _parse_pdf(p, fid, avail, aliases)
        elif suf in {".png", ".jpg", ".jpeg"} and vision and fid in vision:
            obs += _parse_vision(fid, avail, vision[fid])
    obs += activity_obs(activity)
    return obs, activity
