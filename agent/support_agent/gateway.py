"""Adapt the injected tau2 gateway; never leave its model allowlist."""

import json
from dataclasses import dataclass

from tau2.data_model.message import MultiToolMessage, ToolMessage
from support_agent.state import decode
from support_agent.adapters.customer_tools import READ_ACTION_NAMES


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
        self.model = self.gateway.available_models[0]
        self.actions = context.action_interface.select(READ_ACTION_NAMES)
        self.actions_by_name = {action.name: action for action in self.actions}
        if any(action.info.get("tool_type") != "read" for action in self.actions):
            raise ValueError("This stage only supports read actions")
        configs = [config for config in self.gateway.models if config.model == self.model]
        if len(configs) != 1:
            raise ValueError("Model configuration is ambiguous; explicit configuration required")
        self.options = {}
        for name, value in configs[0].constrained_args.items():
            if isinstance(value, dict) and set(value) == {"one_of"}:
                if not value["one_of"]:
                    raise ValueError("Empty model option choice")
                self.options[name] = value["one_of"][0]
        # Pinned values returned here are deliberately NOT sent by the Agent.
        self.gateway.validate_request(self.model, self.options)

    def generate(self, messages):
        return self.gateway.generate(
            model=self.model, messages=messages, actions=self.actions,
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
                message = message.model_copy(update={
                    "tool_messages": [project_result(result) for result in message.tool_messages],
                })
            elif isinstance(message, ToolMessage):
                message = project_result(message)
            messages.append(message)
        return messages
