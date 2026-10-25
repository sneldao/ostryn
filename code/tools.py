"""Tool facade over the deterministic audit engine.

The agent loop in agent.py can only act through these tools: every tool returns
JSON-serializable observations drawn from participant-facing data at or before
the case's audit_at. The deterministic policy engine stays the source of truth
for state reconstruction — the model orchestrates the investigation and must
defend its final verdict against the engine's assessment.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from audit import Auditor, Case
import evidence as E


def _brief_fields(f: dict[str, Any]) -> dict[str, Any]:
    """Trim an observation's fields to what an investigator needs to see."""
    keep = {
        k: v
        for k, v in f.items()
        if v not in (None, "", "null")
        and k
        in {
            "result",
            "status_code",
            "rows",
            "total_hits",
            "enabled",
            "event",
            "event_name",
            "account_status",
            "email",
            "subject_reference",
            "customer_reference",
            "retention_until",
            "record_type",
            "actor_type",
            "collection_name",
        }
    }
    return keep


class AuditToolbox:
    """Per-case investigative tools backed by a shared Auditor."""

    def __init__(self, auditor: Auditor, intent: dict[str, bool]) -> None:
        self.aud = auditor
        self.intent = intent
        self.cases = {c.case_id: c for c in auditor.cases}

    def _case(self, case_id: str) -> Case:
        if case_id not in self.cases:
            raise KeyError(f"unknown case_id {case_id!r}")
        return self.cases[case_id]

    # -- tools ------------------------------------------------------

    def resolve_identity(self, case_id: str) -> dict:
        """Resolve the request email to the stable user_id and every known
        current/historical identifier (emails, phones, name, hashes)."""
        c = self._case(case_id)
        ident = self.aud.ident[case_id]
        return {
            "request_email": c.email,
            "user_id": ident.user_id,
            "identifiers": sorted(ident.link_tokens),
        }

    def classify_request(self, case_id: str) -> dict:
        """Whether request_text authorizes deletion (vs access/correction/
        export/opt-out). Non-deletion requests make the case NOT_APPLICABLE."""
        c = self._case(case_id)
        authorized = self.intent.get(c.request_text, True)
        return {
            "request_text": c.request_text,
            "authorizes_deletion": authorized,
            "note": "NOT_APPLICABLE verdict with empty steps_json is required "
            "when the request does not authorize deletion.",
        }

    def linked_resources(self, case_id: str) -> dict:
        """Every resource linked to the subject with its policy treatment and
        compliance verdict at audit_at."""
        c = self._case(case_id)
        views = []
        for rid in sorted(self.aud.links[case_id]):
            v = self.aud._view(rid, c)
            if v is not None:
                views.append(asdict(v))
        return {
            "case_id": case_id,
            "audit_at": c.audit_at,
            "resources": views,
        }

    def inspect_resource(self, case_id: str, resource_id: str) -> dict:
        """Observation timeline for one resource: every sighting that could
        establish state at audit_at, plus worker operations planned for it."""
        c = self._case(case_id)
        if resource_id not in self.aud.links[case_id]:
            return {"error": f"{resource_id} is not linked to {case_id}"}
        obs = sorted(self.aud.by_rid.get(resource_id, []), key=lambda o: o.obs_at)
        return {
            "resource_id": resource_id,
            "system_id": E.system_of(resource_id),
            "worker_ops": sorted(self.aud.worker_ops.get(resource_id, ())),
            "observations": [
                {
                    "evidence_id": o.evidence_id,
                    "avail_at": o.avail_at,
                    "obs_at": o.obs_at,
                    "usable": o.avail_at <= c.audit_at and o.obs_at <= c.audit_at,
                    "fields": _brief_fields(o.fields),
                }
                for o in obs
            ],
        }

    def batch_activity(self, case_id: str) -> dict:
        """Deletion-worker events inside the case's reported_at batch window.
        Needed to reason about primary-profile claims and identifier-stripped
        resources that cannot be linked by identity."""
        c = self._case(case_id)
        events = [
            {
                "event_time": a["event_time"],
                "event_type": a["event_type"],
                "target_system": a["target_system"],
                "resource_id": a["resource_id"],
                "reported_result": a["reported_result"],
            }
            for a in self.aud._batch_events(c)
        ]
        return {
            "case_id": case_id,
            "reported_status": c.reported_status,
            "reported_at": c.reported_at,
            "batch_events": events,
        }

    def policy_assessment(self, case_id: str) -> dict:
        """The deterministic engine's verdict: status plus the ordered
        FINDING/REMEDIATION/VERIFICATION plan it supports."""
        c = self._case(case_id)
        r = self.aud.audit(c)
        return {
            "deletion_status": r.status,
            "step_count": len(r.steps),
            "steps": r.steps,
        }

    # -- dispatch ---------------------------------------------------

    def call(self, name: str, args: dict) -> Any:
        fn = {
            "resolve_identity": lambda: self.resolve_identity(args["case_id"]),
            "classify_request": lambda: self.classify_request(args["case_id"]),
            "linked_resources": lambda: self.linked_resources(args["case_id"]),
            "inspect_resource": lambda: self.inspect_resource(
                args["case_id"], args["resource_id"]
            ),
            "batch_activity": lambda: self.batch_activity(args["case_id"]),
            "policy_assessment": lambda: self.policy_assessment(args["case_id"]),
        }.get(name)
        if fn is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return fn()
        except (KeyError, TypeError) as e:
            return {"error": f"bad arguments for {name}: {e}"}


def tool_specs() -> list[dict]:
    """JSON-schema-style tool descriptions embedded in the system prompt."""
    import inspect as _i

    t = AuditToolbox
    out = []
    for name in (
        "resolve_identity",
        "classify_request",
        "linked_resources",
        "inspect_resource",
        "batch_activity",
        "policy_assessment",
    ):
        sig = _i.signature(getattr(t, name))
        params = {p: {"type": "string"} for p in sig.parameters if p != "self"}
        out.append(
            {
                "name": name,
                "description": (getattr(t, name).__doc__ or "").strip(),
                "parameters": params,
            }
        )
    return out
