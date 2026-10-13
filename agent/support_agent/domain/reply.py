"""Native tau2 assistant text; no additional model reply protocol."""


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
"""
