"""Model-visible shapes for the existing seven preparation operations."""
from typing import Literal
from pydantic import Field
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


class PlanRequest(Record):
    kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"]
    parameters: ChangeParameters
