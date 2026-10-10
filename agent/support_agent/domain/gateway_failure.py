"""Classify SDK/broker failures without persisting arbitrary exception text.

These patterns describe infrastructure errors, never customer language. Only
fixed labels, known exception types and integer HTTP statuses leave this module.
"""

import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field


ErrorType = Literal[
    "ValueError", "TypeError", "RuntimeError", "KeyError", "AttributeError",
    "AuthenticationError", "PermissionDeniedError", "RateLimitError",
    "APITimeoutError", "APIConnectionError", "BadRequestError",
    "InternalServerError", "EmptyCompletionError", "MissingProviderKeyError",
    "ModelRoutingError", "NotFoundError", "UnsupportedParamsError",
    "ValidationError", "JSONDecodeError", "OtherError",
]


ProviderErrorCode = Literal[
    "insufficient_quota", "rate_limit_exceeded", "rate_limit_error",
    "tokens", "requests", "resource_exhausted", "RESOURCE_EXHAUSTED",
    "model_rate_limit", "too_many_requests", "overloaded_error",
]

# A vocabulary of infrastructure concepts, not a redacted copy of a response.
# No arbitrary words, identifiers, numbers, URLs or credentials can survive.
ProviderTerm = Literal[
    "quota", "credits", "balance", "billing", "budget", "exhausted",
    "rate_limit", "tokens", "requests", "concurrency", "capacity",
    "overloaded", "unavailable", "daily", "retry", "model", "provider",
    "invalid", "unsupported", "disabled", "authentication",
]


class GatewayDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal["history_projection", "gateway_generate"] = "gateway_generate"
    exception_type: ErrorType
    http_status: int | None = Field(default=None, ge=100, le=599)
    reported_exception_type: ErrorType | None = None
    provider_error_code: ProviderErrorCode | None = None
    provider_message_terms: list[ProviderTerm] = Field(default_factory=list)
    rate_limit_kind: Literal[
        "account_quota", "token_rate", "request_rate", "concurrency", "upstream_capacity",
    ] | None = None
    origin: Literal[
        "model_broker", "gateway_constraints", "gateway_accounting",
        "model_routing", "message_conversion", "sdk_generation",
    ] | None = None
    failure_code: Literal[
        "broker_response_mismatch", "broker_unspecified_failure",
        "unsupported_request_arguments", "model_gateway_uninitialized",
        "model_constraint_violation", "model_configuration_ambiguous",
        "model_not_allowed", "model_configuration_unmatched", "model_option_choice_required",
        "missing_provider_key", "missing_token_usage", "empty_completion",
        "provider_authentication", "provider_permission", "provider_rate_limit",
        "provider_timeout", "provider_connection", "provider_bad_request",
        "provider_internal_error", "provider_model_not_found",
        "provider_unsupported_parameters", "model_routing_error",
        "unclassified_broker_error",
    ] | None = None


_ORIGINS = {
    ("tau2.hyper.sandbox.candidate_server", "_brokered_generate"): "model_broker",
    ("tau2.hyper.agent_context", "_config_for"): "gateway_constraints",
    ("tau2.hyper.agent_context", "_resolve_args"): "gateway_constraints",
    ("tau2.hyper.agent_context", "record"): "gateway_accounting",
    ("tau2.utils.model_routing", "resolve_model"): "model_routing",
    ("tau2.utils.model_routing", "require_api_key"): "model_routing",
    ("tau2.utils.llm_utils", "validate_message_history"): "message_conversion",
    ("tau2.utils.llm_utils", "to_litellm_messages"): "message_conversion",
    ("tau2.utils.llm_utils", "generate"): "sdk_generation",
}
_TYPE_CODES = {
    "MissingProviderKeyError": "missing_provider_key",
    "ModelRoutingError": "model_routing_error",
    "EmptyCompletionError": "empty_completion",
    "AuthenticationError": "provider_authentication",
    "PermissionDeniedError": "provider_permission",
    "RateLimitError": "provider_rate_limit",
    "APITimeoutError": "provider_timeout",
    "APIConnectionError": "provider_connection",
    "BadRequestError": "provider_bad_request",
    "InternalServerError": "provider_internal_error",
    "NotFoundError": "provider_model_not_found",
    "UnsupportedParamsError": "provider_unsupported_parameters",
}


def _origin(error):
    origin = None
    frame = error.__traceback__
    while frame is not None:
        key = (frame.tb_frame.f_globals.get("__name__"), frame.tb_frame.f_code.co_name)
        origin = _ORIGINS.get(key, origin)
        frame = frame.tb_next
    return origin


def _message_code(text):
    # Match actual SDK wording. Variable model names, parameter names, provider
    # names and environment-variable names are deliberately never returned.
    if text == "Model broker returned a mismatched response":
        return "broker_response_mismatch"
    if text == "Model broker call failed":
        return "broker_unspecified_failure"
    if text == "Candidate model gateway is not initialized":
        return "model_gateway_uninitialized"
    if text.startswith("Candidate model request contains unsupported arguments: "):
        return "unsupported_request_arguments"
    if text.startswith("Model call violates configured constraints: "):
        return "model_constraint_violation"
    if text.startswith("Model call is ambiguous across "):
        return "model_configuration_ambiguous"
    if text.startswith("Model call does not match any allowed configuration for "):
        return "model_configuration_unmatched"
    if text.startswith("Model ") and " is not allowed; choose one of " in text:
        return "model_not_allowed"
    if text.startswith("Model ") and text.endswith("call must choose one explicitly"):
        return "model_option_choice_required"
    if re.fullmatch(r"\S+ is not set, but [^\n]+ is routed to the [^\n]+ provider\. Export it in \.env, or point [^\n]+ at a provider whose key you have\.", text):
        return "missing_provider_key"
    if text.startswith("Model ") and text.endswith("returned no token usage; credit accounting cannot continue"):
        return "missing_token_usage"
    return None


def diagnose_gateway_failure(error, *, stage="gateway_generate"):
    allowed = ErrorType.__args__
    name = type(error).__name__
    try:
        status = getattr(error, "status_code", None)
    except Exception:
        status = None
    status = status if type(status) is int and 100 <= status <= 599 else None
    origin = _origin(error)
    # Use a bounded string argument, avoiding arbitrary __str__ implementations.
    text = error.args[0][:8192] if error.args and type(error.args[0]) is str else ""
    reported = None
    if origin == "model_broker":
        # LiteLLM/OpenAI include their type in the broker's retained message;
        # this is a reported type, not a recovered original exception object.
        match = re.match(r"(?:litellm\.|openai\.)?([A-Za-z]+Error):", text)
        if match and match[1] in allowed and match[1] != "OtherError":
            reported = match[1]
            if status is None:
                match_status = re.search(r"\bError code: ([1-5][0-9]{2})\b", text)
                if match_status:
                    status = int(match_status[1])
    provider_code = None
    terms = []
    rate_kind = None
    if status == 429 and (reported == "RateLimitError" or name == "RateLimitError"):
        # Inspect only a recognized provider error. Retain fixed labels rather
        # than arbitrary message/body text, which can contain account IDs/keys.
        known_codes = get_args(ProviderErrorCode)
        for match in re.finditer(r"[\"'](?:code|type)[\"']\s*:\s*[\"']([A-Za-z_]+)[\"']", text):
            if match[1] in known_codes:
                provider_code = match[1]
                break
        lower = text.lower()
        words = set(re.findall(r"[a-z]+", lower))
        forms = {
            "quota": ("quota", "额度", "配额"), "credits": ("credit", "credits"),
            "balance": ("balance", "余额"), "billing": ("billing",), "budget": ("budget",),
            "exhausted": ("exhausted", "用尽", "耗尽"),
            "rate_limit": ("rate", "限流", "频率"), "tokens": ("token", "tokens"),
            "requests": ("request", "requests"), "concurrency": ("concurrency", "concurrent", "并发"),
            "capacity": ("capacity",), "overloaded": ("overloaded", "过载"),
            "unavailable": ("unavailable", "不可用"), "daily": ("daily", "每天", "每日"),
            "retry": ("retry",), "model": ("model", "模型"), "provider": ("provider",),
            "invalid": ("invalid",), "unsupported": ("unsupported",),
            "disabled": ("disabled", "停用"), "authentication": ("authentication",),
        }
        terms = [label for label, variants in forms.items()
                 if any(value in words if value.isascii() else value in lower for value in variants)]
        if provider_code == "insufficient_quota" or "exceeded your current quota" in lower:
            rate_kind = "account_quota"
        elif provider_code == "tokens" or "tokens per min" in lower or "tokens per minute" in lower:
            rate_kind = "token_rate"
        elif provider_code == "requests" or "requests per min" in lower or "requests per minute" in lower:
            rate_kind = "request_rate"
        elif "concurrent requests" in lower or "concurrency limit" in lower:
            rate_kind = "concurrency"
        elif provider_code == "overloaded_error" or "model capacity" in lower or "temporarily overloaded" in lower:
            rate_kind = "upstream_capacity"
    code = _message_code(text) or _TYPE_CODES.get(reported or name)
    if origin == "model_broker" and code is None:
        code = "unclassified_broker_error"
    return GatewayDiagnostic(
        stage=stage,
        exception_type=name if name in allowed else "OtherError",
        http_status=status, reported_exception_type=reported,
        provider_error_code=provider_code, rate_limit_kind=rate_kind,
        provider_message_terms=terms,
        origin=origin, failure_code=code,
    )
