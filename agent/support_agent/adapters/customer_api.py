"""Business API transport: no private DB, external endpoint, or credentials."""

from urllib.parse import quote


def verify_and_read_customer(client_api, customer_id: str, email: str) -> dict:
    """Retail: independent search must match the claimed ID before any detail read."""
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Provide your email to verify identity; customer ID alone is insufficient")
    response = client_api.request("POST", "/v1/customers/search", body={"email": email})
    response.raise_for_status()
    found = response.body.get("customer_id") if isinstance(response.body, dict) else None
    if not isinstance(found, str) or not found or (customer_id and found != customer_id):
        raise ValueError("Identity verification failed; no customer details were loaded")
    return read_customer(client_api, found)


def read_customer(client_api, customer_id: str) -> dict:
    """Read one customer through the injected environment API."""
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("customer_id is required")
    response = client_api.request("GET", f"/v1/customers/{quote(customer_id, safe='')}")
    response.raise_for_status()
    if not isinstance(response.body, dict):
        raise ValueError("Expected a customer object")
    return response.body
