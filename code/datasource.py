"""Data-source abstraction: local CSVs (default) or Appwrite TablesDB.

Every logical table the auditor needs — deletion requests, identities, the
identity audit log, the system-activity stream, deletion rules, the evidence
manifest, and the evidence file bytes themselves — is read through this module.
Select the backend with ``OSTRYN_SOURCE=csv|appwrite`` or ``--source``.

The Appwrite backend expects the schema created by ``code/seed_appwrite.py``
and reads credentials from the environment (.env):

    APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY,
    APPWRITE_DATABASE_ID (default: ostryn)
"""

from __future__ import annotations

import base64
import csv
import json
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset"

load_dotenv(REPO_ROOT / ".env")

PAGE = 500  # list_rows page size


def _cli_token() -> str | None:
    """Access token stored by `appwrite login` (~/.appwrite/prefs.json)."""
    prefs = Path.home() / ".appwrite" / "prefs.json"
    try:
        d = json.loads(prefs.read_text())
        return d[d["current"]]["accessToken"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def connect():
    """TablesDB client. Prefers APPWRITE_API_KEY; if it lacks scopes, falls
    back to the OAuth bearer token from `appwrite login`."""
    from appwrite.client import Client
    from appwrite.exception import AppwriteException
    from appwrite.services.tables_db import TablesDB

    db = os.environ.get("APPWRITE_DATABASE_ID", "ostryn")

    def build(use_key: bool) -> TablesDB:
        c = (
            Client()
            .set_endpoint(os.environ["APPWRITE_ENDPOINT"])
            .set_project(os.environ["APPWRITE_PROJECT_ID"])
        )
        if use_key:
            c.set_key(os.environ["APPWRITE_API_KEY"])
        else:
            c.set_bearer(_cli_token() or "")
        return TablesDB(c)

    tdb = build(use_key=bool(os.environ.get("APPWRITE_API_KEY")))
    try:
        tdb.get(database_id=db)
        return tdb
    except AppwriteException:
        pass
    if _cli_token():
        tdb = build(use_key=False)
        tdb.get(database_id=db)  # raises if this auth path fails too
        return tdb
    return tdb


class CsvSource:
    """Original behaviour: read dataset/*.csv and evidence bytes from disk."""

    name = "csv"

    def rows(self, table: str) -> list[dict]:
        with open(DATASET / f"{table}.csv", newline="") as f:
            return list(csv.DictReader(f))

    def file_bytes(self, rel_path: str) -> bytes:
        return (DATASET / rel_path).read_bytes()

    def push_results(self, rows: list[dict]) -> int:
        return 0  # nothing to push to for the local backend


def _rowdata(r) -> dict:
    d = getattr(r, "data", r)
    if not isinstance(d, dict):
        d = dict(d)
    return {k: v for k, v in d.items() if not str(k).startswith("$")}


def _rowid(r) -> str:
    for attr in ("id", "$id"):
        v = getattr(r, attr, None)
        if v:
            return v
    if isinstance(r, dict):
        return r.get("$id") or r.get("id")
    d = getattr(r, "data", None)
    if isinstance(d, dict):
        return d.get("$id") or d.get("id")
    return ""


class AppwriteSource:
    """Appwrite TablesDB backend: the whole audit dataset lives in one
    database; evidence file bytes ride along as base64 column content."""

    name = "appwrite"

    def __init__(self) -> None:
        self.db = os.environ.get("APPWRITE_DATABASE_ID", "ostryn")
        self.tdb = connect()
        self._cache: dict[str, list[dict]] = {}
        self._files: dict[str, bytes] = {}

    def _fetch_all(self, table: str) -> list[dict]:
        from appwrite.query import Query

        out: list[dict] = []
        cursor = None
        while True:
            q = [Query.limit(PAGE), Query.order_asc("$id")]
            if cursor:
                q.append(Query.cursor_after(cursor))
            rows = self.tdb.list_rows(
                database_id=self.db, table_id=table, queries=q
            ).rows
            if not rows:
                break
            out += [_rowdata(r) for r in rows]
            if len(rows) < PAGE:
                break
            cursor = _rowid(rows[-1])
        return out

    def rows(self, table: str) -> list[dict]:
        if table not in self._cache:
            self._cache[table] = self._fetch_all(table)
        return self._cache[table]

    def file_bytes(self, rel_path: str) -> bytes:
        if rel_path not in self._files:
            from appwrite.query import Query

            res = self.tdb.list_rows(
                database_id=self.db,
                table_id="evidence_files",
                queries=[Query.equal("file_path", rel_path), Query.limit(1)],
            ).rows
            if not res:
                raise FileNotFoundError(f"{rel_path} not in evidence_files")
            b64 = _rowdata(res[0]).get("content_b64") or ""
            self._files[rel_path] = base64.b64decode(b64)
        return self._files[rel_path]

    def push_results(self, rows: list[dict]) -> int:
        """Upsert audit results keyed by case_id so reruns stay idempotent."""
        for r in rows:
            self.tdb.upsert_row(
                database_id=self.db,
                table_id="audit_results",
                row_id=r["case_id"],
                data=r,
            )
        return len(rows)


class _Proxy:
    """Stable handle so `from datasource import src` keeps working after
    init() swaps the backend."""

    impl: CsvSource | AppwriteSource

    def __init__(self) -> None:
        self.impl = CsvSource()

    def __getattr__(self, k):
        return getattr(self.impl, k)


src = _Proxy()


def init(source: str | None = None) -> None:
    """Select the backend. ``source`` falls back to $OSTRYN_SOURCE, then csv."""
    which = (source or os.environ.get("OSTRYN_SOURCE") or "csv").lower()
    src.impl = AppwriteSource() if which == "appwrite" else CsvSource()
