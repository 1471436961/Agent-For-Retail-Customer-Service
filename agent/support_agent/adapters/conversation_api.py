"""Exact public transfer contract and notice, no model-generated routing ID."""
from urllib.parse import quote
from typing import Literal
from support_agent.adapters.read_models import Record

TRANSFER_NOTICE = "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."


class TransferReceipt(Record):
    transfer_id: str
    status: Literal["accepted"]


def transfer_once(api, summary):
    try:
        response = api.request("POST", f"/v1/conversations/{quote(api.context.conversation_id, safe='')}/transfers", body={"summary": summary})
    except Exception:
        return {"state": "unknown", "error": "transfer_transport_failure"}
    if response.status_code != 201:
        return {"state": "failed" if response.status_code in {400, 404, 405, 409, 413, 422} else "unknown",
                "http_status": response.status_code, "error": "transfer_http_failure"}
    try:
        receipt = TransferReceipt.model_validate(response.body)
        if not receipt.transfer_id.strip():
            raise ValueError("Empty transfer identifier")
    except ValueError:
        return {"state": "unknown", "error": "invalid_transfer_receipt"}
    return {"state": "accepted", "receipt": receipt.model_dump()}
