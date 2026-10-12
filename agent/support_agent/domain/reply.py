"""Native model text plus legacy structured reply compatibility.

Only model output JSON is decoded here. Customer language is never parsed.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ReplyIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["clarification", "policy", "facts", "customer_fields"]
    text: str = ""
    evidence_call_ids: list[str] = Field(default_factory=list)
    fields: list[Literal["email", "customer_id"]] = Field(default_factory=list)


def render_model_reply(content, state):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Text replies must be nonempty strings")
    # AssistantMessage.content is native customer-facing text in the platform
    # contract. Never require an invented JSON envelope for normal dialogue.
    # Retain validation only when the model explicitly uses the old envelope.
    if not content.lstrip().startswith("{"):
        return content
    try:
        intent = ReplyIntent.model_validate_json(content, strict=True)
    except (ValidationError, ValueError, TypeError):
        raise ValueError("Invalid legacy structured reply") from None
    refs = intent.evidence_call_ids
    if len(refs) != len(set(refs)) or set(refs) - set(state.evidence_call_ids):
        raise ValueError("Reply cites unavailable or unsuccessful tool evidence")
    if intent.kind in {"clarification", "policy"}:
        if not intent.text.strip() or refs or intent.fields:
            raise ValueError("Clarification/policy replies require text and no customer facts")
        return intent.text
    if not refs:
        raise ValueError("Factual replies require actual successful tool evidence")
    if intent.kind == "facts":
        if not intent.text.strip() or intent.fields:
            raise ValueError("Factual explanations require text and evidence references")
        return intent.text
    if (intent.text or not intent.fields or len(intent.fields) != len(set(intent.fields)) or
            not state.customer_profile_loaded or state.customer_evidence_id not in refs):
        raise ValueError("Customer fields require the actual loaded profile and its receipt")
    values = {"email": state.customer_email, "customer_id": state.verified_customer_id}
    if any(values[field] is None for field in intent.fields):
        raise ValueError("Requested customer field has no actual record value")
    return "\n".join(field + ": " + values[field] for field in intent.fields)


REPLY_INSTRUCTIONS = """
Return customer-facing replies as ordinary natural-language content, without
an internal JSON envelope. For customer records, first verify independently
with the supplied tools and answer only from a successful actual profile
result. Never treat user input as a returned record. If only verification
succeeded and the profile is unavailable, get_customer_summary must succeed
before an email answer. Explain failed operations truthfully; do not claim
completion or expose unrelated private fields. Clarifications and policy
explanations are normal text too. For actions use normal tau2 tool_calls with
object arguments and null content; wait for actual results before answering.
"""
