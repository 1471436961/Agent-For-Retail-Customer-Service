"""Session-authorized tools; model-provided IDs never constitute authentication."""

from threading import RLock
from copy import deepcopy

from tau2.environment.toolkit import ToolType, is_tool
from tau2.hyper.client_api import ClientAPIToolKitBase
from support_agent.adapters.read_api import (
    read_catalog_item, read_customer, read_order, read_product, read_products, search_customer,
)
from support_agent.domain.identity import (
    IdentityState, ReadError, identity_selector, resource_id, verification_failed,
)
from support_agent.domain.policy import service_policy

READ_ACTION_NAMES = (
    "verify_customer", "lookup_customer", "get_customer_summary", "list_customer_orders",
    "get_owned_order", "list_products", "get_product_variants", "get_catalog_item",
    "get_catalog_summary",
)
VERIFICATION_ACTION_NAMES = frozenset({"verify_customer", "lookup_customer"})


class CustomerTools(ClientAPIToolKitBase):
    """Only six read REST operations, gated by one toolkit-owned customer."""

    def __init__(self, client_api):
        super().__init__(client_api)
        self._identity = IdentityState()
        self._conversation_id = None
        self._lock = RLock()
        self._observed_profile = None
        self._observed_orders = {}
        self._observed_products = {}

    def _sync_session(self):
        # The host creates a fresh toolkit for each trial. Ordinary customer
        # endpoints do not take a conversation ID, so optional host context
        # must not prevent these requests. Still clear state when a supplied
        # context changes (or disappears); never invent an ID for transfers.
        try:
            conversation_id = self.client_api.context.conversation_id
        except RuntimeError as error:
            if str(error) != "Client API context has not been initialized":
                raise
            conversation_id = None
        if self._conversation_id != conversation_id:
            self._identity = IdentityState()
            self._observed_profile = None
            self._observed_orders = {}
            self._observed_products = {}
            self._conversation_id = conversation_id

    def _customer_id(self):
        self._sync_session()
        self._assert_service_active()
        if self._identity.customer_id is None:
            raise ReadError("identity_required")
        return self._identity.customer_id

    def _assert_service_active(self):
        """Extension hook for terminal handoff, without changing identity policy."""

    def _profile(self):
        profile = read_customer(self.client_api, self._customer_id())
        self._observed_profile = deepcopy(profile)
        return profile

    @is_tool(ToolType.READ)
    def verify_customer(self, email: str = "", first_name: str = "", last_name: str = "",
                        postal_code: str = "", claimed_customer_id: str | None = None,
                        order_id: str = "", include_product_variants: bool = False) -> dict:
        """Verify the customer's independently supplied identity, then read their profile.

        Provide email alone OR all of first_name, last_name and postal_code. An
        claimed_customer_id must match the independent search when supplied; use
        null only when the customer has not supplied an account ID. A customer
        ID alone never verifies identity. This conversation cannot switch customers.
        If search verifies identity but profile loading fails, returns a verified
        receipt with profile_state="unavailable" and profile_error; no profile facts.
        Recover with get_customer_summary rather than repeating the search.
        If the customer already supplied an order ID, include it as order_id
        to read requested_order AFTER successful independent verification and
        ownership checks, in this same read-only action. For an exchange/item
        change also set include_product_variants=true. This returns the actual
        order/catalog/policy evidence without extra model/tool round trips.
        The optional order is not an identity selector. A failed optional read
        returns requested_order_error without discarding verified profile facts.
        """
        selector = identity_selector(email, first_name, last_name, postal_code)
        if claimed_customer_id is not None and not isinstance(claimed_customer_id, str):
            raise ReadError("invalid_resource_identifier")
        if isinstance(claimed_customer_id, str) and not claimed_customer_id.strip():
            raise ReadError("invalid_resource_identifier")
        with self._lock:
            self._sync_session()
            self._assert_service_active()
            if (self._identity.customer_id is not None and claimed_customer_id and
                    claimed_customer_id != self._identity.customer_id):
                raise verification_failed()
            try:
                found = search_customer(self.client_api, selector)["customer_id"]
            except ReadError as error:
                if error.http_status == 404:
                    raise verification_failed() from None
                raise
            if (not found.strip() or (claimed_customer_id and found != claimed_customer_id) or
                    (self._identity.customer_id is not None and found != self._identity.customer_id)):
                raise verification_failed()
            # Search is the verification authority. Lock the identity before the
            # detail read; even a later transport/schema failure cannot switch it.
            self._identity.customer_id = found
            try:
                profile = self._profile()
            except ReadError as error:
                # Independent verification succeeded. Preserve that evidence
                # without claiming profile data was loaded or inventing values.
                return {"customer_id": found,
                        "verification": {"state": "verified", "profile_state": "unavailable"},
                        "profile_error": {"code": error.code, "operation": error.operation,
                                          "http_status": error.http_status}}
            if order_id:
                try:
                    profile["requested_order"] = self.get_owned_order(order_id, include_product_variants)
                except ReadError as error:
                    profile["requested_order_error"] = {"code": error.code, "operation": error.operation,
                                                        "http_status": error.http_status}
            return profile

    @is_tool(ToolType.READ)
    def lookup_customer(self, customer_id: str, email: str = "") -> dict:
        """Compatibility email lookup; uses the same verification and session guard.

        Prefer verify_customer for email or full-name/postal-code verification.
        customer_id is only an optional claim; use an empty string when absent.
        """
        return self.verify_customer(email=email, claimed_customer_id=customer_id or None)

    @is_tool(ToolType.READ)
    def get_customer_summary(self) -> dict:
        """Read the verified customer's profile, saved payments and own order IDs.

        No customer_id parameter is accepted; the identity comes from this toolkit.
        """
        with self._lock:
            return self._profile()

    @is_tool(ToolType.READ)
    def list_customer_orders(self, include_details: bool = False) -> dict:
        """List the verified customer's orders; optionally read every owned order.

        Use include_details=true when the customer does not know which order
        contains their items. The model matches requested items from actual
        records instead of asking the customer to guess from order IDs.
        Uses existing getOrder calls only, checks every order's ownership, and
        preserves occurrences, recorded prices and statuses. No inferred dates
        or recency ordering. A failed read fails the call, never silently drops
        an order or returns a falsely complete result. Performs no writes.
        """
        with self._lock:
            profile = self._profile()
            result = {"order_ids": profile["order_ids"]}
            if include_details:
                orders = []
                for identifier in profile["order_ids"]:
                    order = read_order(self.client_api, identifier)
                    if order["customer_id"] != self._customer_id():
                        raise ReadError("order_access_denied")
                    orders.append(order)
                    self._observed_orders[identifier] = deepcopy(order)
                result["orders"] = orders
                kinds = set()
                for order in orders:
                    if order["status"] == "delivered":
                        kinds.update(("return", "exchange"))
                    elif order["status"] == "pending":
                        kinds.update(("order_address", "cancel", "payment", "modify_items"))
                result["change_policies"] = {kind: service_policy(kind) for kind in sorted(kinds)}
            return result

    @is_tool(ToolType.READ)
    def get_owned_order(self, order_id: str, include_product_variants: bool = False) -> dict:
        """Read one of the verified customer's orders, preserving all occurrences.

        Checks the current own-order list before requesting the order, then checks
        actual customer ownership before returning any order data. No partial
        cancellation, refund or other write is performed.
        Copy the complete order ID exactly, including a leading #; the tool
        encodes it for the URL. A response schema/type failure is not an ID error.
        Never strip punctuation or try a fabricated alternative identifier.
        For item changes or exchanges set include_product_variants=true. This
        reads the actual variant catalogs of this order's distinct products in
        the same read-only action, under product_variants. Choose replacements
        from those results rather than fetching each catalog in extra turns.
        It makes only the existing owned-order and catalog GET requests, never
        a write, and preserves the original order fields and duplicate items.
        """
        order_id = resource_id(order_id)
        with self._lock:
            profile = self._profile()
            if order_id not in profile["order_ids"]:
                raise ReadError("order_access_denied")
            order = read_order(self.client_api, order_id)
            if order["customer_id"] != self._customer_id():
                raise ReadError("order_access_denied")
            self._observed_orders[order_id] = deepcopy(order)
            if include_product_variants:
                products = {}
                for item in order["items"]:
                    identifier = item["product_id"]
                    if identifier not in products:
                        products[identifier] = self.get_product_variants(identifier)
                kinds = ("return", "exchange") if order["status"] == "delivered" else (
                    "order_address", "cancel", "payment", "modify_items") if order["status"] == "pending" else ()
                return {**order, "product_variants": list(products.values()),
                        "change_policies": {kind: service_policy(kind) for kind in kinds}}
            return order

    @is_tool(ToolType.READ)
    def list_products(self) -> dict:
        """Read the complete public product list after identity verification.

        Returns product types and names, not variant or available-variant counts.
        No undocumented pagination or filters are sent to the API.
        """
        with self._lock:
            self._customer_id()
            return read_products(self.client_api)

    @is_tool(ToolType.READ)
    def get_product_variants(self, product_id: str) -> dict:
        """Read all variants, options, availability and current prices of a product.

        Public catalog products need not appear in the customer's purchased orders.
        This does not grant permission to buy, modify quantity or access other accounts.
        variant_count includes out-of-stock variants; available_variant_count
        counts only actual available=true variants. These are computed locally
        from the returned records, never guessed or fixed to a benchmark answer.
        """
        with self._lock:
            self._customer_id()
            product = read_product(self.client_api, product_id)
            self._observed_products[product_id] = deepcopy(product)
            return {**product, "variant_count": len(product["items"]),
                    "available_variant_count": sum(item["available"] for item in product["items"])}

    @is_tool(ToolType.READ)
    def get_catalog_item(self, item_id: str) -> dict:
        """Read one public variant's options, availability and current price.

        The response has no product_id. Use get_product_variants to establish a
        product relationship; never infer it by parsing item_id text.
        """
        with self._lock:
            self._customer_id()
            return read_catalog_item(self.client_api, item_id)

    @is_tool(ToolType.READ)
    def get_catalog_summary(self, product_id: str = "") -> dict:
        """Count product types, total variants and available variants separately.

        For a named product, first find its real ID, then pass product_id to
        count that product only (e.g. available T-shirt variants). Empty
        product_id counts the whole store using every product's details.
        Counts are derived
        from actual records; they are tool output fields, not additional REST
        fields. Failure yields no partial or hardcoded count. Performs no writes.
        """
        with self._lock:
            self._customer_id()
            ids = ([resource_id(product_id)] if product_id else
                   [product["product_id"] for product in read_products(self.client_api)["products"]])
            if len(ids) != len(set(ids)):
                raise ReadError("invalid_read_response", operation="listProducts")
            products = [read_product(self.client_api, product_id) for product_id in ids]
            self._observed_products.update({product["product_id"]: deepcopy(product) for product in products})
            items = [item for product in products for item in product["items"]]
            return {"product_type_count": len(products), "variant_count": len(items),
                    "available_variant_count": sum(item["available"] for item in items),
                    "products": products}
