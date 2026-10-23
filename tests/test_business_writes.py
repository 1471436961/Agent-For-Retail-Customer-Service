"""P5 seven-operation outcomes and one-time lists on synthetic API state."""
import json
import unittest
from copy import deepcopy
import test_confirmation as confirmation
from test_agent_protocol import user, call, calls_reply
from fixtures.write_backend import WriteBackend
from support_agent.domain.identity import ReadError


class BusinessWriteTests(unittest.TestCase):
    setUp = confirmation.ConfirmationTests.setUp
    decision = confirmation.ConfirmationTests.decision
    host = confirmation.ConfirmationTests.host
    prepared = confirmation.ConfirmationTests.prepared

    def parameters(self, kind):
        if kind == "payment":
            return {"order_id": "#P0-PENDING", "payment_method_id": "p0_paypal"}
        if kind == "return":
            return {"order_id": "#P0-DELIVERED", "item_ids": ["p0_shirt_s_blue", "p0_shirt_s_blue"], "refund_payment_method_id": "p0_card"}
        return {"order_id": "#P0-DELIVERED" if kind == "exchange" else "#P0-PENDING",
                "replacements": [{"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": "p0_shirt_m_blue"}] * 2,
                "payment_method_id": "p0_paypal", "preserve_options": ["color"]}

    def review(self, kind):
        p = self.prepared(kind, self.parameters(kind))
        _, text = self.host(self.decision(user("Show me exactly what changes."), "review_change", {"proposal_id": p["proposal_id"]}))
        if kind != "payment":
            self.assertIn("complete list", text.content)
            for row in p["facts"]["body"].get("replacements", []):
                self.assertIn(row["existing_item_id"], text.content)
                self.assertIn(row["replacement_item_id"], text.content)
            for identifier in p["facts"]["body"].get("item_ids", []):
                self.assertIn(identifier, text.content)
            self.assertIn(p["facts"]["body"].get("payment_method_id", p["facts"]["body"].get("refund_payment_method_id")), text.content)
        return p

    def executors(self):
        return {"payment": "execute_payment", "modify_items": "execute_item_modification", "return": "execute_return", "exchange": "execute_exchange"}

    def test_four_complete_business_slices_and_repeat_call(self):
        for kind, execute in self.executors().items():
            with self.subTest(kind=kind):
                self.setUp()
                p = self.review(kind)
                out, _ = self.host(self.decision(user("This is the entire request. Please proceed."), execute, {"proposal_id": p["proposal_id"]}))
                self.assertEqual(out["state"], "accepted" if kind in {"return", "exchange"} else "completed")
                self.assertEqual(getattr(self.kit, execute)(p["proposal_id"]), out)
                self.assertEqual(len(self.backend.writes()), 1)
                self.assertEqual(self.backend.writes()[0]["body"], p["facts"]["body"])
                order = next(o for o in self.backend.records["orders"] if o["order_id"] == p["facts"]["order_id"])
                if kind == "payment":
                    self.assertEqual(order["status"], "pending")
                    self.assertEqual(order["payments"][-2:],[{"transaction_type": "payment", "amount": 50.0, "payment_method_id": "p0_paypal"}, {"transaction_type": "refund", "amount": 50.0, "payment_method_id": "p0_card"}])
                elif kind == "modify_items":
                    self.assertEqual(order["status"], "pending (items modified)")
                    self.assertEqual([i["item_id"] for i in order["items"]].count("p0_shirt_m_blue"), 2)
                elif kind == "return":
                    self.assertEqual(order["return_request"]["item_ids"], ["p0_shirt_s_blue"] * 2)
                    self.assertEqual(len(order["payments"]), 1)  # request != refund arrival
                else:
                    self.assertEqual(order["exchange"]["price_difference"], 10.0)

    def test_each_kind_withdrawal_and_unapproved_execution_zero_writes(self):
        for kind, execute in self.executors().items():
            with self.subTest(kind=kind):
                self.setUp()
                p = self.review(kind)
                self.host(self.decision(user("Don't do it; I have another question."), "invalidate_change", {"proposal_id": p["proposal_id"]}))
                reply = self.decision(user("yes to that question only"), execute, {"proposal_id": p["proposal_id"]})
                self.assertFalse(reply.tool_calls)
                self.assertEqual(self.backend.writes(), [])

    def test_each_kind_rejection_unknown_and_capacity_never_retries(self):
        suffix = {"payment": ("PUT", "payment-method"), "modify_items": ("POST", "item-modifications"), "return": ("POST", "returns"), "exchange": ("POST", "exchanges")}
        for kind, execute in self.executors().items():
            for failure, state in ((409, "failed"), (422, "failed"), (413, "failed"), (502, "unknown"), (503, "unknown"), (TimeoutError("private token"), "unknown")):
                with self.subTest(kind=kind, failure=str(failure)):
                    self.setUp()
                    p = self.review(kind)
                    method, tail = suffix[kind]
                    oid = "%23P0-DELIVERED" if kind in {"return", "exchange"} else "%23P0-PENDING"
                    override = failure if isinstance(failure, Exception) else WriteBackend.response({"error": {"code": "business_rule_violation", "message": "private token"}}, failure)
                    self.backend.overrides[(method, f"/v1/orders/{oid}/{tail}")] = override
                    out = getattr(self.kit, execute)(p["proposal_id"])
                    self.assertEqual(out["state"], state)
                    self.assertNotIn("private token", json.dumps(out))
                    self.assertEqual(getattr(self.kit, execute)(p["proposal_id"]), out)
                    self.assertEqual(len(self.backend.writes()), 1)
                    if state == "unknown":
                        with self.assertRaisesRegex(ReadError, "unresolved_write"):
                            self.kit.prepare_change(kind, self.parameters(kind))

    def test_one_time_requests_cannot_append_after_success(self):
        for kind, execute in self.executors().items():
            if kind == "payment":
                continue
            with self.subTest(kind=kind):
                self.setUp()
                p = self.review(kind)
                getattr(self.kit, execute)(p["proposal_id"])
                with self.assertRaisesRegex(ReadError, "ineligible_order_status"):
                    self.kit.prepare_change(kind, self.parameters(kind))
                self.assertEqual(len(self.backend.writes()), 1)

    def test_negative_difference_keeps_direction_and_other_saved_method(self):
        params = {"order_id": "#P0-NEGATIVE", "replacements": [{"existing_item_id": "p0_shirt_m_blue", "replacement_item_id": "p0_shirt_s_blue"}], "payment_method_id": "p0_paypal", "preserve_options": ["color"]}
        p = self.kit.prepare_change("exchange", params)
        self.assertLess(p["facts"]["calculations"][0]["result"], 0)
        out = self.kit.execute_exchange(p["proposal_id"])
        self.assertEqual(out["state"], "accepted")
        self.assertEqual(out["receipt"]["exchange"]["payment_method_id"], "p0_paypal")

    def test_price_availability_payment_or_target_changes_need_new_review(self):
        for change in ("price", "available", "payment"):
            self.setUp()
            p = self.review("modify_items")
            if change == "payment":
                self.backend.records["customers"][0]["payment_methods"].pop(1)
            else:
                self.backend.records["products"][0]["items"][1][change] = 30.0 if change == "price" else False
            with self.assertRaises(ReadError):
                self.kit.execute_item_modification(p["proposal_id"])
            self.assertEqual(self.backend.writes(), [])

    def test_wrong_financial_or_replacement_receipt_remains_unknown(self):
        p = self.review("modify_items")
        order = deepcopy(self.backend.records["orders"][0])
        receipt = {k: order[k] for k in ("order_id", "items", "payments")}
        receipt["status"] = "pending (items modified)"
        self.backend.overrides[("POST", "/v1/orders/%23P0-PENDING/item-modifications")] = WriteBackend.response(receipt)
        out = self.kit.execute_item_modification(p["proposal_id"])
        self.assertEqual(out["state"], "unknown")
        self.assertEqual(len(self.backend.writes()), 1)

    def test_submission_is_one_full_list_with_preserved_duplicate_targets(self):
        p = self.review("modify_items")
        call = self.decision(user("Only those two, proceed."), "execute_item_modification", {"proposal_id": p["proposal_id"]})
        self.host(call)
        self.assertEqual(len(self.backend.writes()), 1)
        self.assertEqual(len(self.backend.writes()[0]["body"]["replacements"]), 2)
