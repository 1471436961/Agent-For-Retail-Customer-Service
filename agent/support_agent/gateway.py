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
from support_agent.adapters.change_tools import ACTION_NAMES, EXECUTE_ACTIONS, DIRECT_ACTION, BATCH_DIRECT_ACTION, DIRECT_WRITE_KINDS
from support_agent.config import PREFERRED_MODEL
from support_agent.domain.money import ArithmeticOperation
from support_agent.domain.presentation import proposal_view
from support_agent.adapters.change_parameters import ChangeParameters, PlanRequest, SubmissionRequest, PARAMETERS_BY_KIND
from support_agent.skills import SkillName, policy_reference
from support_agent.adapters.read_models import Record, NonEmpty
from support_agent.adapters.change_parameters import ReplacementParameters


def _without_schema_titles(schema):
    """Drop display-only labels, preserving fields, defaults and every constraint."""
    if not isinstance(schema, dict):
        return schema
    result = {key: value for key, value in schema.items() if key != "title"}
    for key in ("properties", "$defs", "patternProperties", "dependentSchemas"):
        if isinstance(result.get(key), dict):
            result[key] = {name: _without_schema_titles(value) for name, value in result[key].items()}
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [_without_schema_titles(value) for value in result[key]]
    for key in ("items", "additionalProperties", "contains", "not", "if", "then", "else"):
        if isinstance(result.get(key), dict):
            result[key] = _without_schema_titles(result[key])
    return result


def _compact_parameters(model):
    # Restrict compaction to the advertised schema. Pydantic validation and
    # callable/history parameter models retain exactly the same constraints.
    class CompactParameters(model):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            return _without_schema_titles(super().model_json_schema(*args, **kwargs))
    return CompactParameters


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
        self._grounded_cache = {}
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
        # compatibility tests. New single writes have operation-specific schemas;
        # all paths still share the same business validation and executor.
        self.compatibility_actions = self.live_actions
        live_names = {"verify_customer", "get_customer_summary",
                      "get_owned_order", "list_products", "read_products", "read_context",
                      "get_catalog_summary", "query_product_variants", "calculate", "prepare_change", "load_skills",
                      "submit_changes", "invalidate_change", "inspect_change_result",
                      "transfer_to_human", *DIRECT_WRITE_KINDS} - {"list_products"}
        self.live_actions = tuple(action for action in self.live_actions if action.name in live_names)
        descriptions = {
            "verify_customer": "Verify email OR full name+postal_code and match a claimed ID. Select skill_names. Include a named order_id; use include_order_details only for discovery/multi-order scope. product_names batches already named related catalogs AFTER verification. Do not discover all orders for one named order. Recover profile failure via get_customer_summary.",
            "get_customer_summary": "Read the verified profile, saved methods and owned order IDs.",
            "list_customer_orders": "Read owned order IDs; include_details reads each owned order. No inferred recency.",
            "get_owned_order": "Read exact owned order_id, preserving #. Optional include_product_variants reads related catalogs.",
            "list_products": "Read the actual public product index after verification.",
            "read_products": "Read selected product catalogs with variants, prices and availability in one batch.",
            "read_context": "Batch explicit owned orders and selected products. include_order_details discovers all owned orders. No writes.",
            "get_catalog_summary": "Count product types or variants; exact product_name selects a catalog. Available and total counts differ.",
            "query_product_variants": "Batch exact filters, explicit numeric comparisons and sorting of available variants. No limit for all matches. Invalid criteria are not a stock finding.",
            "calculate": "Evaluate ONE complete parenthesized arithmetic expression (+ - * / and round). Model selects the formula. Optional decimal_places rounds the final result. Reuse known calculations; do not split one formula into repeated calls.",
            "load_skills": "Load named business skills with dependencies, REPLACING active skills. Public read only; no customer API or approval. Retain multiple skills for mixed goals.",
            "prepare_change": "Optional READ-ONLY item quote: modify_items or exchange. Returns row and net differences for the full replacement list. Reuse those when removing rows. Other operations use actual recorded facts/calculator, then their write tool after approval. supersedes replaces drafts.",
            "prepare_changes": "Batch optional read-only quotes for explicit changes; no submission or cross-operation atomicity.",
            "submit_changes": "WRITE approved explicit changes sequentially, each with fresh checks. Separate actual outcomes; stops on failure/uncertainty. Agent binds request_id.",
            "invalidate_change": "Withdraw an unsubmitted draft without replacement; not cancellation of a registered request.",
            "inspect_change_result": "Inspect a pending/unknown write through its real status resource; never repost it.",
            "get_service_status": "Read current local draft/submission outcomes; no new business write.",
            "transfer_to_human": "Transfer on explicit human request, requested unsupported-limit escalation, or a true policy gap. Minimal factual summary; accepted outcome ends automation.",
            "update_default_address": "WRITE the approved verified profile default address only.",
            "update_order_address": "WRITE the approved complete address of this pending owned order only.",
            "cancel_order": "WRITE the approved whole pending order cancellation with its supported reason.",
            "change_order_payment": "WRITE the approved whole pending order payment replacement to one saved method.",
            "modify_order_items": "WRITE the complete approved pending replacement list once; preserve occurrences and per-row unchanged options.",
            "request_return": "WRITE the complete approved delivered return list once with the eligible refund destination.",
            "request_exchange": "WRITE the complete approved delivered replacement list once with the chosen saved settlement method.",
        }
        self.live_actions = tuple(action.model_copy(update={
            "params": create_model("Focused" + action.name,
                __base__=PlanRequest if action.name in {"prepare_change", DIRECT_ACTION} else action.params, **{
                key: (field.annotation, deepcopy(PlanRequest.model_fields["parameters"] if key == "parameters" else field))
                for key, field in action.params.model_fields.items()
            }),
            "short_desc": descriptions[action.name], "long_desc": "",
        }) if action.name in descriptions else action for action in self.live_actions)
        # One current formula interface, with the old operand ABI kept only in
        # callable/history schemas. Item quotes also have one concrete shape.
        focused = []
        for action in self.live_actions:
            params = action.params
            if action.name == "calculate":
                params = create_model("ExpressionArithmetic", __base__=Record,
                    expression=(NonEmpty, Field(...)), decimal_places=(int | None, None))
            elif action.name == "verify_customer":
                params = create_model("SelectedVerification", __base__=params,
                    skill_names=(list[SkillName], Field(..., min_length=1,
                        description="Relevant business procedures, or service-workflow for general profile help.")))
            elif action.name == "prepare_change":
                params = create_model("ItemQuote", __base__=PlanRequest,
                    kind=(Literal["modify_items", "exchange"], Field(...)),
                    parameters=(ReplacementParameters, Field(...)), supersedes=(list[str] | None, None))
            focused.append(action.model_copy(update={"params": _compact_parameters(params)}))
        self.live_actions = tuple(focused)
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
                    "get_customer_summary", "transfer_to_human", "load_skills",
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
                         if (action.name not in EXECUTE_ACTIONS or EXECUTE_ACTIONS[action.name] in ready_kinds))
            # Skill selection controls knowledge, not business capabilities.
            # State validation still rejects writes without real recap chronology;
            # the toolkit independently verifies identity/ownership/current facts.
            key = (tuple(action.name for action in actions), tuple(state.owned_order_ids or []))
            if key not in self._grounded_cache:
                self._grounded_cache[key] = self._ground_order_ids(actions, state.owned_order_ids)
            return self._grounded_cache[key]
        return tuple(action for action in self.live_actions if action.name in {
            "verify_customer", "transfer_to_human", "load_skills",
        })

    @staticmethod
    def _ground_order_ids(actions, identifiers, kinds=None):
        """Constrain selection to observed IDs, without normalizing or choosing one.

        The toolkit independently checks current ownership at execution. This
        schema catches a model's misspelled parameter before a host tool round.
        """
        order_id = Literal[tuple(dict.fromkeys(identifiers))] if identifiers else str
        models = tuple(dict.fromkeys(PARAMETERS_BY_KIND[kind] for kind in kinds)) if kinds else get_args(ChangeParameters)
        variants = tuple(create_model("Owned" + model.__name__, __base__=model,
            order_id=(order_id, Field(..., description="Exact ID from the verified customer's own order_ids; keep every character.")))
            if identifiers and "order_id" in model.model_fields else model for model in models)
        changes = variants[0]
        for variant in variants[1:]:
            changes = changes | variant
        kind_field = {"kind": (Literal[kinds], Field(...))} if kinds else {}
        quote_request = create_model("OwnedPlanRequest", __base__=PlanRequest,
                                     parameters=(changes, Field(...)), **kind_field)
        submission_request = create_model("OwnedSubmissionRequest", __base__=SubmissionRequest,
                                          parameters=(changes, Field(...)), **kind_field)
        result = []
        for action in actions:
            fields = {}
            if action.name == "get_owned_order" or (action.name in DIRECT_WRITE_KINDS and "order_id" in action.params.model_fields):
                fields["order_id"] = (order_id, Field(...))
            elif action.name == "read_context":
                fields["order_ids"] = (list[order_id] | None, Field(default=None))
            elif action.name in {"prepare_change", "submit_change"}:
                selected_kinds = get_args(action.params.model_fields["kind"].annotation)
                if selected_kinds == ("modify_items", "exchange"):
                    fields["parameters"] = (create_model("OwnedItemQuote", __base__=ReplacementParameters,
                        order_id=(order_id, Field(...))), Field(...))
                else:
                    fields["parameters"] = (changes, Field(...))
                    fields.update(kind_field)
            elif action.name == "prepare_changes":
                fields["requests"] = (list[quote_request], Field(...))
            elif action.name == BATCH_DIRECT_ACTION:
                fields["requests"] = (list[submission_request], Field(...))
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
                        for key in ("outcome", "superseded_proposal_ids"):
                            if key in proposal:
                                view[key] = proposal[key]
                        return view
                    if isinstance(actual, dict):
                        if {"proposal_id", "facts", "parameters"} <= actual.keys():
                            actual = compact(actual)
                        elif isinstance(actual.get("review"), dict):
                            actual = {**actual, "review": compact(actual["review"])}
                        elif isinstance(actual.get("changes"), list):
                            actual = {**actual, "changes": [compact(p) for p in actual["changes"]]}
                    actual = GatewayAdapter._policy_references(actual)
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

    @staticmethod
    def _policy_references(value):
        """Project repeated policy text to references; retain raw state evidence.

        Active instructions come once from trusted packaged assets, not from a
        customer record or the tool result's instruction text.
        """
        if isinstance(value, list):
            return [GatewayAdapter._policy_references(row) for row in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, row in value.items():
            if key == "business_skills" and isinstance(row, dict) and "names" in row:
                result[key] = {"names": row["names"], "versions": row["versions"]}
            elif key == "policy" and isinstance(row, dict) and row.get("operation") in PARAMETERS_BY_KIND:
                result[key] = policy_reference(row["operation"])
            elif key == "change_policies" and isinstance(row, dict):
                result[key] = {kind: policy_reference(kind) for kind in row if kind in PARAMETERS_BY_KIND}
            else:
                result[key] = GatewayAdapter._policy_references(row)
        return result
