"""Model-declared reply intent and references to actual tool evidence.

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
    try:
        intent = ReplyIntent.model_validate_json(content, strict=True)
    except (ValidationError, ValueError, TypeError):
        raise ValueError("Text replies require the documented reply-intent JSON") from None
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
For a customer-facing text reply, put ONLY one JSON object in content (no code
fences). This is an internal format; the Agent renders it for the customer:
{"kind":"clarification","text":"your question"} for greetings, missing
information or clarification, with no account facts or claims of completion.
{"kind":"policy","text":"your general policy explanation"} for policy,
with no claims about a particular customer's records or an operation outcome.
{"kind":"facts","text":"your explanation based on tool results",
 "evidence_call_ids":["actual successful call ID"]} for factual explanations.
For an account email/ID answer use {"kind":"customer_fields",
"fields":["email"],"evidence_call_ids":["actual profile receipt call ID"]}.
Customer fields are rendered from the real profile; do not put email values in
text. Use customer_id in fields only if requested/useful. Do not echo identifying
input as a result. A profile-unavailable verification receipt contains no email;
use get_customer_summary to read details, then reference its successful receipt.
Choose reply kind semantically and honestly; never label an account fact as
policy/clarification to avoid its evidence requirement. Tool calls still use
normal tau2 tool_calls with object arguments, and content must be null.
Take evidence_call_ids from evidence_call_id in successful projected tool
content, not from guessed IDs. Failed results have no successful reference.
"""
