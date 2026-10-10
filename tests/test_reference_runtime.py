"""Real installed tau2 REST runtime/backend using only synthetic public data.

No canned REST response or model completion is used. The SDK's public
development seed is applied to a tiny fixture DB; benchmark tasks are not read.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from fixtures.p2_backend import SyntheticBackend
from tools import Tools
from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail_plus.tools import RetailPlusTools
from tau2.environment.environment import Environment
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.development import apply_development_seed
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage
from support_agent.domain.identity import ReadError

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "materials/client_api/development_seed.json").read_text())


def legacy_address(address):
    return {"address1": address["address_line_1"], "address2": address.get("address_line_2") or "",
            "city": address["city"], "state": address["region"],
            "country": address["country"], "zip": address["postal_code"]}


def reference_kit():
    records = deepcopy(SyntheticBackend().records)
    db = RetailDB.model_validate({
        "users": {c["customer_id"]: {"user_id": c["customer_id"], "name": c["name"],
                  "email": c["email"], "address": legacy_address(c["default_shipping_address"]),
                  "payment_methods": {m["id"]: m for m in c["payment_methods"]}, "orders": c["order_ids"]}
                  for c in records["customers"]},
        "products": {p["product_id"]: {"product_id": p["product_id"], "name": p["name"],
                     "variants": {i["item_id"]: i for i in p["items"]}} for p in records["products"]},
        "orders": {o["order_id"]: {"order_id": o["order_id"], "user_id": o["customer_id"],
                   "address": legacy_address(o["shipping_address"]), "items": o["items"],
                   "status": o["status"], "fulfillments": o["fulfillments"], "payment_history": o["payments"]}
                   for o in records["orders"]},
    })
    environment = Environment("retail_plus", policy="", tools=RetailPlusTools(db))
    apply_development_seed(environment)
    runtime = ClientAPIRuntime(environment, conversation_id="public-reference-runtime")
    requests = []

    def transport(request):
        requests.append(deepcopy(request))
        return runtime.dispatch(request)

    return Tools(ClientAPI(transport, context=runtime.context)), runtime, requests


class ReferenceRuntimeTests(unittest.TestCase):
    def test_t1_identity_search_precedes_real_customer_details(self):
        kit, runtime, requests = reference_kit()
        case = SEED["cases"][0]
        result = kit.verify_customer(email=case["email"], claimed_customer_id=case["customer_id"])
        self.assertEqual(result["email"], case["email"])
        self.assertEqual([r["path"] for r in requests],
                         ["/v1/customers/search", "/v1/customers/" + case["customer_id"]])

    def test_claim_mismatch_never_loads_profile_on_reference_runtime(self):
        kit, runtime, requests = reference_kit()
        with self.assertRaisesRegex(ReadError, "identity_verification_failed"):
            kit.verify_customer(email=SEED["cases"][0]["email"],
                                claimed_customer_id=SEED["cases"][1]["customer_id"])
        self.assertEqual([r["path"] for r in requests], ["/v1/customers/search"])

    def test_reference_transfer_and_terminal_customer_api_guard(self):
        kit, runtime, requests = reference_kit()
        result = kit.transfer_to_human("Customer requests a human.", "explicit_request")
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(requests[0]["path"], "/v1/conversations/public-reference-runtime/transfers")
        self.assertEqual(kit.transfer_to_human("Customer requests a human.", "explicit_request"), result)
        with self.assertRaisesRegex(ReadError, "human_handoff_terminal"):
            kit.verify_customer(email=SEED["cases"][0]["email"])
        self.assertEqual(len(requests), 1)

    def test_actual_sdk_replay_rebuilds_verification_and_preparation_before_write(self):
        kit, runtime, requests = reference_kit()
        history = []
        live_environment = Environment("retail_plus", policy="", tools=kit)
        case = SEED["cases"][0]
        calls = [("verify_customer", {"email": case["email"]}),
                 ("prepare_change", {"kind": "cancel", "parameters": {"order_id": case["order_id"], "reason": "no longer needed"}}),
                 ("review_change", {"proposal_id": "proposal-1"}),
                 ("execute_cancellation", {"proposal_id": "proposal-1"})]
        for index, (name, arguments) in enumerate(calls):
            call = ToolCall(id=f"reference-{index}", name=name, arguments=arguments)
            history.extend([AssistantMessage(role="assistant", tool_calls=[call]),
                            live_environment.get_response(call)])
            self.assertFalse(history[-1].error)
        fresh, replay_runtime, replay_requests = reference_kit()
        submitted_environment = Environment("retail_plus", policy="", tools=fresh)
        submitted_environment.set_state(None, None, history)
        self.assertEqual(fresh.get_service_status(), kit.get_service_status())
        self.assertEqual(replay_runtime.environment.tools.db.model_dump(), runtime.environment.tools.db.model_dump())
        self.assertEqual(len([r for r in replay_requests if r["path"].endswith("/cancellations")]), 1)

    def test_seven_changes_against_real_rest_runtime(self):
        address = {"address_line_1": "90 New Road", "city": "Boston", "region": "MA",
                   "country": "USA", "postal_code": "02101"}
        for kind, executor in (("default_address", "execute_default_address"),
                               ("order_address", "execute_order_address"), ("cancel", "execute_cancellation"),
                               ("payment", "execute_payment"), ("modify_items", "execute_item_modification"),
                               ("return", "execute_return"), ("exchange", "execute_exchange")):
            with self.subTest(kind=kind):
                kit, runtime, requests = reference_kit()
                case = SEED["cases"][1 if kind in {"return", "exchange"} else 0]
                kit.verify_customer(email=case["email"])
                params = {"order_id": case["order_id"]}
                if "address" in kind:
                    params["address"] = address
                    if kind == "default_address":
                        params.pop("order_id")
                elif kind == "cancel":
                    params["reason"] = "no longer needed"
                elif kind == "payment":
                    params["payment_method_id"] = case["payment_method_ids"]["paypal"]
                else:
                    order = kit.get_owned_order(case["order_id"])
                    item = order["items"][0]
                    if kind == "return":
                        params.update(item_ids=[item["item_id"]],
                                      refund_payment_method_id=case["payment_method_ids"]["credit_card"])
                    else:
                        product = kit.get_product_variants(item["product_id"])
                        target = next(i for i in product["items"] if i["available"] and i["item_id"] != item["item_id"])
                        params.update(replacements=[{"existing_item_id": item["item_id"], "replacement_item_id": target["item_id"]}],
                                      preserve_options=[], payment_method_id=case["payment_method_ids"]["paypal"])
                proposal = kit.prepare_change(kind, params)
                outcome = getattr(kit, executor)(proposal["proposal_id"])
                self.assertEqual(outcome["state"], "accepted" if kind in {"return", "exchange"} else "completed", outcome)
                writes = [r for r in requests if r["method"] != "GET" and r["path"] != "/v1/customers/search"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(getattr(kit, executor)(proposal["proposal_id"]), outcome)
                self.assertEqual(len([r for r in requests if r["method"] != "GET" and r["path"] != "/v1/customers/search"]), 1)

    def test_real_address_receipts_normalize_only_optional_second_line(self):
        address = {"address_line_1": "90 New Road", "city": "Boston", "region": "MA",
                   "country": "USA", "postal_code": "02101"}
        for kind, executor in (("default_address", "execute_default_address"),
                               ("order_address", "execute_order_address")):
            for second_line in ({}, {"address_line_2": None}, {"address_line_2": ""}, {"address_line_2": "Unit 42"}):
                with self.subTest(kind=kind, second_line=second_line):
                    kit, runtime, requests = reference_kit()
                    case = SEED["cases"][0]
                    kit.verify_customer(email=case["email"])
                    params = {"address": {**address, **second_line}}
                    if kind == "order_address":
                        params["order_id"] = case["order_id"]
                    proposal = kit.prepare_change(kind, params)
                    outcome = getattr(kit, executor)(proposal["proposal_id"])
                    self.assertEqual(outcome["state"], "completed", outcome)
                    key = "default_shipping_address" if kind == "default_address" else "shipping_address"
                    self.assertEqual(outcome["receipt"][key]["address_line_2"], second_line.get("address_line_2") or "")
