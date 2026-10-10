"""Adapt the injected tau2 gateway; never leave its model allowlist."""

import json
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
import re

from tau2.data_model.message import MultiToolMessage, ToolMessage
from support_agent.state import decode
from support_agent.adapters.change_tools import ACTION_NAMES


@dataclass(frozen=True)
class AgentLimits:
    # Engineering defaults, not provider pricing or platform quotas.
    max_model_calls: int = 128
    max_tool_rounds: int = 16

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
        if any(action.info.get("tool_type") not in {"read", "write", "generic"} for action in self.actions):
            raise ValueError("Unsupported action type")
        self.model, self.options = self._select_configuration()

    def _select_configuration(self):
        # Preflight only: enumerate teacher-allowed choices, never generate a
        # response or pass pinned values. The gateway determines uniqueness.
        for model in self.gateway.available_models:
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
            return self.actions
        return tuple(action for action in self.actions if action.name in {
            "verify_customer", "lookup_customer", "transfer_to_human",
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
                "fixed_token_settings": constraints}

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
                    body = {"ok": True, "result": json.loads(result.content)}
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
