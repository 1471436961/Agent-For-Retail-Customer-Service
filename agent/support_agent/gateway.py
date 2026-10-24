"""Adapt the injected tau2 gateway; never leave its model allowlist."""

import json
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from typing import Literal, get_args
import re
from pydantic import Field, create_model

from tau2.data_model.message import MultiToolMessage, ToolMessage
from support_agent.state import decode, has_prior_assistant_turn
from support_agent.adapters.change_tools import ACTION_NAMES, EXECUTE_ACTIONS, DIRECT_ACTION
from support_agent.config import PREFERRED_MODEL
from support_agent.domain.money import ArithmeticOperation
from support_agent.domain.presentation import proposal_view
from support_agent.adapters.change_parameters import ChangeParameters


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
        # Earlier histories still validate against the original action schema.
        # New decisions see a general arithmetic interface, not retail-specific
        # new/old-price pair conventions or legacy operation aliases.
        self.live_actions = tuple(action.model_copy(update={"params": create_model(
            "ArithmeticParameters", __base__=action.params,
            operation=(ArithmeticOperation, Field(..., description=
                "General arithmetic operation: add, subtract, multiply, divide or round. "
                "The model supplies the operands; this tool does not choose a business formula.")))})
            if action.name == "calculate" else action for action in self.live_actions)
        # Old callable operations remain available to historical replay and
        # compatibility tests. New decisions have one business write channel.
        self.compatibility_actions = self.live_actions
        live_names = {"verify_customer", "get_customer_summary", "list_customer_orders",
                      "get_owned_order", "list_products", "read_products",
                      "get_catalog_summary", "calculate", "get_service_policy", "prepare_change",
                      "submit_change", "invalidate_change", "inspect_change_result",
                      "get_service_status", "transfer_to_human"}
        self.live_actions = tuple(action for action in self.live_actions if action.name in live_names)
        descriptions = {
            "verify_customer": "Independently verify by email OR full first/last name and postal code; match a supplied customer ID before profile details. Optionally read the explicitly named owned order. No catalogs are loaded. Use null claimed_customer_id only when none was supplied. Profile failure preserves verification, recover via get_customer_summary.",
            "get_owned_order": "Read one exact owned order ID, checking membership and owner. Preserve #. Returns items, status and payments; no unrelated catalogs. Select product IDs from these records for read_products. Apply the supplied business rules.",
            "prepare_change": "READ ONLY: obtain an optional quote before explaining a proposed change. It changes no business data. No permission to prepare is needed. After an accurate recap and clear approval, use submit_change instead, even if no quote was prepared. parameters describe the complete change; preserve_options belong to each replacement row. Select order_id exactly from the verified records.",
            "submit_change": "WRITE: submit the exact complete change already explained and clearly approved in a later customer reply. This performs one real business API write after fresh checks. Use it immediately on approval; prepare_change would only quote again. No proposal ID or extra confirmation is required. parameters use the actual observed IDs, saved settlement method and ordered item occurrences. A question alone or unmet condition is not consent. Never retry an uncertain write. request_id is filled by the Agent.",
        }
        self.live_actions = tuple(action.model_copy(update={
            "params": create_model("Focused" + action.name, **{
                key: (field.annotation, deepcopy(field)) for key, field in action.params.model_fields.items()
                if key != "include_product_variants"}),
            "short_desc": descriptions[action.name], "long_desc": "",
        }) if action.name in descriptions else action for action in self.live_actions)
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
            # Advertise executors after a genuine customer-facing quote
            # response and later customer input. Chronology is not proof of
            # complete disclosure or consent: the model judges both meanings.
            # Preparation remains
            # available when the customer changes their request.
            ready_kinds = {p["facts"]["kind"] for key, p in state.proposals.items()
                           if p["state"] == "prepared" and key in state.quote_response_turns
                           and state.user_turn > state.quote_response_turns[key]}
            actions = tuple(action for action in self.live_actions
                         if action.name != "verify_customer"
                         if (action.name not in EXECUTE_ACTIONS or EXECUTE_ACTIONS[action.name] in ready_kinds)
                         and (action.name != DIRECT_ACTION or has_prior_assistant_turn(state)))
            return self._ground_order_ids(actions, state.owned_order_ids)
        return tuple(action for action in self.live_actions if action.name in {
            "verify_customer", "transfer_to_human",
        })

    @staticmethod
    def _ground_order_ids(actions, identifiers):
        """Constrain selection to observed IDs, without normalizing or choosing one.

        The toolkit independently checks current ownership at execution. This
        schema catches a model's misspelled parameter before a host tool round.
        """
        if not identifiers:
            return actions
        order_id = Literal[tuple(dict.fromkeys(identifiers))]
        variants = tuple(create_model("Owned" + model.__name__, __base__=model,
            order_id=(order_id, Field(..., description="Exact ID from the verified customer's own order_ids; keep every character.")))
            if "order_id" in model.model_fields else model for model in get_args(ChangeParameters))
        changes = variants[0]
        for variant in variants[1:]:
            changes = changes | variant
        result = []
        for action in actions:
            fields = {}
            if action.name == "get_owned_order":
                fields["order_id"] = (order_id, Field(...))
            elif action.name in {"prepare_change", "submit_change"}:
                fields["parameters"] = (changes, Field(...))
            result.append(action.model_copy(update={"params": create_model(
                "Grounded" + action.name, __base__=action.params, **fields)}) if fields else action)
        return tuple(result)

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
                    actual = json.loads(result.content)
                    def compact(proposal):
                        view = proposal_view(proposal)
                        view["parameters"] = proposal["parameters"]
                        if "outcome" in proposal:
                            view["outcome"] = proposal["outcome"]
                        return view
                    if isinstance(actual, dict):
                        if {"proposal_id", "facts", "parameters"} <= actual.keys():
                            actual = compact(actual)
                        elif isinstance(actual.get("review"), dict):
                            actual = {**actual, "review": compact(actual["review"])}
                        elif isinstance(actual.get("changes"), list):
                            actual = {**actual, "changes": [compact(p) for p in actual["changes"]]}
                    body = {"ok": True, "evidence_call_id": result.id,
                            "result": actual}
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
