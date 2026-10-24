"""Tiered LLM client for ostryn.

All calls go through the Neon AI Gateway (OpenAI-compatible, prepaid credits)
and are auto-traced by Respan once Respan() is initialized at startup.

Tiers (override via env):
    bulk    cheap model for high-volume evidence scanning/extraction
    judge   strong model for per-case deletion verdicts
    vision  multimodal model for image/PDF evidence (defaults to judge)

Every call is appended to runs/usage.jsonl so evaluation/usage_report.md
can be generated without re-parsing Respan traces.

Usage:
    from llm import init, chat
    init()  # once at startup, before first call
    text = chat("bulk", [{"role": "user", "content": "..."}])
    obj = chat_json("judge", messages, tag="verdict:case_33")
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from respan import Respan

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
USAGE_LOG = REPO_ROOT / "runs" / "usage.jsonl"

# Verified 2026-10-25 on Neon AI Gateway:
#   gpt-5-4-nano: 4 completion tok, no reasoning overhead -> bulk driver
#   claude-sonnet-4-5: strong + vision-capable -> judge / vision
#   kimi-k3: works but is a reasoning model, ~34+ reasoning tok per call;
#            reserve for complex cases via MODEL_* overrides
DEFAULT_MODELS = {
    "bulk": "gpt-5-4-nano",
    "judge": "claude-sonnet-4-5",
    "vision": "claude-sonnet-4-5",
}

_respan: Respan | None = None
_client: OpenAI | None = None
_lock = threading.Lock()


def init() -> Respan:
    """Initialize Respan tracing + the Neon gateway client. Idempotent."""
    global _respan, _client
    if _respan is None:
        _respan = Respan()  # reads RESPAN_API_KEY
    if _client is None:
        _client = OpenAI(
            base_url=f"{os.environ['NEON_AI_GATEWAY_BASE_URL']}/v1",
            api_key=os.environ["NEON_AI_GATEWAY_TOKEN"],
        )
    return _respan


def flush() -> None:
    if _respan is not None:
        _respan.flush()


def model_for(tier: str) -> str:
    return os.environ.get(f"MODEL_{tier.upper()}", DEFAULT_MODELS[tier])


def _record(tier: str, model: str, usage: Any, tag: str, latency_s: float) -> None:
    if usage is None:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
        "model": model,
        "tag": tag,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "latency_s": round(latency_s, 3),
    }
    with _lock:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def chat(
    tier: str,
    messages: list[dict],
    *,
    tag: str = "",
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """One chat completion on the given tier. Returns message content."""
    init()
    model = model_for(tier)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    t0 = time.monotonic()
    resp = _client.chat.completions.create(**kwargs)
    _record(tier, model, resp.usage, tag, time.monotonic() - t0)
    return resp.choices[0].message.content or ""


def _extract_json(raw: str) -> Any:
    """Parse JSON out of a model reply, tolerating prose/fences."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise json.JSONDecodeError("no JSON found", text, 0)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    return json.loads(text[start : end + 1])


def chat_json(tier: str, messages: list[dict], *, tag: str = "", **kw: Any) -> Any:
    """Chat completion parsed as JSON. Retries once on parse failure.

    Note: the Neon gateway rejects response_format=json_object for some
    upstream models (e.g. Claude), so we instruct-and-parse instead.
    """
    raw = chat(tier, messages, tag=tag, **kw)
    try:
        return _extract_json(raw)
    except json.JSONDecodeError:
        fixed = chat(
            tier,
            messages
            + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Re-emit as valid JSON only."},
            ],
            tag=f"{tag}:retry",
            **kw,
        )
        return _extract_json(fixed)


def usage_summary() -> dict:
    """Aggregate runs/usage.jsonl -> per-model and overall token totals."""
    if not USAGE_LOG.exists():
        return {}
    per_model: dict[str, dict] = {}
    for line in USAGE_LOG.read_text().splitlines():
        e = json.loads(line)
        m = per_model.setdefault(
            e["model"],
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        m["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            m[k] += e[k]
    return {
        "per_model": per_model,
        "overall": {
            k: sum(m[k] for m in per_model.values())
            for k in ("calls", "prompt_tokens", "completion_tokens", "total_tokens")
        },
    }
