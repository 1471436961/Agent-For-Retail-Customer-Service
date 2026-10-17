"""Different products keep their own option constraints; REST receives ID pairs."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from tools import Tools
from fixtures.write_backend import WriteBackend
from support_agent.domain.identity import ReadError
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class ReplacementOptionTests(unittest.TestCase):
    def setUp(self):
        self.backend = WriteBackend()
        self.kit = Tools(ClientAPI(self.backend, context=ClientAPIContext(conversation_id="row-options")))
        self.kit.verify_customer(email="alex.p0@example.test")
        self.order = next(row for row in self.backend.records["orders"] if row["order_id"] == "#P0-DELIVERED")
        for item in self.order["items"]:
            if item["product_id"] == "p0_shirt":
                item["options"]["style"] = "crew neck"
        product = self.backend.records["products"][0]
        original = product["items"][0]
        original["options"]["style"] = "crew neck"
        self.shirt_target = {**deepcopy(original), "item_id": "synthetic-v-neck",
                             "options": {**original["options"], "style": "v-neck"}, "price": 22.0}
        product["items"].append(self.shirt_target)
        self.rows = [
            {"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": self.shirt_target["item_id"]},
            {"existing_item_id": "p0_mug_small", "replacement_item_id": "p0_mug_large"},
        ]

    def params(self, **extra):
        return {"order_id": self.order["order_id"], "payment_method_id": "p0_card", "preserve_options": [],
                "replacements": deepcopy(self.rows), **extra}

    def test_shared_union_applies_only_to_products_with_each_option(self):
        before = deepcopy(self.backend.records)
        result = self.kit.prepare_change("exchange", self.params(preserve_options=["size", "color"]))
        self.assertEqual(result["state"], "prepared")
        self.assertEqual(result["facts"]["body"]["replacements"], self.rows)
        self.assertEqual(self.backend.records, before)
        self.assertEqual(self.backend.writes(), [])

    def test_row_options_allow_one_product_color_change_without_changing_another(self):
        self.shirt_target["options"]["color"] = "black"
        rows = deepcopy(self.rows)
        rows[0]["preserve_options"] = ["size"]
        rows[1]["preserve_options"] = ["color"]
        result = self.kit.get_tools()["prepare_change"](kind="exchange", parameters=self.params(replacements=rows))
        self.assertEqual(result["facts"]["body"]["replacements"], self.rows)
        self.assertNotIn("preserve_options", result["facts"]["body"])
        self.assertTrue(all(set(row) == {"existing_item_id", "replacement_item_id"}
                            for row in result["facts"]["body"]["replacements"]))
        self.assertEqual(self.backend.writes(), [])

    def test_real_change_of_preserved_option_or_typo_is_still_rejected(self):
        for changed_key, preserved in (("size", ["size"]), ("color", ["color"])):
            original = deepcopy(self.shirt_target["options"])
            self.shirt_target["options"][changed_key] = "changed-value"
            with self.subTest(key=changed_key), self.assertRaisesRegex(ReadError, "required_option_changed"):
                self.kit.prepare_change("exchange", self.params(preserve_options=preserved))
            self.shirt_target["options"] = original
        with self.assertRaisesRegex(ReadError, "required_option_changed"):
            self.kit.prepare_change("exchange", self.params(preserve_options=["unknown-option"]))
        rows = deepcopy(self.rows)
        rows[0]["preserve_options"] = ["capacity"]  # belongs only to the other product
        with self.assertRaisesRegex(ReadError, "required_option_changed"):
            self.kit.prepare_change("exchange", self.params(replacements=rows))
        self.assertEqual(self.backend.writes(), [])

    def test_invalid_preserved_option_types_are_rejected_without_writes(self):
        for value in (None, "size", [True], [17]):
            with self.subTest(shared=value), self.assertRaises(ReadError):
                self.kit.prepare_change("exchange", self.params(preserve_options=value))
            rows = deepcopy(self.rows)
            rows[0]["preserve_options"] = value
            with self.subTest(row=value), self.assertRaises(ReadError):
                self.kit.prepare_change("exchange", self.params(replacements=rows))
        self.assertEqual(self.backend.writes(), [])

    def test_constraints_are_rechecked_before_execution(self):
        rows = deepcopy(self.rows)
        rows[0]["preserve_options"] = ["size"]
        rows[1]["preserve_options"] = ["color"]
        proposal = self.kit.prepare_change("exchange", self.params(replacements=rows))
        self.shirt_target["options"]["size"] = "different-size"
        with self.assertRaisesRegex(ReadError, "required_option_changed"):
            self.kit.execute_exchange(proposal["proposal_id"])
        self.assertEqual(self.backend.writes(), [])

    def test_two_product_exchange_is_one_write_with_only_rest_fields(self):
        rows = deepcopy(self.rows)
        rows[0]["preserve_options"] = ["size", "color"]
        rows[1]["preserve_options"] = ["color"]
        proposal = self.kit.prepare_change("exchange", self.params(replacements=rows))
        result = self.kit.execute_exchange(proposal["proposal_id"])
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(len(self.backend.writes()), 1)
        self.assertEqual(self.backend.writes()[0]["body"],
                         {"replacements": self.rows, "payment_method_id": "p0_card"})
        self.assertEqual(self.kit.execute_exchange(proposal["proposal_id"]), result)
        self.assertEqual(len(self.backend.writes()), 1)
