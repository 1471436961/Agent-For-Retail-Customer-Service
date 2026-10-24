"""Describe actual evidence and write outcomes, without inventing dialogue stages."""


def evidence_view(state):
    return {
        "verified_customer": state.verified_customer_id,
        "profile_loaded": state.customer_profile_loaded,
        "current_customer_turn": state.user_turn,
        "quotes": [{"id": key, "kind": p["facts"]["kind"], "order_id": p["facts"]["order_id"],
                    "state": p["state"],
                    "assistant_response_after_quote": key in state.quote_response_turns}
                   for key, p in state.proposals.items()],
        "actual_submissions": list(state.submissions.values()),
        "meaning": "A quote is read-only. A response after it proves chronology, not that its details were explained or approved. Interpret the actual dialogue. Determine remaining goals from the whole conversation; no completion is inferred here.",
    }


def unresolved_operations(state):
    return any(p["state"] in {"prepared", "attempted", "pending", "unknown"}
               for p in [*state.proposals.values(), *state.submissions.values()])
