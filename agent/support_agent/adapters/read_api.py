"""Six explicit read operations through the injected ClientAPI only."""

from copy import deepcopy
from urllib.parse import quote

from pydantic import ValidationError
from support_agent.adapters.read_models import READ_MODELS
from support_agent.domain.identity import ReadError, resource_id

_ERROR_CODES = {
    "invalid_body", "resource_not_found", "resource_conflict", "business_rule_violation",
    "request_too_large", "response_too_large", "invalid_response_body", "method_not_allowed",
}


def _read(client_api, operation, method, path, *, body=None):
    try:
        if body is None:
            response = client_api.request(method, path)
        else:
            response = client_api.request(method, path, body=body)
    except Exception:
        raise ReadError("read_transport_failure", operation=operation) from None
    status = response.status_code
    if status != 200:
        error = response.body.get("error") if isinstance(response.body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise ReadError("read_http_failure", operation=operation, http_status=status,
                        api_code=code if isinstance(code, str) and code in _ERROR_CODES else None)
    try:
        if not isinstance(response.body, dict):
            raise ReadError("invalid_read_response", operation=operation)
        READ_MODELS[operation].model_validate(response.body)
    except ValidationError:
        raise ReadError("invalid_read_response", operation=operation) from None
    # Validate without rewriting floats/optional fields, list ordering or repeats.
    return deepcopy(response.body)


def search_customer(client_api, selector):
    return _read(client_api, "searchCustomers", "POST", "/v1/customers/search", body=selector)


def read_customer(client_api, customer_id):
    customer_id = resource_id(customer_id)
    record = _read(client_api, "getCustomer", "GET", f"/v1/customers/{quote(customer_id, safe='')}")
    if record["customer_id"] != customer_id:
        raise ReadError("invalid_read_response", operation="getCustomer")
    return record


def read_order(client_api, order_id):
    order_id = resource_id(order_id)
    record = _read(client_api, "getOrder", "GET", f"/v1/orders/{quote(order_id, safe='')}")
    if record["order_id"] != order_id:
        raise ReadError("invalid_read_response", operation="getOrder")
    return record


def read_products(client_api):
    return _read(client_api, "listProducts", "GET", "/v1/catalog/products")


def read_product(client_api, product_id):
    product_id = resource_id(product_id)
    record = _read(client_api, "getProduct", "GET", f"/v1/catalog/products/{quote(product_id, safe='')}")
    if record["product_id"] != product_id:
        raise ReadError("invalid_read_response", operation="getProduct")
    return record


def read_catalog_item(client_api, item_id):
    item_id = resource_id(item_id)
    record = _read(client_api, "getCatalogItem", "GET", f"/v1/catalog/items/{quote(item_id, safe='')}")
    if record["item_id"] != item_id:
        raise ReadError("invalid_read_response", operation="getCatalogItem")
    return record
