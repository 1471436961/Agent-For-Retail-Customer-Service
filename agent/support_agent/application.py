"""Turn orchestration; business rules and API transport remain separate."""

from support_agent.domain.customer import customer_reply, find_customer_id, find_email
from support_agent.config import DOMAIN
from support_agent.state import initial_state
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall


class CustomerAgent:
    """Deterministic t1 lookup example, not a complete p1/t2 business solution."""

    def get_init_state(self, message_history=None):
        """Start an independent session. This example does not replay history."""
        return initial_state()

    def generate_next_message(self, message, state):
        """Consume a user/tool message and return a reply plus serializable state."""
        next_state = {**state, "turn": state["turn"] + 1}
        if isinstance(message, MultiToolMessage):
            pending = state["pending_call_id"]
            result = (
                next(
                    (item for item in message.tool_messages if item.id == pending),
                    None,
                )
                if pending
                else None
            )
            next_state["pending_call_id"] = None
            text = (
                customer_reply(result.content, result.error)
                if result
                else "No matching customer lookup result was received. Please try again."
            )
            return AssistantMessage(role="assistant", content=text), next_state
        customer_id = find_customer_id(message.content or "") or state["customer_id"]
        email = find_email(message.content or "")
        next_state["customer_id"] = customer_id
        if DOMAIN == "retail_plus" and not email:
            return AssistantMessage(role="assistant", content="Please provide your email to verify your identity before I access your profile. A customer ID alone is not verification."), next_state
        if not customer_id and DOMAIN != "retail_plus":
            return AssistantMessage(
                role="assistant",
                content="This example supports customer lookup. Please provide your customer ID.",
            ), next_state
        call_id = f"lookup-{next_state['turn']}"
        next_state.update(customer_id=customer_id, pending_call_id=call_id)
        return AssistantMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id=call_id,
                    name="lookup_customer",
                    arguments={"customer_id": customer_id or "", "email": email or ""},
                    requestor="assistant",
                )
            ],
        ), next_state
