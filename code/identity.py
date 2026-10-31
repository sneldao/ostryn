"""Identity resolution: request email -> stable user_id + every known identifier.

The request email may be the current address or any historical one recorded in
user_audit_log.csv. All current and historical emails, phones, email hashes and
names are returned so evidence written under an old identifier still links.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from datasource import src


@dataclass
class Identity:
    user_id: str | None
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    hashes: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    audit_ids: list[str] = field(default_factory=list)

    @property
    def link_tokens(self) -> set[str]:
        """Tokens strong enough to link evidence on their own (names excluded:
        they are not unique and decoys reuse similar names)."""
        toks = set(self.emails) | set(self.phones) | set(self.hashes)
        if self.user_id:
            toks.add(self.user_id.lower())
        return toks


class IdentityIndex:
    def __init__(self) -> None:
        self.users = {r["user_id"]: r for r in src.rows("user_identities")}
        self.audit = src.rows("user_audit_log")
        self.by_email: dict[str, set[str]] = {}
        for uid, r in self.users.items():
            self.by_email.setdefault(r["email"].lower(), set()).add(uid)
        for a in self.audit:
            if a["field_name"] == "email":
                for v in (a["old_value"], a["new_value"]):
                    self.by_email.setdefault(v.lower(), set()).add(a["user_id"])

    def resolve(self, email: str) -> Identity:
        uids = self.by_email.get(email.strip().lower(), set())
        if not uids:
            ident = Identity(None)
            ident.emails.add(email.strip().lower())
            return ident
        uid = sorted(uids)[0]
        u = self.users.get(uid, {})
        ident = Identity(uid)
        if u:
            ident.emails.add(u["email"].lower())
            ident.phones.add(u["phone"].lower())
            ident.hashes.add(u["email_hash"].lower())
            ident.names.add(u["name"].lower())
        ident.emails.add(email.strip().lower())
        for a in self.audit:
            if a["user_id"] != uid:
                continue
            ident.audit_ids.append(a["audit_id"])
            bucket = {
                "email": ident.emails,
                "phone": ident.phones,
                "email_hash": ident.hashes,
                "name": ident.names,
                "full_name": ident.names,
            }.get(a["field_name"])
            if bucket is not None:
                for v in (a["old_value"], a["new_value"]):
                    if v:
                        bucket.add(v.lower())
        # email_hash is sha256(lower(email)) in user_identities.csv, so evidence
        # keyed by the hash of any current or historical address links as well
        ident.hashes |= {hashlib.sha256(e.encode()).hexdigest() for e in ident.emails}
        return ident
