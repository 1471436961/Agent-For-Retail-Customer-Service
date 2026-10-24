"""Model-visible shapes for the existing seven preparation operations."""
from typing import Literal
from pydantic import Field, ValidationError, model_validator
from support_agent.adapters.read_models import Record, Address, ItemReplacement, NonEmpty


class DefaultAddressParameters(Record):
    address: Address


class OrderAddressParameters(DefaultAddressParameters):
    order_id: NonEmpty


class CancellationParameters(Record):
    order_id: NonEmpty
    reason: Literal["no longer needed", "ordered by mistake"]


class PaymentParameters(Record):
    order_id: NonEmpty
    payment_method_id: NonEmpty


class ReplacementChoice(ItemReplacement):
    preserve_options: list[str] = Field(default_factory=list)


class ReplacementParameters(PaymentParameters):
    replacements: list[ReplacementChoice]
    preserve_options: list[str] = Field(default_factory=list)
    specific_instance: bool = False


class ReturnParameters(Record):
    order_id: NonEmpty
    item_ids: list[NonEmpty]
    refund_payment_method_id: NonEmpty
    specific_instance: bool = False


ChangeParameters = (DefaultAddressParameters | OrderAddressParameters | CancellationParameters |
                    PaymentParameters | ReplacementParameters | ReturnParameters)

PARAMETERS_BY_KIND = {
    "default_address": DefaultAddressParameters, "order_address": OrderAddressParameters,
    "cancel": CancellationParameters, "payment": PaymentParameters,
    "modify_items": ReplacementParameters, "return": ReturnParameters,
    "exchange": ReplacementParameters,
}


def equivalent_change_parameters(kind, left, right):
    """Compare typed parameters with omitted/explicit defaults treated equally.

    Preserve all non-default constraints, ID occurrences, order and amounts.
    This affects local draft/replay identity only, never business API bodies.
    """
    model = PARAMETERS_BY_KIND.get(kind)
    if model is None:
        return False
    try:
        return (model.model_validate(left, strict=True).model_dump(exclude_defaults=True) ==
                model.model_validate(right, strict=True).model_dump(exclude_defaults=True))
    except ValidationError:
        return False


def validate_change_shape(kind, parameters):
    """Bind an operation to its own schema, without interpreting user intent.

    An untagged union alone accepts a replacement payload for an address kind.
    Error descriptions contain schema names only, never private input values.
    """
    model = PARAMETERS_BY_KIND.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise ValueError("Select a supported change kind")
    try:
        model.model_validate(parameters, strict=True)
    except ValidationError:
        required = [name for name, field in model.model_fields.items() if field.is_required()]
        raise ValueError(f"For {kind}, parameters require {', '.join(required)}; allowed fields: " +
                         ", ".join(model.model_fields) + ". Use this operation's schema, not another kind's parameters") from None


class PlanRequest(Record):
    kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"]
    parameters: ChangeParameters = Field(description=
        "Choose the shape belonging to kind: default_address={address}; order_address={order_id,address}; "
        "cancel={order_id,reason}; payment={order_id,payment_method_id}; "
        "modify_items/exchange={order_id,replacements,payment_method_id}; "
        "return={order_id,item_ids,refund_payment_method_id}. Do not mix operation fields.")

    @model_validator(mode="before")
    @classmethod
    def match_operation(cls, value):
        if isinstance(value, dict):
            validate_change_shape(value.get("kind"), value.get("parameters"))
        return value


class SubmissionRequest(PlanRequest):
    request_id: str = ""
