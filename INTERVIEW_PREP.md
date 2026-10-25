# AI Judge Interview — Prep Sheet

The judge interviews for ~30 min: opening pitch, problem framing, failure modes,
technical deep-dive into the code, testing, production viability, and how you
directed AI. Weights: technical depth/ownership 40%, problem understanding 25%,
communication 20%, honesty/self-awareness 15%.

## 60-second pitch

> We built a deletion-audit agent for a commerce platform where workflows report
> "complete" while personal data survives in replicas, logs, exports, search
> indexes, backups, and sync pipelines. The agent resolves the requester's
> current and historical identity, reconstructs every resource's state at the
> audit cutoff from raw evidence — never trusting reported workflow statuses —
> applies the supplied deletion policy, and emits either the evidence that
> proves deletion is complete or the remaining findings, an ordered remediation
> plan, and the checks that must pass afterward. Non-deletion requests
> (access, export, opt-out) are correctly classified out. On the labelled set:
> 100% status accuracy, ~97% step precision, ~98% evidence accuracy.

## Architecture (know it cold)

```
request ──▶ intent classifier (LLM, bulk tier)      authorized? ──no──▶ NOT_APPLICABLE
        ──▶ vision extractor (LLM, vision tier)     PNG consoles ──▶ typed observations
        ──▶ evidence parser (deterministic)         SQL/JSON/log/PDF/activity ──▶ Obs
        ──▶ identity index                          email+history+sha256 hashes ──▶ tokens
        ──▶ Auditor                                 link → state → policy → assemble
        ──▶ AuditAgent (LLM, judge tier)            tool-calling investigation loop
```

- **`identity.py`** — the request email may be historical. We join
  `user_identities.csv` with every row of `user_audit_log.csv`, so renamed
  emails, phones, names and *sha256(email)* hashes all become link tokens.
  100% resolution across 550 requests.
- **`evidence.py`** — every sighting becomes an `Obs` with two clocks:
  `avail_at` (when the evidence became available) and `obs_at` (when the
  resource was seen). SQL snapshots are executed in-memory; logs grouped by
  `trace_id`; PDFs via pypdf; PNGs via the vision tier. Decoy files parse fine
  but link to nobody.
- **`audit.py`** — the policy engine:
  - *linking*: identifier co-occurrence first; identifier-stripped resources
    (deleted profiles, anonymized analytics) are claimed through the
    deletion-worker batch around `reported_at`, status-paired
    (COMPLETE↔SUCCESS, NOT_STARTED↔FAILED, PARTIAL↔PARTIALLY_COMPLETE), with a
    nearest-reporter guard so overlapping batches can't steal events, then
    mutually-unique elimination. Ambiguous pairs stay unlinked rather than
    guessed.
  - *state*: latest observation with `avail_at ≤ audit_at` AND
    `obs_at ≤ audit_at` wins; any replica still violating at that instant wins
    (replica drift). Worker `SUCCESS` is a claim, not proof.
  - *policy*: `deletion_rules.csv` maps 15 categories to DELETE / REDACT /
    ANONYMIZE / RETAIN / tombstone-REDACT / DISABLE flow / BLOCK restore.
  - *assembly*: FINDING → REMEDIATION → VERIFICATION, then operation-table
    order, then `system_id`, `resource_id`.
- **`agent.py` + `tools.py`** — the agentic layer: the judge-tier model drives
  a per-case investigation through typed tools (`classify_request`,
  `resolve_identity`, `linked_resources`, `inspect_resource`, `batch_activity`,
  `policy_assessment`), then finalizes with verdict + rationale. Guardrails:
  12-iteration cap, one correction turn on malformed JSON, and a consistency
  check — the deterministic engine owns state truth, so a contested verdict is
  challenged once and divergences are logged. `--agent --offline` replays the
  canonical tool sequence for a zero-cost parity check of the whole layer.

## Design decisions you'll be asked to defend

1. **Why is reported_status untrusted?** Sample data proved a `SUCCESS`
   deletion can leave the record in a failover replica. Only state-establishing
   evidence (lookups, snapshots, restores, checks) decides state; worker
   outcomes are used solely for batch linkage.
2. **Why a deterministic core instead of LLM-everything?** Ordering,
   timestamps, and policy are exact — a serializer is the right tool, and it
   makes `output.csv` reproducible for the grader. The LLM is used where
   judgment is real: multilingual intent, image consoles, and orchestrating
   the investigation.
3. **Why does verification differ for COMPLETE vs INCOMPLETE?** COMPLETE
   verifies every linked resource with evidence; INCOMPLETE verifies only
   acted-on resources plus the two unconditional checks — the primary profile
   and policy-retained records. Derived by diffing our emission against the
   labelled set.
4. **How do you handle a profile with no identifiers left?** Two mechanisms:
   the deletion-worker's batch events claimed by status-pairing and
   elimination, and absence-by-omission — an empty snapshot covering the
   profile store is a valid absence proof.
5. **Why cache everything?** Intent is cached per unique `request_text`, vision
   per `file_id` — the shipped `output.csv` regenerates offline in seconds,
   LLM-free, and every call is ledgered to `runs/usage.jsonl`.

## Failure modes to name proactively

- **Generator-internal IDs**: gold cites e.g. `account_004`,
  `audit_record_6645` for resources whose IDs appear in no participant file.
  We report what the evidence supports rather than hardcode labels — that's a
  principled miss, ~4 sample cases, each within 1–2 steps.
- **Ambiguous batch pairing**: when several same-status cases share a batch
  and several matching events exist, no participant-visible signal fully
  orders them; we claim mutually-unique pairs and leave contested ones
  unlinked instead of guessing (a wrong guess could flip a verdict).
- **Replica drift**: a record deleted in the primary can persist in a replica
  snapshot — the violating copy wins.
- **Audit cutoff**: anything after `audit_at` is ignored, including evidence
  that would later prove the deletion completed.

## Numbers

- Sample (32 labelled): status 100%, field 97.6%, evidence 97.9%, step
  precision 96.8% / recall 95.3% / F1 96.1%, sequence 87.5%.
- Final output: 518 rows — 153 COMPLETE / 355 INCOMPLETE / 10 NOT_APPLICABLE.
- Agent parity (live, judge tier): 41/41 verdicts agreed with the engine.
- Cost of the output-producing run: 406 calls, ~104k tokens, ≈ $0.14 total.

## How you worked with AI

Use concrete stories: directed the pipeline design myself; the harness caught
a batch-stealing regression (case_04↔30 swap) and an intent-prompt crash
(`str.format` on JSON braces) that silently degraded classification; an
absence-proof preference order was derived from labelled evidence choices;
the verification-scope rule and the nearest-reporter status bug were found by
diffing predictions against gold, not by reading the problem statement.
