"""Strict read models copied from the reviewed OpenAPI 3.1.0 field shapes.

No legacy field aliases, coerced strings, synthetic fields or hidden selectors.
Finite monetary values add the documented U3/U6 robustness requirement.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NonEmpty = Annotated[str, Field(min_length=1)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Address(Record):
    address_line_1: NonEmpty
    address_line_2: str | None = None
    city: NonEmpty
    region: NonEmpty
    country: NonEmpty
    postal_code: NonEmpty


class CustomerName(Record):
    first_name: str
    last_name: str


class CreditCard(Record):
    source: Literal["credit_card"]
    id: str
    brand: str
    last_four: str


class Paypal(Record):
    source: Literal["paypal"]
    id: str


class GiftCard(Record):
    source: Literal["gift_card"]
    id: str
    balance: float


class CustomerLookupResult(Record):
    customer_id: str


class Customer(Record):
    customer_id: str
    name: CustomerName
    default_shipping_address: Address
    email: str
    payment_methods: list[CreditCard | Paypal | GiftCard]
    order_ids: list[str]


class ProductSummary(Record):
    product_id: str
    name: str


class ProductList(Record):
    products: list[ProductSummary]


class CatalogItem(Record):
    item_id: str
    options: dict[str, str]
    available: bool
    price: float


class Product(Record):
    name: str
    product_id: str
    items: list[CatalogItem]


class OrderItem(Record):
    name: str
    product_id: str
    item_id: str
    price: float
    options: dict[str, str]


class OrderFulfillment(Record):
    tracking_id: list[str]
    item_ids: list[str]


class OrderPayment(Record):
    transaction_type: Literal["payment", "refund"]
    amount: float
    payment_method_id: str


class ItemReplacement(Record):
    existing_item_id: NonEmpty
    replacement_item_id: NonEmpty


class OrderCancellation(Record):
    reason: Literal["no longer needed", "ordered by mistake"]


class OrderExchange(Record):
    replacements: list[ItemReplacement]
    payment_method_id: str
    price_difference: float


class OrderReturn(Record):
    item_ids: list[str]
    refund_payment_method_id: str


class Order(Record):
    order_id: str
    customer_id: str
    shipping_address: Address
    items: list[OrderItem]
    status: Literal["processed", "pending", "pending (items modified)", "delivered",
                    "cancelled", "exchange requested", "return requested"]
    fulfillments: list[OrderFulfillment]
    payments: list[OrderPayment]
    cancellation: OrderCancellation | None = None
    exchange: OrderExchange | None = None
    return_request: OrderReturn | None = None


READ_MODELS = {
    "searchCustomers": CustomerLookupResult,
    "getCustomer": Customer,
    "listProducts": ProductList,
    "getProduct": Product,
    "getCatalogItem": CatalogItem,
    "getOrder": Order,
}
