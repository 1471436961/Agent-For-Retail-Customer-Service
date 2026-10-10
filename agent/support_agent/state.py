"""Serializable evidence; no identity inference or business parsing."""

from typing import Literal

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
    elif isinstance(message, (ToolMessage, MultiToolMessage)):
        results = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
        identifiers = [result.id for result in results]
        if (not state.pending_call_ids or len(set(identifiers)) != len(identifiers) or
                set(identifiers) != set(state.pending_call_ids) or
                any(result.requestor != "assistant" for result in results)):
            raise ProtocolError("Tool result batch does not match pending calls")
        state.pending_call_ids = []
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
    if calls:
        reply.content = None
        state.pending_call_ids = identifiers
        state.seen_call_ids.extend(identifiers)
        state.tool_rounds += 1
        state.request_tool_rounds += 1
    store(state, reply)


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
