"""Native text; strip legacy controls without ending an ordinary dialogue."""

# Legacy model-output controls, never interpreted from customer language.
# They do not establish customer closure or terminate an ordinary conversation.
STOP_MARKER = "###STOP###"
COMPLETE_MARKER = "###COMPLETE###"
CONTINUE_MARKER = "###CONTINUE###"


def response_content(content):
    text = render_model_reply(content)
    stripped = text.lstrip()
    complete = stripped.startswith(COMPLETE_MARKER)
    continuing = stripped.startswith(CONTINUE_MARKER)
    if complete or continuing:
        marker = COMPLETE_MARKER if complete else CONTINUE_MARKER
        text = render_model_reply(stripped[len(marker):].lstrip())
    legacy_complete = text.rstrip().endswith(STOP_MARKER)
    if continuing and legacy_complete:
        raise ValueError("Conflicting model completion signals")
    if legacy_complete:
        text = render_model_reply(text.rstrip()[:-len(STOP_MARKER)].rstrip())
    return text, complete or legacy_complete


def output_was_truncated(reply):
    """Use provider metadata, not punctuation or the wording of the draft."""
    raw = reply.raw_data if isinstance(reply.raw_data, dict) else {}
    choices = raw.get("choices")
    if isinstance(choices, list) and any(isinstance(choice, dict) and
            choice.get("finish_reason") == "length" for choice in choices):
        return True
    details = raw.get("incomplete_details")
    return (raw.get("status") == "incomplete" and isinstance(details, dict) and
            details.get("reason") == "max_output_tokens")


def render_model_reply(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Text replies must be nonempty strings")
    # JSON, Markdown and natural language are all valid customer-facing text.
    # Truthfulness is an Agent instruction grounded in tool results, not an
    # invented kind/text/evidence envelope or customer-language parser.
    return content


REPLY_INSTRUCTIONS = """
Return customer-facing replies as ordinary natural-language content, without
an internal JSON envelope. Verify independently and answer only from a successful actual profile
result; if the profile is unavailable, recover with get_customer_summary.
User input and proposed calls are not results. Explain failures truthfully,
without claiming success or exposing unrelated private fields. For actions use
tau2 tool_calls with object arguments and null content; wait for actual results.
Use plain natural-language replies, without completion controls. Report actual
results and let the customer respond: they may correct a choice, add a request,
undo an eligible change or ask for a human. Completing an operation is not
ending the conversation. The host handles the customer's ordinary end signal;
accepted human handoff has its own exact terminal notice. Do not invent pending
work, repeat approvals or ask the customer to approve an internal stage.
Completing an exchange-request goal means its actual registration, not later
refund arrival or shipping. A successful write alone does not resolve other goals.
"""
