"""Freeze actual earlier reads before a confirmed write is revalidated.

This contains no user-text interpretation. The model decides whether the real
recap and customer reply authorize the parameters; the toolkit checks facts.
"""
from copy import deepcopy
from support_agent.domain.identity import ReadError


class CurrentFacts:
    """Load each required current record once in one write preflight.

    This snapshot is never reused across writes or customer turns. The toolkit
    retains the actual observations for revision after a rejected stale quote.
    """
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.profile = None
        self.orders = {}
        self.products = {}

    @property
    def _opening_gift_ids(self):
        return self.toolkit._opening_gift_ids

    def _profile(self):
        if self.profile is None:
            self.profile = self.toolkit._profile()
        return deepcopy(self.profile)

    def get_owned_order(self, order_id):
        if order_id not in self.orders:
            self.orders[order_id] = self.toolkit._read_owned_order(order_id, self._profile())
        return deepcopy(self.orders[order_id])

    def get_product_variants(self, product_id):
        if product_id not in self.products:
            self.products[product_id] = self.toolkit.get_product_variants(product_id)
        return deepcopy(self.products[product_id])


class ObservedFacts:
    def __init__(self, toolkit, *, load_missing=False):
        self.toolkit = toolkit
        self.load_missing = load_missing
        self.customer_id = toolkit._customer_id()
        self.profile = deepcopy(toolkit._observed_profile)
        self.orders = deepcopy(toolkit._observed_orders)
        self.products = deepcopy(toolkit._observed_products)
        self._opening_gift_ids = deepcopy(toolkit._opening_gift_ids)

    def _profile(self):
        if self.profile is None and self.load_missing:
            self.profile = self.toolkit._profile()
            self._opening_gift_ids = deepcopy(self.toolkit._opening_gift_ids)
        if not self.profile or self.profile["customer_id"] != self.customer_id:
            raise ReadError("read_customer_facts_before_submission")
        return deepcopy(self.profile)

    def get_owned_order(self, order_id):
        if order_id not in self.orders and self.load_missing:
            self.orders[order_id] = self.toolkit._read_owned_order(order_id, self._profile())
        if order_id not in self.orders:
            raise ReadError("read_order_facts_before_submission")
        order = self.orders[order_id]
        if order["customer_id"] != self.customer_id:
            raise ReadError("order_access_denied")
        return deepcopy(order)

    def get_product_variants(self, product_id):
        if product_id not in self.products and self.load_missing:
            self.products[product_id] = self.toolkit.get_product_variants(product_id)
        if product_id not in self.products:
            raise ReadError("read_replacement_variants_before_submission")
        return deepcopy(self.products[product_id])
