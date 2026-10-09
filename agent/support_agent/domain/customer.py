"""Minimal lookup rules. Extend with policies from the business materials."""

import json
import re

CUSTOMER_ID = re.compile(
    r"\b(?:developer_(?:traveler|pending|delivered)_\d+|customer_[A-Za-z0-9_-]+)\b"
)


def find_email(text: str) -> str | None:
    """Read independently supplied verification information, not profile data."""
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group() if match else None


def find_customer_id(text: str) -> str | None:
    """Extract an explicitly supplied demo ID; never invent a customer identity."""
    match = CUSTOMER_ID.search(text)
    return match.group() if match else None


def customer_reply(content: str, error: bool = False) -> str:
    """Convert a tool result into a truthful reply, including malformed responses."""
    if error:
        return "I could not find that customer. Please check the customer ID."
    try:
        customer = json.loads(content)
    except (ValueError, TypeError):
        return "The customer service returned an invalid response. Please try again."
    if not isinstance(customer, dict):
        return "The customer service returned an invalid response. Please try again."
    email = customer.get("email")
    if not isinstance(email, str) or not email.strip():
        return "The customer record has no email address available."
    return f"The customer email is {email}."
