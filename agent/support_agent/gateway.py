"""Adapt the injected tau2 gateway; never leave its model allowlist."""

import json
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
import re
from pydantic import Field, create_model

from tau2.data_model.message import MultiToolMessage, ToolMessage
from support_agent.state import decode
from support_agent.adapters.change_tools import ACTION_NAMES
from support_agent.config import PREFERRED_MODEL


@dataclass(frozen=True)
class AgentLimits:
    # Engineering defaults, not provider pricing or platform quotas.
    max_model_calls: int = 128
    # Do not add a smaller per-user tool cap that truncates multi-order reads.
    # The session model-call budget already bounds the default decision loop.
    max_tool_rounds: int = 128

    @classmethod
    def from_runtime(cls, runtime):
        config = dict(runtime.get("agent_limits", {}))
        if set(config) - {"max_model_calls", "max_tool_rounds"}:
            raise ValueError("Unknown local Agent limit")
        if any(type(value) is not int or value <= 0 for value in config.values()):
            raise ValueError("Agent limits must be positive integers")
        return cls(**config)


class GatewayAdapter:
    def __init__(self, context):
        self.gateway = context.model_gateway
        self.limits = AgentLimits.from_runtime(context.runtime_config)
        self.actions = context.action_interface.select(ACTION_NAMES)
        self.actions_by_name = {action.name: action for action in self.actions}
        # Keep original schemas for legitimate historical calls. Live inference
        # must explicitly choose a claimed ID or null, never silently omit it.
        # The redundant legacy lookup remains callable/restorable, but is not
        # advertised to the model alongside the canonical verification tool.
        self.live_actions = tuple(action.model_copy(update={"params": create_model(
            "ExplicitCustomerClaim", __base__=action.params,
            claimed_customer_id=(str | None, Field(..., description=
                "The exact account/customer ID explicitly supplied anywhere in the customer conversation. "
                "If the request names a customer to look up, copy that ID here so the independent email/name search "
                "can match it before details are read. It is a consistency claim, not an authentication credential. "
                "Use null ONLY when no customer ID was supplied. Never discard a supplied ID.")))})
            if action.name == "verify_customer" else action for action in self.actions
            if action.name != "lookup_customer")
        if any(action.info.get("tool_type") not in {"read", "write", "generic"} for action in self.actions):
            raise ValueError("Unsupported action type")
        self.model, self.options = self._select_configuration()

    def _select_configuration(self):
        # Preflight only: enumerate teacher-allowed choices, never generate a
        # response or pass pinned values. The gateway determines uniqueness.
        # Prefer the user's selected Kimi only when the host actually permits
        # it. Keep full injected IDs and stable ordering for every other model;
        # a host alias need not expose its backing provider/model name.
        models = sorted(self.gateway.available_models,
                        key=lambda model: model.rsplit("/", 1)[-1] != PREFERRED_MODEL)
        for model in models:
            for config in self.gateway.models:
                if config.model != model:
                    continue
                choices = {name: value["one_of"] for name, value in config.constrained_args.items()
                           if isinstance(value, dict) and set(value) == {"one_of"}}
                for selected in product(*choices.values()):
                    options = deepcopy(dict(zip(choices, selected)))
                    try:
                        self.gateway.validate_request(model, options)
                    except ValueError:
                        continue
                    return model, options
        raise ValueError("Allowed model configurations are ambiguous or have no valid explicit choices")

    def actions_for(self, state):
        # These are capabilities unlocked by genuine tool evidence, never by
        # matching the wording of the customer's request. The toolkit remains
        # the authority for identity, ownership and write authorization.
        if state.verified_customer_id:
            if not state.customer_profile_loaded:
                return tuple(action for action in self.live_actions if action.name in {
                    "get_customer_summary", "transfer_to_human",
                })
            return self.live_actions
        return tuple(action for action in self.live_actions if action.name in {
            "verify_customer", "transfer_to_human",
        })

    def request_summary(self, messages, actions, model_call):
        # Serialize only to measure the candidate-visible wire size. No message,
        # tool result, credential or arbitrary provider text is retained here.
        payload = {"messages": [message.model_dump(mode="json", exclude_none=True)
                                for message in messages],
                   "tools": [action.openai_schema for action in actions]}
        _, resolved = self.gateway.validate_request(self.model, self.options)
        constraints = {name: resolved[name] for name in ("max_tokens", "max_completion_tokens")
                       if type(resolved.get(name)) is int and resolved[name] > 0}
        model = self.model if re.fullmatch(r"[A-Za-z0-9_./:@+-]{1,160}", self.model) else "redacted"
        return {"model": model, "model_call": model_call, "action_count": len(actions),
                "message_count": len(messages), "request_bytes": len(json.dumps(payload).encode()),
                "fixed_token_settings": constraints, "allowed_model_count": len(self.gateway.available_models),
                "selected_option_names": sorted(self.options)}

    def generate(self, messages, actions=None):
        return self.gateway.generate(
            model=self.model, messages=messages, actions=self.actions if actions is None else actions,
            call_name="retail_agent_decision", **self.options,
        )

    @staticmethod
    def project_history(history):
        """Make error flags explicit in model text; retain original state evidence."""
        def project_result(result):
            if result.error:
                body = {"ok": False, "error": result.content}
            else:
                try:
                    body = {"ok": True, "evidence_call_id": result.id,
                            "result": json.loads(result.content)}
                except (ValueError, TypeError):
                    body = {"ok": False, "error": "The tool returned invalid JSON."}
            return result.model_copy(update={"content": json.dumps(body, ensure_ascii=False)})

        messages = []
        for raw in history:
            message = decode(raw)
            if isinstance(message, MultiToolMessage):
                # The host batches results; tau2's provider conversion consumes
                # individual ToolMessages. Retain the raw batch only in state.
                messages.extend(project_result(result) for result in message.tool_messages)
                continue
            elif isinstance(message, ToolMessage):
                message = project_result(message)
            messages.append(message)
        return messages
