"""Session-authorized tools; model-provided IDs never constitute authentication."""

from threading import RLock

from tau2.environment.toolkit import ToolType, is_tool
from tau2.hyper.client_api import ClientAPIToolKitBase
from support_agent.adapters.read_api import (
    read_catalog_item, read_customer, read_order, read_product, read_products, search_customer,
)
from support_agent.domain.identity import (
    IdentityState, ReadError, identity_selector, resource_id, verification_failed,
)

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
        return read_customer(self.client_api, self._customer_id())

    @is_tool(ToolType.READ)
    def verify_customer(self, email: str = "", first_name: str = "", last_name: str = "",
                        postal_code: str = "", claimed_customer_id: str | None = None) -> dict:
        """Verify the customer's independently supplied identity, then read their profile.

        Provide email alone OR all of first_name, last_name and postal_code. An
        claimed_customer_id must match the independent search when supplied; use
        null only when the customer has not supplied an account ID. A customer
        ID alone never verifies identity. This conversation cannot switch customers.
        If search verifies identity but profile loading fails, returns a verified
        receipt with profile_state="unavailable" and profile_error; no profile facts.
        Recover with get_customer_summary rather than repeating the search.
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
                return self._profile()
            except ReadError as error:
                # Independent verification succeeded. Preserve that evidence
                # without claiming profile data was loaded or inventing values.
                return {"customer_id": found,
                        "verification": {"state": "verified", "profile_state": "unavailable"},
                        "profile_error": {"code": error.code, "operation": error.operation,
                                          "http_status": error.http_status}}

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
    def list_customer_orders(self) -> dict:
        """List all order identifiers in the verified customer's current profile.

        No inferred dates or recency ordering are added. Select relevant IDs with
        the customer, then use get_owned_order to obtain current order details.
        """
        with self._lock:
            return {"order_ids": self._profile()["order_ids"]}

    @is_tool(ToolType.READ)
    def get_owned_order(self, order_id: str) -> dict:
        """Read one of the verified customer's orders, preserving all occurrences.

        Checks the current own-order list before requesting the order, then checks
        actual customer ownership before returning any order data. No partial
        cancellation, refund or other write is performed.
        """
        order_id = resource_id(order_id)
        with self._lock:
            profile = self._profile()
            if order_id not in profile["order_ids"]:
                raise ReadError("order_access_denied")
            order = read_order(self.client_api, order_id)
            if order["customer_id"] != self._customer_id():
                raise ReadError("order_access_denied")
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
        """
        with self._lock:
            self._customer_id()
            return read_product(self.client_api, product_id)

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
    def get_catalog_summary(self) -> dict:
        """Count all product types, all variants and available variants separately.

        Reads the complete list and every product's details. Counts are derived
        from actual records; they are tool output fields, not additional REST
        fields. Failure yields no partial or hardcoded count. Performs no writes.
        """
        with self._lock:
            self._customer_id()
            summaries = read_products(self.client_api)["products"]
            ids = [product["product_id"] for product in summaries]
            if len(ids) != len(set(ids)):
                raise ReadError("invalid_read_response", operation="listProducts")
            products = [read_product(self.client_api, product_id) for product_id in ids]
            items = [item for product in products for item in product["items"]]
            return {"product_type_count": len(products), "variant_count": len(items),
                    "available_variant_count": sum(item["available"] for item in items),
                    "products": products}
