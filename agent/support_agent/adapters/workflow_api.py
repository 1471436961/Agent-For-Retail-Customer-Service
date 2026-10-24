"""Follow an actual asynchronous receipt; never resubmit its business write.

The status resource comes exclusively from the service's Location header.
No deployment path, case identifier or guessed status endpoint is embedded here.
"""
from copy import deepcopy
import math
import time
from urllib.parse import unquote, urlsplit


# One tool invocation waits a bounded time. A still-pending receipt is retained
# for inspect_change_result, without changing the customer's authorization.
STATUS_WAIT_SECONDS = 20.0


def header(response, name):
    headers = getattr(response, "headers", None) or {}
    return next((value for key, value in headers.items()
                 if key.lower() == name.lower()), None)


def retry_delay(response):
    raw = header(response, "Retry-After")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise ValueError("Missing or invalid Retry-After")
    value = int(raw)
    if value <= 0 or not math.isfinite(float(value)):
        raise ValueError("Invalid Retry-After")
    return value


def accepted_workflow(response, facts):
    body = response.body
    if (not isinstance(body, dict) or set(body) != {"workflow_id", "status"}
            or body["status"] != "pending"):
        raise ValueError("Invalid pending receipt")
    identifier = body["workflow_id"]
    location = header(response, "Location")
    if (not isinstance(identifier, str) or not identifier or len(identifier) > 256
            or not isinstance(location, str) or len(location) > 2048):
        raise ValueError("Invalid workflow reference")
    parsed = urlsplit(location)
    # The injected ClientAPI accepts local paths. Do not follow external URLs,
    # queries, fragments, traversal or an unrelated resource in a status header.
    parts = [unquote(part) for part in parsed.path.split("/")]
    if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or not location.startswith("/v1/") or parsed.path != location
            or parts[-1] != identifier or len(parts) < 4
            or any(part in {".", ".."} or "/" in part or "\\" in part
                   or any(ord(char) < 32 for char in part) for part in parts)):
        raise ValueError("Invalid local workflow Location")
    owners = {"customers": facts["customer_id"], "orders": facts["order_id"]}
    if ((parts[2] in owners and parts[3] != owners[parts[2]])
            or parts[2] == "conversations"):
        raise ValueError("Workflow Location outside the approved resource scope")
    return {"workflow_id": identifier, "location": location,
            "retry_after": retry_delay(response)}


def poll_workflow(api, workflow):
    """Wait using Retry-After, retaining pending/unknown state on interruption.

    Terminal success still needs the original operation's full receipt checks.
    A terminal workflow failure is a definite failure; other failures never
    authorize replay. Only GET is sent to the exact retained status resource.
    """
    workflow = deepcopy(workflow)
    waited = 0
    while True:
        delay = workflow["retry_after"]
        if waited + delay > STATUS_WAIT_SECONDS:
            return {"state": "pending", "workflow": workflow}
        time.sleep(delay)
        waited += delay
        try:
            response = api.request("GET", workflow["location"])
        except Exception:
            return {"state": "unknown", "workflow": workflow,
                    "error": "workflow_status_transport_failure"}
        if response.status_code != 200:
            return {"state": "unknown", "workflow": workflow,
                    "http_status": response.status_code,
                    "error": "workflow_status_http_failure"}
        body = response.body
        if (not isinstance(body, dict)
                or body.get("workflow_id") != workflow["workflow_id"]):
            return {"state": "unknown", "workflow": workflow,
                    "error": "invalid_workflow_status_receipt"}
        status = body.get("status")
        if status == "pending" and set(body) == {"workflow_id", "status"}:
            try:
                workflow["retry_after"] = retry_delay(response)
            except (ValueError, OverflowError):
                return {"state": "pending", "workflow": workflow,
                        "error": "invalid_workflow_retry_after"}
            continue
        if status == "succeeded" and "result" in body:
            return {"state": "succeeded", "workflow": workflow,
                    "result": deepcopy(body["result"])}
        if status == "failed" and isinstance(body.get("error"), dict):
            return {"state": "failed", "workflow": workflow,
                    "error": "workflow_operation_failed"}
        return {"state": "unknown", "workflow": workflow,
                "error": "invalid_workflow_status_receipt"}
