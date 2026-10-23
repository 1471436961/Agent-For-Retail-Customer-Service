"""Host-executed tools with one bounded correction of an invalid model reply."""

from support_agent.gateway import GatewayAdapter
from support_agent.prompts import IDENTITY_PROMPT, SYSTEM_PROMPT
from support_agent.state import (
    ProtocolError, SessionState, initial_state, receive, record_reply, restore_history, pending_reviews, review_bundle_text, receipt_text, store, normalize_call_ids,
)
from tau2.data_model.message import AssistantMessage, SystemMessage
from support_agent.adapters.conversation_api import TRANSFER_NOTICE
from support_agent.domain.gateway_failure import diagnose_gateway_failure
from support_agent.domain.reply import REPLY_INSTRUCTIONS, render_model_reply
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
        if current.model_calls >= self.gateway.limits.max_model_calls:
            return self._stop(current, "model_call_limit")
        try:
            prompt = SYSTEM_PROMPT if current.verified_customer_id else IDENTITY_PROMPT
            if current.proposals:
                progress = [{"proposal_id": identifier, "kind": p["facts"]["kind"],
                             "order_id": p["facts"]["order_id"], "state": p["state"],
                             "displayed_at_user_turn": current.reviewed.get(identifier)}
                            for identifier, p in current.proposals.items()]
                prompt += "\nTrusted conversation progress (not customer consent): " + json.dumps(
                    {"user_turn": current.user_turn, "changes": progress})
            if current.review_to_display:
                prompt += ("\nA complete verified proposal is ready to display. Answer the customer's "
                           "outstanding questions using the policy and actual tool results, and explain the "
                           "proposal now. Your text will be followed by its exact factual recap and final "
                           "confirmation question, so do not duplicate that recap or question. "
                           "Do not call review_change merely to fetch the same "
                           "proposal again. Do not execute before it has been displayed and the customer "
                           "subsequently clearly approves it. If facts need correction, invalidate or "
                           "prepare the corrected proposal instead. Never say preparation submitted it.")
            actions = self.gateway.actions_for(current)
            messages = [SystemMessage(role="system", content=prompt + REPLY_INSTRUCTIONS, timestamp=None),
                        *self.gateway.project_history(current.history)]
        except Exception as error:
            current.gateway_diagnostic = diagnose_gateway_failure(error, stage="history_projection")
            return self._stop(current, "gateway_failure")
        for attempt in range(2):
            current.model_calls += 1  # Count attempts, including corrections/failures.
            current.gateway_request = None
            try:
                current.gateway_request = self.gateway.request_summary(messages, actions, current.model_calls)
                reply = self.gateway.generate(messages, actions=actions)
            except Exception as error:
                # Provider failures are not retried by the Agent.
                current.gateway_diagnostic = diagnose_gateway_failure(error)
                return self._stop(current, "gateway_failure")
            try:
                reply = normalize_call_ids(current, reply)
                if isinstance(reply, AssistantMessage) and reply.tool_calls and reply.content:
                    reply = reply.model_copy(update={"content": None})
                if (isinstance(reply, AssistantMessage) and reply.tool_calls and
                        current.request_tool_rounds >= self.gateway.limits.max_tool_rounds):
                    return self._stop(current, "tool_round_limit")
                if isinstance(reply, AssistantMessage) and not reply.tool_calls and reply.content:
                    try:
                        text = render_model_reply(reply.content)
                    except ValueError as error:
                        raise ProtocolError(str(error)) from None
                    if current.review_to_display:
                        recap = review_bundle_text(pending_reviews(current))
                        if not text.endswith(recap):
                            text += "\n\n" + recap
                    if current.receipt_facts_to_display:
                        text += receipt_text(current.receipt_facts_to_display)
                    reply = reply.model_copy(update={"content": text})
                candidate = current.model_copy(deep=True)
                record_reply(candidate, reply, {action.name: action for action in actions}, live=True)
            except ProtocolError as error:
                # These errors are fixed local validation descriptions, never
                # raw provider output or private record contents.
                current.protocol_diagnostic = str(error)
                if attempt == 0 and current.model_calls < self.gateway.limits.max_model_calls:
                    # A local validation rejection executed nothing. Correct it
                    # using genuine history + trusted feedback, never fake tool
                    # results or a fabricated customer approval. Error text here
                    # consists only of our fixed ProtocolError descriptions.
                    messages = [*messages, SystemMessage(role="system", timestamp=None,
                        content="Your proposed reply was rejected before any tool was executed: " + str(error) +
                        ". Return a corrected reply using the supplied schemas and actual history. "
                        "Retail policy permits exactly one tool call at a time, followed by its actual result. "
                        "A write still requires the exact displayed proposal and later clear customer approval. "
                        "If approval or information is missing, ask the customer. No rejected call has taken effect.")]
                    continue
                return self._stop(current, "invalid_model_reply")
            candidate.protocol_diagnostic = None
            return reply, candidate.model_dump(mode="json")

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
        if reason == "invalid_model_reply" and state.protocol_diagnostic:
            diagnostic = " Validation: " + state.protocol_diagnostic
        if reason == "gateway_failure" and state.gateway_diagnostic:
            diagnostic = " Service diagnostic: " + json.dumps(
                state.gateway_diagnostic.model_dump(exclude_none=True), sort_keys=True,
            )
            if state.gateway_request and state.gateway_diagnostic.stage == "gateway_generate":
                diagnostic += " Model request: " + json.dumps(state.gateway_request, sort_keys=True)
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
