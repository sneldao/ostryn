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

PROMPT_VERSION = 2
PROMPT = """Read this internal admin-console screenshot. Extract the page's primary
entity and every data row as structured records.
Return JSON only:
{"records": [{"resource_id": "...", "aliases": ["..."], "status": "...",
              "email": "...|null", "phone": "...|null", "name": "...|null",
              "user_reference": "...|null", "detail": "..."}]}

Rules:
- First record: the primary entity in the page header / subtitle / detail panel.
  Its resource_id is the internal id shown there in the form word_digits
  (e.g. support_1034, account_5762, analytics_7438, data_flow_1121, backup_9287,
  marketing_001). If the only id shown is a customer key such as user_17, use it.
  Put every other id or reference displayed for it (ticket number, order id,
  file name, user id) in aliases.
- Then one record per table row; resource_id is that row's own id, file name or
  reference exactly as displayed; aliases lists other ids shown in the row.
- status is the row's / entity's status text exactly as displayed (e.g. Live,
  Healthy, Ready, FOUND, Active, Archive, Valid, Retained reference); null if none.
- email, phone, name, user_reference: only values actually displayed for that
  record (user_reference is a user_<digits> id); null otherwise. Never invent.
- detail: short free text with any other displayed facts (dates, retention end,
  restore activity, opt-in state)."""


def _b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def load_cache() -> dict[str, dict]:
    """Cached extractions only (no model calls) — used by offline runs."""
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def extract_all(force: bool = False) -> dict[str, dict]:
    cache = {} if force else load_cache()
    from llm import chat_json, flush

    todo = []
    for row in load_manifest():
        fid, rel = row["file_id"], row["file_path"]
        if not rel.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        if cache.get(fid, {}).get("v") != PROMPT_VERSION:
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
        except (
            Exception
        ) as exc:  # transient failure: keep the old entry, retry next run
            print(f"vision extraction failed for {fid}: {type(exc).__name__}")
            continue
        cache[fid] = {"v": PROMPT_VERSION, "records": records}
    if todo:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        flush()
    return cache
