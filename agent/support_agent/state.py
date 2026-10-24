"""Serializable evidence; no identity inference or business parsing."""

from typing import Literal
import json
from support_agent.adapters.change_tools import EXECUTE_ACTIONS, DIRECT_ACTION
from support_agent.adapters.conversation_api import TRANSFER_NOTICE
from support_agent.domain.gateway_failure import GatewayDiagnostic

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from tau2.data_model.message import (
    AssistantMessage, Message, MultiToolMessage, SystemMessage, ToolMessage, UserMessage,
)

MESSAGE_ADAPTER = TypeAdapter(Message)


class ProtocolError(ValueError):
    """An event cannot safely participate in the host/model protocol."""


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    history: list[dict] = Field(default_factory=list)
    pending_call_ids: list[str] = Field(default_factory=list)
    seen_call_ids: list[str] = Field(default_factory=list)
    turn: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_rounds: int = Field(default=0, ge=0)
    request_tool_rounds: int = Field(default=0, ge=0)
    halt_reason: str | None = None
    protocol_diagnostic: str | None = None
    gateway_diagnostic: GatewayDiagnostic | None = None
    gateway_request: dict | None = None
    verified_customer_id: str | None = None
    customer_profile_loaded: bool = False
    customer_email: str | None = None
    customer_evidence_id: str | None = None
    evidence_call_ids: list[str] = Field(default_factory=list)
    pending_calls: list[dict] = Field(default_factory=list)
    proposals: dict[str, dict] = Field(default_factory=dict)
    reviewed: dict[str, int] = Field(default_factory=dict)
    review_to_display: dict | None = None
    user_turn: int = 0
    handoff: str | None = None
    receipt_facts_to_display: list[dict] = Field(default_factory=list)
    submissions: dict[str, dict] = Field(default_factory=dict)


def initial_state() -> SessionState:
    return SessionState()


def decode(message):
    try:
        return MESSAGE_ADAPTER.validate_python(message).model_copy(deep=True)
    except ValueError as error:
        raise ProtocolError("Invalid message shape") from error


def store(state, message):
    state.history.append(message.model_dump(mode="json", exclude_none=True))


def has_prior_recap_turn(state):
    """Structural evidence only; the model judges recap completeness and consent."""
    customer = next((index for index in range(len(state.history) - 1, -1, -1)
                     if state.history[index]["role"] == "user"), None)
    return customer is not None and any(
        row["role"] == "assistant" and row.get("content") and not row.get("tool_calls")
        for row in state.history[:customer])


def bind_submission_reference(state, reply):
    """Bind replay identity to a real user turn/operation/target, not model wording."""
    if not isinstance(reply, AssistantMessage) or not reply.tool_calls:
        return reply
    calls = []
    for call in reply.tool_calls:
        if call.name == DIRECT_ACTION:
            arguments = dict(call.arguments)
            params = arguments.get("parameters")
            target = params.get("order_id") if isinstance(params, dict) else None
            if arguments.get("kind") == "default_address":
                target = state.verified_customer_id
            arguments["request_id"] = submission_reference(state, arguments.get("kind"), target)
            call = call.model_copy(update={"arguments": arguments})
        calls.append(call)
    return reply.model_copy(update={"tool_calls": calls})


def submission_reference(state, kind, target):
    return json.dumps([state.user_turn, kind, target], separators=(",", ":"))


def pending_reviews(state):
    if not state.review_to_display:
        return []
    return [proposal for identifier, proposal in state.proposals.items()
            if proposal["state"] == "prepared" and identifier not in state.reviewed]


def receive(state, message):
    """Associate every host result with the complete pending assistant batch."""
    message = decode(message)
    if isinstance(message, UserMessage):
        if state.pending_call_ids or message.tool_calls or not message.content or message.is_audio:
            raise ProtocolError("Unexpected user message or unfinished tool batch")
        state.request_tool_rounds = 0
        state.user_turn += 1
    elif isinstance(message, (ToolMessage, MultiToolMessage)):
        results = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        identifiers = [result.id for result in results]
        if (not state.pending_call_ids or len(set(identifiers)) != len(identifiers) or
                set(identifiers) != set(state.pending_call_ids) or
                any(result.requestor != "assistant" for result in results)):
            raise ProtocolError("Tool result batch does not match pending calls")
        state.pending_call_ids = []
        calls = {call["id"]: call for call in state.pending_calls}
        for result in results:
            call = calls.get(result.id)
            if result.error or not call:
                if call and call["name"] in EXECUTE_ACTIONS:
                    # An execution error supplies no successful receipt. Never
                    # let the old display record authorize another attempt.
                    state.reviewed.pop(call["arguments"].get("proposal_id"), None)
                if call and call["name"] == "get_customer_summary":
                    state.customer_profile_loaded = False
                    state.customer_email = None
                    state.customer_evidence_id = None
                continue
            try:
                body = json.loads(result.content)
            except (ValueError, TypeError):
                continue
            if not isinstance(body, dict):
                continue
            state.evidence_call_ids.append(result.id)
            if call["name"] in {"verify_customer", "lookup_customer"}:
                identifier = body.get("customer_id")
                if isinstance(identifier, str) and identifier.strip():
                    state.verified_customer_id = identifier
                    state.customer_profile_loaded = False
                    state.customer_email = None
                    state.customer_evidence_id = None
                    if isinstance(body.get("email"), str):
                        state.customer_profile_loaded = True
                        state.customer_email = body["email"]
                        state.customer_evidence_id = result.id
            elif call["name"] == "get_customer_summary":
                if body.get("customer_id") == state.verified_customer_id and isinstance(body.get("email"), str):
                    state.customer_profile_loaded = True
                    state.customer_email = body["email"]
                    state.customer_evidence_id = result.id
            elif call["name"] in {"prepare_change", "prepare_plan_step"} and body.get("state") == "prepared":
                identifier = body.get("proposal_id")
                if isinstance(identifier, str) and isinstance(body.get("facts"), dict):
                    if state.proposals.get(identifier) == body:
                        # An unchanged quote read does not reset the actual
                        # earlier response or force another customer approval.
                        if identifier not in state.reviewed:
                            state.review_to_display = body
                        continue
                    target = body["facts"].get("order_id")
                    for previous_id, previous in state.proposals.items():
                        if previous["facts"].get("order_id") == target and previous.get("state") == "prepared":
                            state.reviewed.pop(previous_id, None)
                            previous["state"] = "superseded"
                    state.proposals[identifier] = body
                    # The successful preparation already contains the complete
                    # verified proposal. No second tool read is required to show it.
                    state.review_to_display = body
            elif call["name"] == "review_change":
                proposal = body.get("review")
                identifier = call["arguments"].get("proposal_id")
                if proposal == state.proposals.get(identifier) and proposal and proposal.get("state") == "prepared":
                    if identifier not in state.reviewed:
                        state.review_to_display = proposal
                else:
                    state.reviewed.pop(identifier, None)
                    if state.review_to_display and state.review_to_display["proposal_id"] == identifier:
                        state.review_to_display = None
            elif call["name"] == "invalidate_change":
                identifier = call["arguments"].get("proposal_id")
                state.reviewed.pop(identifier, None)
                if identifier in state.proposals:
                    state.proposals[identifier]["state"] = body.get("state", "withdrawn")
                if state.review_to_display and state.review_to_display["proposal_id"] == identifier:
                    state.review_to_display = None
            elif call["name"] in EXECUTE_ACTIONS:
                identifier = call["arguments"].get("proposal_id")
                state.reviewed.pop(identifier, None)
                if identifier in state.proposals:
                    state.proposals[identifier]["state"] = body.get("state", "unknown")
                    facts = state.proposals[identifier]["facts"]
                    target = facts["order_id"] if facts["kind"] != "default_address" else state.verified_customer_id
                    state.submissions[submission_reference(state, facts["kind"], target)] = {
                        "kind": facts["kind"], "order_id": facts["order_id"],
                        "state": body.get("state", "unknown"), "proposal_id": identifier}
                if isinstance(body.get("customer_facts"), dict):
                    state.receipt_facts_to_display.append(body["customer_facts"])
            elif call["name"] == DIRECT_ACTION:
                identifier = call["arguments"]["request_id"]
                state.submissions[identifier] = {"kind": call["arguments"]["kind"],
                    "order_id": call["arguments"]["parameters"].get("order_id"),
                    "state": body.get("state", "unknown"), "proposal_id": body.get("proposal_id")}
                if isinstance(body.get("customer_facts"), dict):
                    state.receipt_facts_to_display.append(body["customer_facts"])
                # A direct submission supersedes the unattempted draft of the
                # same target in the toolkit; do not subsequently display it.
                for proposal_id, proposal in state.proposals.items():
                    if proposal["facts"]["order_id"] == state.submissions[identifier]["order_id"] and proposal["state"] == "prepared":
                        proposal["state"] = "superseded"
                        state.reviewed.pop(proposal_id, None)
                if state.review_to_display and state.review_to_display["facts"]["order_id"] == state.submissions[identifier]["order_id"]:
                    state.review_to_display = None
            elif call["name"] == "inspect_change_result":
                identifier = call["arguments"].get("proposal_id")
                outcome = body.get("write_outcome")
                if isinstance(outcome, dict) and body.get("proposal_id") == identifier:
                    previous = state.proposals.get(identifier, {}).get("state")
                    if identifier in state.proposals:
                        state.proposals[identifier]["state"] = body.get("state", "unknown")
                    for submission in state.submissions.values():
                        if submission.get("proposal_id") == identifier:
                            previous = submission["state"]
                            submission["state"] = body.get("state", "unknown")
                    if (previous in {"pending", "unknown"} and
                            isinstance(body.get("customer_facts"), dict)):
                        # Replace an earlier pending/uncertain facts block with
                        # the actual inspection outcome before replying.
                        facts = body["customer_facts"]
                        state.receipt_facts_to_display = [row for row in state.receipt_facts_to_display
                            if (row.get("kind"), row.get("order_id")) != (facts.get("kind"), facts.get("order_id"))]
                        state.receipt_facts_to_display.append(facts)
            elif call["name"] in {"abandon_plan", "transfer_to_human"}:
                if call["name"] == "abandon_plan":
                    ids = {step.get("proposal_id") for step in body.get("steps", []) if step.get("state") == "paused"}
                else:
                    ids = set(state.proposals)
                for identifier in ids:
                    state.reviewed.pop(identifier, None)
                    if identifier in state.proposals and state.proposals[identifier]["state"] == "prepared":
                        state.proposals[identifier]["state"] = "paused"
                if state.review_to_display and state.review_to_display["proposal_id"] in ids:
                    state.review_to_display = None
                if call["name"] == "transfer_to_human":
                    state.handoff = body.get("state")
        state.pending_calls = []
    else:
        raise ProtocolError("Host input must be a user or tool result")
    store(state, message)


def record_reply(state, reply, actions, *, live=False):
    if not isinstance(reply, AssistantMessage) or reply.is_audio:
        raise ProtocolError("Expected a text assistant or assistant tool batch")
    has_text = bool(reply.content and reply.content.strip())
    calls = reply.tool_calls or []
    if live and len(calls) > 1:
        raise ProtocolError("Retail policy requires at most one tool call per assistant message")
    if not (has_text or calls) or state.pending_call_ids:
        raise ProtocolError("Empty assistant message or unfinished tool batch")
    identifiers = [call.id for call in calls]
    if (any(not identifier.strip() for identifier in identifiers) or
            len(identifiers) != len(set(identifiers)) or
            set(identifiers).intersection(state.seen_call_ids)):
        raise ProtocolError("Missing, duplicate or reused tool call IDs")
    for call in calls:
        if call.requestor != "assistant" or call.name not in actions:
            raise ProtocolError("Unavailable tool or wrong requestor")
        tool = actions[call.name]
        properties = tool.params.model_json_schema().get("properties", {})
        if set(call.arguments) - set(properties):
            raise ProtocolError("Unexpected tool argument")
        try:
            tool.params.model_validate(call.arguments, strict=True)
        except ValueError as error:
            raise ProtocolError("Arguments do not match tool schema") from error
    if len(calls) > 1 and any(call.name in {"verify_customer", "lookup_customer"} for call in calls):
        # Verification changes toolkit identity; dependent reads wait for its result.
        raise ProtocolError("Verification must use its own tool batch")
    exclusive = {"prepare_change", "review_change", "invalidate_change", "plan_changes", "prepare_plan_step", "abandon_plan", "inspect_change_result", "transfer_to_human", DIRECT_ACTION, *EXECUTE_ACTIONS}
    if len(calls) > 1 and any(call.name in exclusive for call in calls):
        raise ProtocolError("State-changing or dependent actions require a single-call batch")
    for call in calls:
        if state.handoff in {"accepted", "unknown"}:
            raise ProtocolError("Terminal handoff forbids further tool calls")
        if call.name == DIRECT_ACTION and not has_prior_recap_turn(state):
            raise ProtocolError("A submitted change needs an actual earlier assistant recap and later customer input")
        if call.name == DIRECT_ACTION:
            previous = state.submissions.get(call.arguments.get("request_id"), {})
            if previous.get("state") in {"accepted", "completed", "attempted", "pending", "unknown"}:
                raise ProtocolError("This operation already has an actual write outcome for this customer turn. Report its receipt; do not submit it again through another tool")
        if call.name in EXECUTE_ACTIONS:
            identifier = call.arguments.get("proposal_id")
            proposal = state.proposals.get(identifier)
            if (identifier not in state.reviewed or state.user_turn <= state.reviewed[identifier] or
                    not proposal or proposal.get("state") != "prepared" or
                    proposal["facts"].get("kind") != EXECUTE_ACTIONS[call.name]):
                raise ProtocolError("Execution requires the exact displayed proposal and later user input")
    if calls:
        # Historical provider messages can contain narration alongside calls.
        # Keep that evidence; the live host-facing path removes narration before
        # recording its reply, as required by the half-duplex orchestrator.
        if not has_text:
            reply.content = None
        state.pending_call_ids = identifiers
        state.pending_calls = [call.model_dump(mode="json") for call in calls]
        state.seen_call_ids.extend(identifiers)
        state.tool_rounds += 1
        state.request_tool_rounds += 1
    if state.review_to_display and has_text and not calls:
        proposals = pending_reviews(state)
        # Chronology only: a genuine customer-facing response followed these
        # quote results. The LLM must explain all relevant facts and judge later
        # consent; matching a fixed footer cannot prove either meaning. Legacy
        # recaps remain valid text during replay, without requiring that format.
        for proposal in proposals:
            state.reviewed[proposal["proposal_id"]] = state.user_turn
        state.review_to_display = None
    if state.handoff == "accepted" and reply.content != TRANSFER_NOTICE:
        raise ProtocolError("Accepted handoff requires exact notice")
    if has_text and not calls and state.receipt_facts_to_display and state.handoff not in {"accepted", "unknown"}:
        # Actual receipts stay in history. Their explanation is model-owned;
        # a mandatory JSON footer cannot prove a truthful or complete reply.
        # Keep the fallback facts until a genuine text response, so a provider
        # failure before that response can still report the actual write.
        state.receipt_facts_to_display = []
    store(state, reply)


def normalize_call_ids(state, reply):
    """Keep valid provider IDs; namespace IDs reused after a completed call.

    Providers can restart their ID sequence on a later completion. Host and
    model history must see the same canonical ID. Never change a pending ID,
    infer intent, or mutate the provider's message object.
    """
    if not isinstance(reply, AssistantMessage) or not reply.tool_calls:
        return reply
    ids = [call.id for call in reply.tool_calls]
    # Malformed IDs within this response remain protocol errors.
    if any(not identifier.strip() for identifier in ids) or len(ids) != len(set(ids)):
        return reply
    taken = set(state.seen_call_ids) | set(ids)
    calls = []
    for index, call in enumerate(reply.tool_calls):
        if call.id in state.seen_call_ids:
            identifier = f"agent-call-{state.tool_rounds + 1}-{index}"
            while identifier in taken:
                identifier += "-next"
            taken.add(identifier)
            call = call.model_copy(update={"id": identifier})
        calls.append(call)
    return reply.model_copy(update={"tool_calls": calls})


def receipt_text(facts):
    return "\nRecorded operation facts:\n" + json.dumps(facts, ensure_ascii=False, indent=2)


def restore_history(state, history, actions):
    """Rebuild pending IDs from evidence, including individual tool results."""
    partial_results = []
    for raw in history:
        message = decode(raw)
        if isinstance(message, SystemMessage):
            if any(row["role"] != "system" for row in state.history) or not message.content:
                raise ProtocolError("System history must precede conversation")
            store(state, message)
        elif isinstance(message, AssistantMessage):
            if partial_results:
                raise ProtocolError("Incomplete historical tool results")
            record_reply(state, message, actions)
            state.model_calls += 1  # Conservative count of historical decisions.
        elif isinstance(message, ToolMessage):
            partial_results.append(message)
            if len(partial_results) == len(state.pending_call_ids):
                receive(state, MultiToolMessage(role="tool", tool_messages=partial_results))
                partial_results = []
        else:
            if partial_results:
                raise ProtocolError("Incomplete historical tool results")
            receive(state, message)
    if partial_results:
        raise ProtocolError("Incomplete historical tool results")
