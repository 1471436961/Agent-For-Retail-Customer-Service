"""P3 general capabilities on synthetic records, including zero-write failures."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from support_agent.domain.identity import ReadError
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.backend = SyntheticBackend()
        self.api = ClientAPI(self.backend, context=ClientAPIContext(conversation_id="prepare"))
        self.kit = Tools(self.api)
        self.kit.verify_customer(email="alex.p0@example.test")
        self.address = {"address_line_1": "90 New Road", "city": "Boston", "region": "MA", "country": "USA", "postal_code": "02101"}

    def params(self, **extra):
        return {"order_id": "#P0-PENDING", "replacements": [
            {"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": "p0_shirt_m_blue"}],
            "payment_method_id": "p0_card", "preserve_options": ["color"], **extra}

    def assert_zero_writes(self):
        self.assertTrue(all(r["method"] == "GET" or r["path"] == "/v1/customers/search" for r in self.backend.calls))

    def test_seven_kinds_prepare_without_mutation(self):
        before = deepcopy(self.backend.records)
        requests = [("default_address", {"address": self.address}),
                    ("order_address", {"order_id": "#P0-PENDING", "address": self.address}),
                    ("cancel", {"order_id": "#P0-PENDING", "reason": "ordered by mistake"}),
                    ("payment", {"order_id": "#P0-PENDING", "payment_method_id": "p0_paypal"}),
                    ("modify_items", self.params()),
                    ("return", {"order_id": "#P0-DELIVERED", "item_ids": ["p0_shirt_s_blue"], "refund_payment_method_id": "p0_gift"}),
                    ("exchange", self.params(order_id="#P0-DELIVERED"))]
        for kind, params in requests:
            with self.subTest(kind=kind):
                p = self.kit.prepare_change(kind, params)
                self.assertEqual(p["state"], "prepared")
                self.assertEqual(p["facts"]["kind"], kind)
        self.assertEqual(self.backend.records, before)
        self.assert_zero_writes()

    def test_complete_address_and_no_extra_fields(self):
        for address in ({"city": "Boston"}, {**self.address, "notes": "text"}, {**self.address, "postal_code": "  "}):
            with self.subTest(address=address), self.assertRaises(ReadError):
                self.kit.prepare_change("default_address", {"address": address})
        self.assert_zero_writes()

    def test_invalid_reason_or_partial_scope_does_not_expand_to_whole_order(self):
        for params in ({"order_id": "#P0-PENDING", "reason": "cheaper elsewhere"},
                       {"order_id": "#P0-PENDING", "reason": "no longer needed", "item_ids": ["x"]}):
            with self.assertRaises(ReadError):
                self.kit.prepare_change("cancel", params)
        self.assert_zero_writes()

    def test_all_ineligible_states_and_foreign_order(self):
        for identifier in ("#P0-MODIFIED", "#P0-PROCESSED", "#P0-DELIVERED", "#P0-CANCELLED", "#P0-EXCHANGE", "#P0-RETURN", "#P0-FOREIGN"):
            with self.subTest(identifier=identifier), self.assertRaises(ReadError):
                self.kit.prepare_change("cancel", {"order_id": identifier, "reason": "no longer needed"})
        self.assert_zero_writes()

    def test_duplicates_and_different_targets_are_not_deduplicated(self):
        product = self.backend.records["products"][0]
        product["items"][2]["available"] = True
        rows = [self.params()["replacements"][0], {"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": "p0_shirt_m_black"}]
        p = self.kit.prepare_change("modify_items", self.params(replacements=rows, preserve_options=[]))
        self.assertEqual(p["facts"]["body"]["replacements"], rows)
        self.assertEqual(p["facts"]["calculations"][0]["result"], 8)
        with self.assertRaises(ReadError):
            self.kit.prepare_change("modify_items", self.params(replacements=rows + rows))

    def test_cross_product_unavailable_unchanged_and_preserved_options(self):
        for target, preserve in (("p0_mug_large", []), ("p0_shirt_m_black", []), ("p0_shirt_s_blue", []), ("p0_shirt_m_blue", ["size"])):
            with self.subTest(target=target), self.assertRaises(ReadError):
                self.kit.prepare_change("modify_items", self.params(replacements=[{"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": target}], preserve_options=preserve))
        self.assert_zero_writes()

    def test_specific_duplicate_instance_is_unaddressable(self):
        with self.assertRaisesRegex(ReadError, "exact_instance_not_addressable"):
            self.kit.prepare_change("modify_items", self.params(order_id="#P0-AMBIGUOUS", specific_instance=True))

    def test_calculator_rounding_sign_order_and_invalid_numbers(self):
        for value, expected in ((1.125, 1.12), (1.375, 1.38), (2.675, 2.67), (-1.125, -1.12)):
            self.assertEqual(self.kit.calculate("round", [value])["result"], expected)
        self.assertEqual(self.kit.calculate("price_difference", [10, 20, 25, 20])["result"], -5)
        for values in ([], [True], ["5"], [None], [float("nan")], [float("inf")]):
            with self.assertRaises(ReadError):
                self.kit.calculate("sum", values)

    def test_whole_payment_balance_and_positive_difference_balance(self):
        with self.assertRaisesRegex(ReadError, "insufficient_gift_balance"):
            self.kit.prepare_change("payment", {"order_id": "#P0-PENDING", "payment_method_id": "p0_gift_low"})
        self.assertEqual(self.kit.prepare_change("modify_items", self.params(payment_method_id="p0_gift_low"))["facts"]["calculations"][0]["result"], 5)
        for method in ("unknown", "p0_card"):
            with self.assertRaises(ReadError):
                self.kit.prepare_change("payment", {"order_id": "#P0-PENDING", "payment_method_id": method})

    def test_return_destination_and_original_prices(self):
        params = {"order_id": "#P0-DELIVERED", "item_ids": ["p0_shirt_s_blue", "p0_shirt_s_blue"], "refund_payment_method_id": "p0_card"}
        self.assertEqual(self.kit.prepare_change("return", params)["facts"]["calculations"][0]["result"], 40)
        for method in ("p0_paypal", "new_gift"):
            if method == "new_gift":
                self.backend.records["customers"][0]["payment_methods"].append({"id": method, "source": "gift_card", "balance": 0.0})
                self.kit.get_customer_summary()
            with self.assertRaisesRegex(ReadError, "ineligible_refund_destination"):
                self.kit.prepare_change("return", {**params, "refund_payment_method_id": method})

    def test_comparison_numeric_units_preservation_ties_and_missing(self):
        result = self.kit.compare_variants("p0_mug", {"color": "white"}, "capacity", True, "ml")
        self.assertEqual([i["item_id"] for i in result["candidates"]], ["p0_mug_large", "p0_mug_small"])
        self.assertEqual(self.kit.compare_variants("p0_shirt", {"color": "black"})["candidates"], [])
        for sort, unit in (("capacity", "liters"), ("missing", "ml"), ("size", "")):
            with self.assertRaises(ReadError):
                self.kit.compare_variants("p0_mug", {}, sort, False, unit)
        self.backend.records["products"][1]["items"][1]["price"] = 10.0
        self.assertEqual(self.kit.compare_variants("p0_mug", {})["candidates"][0]["item_id"], "p0_mug_small")

    def test_proposals_replay_and_supersede_without_shared_mutability(self):
        one = self.kit.prepare_change("default_address", {"address": self.address})
        one["facts"]["body"]["city"] = "tampered"
        two = self.kit.prepare_change("default_address", {"address": self.address})
        self.assertEqual(self.kit._proposals["proposal-1"]["state"], "prepared")
        self.assertEqual(two["proposal_id"], "proposal-1")
        self.assertEqual(self.kit._proposals["proposal-1"]["facts"]["body"]["city"], "Boston")
        fresh = Tools(ClientAPI(SyntheticBackend(), context=ClientAPIContext(conversation_id="prepare")))
        fresh.verify_customer(email="alex.p0@example.test")
        fresh.prepare_change("default_address", {"address": self.address})
        self.assertEqual(two, fresh.prepare_change("default_address", {"address": self.address}))
        changed = self.kit.prepare_change("default_address", {"address": {**self.address, "city": "New York"}})
        self.assertEqual(changed["proposal_id"], "proposal-2")
        self.assertEqual(self.kit._proposals["proposal-1"]["state"], "superseded")
        self.assertEqual(two["state"], "prepared")
