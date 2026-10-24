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
from support_agent.domain.money import calculate_values

READ_ACTION_NAMES = (
    "verify_customer", "lookup_customer", "get_customer_summary", "list_customer_orders",
    "get_owned_order", "list_products", "get_product_variants", "get_catalog_item",
    "get_catalog_summary", "read_products", "read_context",
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

    @staticmethod
    def _order_view(order):
        """Add a general record subtotal, without deciding refund eligibility.

        Raw observed records remain unchanged. Every item occurrence contributes
        its recorded price; no payment/refund netting or category inference.
        """
        result = deepcopy(order)
        prices = [item["price"] for item in order["items"]]
        result["item_price_total"] = (calculate_values("add", prices, 2) if prices else
            {"operation": "add", "inputs": [], "result": 0.0, "decimal_places": 2})
        result["item_price_total"]["scope"] = "All recorded item occurrences; not refund eligibility or settlement. A selected subset needs its own calculation."
        return result

    @is_tool(ToolType.READ)
    def verify_customer(self, email: str = "", first_name: str = "", last_name: str = "",
                        postal_code: str = "", claimed_customer_id: str | None = None,
                        order_id: str = "", include_product_variants: bool = False,
                        include_order_details: bool = False, product_names: list[str] | None = None) -> dict:
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
        This initial display reference can omit a single leading # only when
        it uniquely selects an ID in the independently verified own-order list.
        The returned canonical ID, never a constructed ID, is used for the read.
        include_order_details=true additionally reads the verified customer's
        owned orders when the request needs discovery. Exact product_names can
        select requested catalog facts/counts in the same action. These optional
        reads happen ONLY after successful independent verification; actual
        records are returned as requested_context, not guessed from the request.
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
                    selected = self._initial_order_reference(resource_id(order_id), profile["order_ids"])
                    profile["requested_order"] = self._read_owned_order(
                        selected, profile, include_product_variants)
                    if selected != order_id:
                        profile["requested_order_reference"] = {"input": order_id, "order_id": selected}
                except ReadError as error:
                    profile["requested_order_error"] = {"code": error.code, "operation": error.operation,
                                                        "http_status": error.http_status}
            if include_order_details or product_names:
                try:
                    profile["requested_context"] = self.read_context(
                        include_order_details=include_order_details, product_names=product_names)
                except ReadError as error:
                    profile["requested_context_error"] = {"code": error.code, "operation": error.operation,
                                                          "http_status": error.http_status}
            return profile

    @staticmethod
    def _initial_order_reference(reference, owned_ids):
        """Select a record, not an identifier spelling to probe against the API.

        Only the intake display form permits an omitted #. All later reads and
        writes still require an exact observed ID. Exact matches take priority;
        no case folding, URL decoding, fuzzy matching or foreign-order search.
        """
        if reference in owned_ids:
            return reference
        matches = [value for value in dict.fromkeys(owned_ids)
                   if not reference.startswith("#") and value.startswith("#") and value[1:] == reference]
        if len(matches) != 1:
            raise ReadError("order_access_denied")
        return matches[0]

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
                    orders.append(self._order_view(order))
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
            return self._read_owned_order(order_id, profile, include_product_variants)

    def _read_owned_order(self, order_id, profile, include_product_variants=False):
        """Reuse the profile read in THIS operation, never a cross-turn cache."""
        customer_id = self._customer_id()
        if profile["customer_id"] != customer_id or order_id not in profile["order_ids"]:
            raise ReadError("order_access_denied")
        order = read_order(self.client_api, order_id)
        if order["customer_id"] != customer_id:
            raise ReadError("order_access_denied")
        self._observed_orders[order_id] = deepcopy(order)
        kinds = ("return", "exchange") if order["status"] == "delivered" else (
            "order_address", "cancel", "payment", "modify_items") if order["status"] == "pending" else ()
        result = self._order_view(order)
        if include_product_variants:
            # Explicit related-catalog selection; never an automatic prefetch.
            products = {}
            for item in order["items"]:
                identifier = item["product_id"]
                if identifier not in products:
                    products[identifier] = self.get_product_variants(identifier)
            result["product_variants"] = list(products.values())
            result["change_policies"] = {kind: service_policy(kind) for kind in kinds}
        return result

    @is_tool(ToolType.READ)
    def read_context(self, order_ids: list[str] | None = None, include_order_details: bool = False,
                     product_ids: list[str] | None = None, product_names: list[str] | None = None) -> dict:
        """Read the explicitly selected orders and catalogs together, without writing.

        Use this for several independent reads already needed by the customer's
        request, instead of separate model/tool turns. include_order_details=true
        reads all owned orders when their IDs are unknown; otherwise order_ids
        selects exact IDs from the verified profile. Do not combine those scopes.
        product_ids or exact product_names select only relevant public catalogs;
        each product includes total and available variant counts. Orders do not
        automatically select catalogs. One profile is used for ownership checks
        within this read; every order's actual owner is checked too. Failure
        yields no partial response. No consent or business write is involved.
        """
        if (type(include_order_details) is not bool or
                (order_ids is not None and not isinstance(order_ids, list))):
            raise ReadError("invalid_context_selection")
        if include_order_details and order_ids:
            raise ReadError("choose_all_or_explicit_orders")
        identifiers = [resource_id(value) for value in (order_ids or [])]
        if not (include_order_details or identifiers or product_ids or product_names):
            raise ReadError("context_selection_required")
        with self._lock:
            self._customer_id()
            result = {}
            if include_order_details or identifiers:
                profile = self._profile()
                selected = profile["order_ids"] if include_order_details else identifiers
                result.update(customer_id=profile["customer_id"], order_ids=profile["order_ids"],
                    orders=[self._read_owned_order(value, profile) for value in dict.fromkeys(selected)])
            if product_ids or product_names:
                result.update(self.read_products(product_ids=product_ids, product_names=product_names))
            return result

    @is_tool(ToolType.READ)
    def read_products(self, product_ids: list[str] | None = None,
                      product_names: list[str] | None = None) -> dict:
        """Read explicitly selected products' variants, prices and counts.

        Select actual product IDs from orders or list_products, or supply exact
        catalog product names. Name selection reads the catalog index and uses
        case-insensitive exact equality, never fuzzy matching or language parsing.
        If a name is missing or ambiguous, no product is guessed. Only these
        catalogs are fetched in one host action; unrelated order products are
        not loaded. Every record must succeed, with no falsely complete partial
        result. This is read-only and needs no customer permission.
        """
        with self._lock:
            self._customer_id()
            identifiers = self._selected_product_ids(product_ids, product_names)
            products = {}
            for identifier in identifiers:
                if identifier not in products:
                    products[identifier] = self.get_product_variants(identifier)
            return {"products": list(products.values())}

    def _selected_product_ids(self, product_ids, product_names):
        if ((product_ids is not None and not isinstance(product_ids, list)) or
                (product_names is not None and not isinstance(product_names, list))):
            raise ReadError("invalid_catalog_selection")
        identifiers = [resource_id(value) for value in (product_ids or [])]
        if product_names:
            if any(not isinstance(name, str) or not name.strip() for name in product_names):
                raise ReadError("invalid_catalog_selection")
            catalog = read_products(self.client_api)["products"]
            for name in product_names:
                matches = [row["product_id"] for row in catalog if row["name"].casefold() == name.strip().casefold()]
                if len(matches) != 1:
                    raise ReadError("catalog_name_not_unique_use_list_products")
                identifiers.append(matches[0])
        if not identifiers:
            raise ReadError("product_ids_or_names_required")
        return list(dict.fromkeys(identifiers))

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
    def get_catalog_summary(self, product_id: str = "", product_name: str = "") -> dict:
        """Count product types, total variants and available variants separately.

        For a named product, pass its known product_id OR exact catalog
        product_name. A name is selected from the real index by case-insensitive
        exact equality; there is no guessing. This avoids a separate model turn
        just to read the name/ID index. Both empty counts the whole store.
        Counts are derived
        from actual records; they are tool output fields, not additional REST
        fields. Failure yields no partial or hardcoded count. Performs no writes.
        """
        with self._lock:
            self._customer_id()
            if product_id and product_name:
                raise ReadError("choose_catalog_id_or_name")
            ids = (self._selected_product_ids([product_id] if product_id else None,
                                             [product_name] if product_name else None)
                   if product_id or product_name else
                   [product["product_id"] for product in read_products(self.client_api)["products"]])
            if len(ids) != len(set(ids)):
                raise ReadError("invalid_read_response", operation="listProducts")
            products = [read_product(self.client_api, product_id) for product_id in ids]
            self._observed_products.update({product["product_id"]: deepcopy(product) for product in products})
            items = [item for product in products for item in product["items"]]
            return {"product_type_count": len(products), "variant_count": len(items),
                    "available_variant_count": sum(item["available"] for item in items),
                    "products": products}
