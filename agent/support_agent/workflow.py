"""Event-driven conversation stages; never classify customer words or consent.

The underlying evidence is updated by state.receive/record_reply. This view
provides one explicit checkpoint instead of an implicit collection of stages
scattered through prompts. A later user turn permits interpretation, not a write.
"""
from enum import Enum


class Stage(str, Enum):
    IDENTITY_REQUIRED = "identity_required"
    PROFILE_REQUIRED = "profile_required"
    SERVING = "serving"
    WAITING_TOOL_RESULT = "waiting_tool_result"
    QUOTE_READY = "quote_ready"
    WAITING_CUSTOMER = "waiting_customer"
    INTERPRETING_CUSTOMER = "interpreting_customer"
    VERIFYING_OUTCOME = "verifying_outcome"
    OUTCOME_AVAILABLE = "outcome_available"
    HANDOFF = "handoff"
    HALTED = "halted"


def prepared_kinds_with_later_input(state):
    return {proposal["facts"]["kind"] for identifier, proposal in state.proposals.items()
            if proposal["state"] == "prepared" and identifier in state.reviewed
            and state.user_turn > state.reviewed[identifier]}


def stage_of(state):
    if state.halt_reason:
        return Stage.HALTED
    if state.handoff in {"accepted", "unknown"}:
        return Stage.HANDOFF
    if state.pending_call_ids:
        return Stage.WAITING_TOOL_RESULT
    if not state.verified_customer_id:
        return Stage.IDENTITY_REQUIRED
    if not state.customer_profile_loaded:
        return Stage.PROFILE_REQUIRED
    changes = [*state.proposals.values(), *state.submissions.values()]
    if any(change["state"] in {"attempted", "pending", "unknown"} for change in changes):
        return Stage.VERIFYING_OUTCOME
    prepared = [identifier for identifier, proposal in state.proposals.items()
                if proposal["state"] == "prepared"]
    if any(identifier not in state.reviewed for identifier in prepared):
        return Stage.QUOTE_READY
    if prepared_kinds_with_later_input(state):
        return Stage.INTERPRETING_CUSTOMER
    if prepared:
        return Stage.WAITING_CUSTOMER
    if state.receipt_facts_to_display:
        return Stage.OUTCOME_AVAILABLE
    return Stage.SERVING


def checkpoint(state):
    stage = stage_of(state)
    guidance = {
        Stage.IDENTITY_REQUIRED: "Independently verify before customer or catalog reads.",
        Stage.PROFILE_REQUIRED: "Verification succeeded; recover the actual profile without repeating search or switching accounts.",
        Stage.SERVING: "Choose tools from the full request and business rules. If choices and a proposed settlement are determined, prepare the read-only quote now. Do not recite mental arithmetic or ask permission to prepare it.",
        Stage.WAITING_TOOL_RESULT: "Await the actual result; a proposed tool call is not evidence of success.",
        Stage.QUOTE_READY: "Explain the actual quote once, including its full relevant scope and calculation. Ask one submission confirmation. Do not ask whether to prepare or start a separate review stage.",
        Stage.WAITING_CUSTOMER: "The quote has been explained; there has been no later customer response. Do not submit.",
        Stage.INTERPRETING_CUSTOMER: "A later customer response exists, not proof of consent. Understand whether it approves submission, asks a question, changes, pauses or withdraws. Answer questions without resetting an unchanged quote. Execute only the exact clearly approved change, without asking the same permission again.",
        Stage.VERIFYING_OUTCOME: "Inspect the real pending/uncertain operation without resubmitting it. This check needs no new approval. Other requested goals remain relevant.",
        Stage.OUTCOME_AVAILABLE: "Explain the actual outcome. Check every original goal before concluding; one write receipt is not proof that all tasks are complete. Do not seek approval for the already attempted operation again.",
        Stage.HANDOFF: "Automation is terminal after accepted or uncertain handoff; do not resume customer API calls.",
        Stage.HALTED: "Preserve evidence and report the actual failure; do not invent a successful outcome.",
    }[stage]
    return {"stage": stage.value, "next_decision": guidance,
            "consent": "The model must interpret the whole conversation; stage and chronology do not prove consent."}
