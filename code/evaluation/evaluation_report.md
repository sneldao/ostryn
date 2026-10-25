# Evaluation Report — Ostryn Deletion-Audit Agent

Date: 2026-10-25 · Harness: `python3 code/evaluation/main.py [--verbose]`

The harness scores predictions against `dataset/sample_output.csv` (32 labelled cases)
at four levels: case status, full-step tuples (precision/recall/F1, order-insensitive),
per-field accuracy on steps aligned by `(phase, system_id, resource_id, operation)`, and
`evidence_id` accuracy. Sequence accuracy requires an exact ordered match of `steps_json`.

## Results on the labelled sample set (32 cases)

| Metric | Score |
| --- | --- |
| Status accuracy | **100%** (32/32) |
| Field accuracy | 97.5% |
| Evidence accuracy | 97.9% |
| Step precision | 95.8% |
| Step recall | 94.8% |
| Step F1 | 95.3% |
| Sequence accuracy | 84.4% (27/32) |

## Additional evaluation cases (created by us)

Three hand-constructed requests (not part of the labelled set), each run through the
full pipeline (`Auditor(all_cases + [case]).audit(case)`) and checked against the
expected outcome derived by hand from the participant data.

### Custom case 1 — historical-identifier linkage (`cust-01`)

Subject: `user_214`, request email `mnovak939594@fastmail.com` — the **old** address
renamed in `user_audit_log.csv` (audit_201: `mnovak939594@…` → `mnovak9395@…`).
Expected: resolved to `user_214`; resources written under the old address link via the
historical identifier. Result: **pass** — COMPLETE, `account_9682` VERIFY_ABSENT and
`audit_record_713` VERIFY_PRESENT, both citing `evidence_21`.

### Custom case 2 — absence by omission (`cust-02`)

Subject: `user_826` (`ldarwish5823@fastmail.com`), whose profile appears in no evidence
row at or before `audit_at` but is absent from the later `customer_accounts_failover_
replica` snapshot that covers the profile store. Expected: COMPLETE with an
absence-by-omission proof. Result: **pass** — `account_71094` VERIFY_RECORD_ABSENT
citing `evidence_54`.

### Custom case 3 — retention overrides deletion (`cust-03`)

Subject: `user_31` (`e.lindberg18@yahoo.com`) with a security audit record subject to
the RETAIN rule. Expected: the retained record produces `VERIFY_RECORD_PRESENT` (never
DELETE) with evidence available at `audit_at`; the deleted profile produces
`VERIFY_RECORD_ABSENT`. Result: **pass** — `account_146` VERIFY_ABSENT and
`audit_record_5307` VERIFY_PRESENT, both citing `evidence_03`. (An earlier variant of
this case with `audit_at` before any covering evidence confirmed the complementary
behaviour: an unverifiable retained record is omitted from a COMPLETE verdict rather
than asserted without proof.)

## Failures found and improvements made

1. **Workflow results are claims, not proof.** Early on we trusted `reported_status` and
   `SUCCESS` activity rows. The sample data proved a `SUCCESS` deletion can leave the
   record present in a failover replica. Fix: only state-establishing evidence
   (lookups, checks, snapshots, restores) decides state; worker outcomes are used solely
   for resource→case linking.
2. **Batch-window resource stealing.** Cases whose `reported_at` windows overlap could
   claim another case's deletion event. Fix: an event is only claimable by the batch
   whose `reported_at` is nearest the event, and primary-profile assignment pairs
   `reported_status` with the worker `reported_result` (COMPLETE↔SUCCESS,
   NOT_STARTED↔FAILED, PARTIALLY_COMPLETE↔PARTIAL) before falling back to batch order.
   This fixed a case_04↔case_30 swap found by the harness.
3. **Analytics pairing.** `ANONYMIZE_RECORD` events carry no subject (identifiers already
   stripped). Ordering claimants so cases that already have a linked search resource go
   last resolved the labelled pairings (case_25↔analytics_1895, case_26↔analytics_4566).
4. **Intent misclassification.** The LLM prompt template crashed on literal JSON braces
   (`str.format`), silently degrading classification to a regex prior. Fixed the
   template, added an audit-follow-up override (verification phrasings such as "confirm
   that the profile is gone" are authorized deletions), and re-ran all 398 unique texts
   through the bulk tier. Detection of the 10 `NOT_APPLICABLE` requests (case_533–542)
   is now LLM-based with a deterministic fallback.
5. **Absence-proof selection.** Empty snapshots are valid absence proofs but must not
   outrank a subject-linked bundle; the preference order (subject-linked file →
   non-empty snapshot → latest) was derived from the labelled gold evidence choices.

## Known residual gaps (why sequence accuracy is not 100%)

Five sample cases differ from gold by 1–2 steps. Every residual traces to
generator-internal values that appear nowhere in participant-visible data — e.g.
`account_004` (case_04) and `audit_record_6645` / `audit_record_5569` (case_18/20) are
cited by the gold answers but have no row in any evidence file or activity stream, and
one account pairing (case_23) is undecidable from timing alone. Hardcoding these would
violate the no-gold-labels rule, so the agent reports what the evidence supports.

## Reproduction

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 code/main.py                    # full run -> output.csv (518 rows)
python3 code/evaluation/main.py         # scores the 32 labelled sample cases
python3 code/evaluation/main.py --verbose   # per-case diffs
```
