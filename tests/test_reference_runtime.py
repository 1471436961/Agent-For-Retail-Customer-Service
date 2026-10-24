"""Real installed tau2 REST runtime/backend using only synthetic public data.

No canned REST response or model completion is used. The SDK's public
development seed is applied to a tiny fixture DB; benchmark tasks are not read.
"""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from fixtures.p2_backend import SyntheticBackend
from tools import Tools
from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail_plus.tools import RetailPlusTools
from tau2.environment.environment import Environment
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.development import apply_development_seed
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage, MultiToolMessage
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from unittest.mock import patch
from agent import create_agent
from support_agent.domain.identity import ReadError

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


def serialized_reference_kit():
    """The same real REST backend, with the SDK's actual scalar serializer."""
    kit, runtime, requests = reference_kit()
    transport = kit.client_api._transport

    def serialized_transport(request):
        response = transport(request)
        response["body"] = json.loads(Environment.to_json_str(response["body"]))
        return response

    kit.client_api = ClientAPI(serialized_transport, context=runtime.context)
    return kit, runtime, requests


def apply_native_reference(tools, kind, customer_id, params):
    """Independent native toolkit path; no candidate receipt validation."""
    if kind in {"default_address", "order_address"}:
        address = legacy_address(params["address"])
        args = dict(address1=address["address1"], address2=address["address2"],
                    city=address["city"], state=address["state"],
                    country=address["country"], zip=address["zip"])
        if kind == "default_address":
            return tools.modify_user_address(user_id=customer_id, **args)
        return tools.modify_pending_order_address(order_id=params["order_id"], **args)
    if kind == "cancel":
        return tools.cancel_pending_order(params["order_id"], params["reason"])
    if kind == "payment":
        return tools.modify_pending_order_payment(params["order_id"], params["payment_method_id"])
    if kind == "return":
        return tools.return_delivered_order_items(params["order_id"], params["item_ids"],
                                                 params["refund_payment_method_id"])
    function = tools.modify_pending_order_items if kind == "modify_items" else tools.exchange_delivered_order_items
    return function(params["order_id"], [row["existing_item_id"] for row in params["replacements"]],
                    [row["replacement_item_id"] for row in params["replacements"]], params["payment_method_id"])


class ReferenceRuntimeTests(unittest.TestCase):
    @staticmethod
    def change_parameters(kit, kind, case):
        params = {"order_id": case["order_id"]}
        if "address" in kind:
            params["address"] = {"address_line_1": "90 New Road", "city": "Boston",
                                 "region": "MA", "country": "USA", "postal_code": "02101"}
            if kind == "default_address":
                params.pop("order_id")
        elif kind == "cancel":
            params["reason"] = "no longer needed"
        elif kind == "payment":
            params["payment_method_id"] = case["payment_method_ids"]["paypal"]
        else:
            item = kit.get_owned_order(case["order_id"])["items"][0]
            if kind == "return":
                params.update(item_ids=[item["item_id"]],
                    refund_payment_method_id=case["payment_method_ids"]["credit_card"])
            else:
                product = kit.get_product_variants(item["product_id"])
                target = next(i for i in product["items"] if i["available"] and i["item_id"] != item["item_id"])
                params.update(replacements=[{"existing_item_id": item["item_id"], "replacement_item_id": target["item_id"]}],
                    preserve_options=[], payment_method_id=case["payment_method_ids"]["paypal"])
        return params

    def test_seven_agent_host_dialogues_match_native_reference_database_hashes(self):
        # The decisions are controlled: this checks protocol and actual DB
        # effects, not real-model understanding or the full benchmark score.
        for kind, executor in (("default_address", "execute_default_address"),
                               ("order_address", "execute_order_address"), ("cancel", "execute_cancellation"),
                               ("payment", "execute_payment"), ("modify_items", "execute_item_modification"),
                               ("return", "execute_return"), ("exchange", "execute_exchange")):
            with self.subTest(kind=kind):
                kit, runtime, requests = reference_kit()
                case = SEED["cases"][1 if kind in {"return", "exchange"} else 0]
                # Obtain independent fixture choices through a separate reader.
                reader, _, _ = reference_kit()
                reader.verify_customer(email=case["email"])
                params = self.change_parameters(reader, kind, case)
                context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
                    resource_root=ROOT / "agent", model_configs=[{"model": "offline-contract-model"}])
                with activate_agent_context(context):
                    agent = create_agent()
                    agent.gateway.live_actions = agent.gateway.compatibility_actions
                host = Environment("retail_plus", policy="", tools=kit)
                initial_hash = runtime.environment.get_db_hash()
                state = agent.get_init_state()
                incoming = UserMessage(role="user", content="Please help me. My email is " + case["email"])
                steps = [("verify_customer", {"email": case["email"], "claimed_customer_id": case["customer_id"]}),
                         ("prepare_change", {"kind": kind, "parameters": params}),
                         ("review_change", {"proposal_id": "proposal-1"})]
                with patch("tau2.hyper.agent_context.generate") as model:
                    for index, (name, arguments) in enumerate(steps):
                        model.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(
                            id=f"dialogue-{index}", name=name, arguments=arguments)])
                        reply, state = agent.generate_next_message(incoming, state)
                        self.assertEqual(len(reply.tool_calls), 1)
                        result = host.get_response(reply.tool_calls[0])
                        self.assertFalse(result.error, result.content)
                        incoming = MultiToolMessage(role="tool", tool_messages=[result])
                    from support_agent.domain.presentation import review_text
                    model.return_value = AssistantMessage(role="assistant", content=review_text(json.loads(result.content)["review"]))
                    recap, state = agent.generate_next_message(incoming, state)
                    self.assertFalse(recap.tool_calls)
                    self.assertIn("Please confirm", recap.content)
                    self.assertEqual(state["quote_response_turns"], {"proposal-1": state["user_turn"]})
                    self.assertEqual(runtime.environment.get_db_hash(), initial_hash)
                    # No business write until the actual later approval message.
                    writes = lambda: [request for request in requests if request["method"] != "GET"
                                     and request["path"] != "/v1/customers/search"]
                    self.assertEqual(writes(), [])
                    model.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(
                        id="dialogue-execute", name=executor, arguments={"proposal_id": "proposal-1"})])
                    reply, state = agent.generate_next_message(UserMessage(role="user",
                        content="This is the complete list. I approve the exact change."), state)
                    result = host.get_response(reply.tool_calls[0])
                    self.assertFalse(result.error, result.content)
                    model.return_value = AssistantMessage(role="assistant", content="The requested operation was recorded.")
                    final, state = agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[result]), state)
                    self.assertIsNone(state["halt_reason"])
                    self.assertEqual(final.content, "The requested operation was recorded.")
                    self.assertEqual(state["proposals"]["proposal-1"]["state"], json.loads(result.content)["state"])
                    actual = state["history"][-2]["tool_messages"][0]
                    self.assertEqual(json.loads(actual["content"]), json.loads(result.content))
                    self.assertEqual(len(writes()), 1)
                _, gold, _ = reference_kit()
                apply_native_reference(gold.environment.tools, kind, case["customer_id"], params)
                self.assertEqual(runtime.environment.get_db_hash(), gold.environment.get_db_hash())

    def test_t1_without_optional_context_still_searches_before_details(self):
        kit, runtime, requests = reference_kit()
        # Candidate server constructs ClientAPI without context; its transport
        # already addresses the current backend, as in the remote t1 trial.
        kit = Tools(ClientAPI(kit.client_api._transport))
        case = SEED["cases"][0]
        result = kit.lookup_customer(customer_id=case["customer_id"], email=case["email"])
        self.assertEqual(result["email"], case["email"])
        self.assertEqual([r["path"] for r in requests],
                         ["/v1/customers/search", "/v1/customers/" + case["customer_id"]])
        self.assertEqual(kit.get_customer_summary()["email"], case["email"])
        with self.assertRaisesRegex(ReadError, "identity_verification_failed"):
            kit.lookup_customer(customer_id=SEED["cases"][1]["customer_id"],
                                email=SEED["cases"][1]["email"])
        self.assertEqual(len(requests), 3)

    def test_missing_context_does_not_fabricate_transfer_conversation(self):
        kit, runtime, requests = reference_kit()
        kit = Tools(ClientAPI(kit.client_api._transport))
        outcome = kit.transfer_to_human("Customer requests a human.", "explicit_request")
        self.assertEqual(outcome["state"], "unknown")
        self.assertEqual(outcome["error"], "transfer_transport_failure")
        self.assertEqual(requests, [])

    def test_late_context_binding_clears_prior_toolkit_identity(self):
        kit, runtime, requests = reference_kit()
        kit = Tools(ClientAPI(kit.client_api._transport))
        first, second = SEED["cases"][:2]
        kit.verify_customer(email=first["email"], claimed_customer_id=first["customer_id"])
        kit.client_api._set_context({"conversation_id": "next-trusted-conversation"})
        with self.assertRaisesRegex(ReadError, "identity_required"):
            kit.get_customer_summary()
        self.assertEqual(kit._proposals, {})
        self.assertEqual(kit.verify_customer(email=second["email"],
                         claimed_customer_id=second["customer_id"])["customer_id"], second["customer_id"])

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
        self.exercise_seven_changes(reference_kit)

    def test_seven_changes_accept_sdk_serialized_response_scalars(self):
        self.exercise_seven_changes(serialized_reference_kit)

    def exercise_seven_changes(self, factory):
        address = {"address_line_1": "90 New Road", "city": "Boston", "region": "MA",
                   "country": "USA", "postal_code": "02101"}
        for kind, executor in (("default_address", "execute_default_address"),
                               ("order_address", "execute_order_address"), ("cancel", "execute_cancellation"),
                               ("payment", "execute_payment"), ("modify_items", "execute_item_modification"),
                               ("return", "execute_return"), ("exchange", "execute_exchange")):
            with self.subTest(kind=kind):
                kit, runtime, requests = factory()
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
                _, reference_runtime, _ = reference_kit()
                apply_native_reference(reference_runtime.environment.tools, kind, case["customer_id"], params)
                self.assertEqual(runtime.environment.get_db_hash(), reference_runtime.environment.get_db_hash())
                writes = [r for r in requests if r["method"] != "GET" and r["path"] != "/v1/customers/search"]
                self.assertEqual(len(writes), 1)
                self.assertEqual(getattr(kit, executor)(proposal["proposal_id"]), outcome)
                self.assertEqual(len([r for r in requests if r["method"] != "GET" and r["path"] != "/v1/customers/search"]), 1)

    def test_multiple_return_items_keep_counts_but_accept_reference_sort_order(self):
        kit, runtime, requests = reference_kit()
        case = SEED["cases"][1]
        kit.verify_customer(email=case["email"])
        order = kit.get_owned_order(case["order_id"])
        ids = sorted([item["item_id"] for item in order["items"]], reverse=True)
        self.assertNotEqual(ids, sorted(ids))
        params = {"order_id": case["order_id"], "item_ids": ids,
                  "refund_payment_method_id": case["payment_method_ids"]["credit_card"]}
        proposal = kit.prepare_change("return", params)
        outcome = kit.execute_return(proposal["proposal_id"])
        self.assertEqual(outcome["state"], "accepted", outcome)
        write = next(row for row in requests if row["path"].endswith("/returns"))
        self.assertEqual(write["body"]["item_ids"], ids)
        self.assertEqual(outcome["receipt"]["return_request"]["item_ids"], sorted(ids))
        from support_agent.adapters.write_api import validate_financial_receipt
        invalid = deepcopy(outcome["receipt"])
        invalid["return_request"]["item_ids"].pop()
        with self.assertRaisesRegex(ValueError, "Mismatched return receipt"):
            validate_financial_receipt(proposal["facts"], invalid)
        _, gold, _ = reference_kit()
        apply_native_reference(gold.environment.tools, "return", case["customer_id"], params)
        self.assertEqual(runtime.environment.get_db_hash(), gold.environment.get_db_hash())

    def test_two_product_exchange_handles_exact_reference_independent_sorting(self):
        kit, runtime, requests = reference_kit()
        case = SEED["cases"][1]
        kit.verify_customer(email=case["email"])
        order = kit.get_owned_order(case["order_id"])
        selected = [order["items"][0], order["items"][-1]]
        rows = []
        for item, new_id in zip(selected, ("a-new-shirt", "z-new-mug")):
            product = runtime.environment.tools.db.products[item["product_id"]]
            variant = next(variant for variant in product.variants.values()
                           if variant.available and variant.item_id != item["item_id"])
            product.variants[new_id] = variant.model_copy(update={"item_id": new_id})
            rows.append({"existing_item_id": item["item_id"], "replacement_item_id": new_id})
        _, gold, _ = reference_kit()
        gold.environment.tools.db = runtime.environment.tools.db.model_copy(deep=True)
        params = {"order_id": case["order_id"], "replacements": rows, "preserve_options": [],
                  "payment_method_id": case["payment_method_ids"]["paypal"]}
        proposal = kit.prepare_change("exchange", params)
        outcome = kit.execute_exchange(proposal["proposal_id"])
        self.assertEqual(outcome["state"], "accepted", outcome)
        self.assertIn("reference_exchange_pairing_lost", {row["code"] for row in outcome["response_warnings"]})
        self.assertEqual(outcome["customer_facts"]["requested_replacements"], rows)
        from support_agent.adapters.write_api import validate_financial_receipt
        for change in ("count", "method", "amount"):
            invalid = deepcopy(outcome["receipt"])
            if change == "count":
                invalid["exchange"]["replacements"].pop()
            elif change == "method":
                invalid["exchange"]["payment_method_id"] = "unapproved-method"
            else:
                invalid["exchange"]["price_difference"] += 1
            with self.subTest(invalid=change), self.assertRaisesRegex(ValueError, "Mismatched exchange receipt"):
                validate_financial_receipt(proposal["facts"], invalid)
        write = next(row for row in requests if row["path"].endswith("/exchanges"))
        self.assertEqual(write["body"]["replacements"], rows)
        apply_native_reference(gold.environment.tools, "exchange", case["customer_id"], params)
        self.assertEqual(runtime.environment.get_db_hash(), gold.environment.get_db_hash())

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
