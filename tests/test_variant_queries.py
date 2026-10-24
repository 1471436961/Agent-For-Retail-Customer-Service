"""Structured catalog queries on synthetic records; no provider is called."""
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from support_agent.domain.identity import ReadError
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class VariantQueryTests(unittest.TestCase):
    def setUp(self):
        self.backend = SyntheticBackend()
        self.product = self.backend.records["products"][0]
        self.product["items"] = [
            {"item_id": identifier, "options": {"tone": tone, "capacity": capacity},
             "available": available, "price": price}
            for identifier,tone,capacity,available,price in (
                ("query-a","warm","10ml",True,10.0),
                ("query-b","warm","20ml",True,18.0),
                ("query-c","cool","30ml",True,18.0),
                ("query-d","warm","40ml",False,30.0),
                ("query-e","warm","25ml",True,18.0))]
        self.kit = Tools(ClientAPI(self.backend,context=ClientAPIContext(conversation_id="synthetic-query")))
        self.product_id = self.product["product_id"]

    def verify(self):
        self.kit.verify_customer(email=self.backend.records["customers"][0]["email"])

    def query(self, **criteria):
        return self.kit.query_product_variants([{"product_id":self.product_id,**criteria}])["queries"][0]

    def test_identity_and_all_query_shapes_are_checked_before_catalog_reads(self):
        with self.assertRaisesRegex(ReadError,"identity_required"):
            self.query(filters={"tone":"warm"})
        self.assertEqual(self.backend.calls,[])
        self.verify()
        before = len(self.backend.calls)
        for invalid in ({"limit":0},{"limit":True},{"sort_by":"capacity"},
                        {"numeric_filters":[{"option":"capacity","operator":"gt","value":15,"unit":""}]}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ReadError,"invalid_variant_query"):
                self.kit.query_product_variants([{"product_id":self.product_id},
                                                 {"product_id":self.product_id,**invalid}])
        self.assertEqual(len(self.backend.calls),before)

    def test_price_rank_limit_and_ties_preserve_actual_order_and_stock(self):
        self.verify()
        row = self.query(filters={"tone":"warm"},descending=True,limit=1)
        self.assertEqual(row["state"],"complete")
        self.assertEqual([i["item_id"] for i in row["candidates"]],["query-b"])
        self.assertEqual((row["matching_variant_count"],row["available_matching_count"],row["unavailable_matching_count"]),(4,3,1))
        self.assertTrue(row["has_more"])
        all_rows = self.query(filters={"tone":"warm"},descending=True)
        self.assertEqual([i["item_id"] for i in all_rows["candidates"]],["query-b","query-e","query-a"])
        self.assertFalse(all_rows["has_more"])

    def test_numeric_constraints_use_explicit_units_and_independent_sorting(self):
        self.verify()
        row = self.query(filters={"tone":"warm"},numeric_filters=[
            {"option":"capacity","operator":"gt","value":15,"unit":"ml"},
            {"option":"capacity","operator":"lte","value":30,"unit":"ml"}],
            sort_by="capacity",numeric_unit="ml",descending=True)
        self.assertEqual([i["item_id"] for i in row["candidates"]],["query-e","query-b"])
        self.assertEqual(row["available_matching_count"],2)

    def test_unknown_vocabulary_is_not_an_out_of_stock_answer(self):
        self.verify()
        invalid = self.query(filters={"tone":"tepid"})
        self.assertEqual(invalid["state"],"invalid_criteria")
        self.assertEqual(invalid["option_values"]["tone"],["warm","cool"])
        self.assertNotIn("available_matching_count",invalid)
        no_configuration = self.query(filters={"tone":"cool","capacity":"10ml"})
        self.assertEqual(no_configuration["matching_variant_count"],0)
        out_of_stock = self.query(filters={"tone":"warm","capacity":"40ml"})
        self.assertEqual((out_of_stock["matching_variant_count"],out_of_stock["available_matching_count"],
                          out_of_stock["unavailable_matching_count"]),(1,0,1))

    def test_batch_reuses_real_observations_and_refreshes_once_per_product(self):
        self.verify()
        spec = {"product_id":self.product_id,"filters":{"tone":"warm"},"descending":True,"limit":1}
        first = self.kit.query_product_variants([spec,spec])
        self.assertEqual([r["source"] for r in first["queries"]],["fresh_read","fresh_read"])
        catalog_reads = lambda: [r for r in self.backend.calls if r["path"].startswith("/v1/catalog/products/")]
        self.assertEqual(len(catalog_reads()),1)
        self.product["items"][0]["price"] = 99.0
        cached = self.kit.query_product_variants([spec])["queries"][0]
        self.assertEqual(cached["source"],"observed_catalog")
        self.assertEqual(cached["candidates"][0]["item_id"],"query-b")
        current = self.kit.query_product_variants([spec,spec],refresh=True)
        self.assertEqual(current["queries"][0]["candidates"][0]["item_id"],"query-a")
        self.assertEqual(len(catalog_reads()),2)
        self.assertFalse(any(r["method"]!="GET" and r["path"]!="/v1/customers/search" for r in self.backend.calls))

    def test_mixed_units_fail_instead_of_guessing_a_conversion(self):
        self.product["items"][1]["options"]["capacity"] = "1L"
        self.verify()
        with self.assertRaisesRegex(ReadError,"uncomparable_catalog_option"):
            self.query(numeric_filters=[{"option":"capacity","operator":"gt","value":15,"unit":"ml"}])

    def test_result_mutation_cannot_change_observed_catalog_records(self):
        self.verify()
        before = deepcopy(self.product)
        row = self.query(filters={"tone":"warm"})
        row["candidates"][0]["price"] = 0.0
        row["candidates"][0]["options"]["tone"] = "fabricated"
        self.assertEqual(self.product,before)
        self.assertEqual(self.kit._observed_products[self.product_id],before)


if __name__ == "__main__":
    unittest.main()
