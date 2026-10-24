"""Native assistant text with an optional completion signal; no required envelope."""

# An optional model control signal, never interpreted from customer language.
# The visible reply stays ordinary text; is_stop consumes only local metadata.
STOP_MARKER = "###STOP###"


def response_content(content):
    text = render_model_reply(content)
    complete = text.rstrip().endswith(STOP_MARKER)
    if complete:
        text = render_model_reply(text.rstrip()[:-len(STOP_MARKER)].rstrip())
    return text, complete


def render_model_reply(content):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Text replies must be nonempty strings")
    # JSON, Markdown and natural language are all valid customer-facing text.
    # Truthfulness is an Agent instruction grounded in tool results, not an
    # invented kind/text/evidence envelope or customer-language parser.
    return content


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
When ALL stated goals have been resolved or their real supported limits have
been explained, with no outstanding question, approval or uncertain write,
end the final natural-language response with ###STOP### to report completion.
Intermediate replies need no marker. It signals your completion decision, not
a customer phrase or proof that an operation succeeded.
Never append it while requesting information/approval or leaving work pending.
Completing an exchange-request goal means its actual registration, not later
refund arrival or shipping. A successful write alone does not resolve other goals.
"""
