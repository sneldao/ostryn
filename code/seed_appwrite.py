"""Provision the Ostryn schema in Appwrite TablesDB and ingest dataset/.

Creates the ``ostryn`` database with one table per participant-facing CSV,
uploads every evidence file's bytes as base64 column content so the whole
audit can run off Appwrite, and grants public read on the tables the results
dashboard consumes. Re-running drops and recreates the tables (clean slate).

    python3 code/seed_appwrite.py           # create-or-recreate everything
    python3 code/seed_appwrite.py --schema  # schema only, no row ingest

Env: APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY,
     APPWRITE_DATABASE_ID (default: ostryn)
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from appwrite.enums.tables_db_index_type import TablesDBIndexType
from appwrite.exception import AppwriteException
from appwrite.permission import Permission
from appwrite.role import Role
from appwrite.services.tables_db import TablesDB
from datasource import CsvSource, connect

REPO_ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("APPWRITE_DATABASE_ID", "ostryn")

REQUEST_COLS = [
    ("varchar", "case_id", {"size": 32, "required": True}),
    ("varchar", "email", {"size": 320, "required": False}),
    ("varchar", "requested_at", {"size": 40, "required": False}),
    ("varchar", "reported_status", {"size": 32, "required": False}),
    ("varchar", "reported_at", {"size": 40, "required": False}),
    ("varchar", "audit_at", {"size": 40, "required": False}),
    ("mediumtext", "request_text", {"required": False}),
]

TABLES: dict[str, dict] = {
    "deletion_requests": {
        "cols": REQUEST_COLS,
        "indexes": [("by_case", TablesDBIndexType.UNIQUE, ["case_id"])],
        "public_read": True,
        "row_id": "case_id",
    },
    "sample_deletion_requests": {
        "cols": REQUEST_COLS,
        "indexes": [("by_case", TablesDBIndexType.UNIQUE, ["case_id"])],
        "public_read": False,
        "row_id": "case_id",
    },
    "user_identities": {
        "cols": [
            ("varchar", "user_id", {"size": 32, "required": True}),
            ("varchar", "name", {"size": 255, "required": False}),
            ("varchar", "email", {"size": 320, "required": False}),
            ("varchar", "phone", {"size": 64, "required": False}),
            ("varchar", "email_hash", {"size": 128, "required": False}),
            ("varchar", "updated_at", {"size": 40, "required": False}),
        ],
        "indexes": [("by_user", TablesDBIndexType.UNIQUE, ["user_id"])],
        "public_read": False,
        "row_id": "user_id",
    },
    "user_audit_log": {
        "cols": [
            ("varchar", "audit_id", {"size": 40, "required": True}),
            ("varchar", "user_id", {"size": 32, "required": False}),
            ("varchar", "event_time", {"size": 40, "required": False}),
            ("varchar", "field_name", {"size": 64, "required": False}),
            ("varchar", "old_value", {"size": 512, "required": False}),
            ("varchar", "new_value", {"size": 512, "required": False}),
        ],
        "indexes": [
            ("by_audit", TablesDBIndexType.UNIQUE, ["audit_id"]),
            ("by_user", TablesDBIndexType.KEY, ["user_id"]),
        ],
        "public_read": False,
        "row_id": "audit_id",
    },
    "system_activity": {
        "cols": [
            ("varchar", "activity_id", {"size": 40, "required": True}),
            ("varchar", "event_time", {"size": 40, "required": False}),
            ("varchar", "event_type", {"size": 64, "required": False}),
            ("varchar", "source_system", {"size": 64, "required": False}),
            ("varchar", "target_system", {"size": 64, "required": False}),
            ("varchar", "resource_id", {"size": 64, "required": False}),
            ("varchar", "reported_result", {"size": 32, "required": False}),
            ("mediumtext", "details", {"required": False}),
        ],
        "indexes": [
            ("by_activity", TablesDBIndexType.UNIQUE, ["activity_id"]),
            ("by_time", TablesDBIndexType.KEY, ["event_time"]),
            ("by_resource", TablesDBIndexType.KEY, ["resource_id"]),
        ],
        "public_read": False,
        "row_id": "activity_id",
    },
    "deletion_rules": {
        "cols": [
            ("varchar", "rule_id", {"size": 32, "required": True}),
            ("varchar", "data_category", {"size": 64, "required": False}),
            ("varchar", "condition", {"size": 255, "required": False}),
            ("varchar", "required_action", {"size": 64, "required": False}),
        ],
        "indexes": [("by_rule", TablesDBIndexType.UNIQUE, ["rule_id"])],
        "public_read": True,
        "row_id": "rule_id",
    },
    "evidence_files": {
        "cols": [
            ("varchar", "file_id", {"size": 40, "required": True}),
            ("varchar", "file_path", {"size": 512, "required": False}),
            ("varchar", "origin", {"size": 128, "required": False}),
            ("varchar", "captured_at", {"size": 40, "required": False}),
            ("mediumtext", "content_b64", {"required": False}),
        ],
        "indexes": [
            ("by_file", TablesDBIndexType.UNIQUE, ["file_id"]),
            ("by_path", TablesDBIndexType.UNIQUE, ["file_path"]),
        ],
        "public_read": True,
        "row_id": "file_id",
    },
    "audit_results": {
        "cols": [
            ("varchar", "case_id", {"size": 32, "required": True}),
            ("varchar", "deletion_status", {"size": 32, "required": False}),
            ("mediumtext", "steps_json", {"required": False}),
            ("varchar", "audited_at", {"size": 40, "required": False}),
        ],
        "indexes": [("by_case", TablesDBIndexType.UNIQUE, ["case_id"])],
        "public_read": True,
        "row_id": "case_id",
    },
}

BATCH = 100


def _retry(fn, *args, tries: int = 6, **kw):
    """Schema ops are processed asynchronously; transient 5xx needs backoff."""
    import time

    for i in range(tries):
        try:
            return fn(*args, **kw)
        except AppwriteException as e:
            if getattr(e, "code", 0) < 500 or i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _col(tdb: TablesDB, table: str, kind: str, key: str, kw: dict) -> None:
    fn = {
        "varchar": tdb.create_varchar_column,
        "text": tdb.create_text_column,
        "mediumtext": tdb.create_mediumtext_column,
    }[kind]
    _retry(fn, database_id=DB, table_id=table, key=key, **kw)


def _wait_cols(tdb: TablesDB, table: str, keys: list[str], timeout: int = 180) -> None:
    """Columns provision asynchronously; block until all are available."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        cols = tdb.list_columns(database_id=DB, table_id=table).columns
        status = {c.key: getattr(c.status, "value", c.status) for c in cols}
        if all(status.get(k) == "available" for k in keys):
            return
        time.sleep(2)
    raise TimeoutError(f"columns not available in {table}: {status}")


def _create_table(tdb: TablesDB, name: str, spec: dict) -> None:
    try:
        _retry(tdb.delete_table, database_id=DB, table_id=name)
        print(f"  dropped {name}")
    except AppwriteException:
        pass
    perms = [Permission.read(Role.any())] if spec["public_read"] else None
    _retry(
        tdb.create_table,
        database_id=DB,
        table_id=name,
        name=name,
        permissions=perms,
        row_security=False,
    )
    for kind, key, kw in spec["cols"]:
        _col(tdb, name, kind, key, kw)
    _wait_cols(tdb, name, [k for _, k, _ in spec["cols"]])
    for key, typ, cols in spec["indexes"]:
        _retry(
            tdb.create_index,
            database_id=DB,
            table_id=name,
            key=key,
            type=typ,
            columns=cols,
        )
    print(f"  created {name}")


def _insert(tdb: TablesDB, table: str, rid_col: str, rows: list[dict]) -> int:
    n = 0
    for i in range(0, len(rows), BATCH):
        chunk = [
            {"$id": r[rid_col], **{k: ("" if v is None else v) for k, v in r.items()}}
            for r in rows[i : i + BATCH]
        ]
        try:
            tdb.create_rows(database_id=DB, table_id=table, rows=chunk)
        except AppwriteException:
            # some deployments reject $id in bulk rows: let the server assign
            tdb.create_rows(
                database_id=DB,
                table_id=table,
                rows=[{k: v for k, v in r.items() if k != "$id"} for r in chunk],
            )
        n += len(chunk)
        print(f"    {table}: {n}/{len(rows)}", end="\r")
    print(f"    {table}: {n} rows ingested   ")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true", help="schema only")
    args = ap.parse_args()

    tdb = connect()
    print(f"database {DB} reachable")

    for name, spec in TABLES.items():
        _create_table(tdb, name, spec)

    if args.schema:
        return

    csv_src = CsvSource()
    for name, spec in TABLES.items():
        if name == "audit_results":
            continue
        rows = csv_src.rows(name)
        if name == "evidence_files":
            for r in rows:
                r["content_b64"] = base64.b64encode(
                    csv_src.file_bytes(r["file_path"])
                ).decode()
        _insert(tdb, name, spec["row_id"], rows)

    cfg = REPO_ROOT / "web" / "config.js"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(
        "// generated by code/seed_appwrite.py\n"
        f'const APPWRITE_ENDPOINT = "{os.environ["APPWRITE_ENDPOINT"]}";\n'
        f'const APPWRITE_PROJECT_ID = "{os.environ["APPWRITE_PROJECT_ID"]}";\n'
        f'const APPWRITE_DATABASE_ID = "{DB}";\n'
    )
    print(f"wrote {cfg.relative_to(REPO_ROOT)}")
    print("done.")


if __name__ == "__main__":
    main()
