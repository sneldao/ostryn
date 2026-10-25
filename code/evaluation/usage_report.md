# Token Usage & Cost Report — Final Full-Dataset Run

Date: 2026-10-25 · Pipeline: `python3 code/main.py` (produces root `output.csv`, 518 rows)

## Models

| Tier | Provider | Model | Role |
| --- | --- | --- | --- |
| `bulk` | Neon AI Gateway (OpenAI) | `gpt-5-4-nano` | request-intent classification (deletion vs access/correction/opt-out), one call per unique `request_text` |
| `vision` | Neon AI Gateway (Anthropic) | `claude-sonnet-4-5` | PNG console evidence extraction, one call per image |
| `judge` | Neon AI Gateway (Anthropic) | `claude-sonnet-4-5` | reserved for ambiguous-case verdicts (not triggered in the final run) |
| — | Neon AI Gateway (Moonshot) | `kimi-k3` | gateway smoke test during model bring-up |

All calls are traced by Respan and appended to `runs/usage.jsonl`; the table below is
generated from that ledger by `code/llm.py:usage_summary()`.

## Token totals (all calls that produced the final artifacts)

| Model | Calls | Prompt tokens | Completion tokens | Total tokens |
| --- | ---: | ---: | ---: | ---: |
| gpt-5-4-nano | 393 | 71,667 | 8,742 | 80,409 |
| claude-sonnet-4-5 | 12 | 19,276 | 4,466 | 23,742 |
| kimi-k3 | 1 | 92 | 45 | 137 |
| **Overall** | **406** | **91,035** | **13,253** | **104,288** |

## Averages and estimated cost

Estimated with public list pricing at time of writing: gpt-5-4-nano ≈ $0.10 / 1M input,
$0.40 / 1M output; claude-sonnet-4-5 ≈ $3 / 1M input, $15 / 1M output; kimi-k3 ≈ $0.60 /
1M input, $2.50 / 1M output. Calls were billed through Neon gateway credits.

| Model | Input $ | Output $ | Total $ |
| --- | ---: | ---: | ---: |
| gpt-5-4-nano (393 calls) | $0.0072 | $0.0035 | **$0.0107** |
| claude-sonnet-4-5 (12 calls) | $0.0578 | $0.0670 | **$0.1248** |
| kimi-k3 (1 call) | $0.0001 | $0.0001 | **$0.0002** |
| **Total** | **$0.0651** | **$0.0706** | **≈ $0.1357** |

Per request (518 evaluation cases):

| Metric | Value |
| --- | --- |
| Total tokens / request | 104,288 / 518 ≈ **201** |
| Estimated cost / request | ≈ **$0.00026** |
| LLM calls / request | 406 / 518 ≈ **0.78** (most calls are one-time and cached) |

## Caching behaviour

- Intent classifications are cached by exact `request_text` in `runs/intent_cache.json`
  (398 unique texts; ~376 go through the bulk tier once, then never again).
- Vision extractions are cached by `file_id` in `runs/vision_cache.json` (11 images).
- The deterministic audit engine (identity resolution, evidence parsing, state
  reconstruction, policy application, step assembly) makes **no** LLM calls.
- A cold run from an empty cache costs the full table above; every subsequent run of
  `code/main.py` is LLM-free and completes in seconds.

## Determinism

The pipeline is deterministic given the caches: identical inputs produce an identical
`output.csv` (sorted rows, stable JSON serialization, no sampling temperature).
