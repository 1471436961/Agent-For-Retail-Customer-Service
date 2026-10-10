"""Persist bounded gateway diagnostics without exception messages or credentials."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GatewayDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stage: Literal["gateway_generate"] = "gateway_generate"
    exception_type: Literal[
        "ValueError", "TypeError", "RuntimeError", "KeyError", "AttributeError",
        "AuthenticationError", "PermissionDeniedError", "RateLimitError",
        "APITimeoutError", "APIConnectionError", "BadRequestError",
        "InternalServerError", "EmptyCompletionError", "OtherError",
    ]
    http_status: int | None = Field(default=None, ge=100, le=599)


def diagnose_gateway_failure(error):
    allowed = GatewayDiagnostic.model_fields["exception_type"].annotation.__args__
    name = type(error).__name__
    try:
        status = getattr(error, "status_code", None)
    except Exception:
        status = None
    return GatewayDiagnostic(
        exception_type=name if name in allowed else "OtherError",
        http_status=status if type(status) is int and 100 <= status <= 599 else None,
    )
