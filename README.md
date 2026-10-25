# Ostryn — Ghost Data Still Exists

AI-powered deletion-audit agent for the HackerRank Orchestrate October 2026 challenge.

Northstar Shop lost $2.4M after a "deleted" customer came back as a ghost. Ostryn audits
whether personal data was actually removed: for every deletion request it resolves the
subject's current and historical identifiers, reconstructs the true state of every system
at `audit_at` from evidence (never from workflow claims), applies the supplied retention
policy, and produces the ordered findings, remediation plan, and verification checks.

Read [`problem_statement.md`](./problem_statement.md) for the challenge contract and
[`AGENTS.md`](./AGENTS.md) for the agent protocol.

---

## Architecture

```
code/
├── main.py              Entry point: python3 code/main.py → writes output.csv
├── identity.py          Email → user_id + full current/historical identifier set
├── evidence.py          Parses every evidence format into typed observations
├── audit.py             Policy engine + state reconstruction + step assembler
├── llm.py               Tiered LLM client (Neon gateway, Respan-traced)
├── respan_smoke.py      Standalone gateway/tracing wiring test
└── evaluation/
    └── main.py          Scoring harness vs labelled sample answers
```

### Pipeline (per case)

1. **Identity resolution** (`identity.py`) — the request email may be current or
   historical. Both `user_identities.csv` and every email change in
   `user_audit_log.csv` feed the identifier set (emails, phones, email hashes, names).
   Verified: **100% resolution across all 550 requests** (32 sample + 518 evaluation).
   `email_hash` is confirmed to be `sha256(email)`, so hashes link evidence directly.
2. **Evidence parsing** (`evidence.py`) — one typed *observation* per resource sighting,
   tagged with the `file_id`/`activity_id` that proves it and two timestamps:
   `avail_at` (when the evidence item became available) and `obs_at` (when the resource
   was observed). Formats handled:
   - **SQL snapshots** executed in in-memory SQLite — primary, read, regional,
     reporting, and failover replicas are compared for replication drift; table +
     `snapshot_scope` coverage enables *absence by omission* proofs
   - **JSON bundles/exports** (`service_resource_export_*`, `customer_data_snapshot_bundle_*`,
     `security_audit_ledger_*`, `customer_export_*`, `connector_configurations_*`)
   - **Service logs** — four dialects (key=value, JSON-line, bracketed, pipe-delimited),
     grouped by `trace_id`/`requestId` so related lines merge into one observation
   - **PDFs** via `pypdf`, **PNG consoles** via the vision model tier
   - **`system_activity.csv`** — state-establishing rows only
     (`CHECK_DATA_FLOW`, `CHECK_RESTORE_ACCESS`, `RESTORE_BACKUP`, `LINK_RESOURCE_IDENTITY`);
     plain `SUCCESS` outcomes are treated as claims, never as proof
3. **Resource→subject linking** (`audit.py`) — a resource belongs to the subject when any
   evidence item shows it with one of the subject's identifiers (exact match; names are
   excluded because decoys reuse similar names). Resources already stripped of
   identifiers (deleted profiles, anonymized analytics) are linked through the
   deletion-worker batch in the case's `reported_at` window by elimination.
4. **State reconstruction** — the latest observation with `avail_at ≤ audit_at` and
   `obs_at ≤ audit_at` decides each resource's state; any replica still violating at that
   instant wins (replica drift). Nothing after `audit_at` is ever consulted.
5. **Policy application** — `deletion_rules.csv` maps each of the 15 categories
   (including `ACCOUNT_TOMBSTONE`, `SECONDARY_IDENTIFIER`, `SUPPORT_MESSAGE` vs
   `SUPPORT_ATTACHMENT`, `DATA_FLOW`, `BACKUP_ARCHIVE`) to its required treatment.
6. **Step assembly** — deterministic ordering: `FINDING` → `REMEDIATION` → `VERIFICATION`,
   then the operation order from the problem statement, then `system_id`, `resource_id`.
   Proposed remediation and post-remediation verification steps use `evidence_id: ""`.

### LLM usage (`llm.py`)

| Tier | Default model | Use |
| --- | --- | --- |
| `bulk` | `gpt-5-4-nano` | `request_text` intent classification (deletion vs access/correction/export/marketing) |
| `judge` | `claude-sonnet-4-5` | Ambiguous-case verdicts |
| `vision` | `claude-sonnet-4-5` | PNG console evidence extraction |

All calls go through the Neon AI Gateway and are auto-traced by Respan; every call is
also appended to `runs/usage.jsonl`, which feeds `usage_report.md` (model calls, tokens,
cost). Overrides: `MODEL_BULK`, `MODEL_JUDGE`, `MODEL_VISION`.

---

## Progress

### Done

- ✅ Identity resolution — 100% coverage, historical identifiers included
- ✅ All six evidence formats parsed with provenance (`file_id`/`activity_id`) and timestamps
- ✅ Policy engine covering all 15 `deletion_rules.csv` categories
- ✅ Deterministic step assembler conforming to the output contract (§6.3–6.4)
- ✅ Scoring harness with per-case verbose diffs
- ✅ LLM intent classification (bulk tier, cached) — detects the 10 `NOT_APPLICABLE`
  requests (case_533–542); audit-follow-up phrasings handled by rule
- ✅ LLM vision pass over the 11 PNG consoles (vision tier, cached per `file_id`)
- ✅ Dataset deep-dive: generator linkage patterns identified (worker-batch windows,
  status↔result pairing, analytics ordering), decoy corpora mapped

### Sample evaluation (32 labelled cases)

| Metric | Score |
| --- | --- |
| Status accuracy | **100%** (32/32) |
| Field accuracy | 97.5% |
| Evidence accuracy | 97.9% |
| Step precision | 95.8% |
| Step recall | 94.8% |
| Sequence accuracy | 84.4% (27/32) |

Residual gaps trace to generator-internal IDs absent from participant data
(see `code/evaluation/evaluation_report.md`).

### Final state

- Full 518-case run → root `output.csv` (159 COMPLETE / 349 INCOMPLETE / 10 NOT_APPLICABLE)
- `code/evaluation/evaluation_report.md` — metrics, 3 custom evaluation cases,
  failures found & improvements made
- `code/evaluation/usage_report.md` — per-model calls/tokens/cost from `runs/usage.jsonl`
  (406 calls, ~104k tokens, ≈$0.14 total)

### Remaining

1. Package `code.zip` (excludes `dataset/`, `.venv/`, `runs/`) and submit

---

## Quick Start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 code/main.py                 # writes output.csv (518 rows)
python3 code/evaluation/main.py      # scores the 32 sample cases
```

## Dataset

All participant-facing files are in `dataset/` (see `problem_statement.md` for the full
descriptions). Evidence lives under `dataset/evidence/` as PDFs, PNGs, logs, JSON,
plain text, and SQLite-compatible SQL snapshot exports. Only `deletion_requests.csv`
requires predictions; the other files provide identity, policy, activity, and evidence
context. Organizer-only truth is never read.
