"""Serializable evidence; no identity inference or business parsing."""

from typing import Literal
import json
from support_agent.adapters.change_tools import EXECUTE_ACTIONS
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
    gateway_diagnostic: GatewayDiagnostic | None = None
    pending_calls: list[dict] = Field(default_factory=list)
    proposals: dict[str, dict] = Field(default_factory=dict)
    reviewed: dict[str, int] = Field(default_factory=dict)
    review_to_display: dict | None = None
    user_turn: int = 0
    handoff: str | None = None
    receipt_facts_to_display: list[dict] = Field(default_factory=list)


def initial_state() -> SessionState:
    return SessionState()


def decode(message):
    try:
        return MESSAGE_ADAPTER.validate_python(message).model_copy(deep=True)
    except ValueError as error:
        raise ProtocolError("Invalid message shape") from error


def store(state, message):
    state.history.append(message.model_dump(mode="json", exclude_none=True))


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
                continue
            try:
                body = json.loads(result.content)
            except (ValueError, TypeError):
                continue
            if not isinstance(body, dict):
                continue
            if call["name"] in {"prepare_change", "prepare_plan_step"} and body.get("state") == "prepared":
                identifier = body.get("proposal_id")
                if isinstance(identifier, str) and isinstance(body.get("facts"), dict):
                    target = body["facts"].get("order_id")
                    for previous_id, previous in state.proposals.items():
                        if previous["facts"].get("order_id") == target:
                            state.reviewed.pop(previous_id, None)
                    state.proposals[identifier] = body
            elif call["name"] == "review_change":
                proposal = body.get("review")
                identifier = call["arguments"].get("proposal_id")
                if proposal == state.proposals.get(identifier) and proposal and proposal.get("state") == "prepared":
                    state.review_to_display = proposal
            elif call["name"] == "invalidate_change":
                state.reviewed.pop(call["arguments"].get("proposal_id"), None)
            elif call["name"] in EXECUTE_ACTIONS:
                state.reviewed.pop(call["arguments"].get("proposal_id"), None)
                if isinstance(body.get("customer_facts"), dict):
                    state.receipt_facts_to_display.append(body["customer_facts"])
            elif call["name"] in {"abandon_plan", "transfer_to_human"}:
                state.reviewed = {}
                if call["name"] == "transfer_to_human":
                    state.handoff = body.get("state")
        state.pending_calls = []
    else:
        raise ProtocolError("Host input must be a user or tool result")
    store(state, message)


def record_reply(state, reply, actions):
    if not isinstance(reply, AssistantMessage) or reply.is_audio:
        raise ProtocolError("Expected a text assistant or assistant tool batch")
    has_text = bool(reply.content and reply.content.strip())
    calls = reply.tool_calls or []
    if has_text == bool(calls) or state.pending_call_ids:
        raise ProtocolError("Text and tool calls must be exclusive")
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
    exclusive = {"prepare_change", "review_change", "invalidate_change", "plan_changes", "prepare_plan_step", "abandon_plan", "inspect_change_result", "transfer_to_human", *EXECUTE_ACTIONS}
    if len(calls) > 1 and any(call.name in exclusive for call in calls):
        raise ProtocolError("State-changing or dependent actions require a single-call batch")
    for call in calls:
        if state.handoff in {"accepted", "unknown"}:
            raise ProtocolError("Terminal handoff forbids further tool calls")
        if call.name in EXECUTE_ACTIONS:
            identifier = call.arguments.get("proposal_id")
            proposal = state.proposals.get(identifier)
            if (identifier not in state.reviewed or state.user_turn <= state.reviewed[identifier] or
                    not proposal or proposal["facts"].get("kind") != EXECUTE_ACTIONS[call.name]):
                raise ProtocolError("Execution requires the exact displayed proposal and later user input")
    if calls:
        reply.content = None
        state.pending_call_ids = identifiers
        state.pending_calls = [call.model_dump(mode="json") for call in calls]
        state.seen_call_ids.extend(identifiers)
        state.tool_rounds += 1
        state.request_tool_rounds += 1
    if state.review_to_display and has_text:
        identifier = state.review_to_display["proposal_id"]
        expected = review_text(state.review_to_display)
        if reply.content != expected + (receipt_text(state.receipt_facts_to_display) if state.receipt_facts_to_display else ""):
            raise ProtocolError("Review facts must actually be displayed")
        state.reviewed = {identifier: state.user_turn}
        state.review_to_display = None
    if state.handoff == "accepted" and reply.content != TRANSFER_NOTICE:
        raise ProtocolError("Accepted handoff requires exact notice")
    if has_text and state.receipt_facts_to_display and state.handoff not in {"accepted", "unknown"}:
        if not reply.content.endswith(receipt_text(state.receipt_facts_to_display)):
            raise ProtocolError("Actual outcome facts must be displayed")
        state.receipt_facts_to_display = []
    store(state, reply)


def review_text(proposal):
    facts = proposal["facts"]
    recap = {key: value for key, value in facts.items() if key != "snapshot"}
    if facts["kind"] in {"modify_items", "return", "exchange"}:
        recap["original_items"] = facts["snapshot"]["order"]["items"]
        recap["replacement_details"] = facts["snapshot"]["catalog"]
    ending = "Please confirm this exact change."
    if facts["kind"] in {"modify_items", "return", "exchange"}:
        ending += " Is this the complete list, or do you need any other item changes before this one-time request?"
    return "Prepared change " + proposal["proposal_id"] + ":\n" + json.dumps(recap, ensure_ascii=False, indent=2) + "\n" + ending


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
