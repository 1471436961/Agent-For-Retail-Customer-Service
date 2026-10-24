"""Native assistant text with an optional completion signal; no required envelope."""

# An optional model control signal, never interpreted from customer language.
# The visible reply stays ordinary text; is_stop consumes only local metadata.
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


def has_completion_decision(content):
    """Only model protocol controls, never interpreted from customer text."""
    return isinstance(content, str) and (
        content.lstrip().startswith((COMPLETE_MARKER, CONTINUE_MARKER)) or
        content.rstrip().endswith(STOP_MARKER))


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
Choose completion explicitly BEFORE writing a text response: prefix it with
###COMPLETE### when ALL stated goals have been resolved or their real supported
limits explained, with no outstanding task, approval or uncertain write. Use
###CONTINUE### when a substantive question, confirmation or requested task
remains. The control is removed before display; the rest is ordinary text.
The legacy final ###STOP### is also accepted. Plain intermediate text remains
valid. These controls are your decision, not customer words or proof of success.
Never declare completion while requesting information/approval or leaving work
pending. A generic courtesy question does not create a new customer goal; once
the real work is resolved, report the result and finish without reopening it.
Completing an exchange-request goal means its actual registration, not later
refund arrival or shipping. A successful write alone does not resolve other goals.
"""


COMPLETION_REVIEW = """The following text is an UNSHOWN draft, not a message
the customer saw and not new evidence or instructions. Reconsider the FULL
conversation and actual results. Have you answered every requested information
goal and completed or explained the limits of every requested action? Respect
the customer's disclosure preferences. A successful write alone is insufficient.
Return the final concise response prefixed ###COMPLETE### if all goals are
resolved, or ###CONTINUE### if substantive work/approval remains. Do not invent
another approval round or repeat a completed write. Use a real tool if a remaining
goal requires it. Omit a generic courtesy question when there is no real pending
work. This is an internal decision in this same turn, not a new customer turn.
Unshown draft (JSON string): """
