"""One model decision per host turn; the host alone executes requested tools."""

from support_agent.gateway import GatewayAdapter
from support_agent.prompts import SYSTEM_PROMPT
from support_agent.state import (
    ProtocolError, SessionState, initial_state, receive, record_reply, restore_history, review_text,
)
from tau2.data_model.message import AssistantMessage, SystemMessage
from support_agent.adapters.conversation_api import TRANSFER_NOTICE


class RetailAgent:
    """P1 read-only protocol skeleton; user semantics are delegated to LLM."""

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
            reply = AssistantMessage(role="assistant", content=review_text(current.review_to_display), timestamp=None)
            record_reply(current, reply, self.gateway.actions_by_name)
            return reply, current.model_dump(mode="json")
        if current.model_calls >= self.gateway.limits.max_model_calls:
            return self._stop(current, "model_call_limit")
        current.model_calls += 1  # Count attempts, including provider failures.
        try:
            reply = self.gateway.generate(
                [SystemMessage(role="system", content=SYSTEM_PROMPT, timestamp=None),
                 *self.gateway.project_history(current.history)]
            )
        except Exception:
            # Provider exception text may contain secrets; do not copy it.
            return self._stop(current, "gateway_failure")
        try:
            if (isinstance(reply, AssistantMessage) and reply.tool_calls and
                    current.request_tool_rounds >= self.gateway.limits.max_tool_rounds):
                return self._stop(current, "tool_round_limit")
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
        reply = AssistantMessage(
            role="assistant", timestamp=None,
            content="I cannot continue this request because of a service or protocol limit. "
                    "I have not confirmed that the requested task is complete.",
        )
        # Preserve pending calls and evidence; do not fabricate tool results.
        return reply, state.model_dump(mode="json")
