"""Agentic investigation loop for deletion-audit cases.

The agent reasons over one case at a time through the AuditToolbox: resolve the
identity, judge whether the request authorizes deletion, enumerate and inspect
the linked resources, check the deletion-worker's batch window, then call the
deterministic policy assessment and finalize.

Guardrails
----------
- The model can only reach participant-facing data through tools.
- Iterations are capped (MAX_ITERS); malformed replies get one correction turn.
- The final verdict must match the deterministic policy assessment: the model
  explains the evidence, the engine owns state reconstruction. A contested
  finalize gets one challenge turn, then the engine's verdict stands and the
  divergence is recorded for review.
- `--offline` runs replay the canonical tool sequence (ScriptedInvestigator)
  so output.csv is reproducible without model spend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import llm
from audit import Case
from tools import AuditToolbox, tool_specs

MAX_ITERS = 12

SYSTEM = """You are a deletion-audit agent for a commerce platform's privacy team.

For each request you must decide whether the person's data was actually deleted
by the case's audit_at time. Reported workflow statuses and worker results are
claims, never proof: reconstruct state only from evidence available at or
before audit_at. Deletion_rules decide what each data category requires:
deletion, redaction, anonymization, retention, or an account tombstone that
keeps only the stable internal user reference.

You investigate through tools. A sound sequence is:
1. classify_request — refuse to treat access/correction/export/opt-out texts
   as deletion requests (NOT_APPLICABLE).
2. resolve_identity — current and historical identifiers matter equally.
3. linked_resources — every resource the evidence ties to the subject.
4. inspect_resource / batch_activity — only where you need the timeline or
   the deletion-worker batch to reason about a resource.
5. policy_assessment — the engine's state reconstruction and ordered plan.

Then respond {"action": "finalize", "deletion_status": ..., "rationale": ...}
with status in {COMPLETE, INCOMPLETE, NOT_APPLICABLE}. Your verdict must agree
with the policy_assessment result; if it does not, you will be asked to point
to the observation that proves otherwise.

Every reply must be exactly one JSON object:
  {"action": "call_tool", "tool": "<name>", "args": {...}}
  {"action": "finalize", "deletion_status": "...", "rationale": "..."}
"""


@dataclass
class Investigation:
    case_id: str
    transcript: list[dict] = field(default_factory=list)
    verdict: str | None = None
    rationale: str = ""
    divergent: bool = False


class AuditAgent:
    """LLM-driven investigator. One case per `investigate` call."""

    def __init__(self, toolbox: AuditToolbox, tier: str = "judge") -> None:
        self.tools = toolbox
        self.tier = tier

    def investigate(self, c: Case) -> Investigation:
        inv = Investigation(c.case_id)
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "case": {
                            "case_id": c.case_id,
                            "email": c.email,
                            "requested_at": c.requested_at,
                            "reported_status": c.reported_status,
                            "reported_at": c.reported_at,
                            "audit_at": c.audit_at,
                            "request_text": c.request_text,
                        },
                        "tools": tool_specs(),
                    }
                ),
            },
        ]
        challenged = False
        for _ in range(MAX_ITERS):
            raw = llm.chat(self.tier, messages, tag=f"agent:{c.case_id}")
            inv.transcript.append({"role": "assistant", "content": raw})
            try:
                action = llm._extract_json(raw)
            except json.JSONDecodeError:
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": "Reply with exactly one JSON object per the "
                        "action schema.",
                    },
                ]
                continue
            if action.get("action") == "finalize":
                verdict = str(action.get("deletion_status", "")).upper()
                engine = self.tools.policy_assessment(c.case_id)["deletion_status"]
                if verdict == engine:
                    inv.verdict = verdict
                    inv.rationale = str(action.get("rationale", ""))
                    return inv
                if not challenged:
                    challenged = True
                    messages += [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                f"The deterministic policy assessment for this "
                                f"case is {engine}. If evidence proves "
                                f"otherwise, name the observation; otherwise "
                                f"finalize with {engine}."
                            ),
                        },
                    ]
                    continue
                inv.verdict = engine
                inv.divergent = True
                inv.rationale = str(action.get("rationale", ""))
                return inv
            if action.get("action") == "call_tool":
                result = self.tools.call(
                    str(action.get("tool", "")), action.get("args") or {}
                )
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": json.dumps(result)},
                ]
                inv.transcript.append(
                    {
                        "tool": action.get("tool"),
                        "args": action.get("args"),
                        "result": result,
                    }
                )
                continue
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "Unknown action. Use call_tool or finalize.",
                },
            ]
        # iteration cap reached: the deterministic verdict stands
        inv.verdict = self.tools.policy_assessment(c.case_id)["deletion_status"]
        inv.divergent = True
        inv.rationale = "iteration cap reached; deterministic verdict used"
        return inv


class ScriptedInvestigator:
    """Deterministic replay of the canonical investigation sequence.

    Used for --offline runs so output.csv is reproducible without model spend:
    the same tools, in the same order a minimal investigation takes, feeding the
    same policy engine that owns the final verdict.
    """

    SEQUENCE = (
        "classify_request",
        "resolve_identity",
        "linked_resources",
        "batch_activity",
        "policy_assessment",
    )

    def __init__(self, toolbox: AuditToolbox) -> None:
        self.tools = toolbox

    def investigate(self, c: Case) -> Investigation:
        inv = Investigation(c.case_id)
        for name in self.SEQUENCE:
            result = self.tools.call(name, {"case_id": c.case_id})
            inv.transcript.append(
                {"tool": name, "args": {"case_id": c.case_id}, "result": result}
            )
            if name == "classify_request" and not result.get(
                "authorizes_deletion", True
            ):
                break
        inv.verdict = self.tools.policy_assessment(c.case_id)["deletion_status"]
        return inv
