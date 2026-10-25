"""Request-intent classification: does request_text authorize deletion?

Deterministic keyword prefilter first; the bulk LLM tier classifies the rest
(and double-checks borderline texts). Results are cached in runs/intent_cache.json
keyed by the exact request text, so repeated runs cost nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "runs" / "intent_cache.json"

# multilingual deletion-intent cues (en, es, pt, fr, de, ja, ko, ...)
DELETION_RE = re.compile(
    r"delet|eras|remov|close\s+(my|this|the)|shut\s*down|suppr|elimin|exclus|"
    r"suprimir|löschen|annull|verwijder|削除|삭제|抹去|删除|cancel\s+my|"
    r"permanently\s+(delete|remove|erase)|right\s+to\s+be\s+forgotten|"
    r"purge|wipe\s+my|take\s+down\s+my",
    re.I,
)
# explicit non-deletion intents
KEEP_RE = re.compile(
    r"don'?t\s+(delete|close|remove)|leave\s+(the\s+)?account|keep\s+(the\s+)?account|"
    r"still\s+(use|want|need|have)|do\s*not\s+(delete|close)|no\s+deletion|"
    r"stay\s+open|without\s+(deleting|closing|removing)|"
    r"keep\s+(it|the\s+account)\s+as\s+it\s+is",
    re.I,
)
# follow-up / audit phrasings: the person already asked for deletion and now
# asks us to verify, confirm or finish it — always an authorized deletion
AUDIT_RE = re.compile(
    r"confirm\s+that\s+the\s+|check\s+that\s+nothing|still\s+existed|"
    r"still\s+appears|can\s+still\s+find|inspect\s+the\s+actual|"
    r"was\s+left\s+behind|finish\s+the\s+deletion|complete\s+the\s+deletion|"
    r"handle\s+them",
    re.I,
)

PROMPT = """You are a privacy-ops classifier. Decide whether the customer message
below requests DELETION of their account or personal data.

Classification rules:
- true  -> the person asks to delete/erase/remove/close their account or personal
           data, in any language. A request to VERIFY, CONFIRM or AUDIT a deletion
           they previously asked for also counts as true.
- false -> access/export request, correction/update, marketing opt-out, complaint,
           or any message that explicitly says the account must be kept.

Answer with JSON only, exactly this shape:
{{"deletion_requested": true, "reason": "<10 words max>"}}
(use true or false as the value)

Message:
{text}
"""


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def classify_texts(texts: list[str], use_llm: bool = True) -> dict[str, bool]:
    """Return {text: deletion_requested} for every unique text.

    Obvious keep-intent texts short-circuit; everything else goes through the
    bulk LLM so borderline phrasings (export/correction/opt-out) are judged
    rather than pattern-matched.
    """
    cache = _load_cache()
    out: dict[str, bool] = {}
    pending: list[str] = []
    for t in texts:
        if t in cache:
            # audit/verification follow-ups always override a cached "false"
            if AUDIT_RE.search(t):
                cache[t] = True
                out[t] = True
            else:
                out[t] = cache[t]
            continue
        if KEEP_RE.search(t):
            out[t] = False
            cache[t] = False
            continue
        pending.append(t)
    if use_llm and pending:
        from llm import chat_json, flush

        for t in pending:
            try:
                res = chat_json(
                    "bulk",
                    [{"role": "user", "content": PROMPT.format(text=t)}],
                    tag="intent",
                    max_tokens=200,
                )
                val = bool(res.get("deletion_requested", True))
            except Exception:
                # offline / transient failure: fall back to the keyword prior,
                # defaulting to authorized (the dataset prior is ~98% deletion)
                val = not KEEP_RE.search(t)
            out[t] = val
            cache[t] = val
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        flush()
    elif pending:
        # offline fallback: keyword prior, defaulting to authorized
        for t in pending:
            out[t] = bool(DELETION_RE.search(t))
    return out
