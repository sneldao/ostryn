"""Vision pass: extract structured resource records from PNG console evidence.

Each screenshot is an admin console showing rows with a resource id, its state and
identifiers. The vision tier reads each image once; the structured result is cached
in runs/vision_cache.json keyed by file_id so later runs are free.

The extracted records feed evidence.load_all() as ordinary observations, so console
state (FOUND / ANONYMIZED / ACTIVE / ...) participates in state reconstruction with
full provenance (the manifest's file_id and captured_at).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from evidence import DATASET, load_manifest

CACHE = Path(__file__).resolve().parent.parent / "runs" / "vision_cache.json"

PROMPT = """Read every data row shown in this admin console screenshot.
Return JSON only:
{"records": [{"resource_id": "...", "system": "...", "state": "...",
              "email": "...|null", "phone": "...|null", "email_hash": "...|null",
              "name": "...|null", "user_reference": "...|null", "detail": "..."}]}

Rules:
- resource_id is the exact id string in the row (e.g. account_123, search_45).
- state is the row's status exactly as displayed (e.g. FOUND, NOT_FOUND,
  ANONYMIZED, IDENTIFIABLE, ACTIVE, BLOCKED, DISABLED, DELETED).
- omit rows without a resource id. Use null for unknown fields."""


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def extract_all(force: bool = False) -> dict[str, dict]:
    cache = {} if force or not CACHE.exists() else json.loads(CACHE.read_text())
    from llm import chat_json, flush

    todo = []
    for row in load_manifest():
        fid, rel = row["file_id"], row["file_path"]
        if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        if fid not in cache:
            todo.append((fid, rel, row["captured_at"]))
    for fid, rel, _cap in todo:
        p = DATASET / rel
        try:
            res = chat_json(
                "vision",
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{_b64(p)}"
                                },
                            },
                        ],
                    }
                ],
                tag=f"vision:{fid}",
                max_tokens=2000,
            )
            records = res.get("records", []) if isinstance(res, dict) else []
        except Exception:
            records = []
        cache[fid] = {"records": records}
    if todo:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        flush()
    return cache
