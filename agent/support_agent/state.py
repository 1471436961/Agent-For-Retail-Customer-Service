"""JSON-compatible state, created separately for every conversation."""

from typing import TypedDict


class SessionState(TypedDict):
    turn: int
    pending_call_id: str | None
    customer_id: str | None


def initial_state() -> SessionState:
    """Return fresh state; history-based memory can be added here."""
    return {"turn": 0, "pending_call_id": None, "customer_id": None}
