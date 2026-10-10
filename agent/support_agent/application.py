"""One model decision per host turn; the host alone executes requested tools."""

from support_agent.gateway import GatewayAdapter
from support_agent.prompts import SYSTEM_PROMPT
from support_agent.state import (
    ProtocolError, SessionState, initial_state, receive, record_reply, restore_history, review_text, receipt_text, store,
)
from tau2.data_model.message import AssistantMessage, SystemMessage
from support_agent.adapters.conversation_api import TRANSFER_NOTICE
from support_agent.domain.gateway_failure import diagnose_gateway_failure
import json


class RetailAgent:
    """Host-mediated retail Agent; user semantics are delegated to LLM."""

    def __init__(self, context):
        if context.runtime_config.get("domain") != "retail_plus":
            raise ValueError("This Agent requires the bound retail_plus domain")
        self.context = context
        self.resources = context.resources
        self.runtime_config = context.runtime_config
        self.gateway = GatewayAdapter(context)

    def get_init_state(self, message_history=None):
        state = initial_state()
        restore_history(state, message_history or [], self.gateway.actions_by_name)
        return state.model_dump(mode="json")

    def generate_next_message(self, message, state):
        current = SessionState.model_validate(state).model_copy(deep=True)
        current.turn += 1
        if current.handoff in {"accepted", "unknown"}:
            return self._handoff_reply(current)
        if current.halt_reason:
            return self._stop(current, current.halt_reason)
        try:
            receive(current, message)
        except ProtocolError:
            return self._stop(current, "invalid_host_sequence")
        if current.handoff in {"accepted", "unknown"}:
            return self._handoff_reply(current)
        if current.review_to_display:
            content = review_text(current.review_to_display)
            if current.receipt_facts_to_display:
                content += receipt_text(current.receipt_facts_to_display)
            reply = AssistantMessage(role="assistant", content=content, timestamp=None)
            record_reply(current, reply, self.gateway.actions_by_name)
            return reply, current.model_dump(mode="json")
        if current.model_calls >= self.gateway.limits.max_model_calls:
            return self._stop(current, "model_call_limit")
        try:
            messages = [SystemMessage(role="system", content=SYSTEM_PROMPT, timestamp=None),
                        *self.gateway.project_history(current.history)]
        except Exception as error:
            current.gateway_diagnostic = diagnose_gateway_failure(error, stage="history_projection")
            return self._stop(current, "gateway_failure")
        current.model_calls += 1  # Count actual gateway attempts, including failures.
        try:
            reply = self.gateway.generate(messages)
        except Exception as error:
            # Classify retained broker errors; never expose exception text/args.
            current.gateway_diagnostic = diagnose_gateway_failure(error)
            return self._stop(current, "gateway_failure")
        try:
            if isinstance(reply, AssistantMessage) and reply.tool_calls and reply.content:
                # Provider responses may include narration alongside calls. The
                # host receives only the calls; no unexecuted claim is displayed.
                reply = reply.model_copy(update={"content": None})
            if (isinstance(reply, AssistantMessage) and reply.tool_calls and
                    current.request_tool_rounds >= self.gateway.limits.max_tool_rounds):
                return self._stop(current, "tool_round_limit")
            if isinstance(reply, AssistantMessage) and not reply.tool_calls and reply.content and current.receipt_facts_to_display:
                reply = reply.model_copy(update={"content": reply.content + receipt_text(current.receipt_facts_to_display)})
            record_reply(current, reply, self.gateway.actions_by_name)
        except ProtocolError:
            return self._stop(current, "invalid_model_reply")
        return reply, current.model_dump(mode="json")

    @staticmethod
    def _handoff_reply(state):
        text = TRANSFER_NOTICE if state.handoff == "accepted" else "The transfer outcome is unknown. I will stop automated operations and will not retry it."
        reply = AssistantMessage(role="assistant", content=text, timestamp=None)
        record_reply(state, reply, {})
        return reply, state.model_dump(mode="json")

    @staticmethod
    def _stop(state, reason):
        state.halt_reason = reason
        if reason == "gateway_failure":
            if state.gateway_diagnostic and state.gateway_diagnostic.stage == "history_projection":
                explanation = "I could not continue because the conversation history could not be prepared for the model."
            else:
                explanation = "I could not continue because the model request failed."
        else:
            explanation = {
                "model_call_limit": "I reached the Agent's configured model-call limit for this conversation.",
                "tool_round_limit": "I reached the Agent's configured tool-round limit for this request.",
                "invalid_host_sequence": "I could not continue because the received message or tool results did not match the pending conversation step.",
                "invalid_model_reply": "I could not continue because the model returned an invalid message or tool call.",
            }.get(reason, "I could not continue because the Agent encountered an internal error.")
        diagnostic = ""
        if reason == "gateway_failure" and state.gateway_diagnostic:
            diagnostic = " Service diagnostic: " + json.dumps(
                state.gateway_diagnostic.model_dump(exclude_none=True), sort_keys=True,
            )
        facts = receipt_text(state.receipt_facts_to_display) if state.receipt_facts_to_display else ""
        reply = AssistantMessage(
            role="assistant", timestamp=None,
            content=explanation + f" Reason: {reason}." + diagnostic + facts,
        )
        if facts:
            state.receipt_facts_to_display = []
        if not state.pending_call_ids:
            store(state, reply)
        # Preserve pending calls and evidence; do not fabricate tool results.
        return reply, state.model_dump(mode="json")
