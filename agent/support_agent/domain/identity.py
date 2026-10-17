"""Validate structured selectors and hold one backend-verified session identity."""

from dataclasses import dataclass
import json


class ReadError(ValueError):
    """Safe tool-facing failure; never embeds backend text or record contents."""

    def __init__(self, code, *, operation=None, http_status=None, api_code=None, validation_issues=None,
                 response_shape=None):
        self.code = code
        self.operation = operation
        self.http_status = http_status
        self.api_code = api_code
        self.validation_issues = validation_issues or []
        self.response_shape = response_shape or []
        facts = [code]
        if operation:
            facts.append(f"operation={operation}")
        if http_status is not None:
            facts.append(f"HTTP={http_status}")
        if api_code:
            facts.append(f"api_code={api_code}")
        if self.validation_issues:
            facts.append("validation=" + ",".join(issue["path"] + ":" + issue["type"]
                                                 for issue in self.validation_issues))
        if self.response_shape:
            facts.append("response_shape=" + json.dumps(self.response_shape, ensure_ascii=True, separators=(",", ":")))
        super().__init__("; ".join(facts))


@dataclass
class IdentityState:
    # Owned by one toolkit instance, rebuilt only through the tool trajectory.
    customer_id: str | None = None


def identity_selector(email="", first_name="", last_name="", postal_code=""):
    values = (email, first_name, last_name, postal_code)
    if any(not isinstance(value, str) for value in values):
        raise ReadError("incomplete_identity_selector")
    email, first_name, last_name, postal_code = (value.strip() for value in values)
    name_fields = (first_name, last_name, postal_code)
    if email and not any(name_fields):
        return {"email": email}
    if not email and all(name_fields):
        return {"first_name": first_name, "last_name": last_name, "postal_code": postal_code}
    raise ReadError("incomplete_identity_selector")


def resource_id(value):
    """Opaque identifiers are never normalized or interpreted as user language."""
    if not isinstance(value, str) or not value.strip():
        raise ReadError("invalid_resource_identifier")
    return value


def verification_failed():
    # Same public result for not-found, claimed mismatch and cross-account match.
    return ReadError("identity_verification_failed")
