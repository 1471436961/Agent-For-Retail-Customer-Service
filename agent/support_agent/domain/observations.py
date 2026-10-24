"""Freeze actual earlier reads before a confirmed write is revalidated.

This contains no user-text interpretation. The model decides whether the real
recap and customer reply authorize the parameters; the toolkit checks facts.
"""
from copy import deepcopy
from support_agent.domain.identity import ReadError


class ObservedFacts:
    def __init__(self, toolkit):
        self.customer_id = toolkit._customer_id()
        self.profile = deepcopy(toolkit._observed_profile)
        self.orders = deepcopy(toolkit._observed_orders)
        self.products = deepcopy(toolkit._observed_products)
        self._opening_gift_ids = deepcopy(toolkit._opening_gift_ids)

    def _profile(self):
        if not self.profile or self.profile["customer_id"] != self.customer_id:
            raise ReadError("read_customer_facts_before_submission")
        return deepcopy(self.profile)

    def get_owned_order(self, order_id):
        if order_id not in self.orders:
            raise ReadError("read_order_facts_before_submission")
        order = self.orders[order_id]
        if order["customer_id"] != self.customer_id:
            raise ReadError("order_access_denied")
        return deepcopy(order)

    def get_product_variants(self, product_id):
        if product_id not in self.products:
            raise ReadError("read_replacement_variants_before_submission")
        return deepcopy(self.products[product_id])
