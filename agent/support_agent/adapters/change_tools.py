"""Read-only comparison, calculator and deterministic seven-kind proposals."""
from copy import deepcopy
from typing import Literal
from tau2.environment.toolkit import ToolType, is_tool
from support_agent.adapters.customer_tools import CustomerTools, READ_ACTION_NAMES
from support_agent.domain.catalog import compare
from support_agent.domain.changes import prepare
from support_agent.domain.identity import ReadError
from support_agent.domain.money import calculate_values
from support_agent.adapters.write_api import write_once

PREPARATION_ACTION_NAMES = ("compare_variants", "calculate", "prepare_change")
REVIEW_ACTION_NAMES = ("review_change", "invalidate_change")
EXECUTE_ACTIONS = {"execute_default_address": "default_address", "execute_order_address": "order_address", "execute_cancellation": "cancel"}
ACTION_NAMES = (*READ_ACTION_NAMES, *PREPARATION_ACTION_NAMES, *REVIEW_ACTION_NAMES, *EXECUTE_ACTIONS)


class ChangeTools(CustomerTools):
    def __init__(self, client_api):
        super().__init__(client_api)
        self._proposals = {}
        self._opening_gift_ids = None

    def _sync_session(self):
        previous = self._conversation_id
        super()._sync_session()
        if previous != self._conversation_id:
            self._proposals = {}
            self._opening_gift_ids = None

    def _profile(self):
        profile = super()._profile()
        if self._opening_gift_ids is None:
            self._opening_gift_ids = {m["id"] for m in profile["payment_methods"] if m["source"] == "gift_card"}
        return profile

    @is_tool(ToolType.READ)
    def compare_variants(self, product_id: str, filters: dict[str, str], sort_by: str = "price",
                         descending: bool = False, numeric_unit: str = "") -> dict:
        """Compare available same-product variants using exact option filters.

        The model interprets preferences and includes every unchanged option in
        filters. Price is numeric; other sort keys require an explicit unit, e.g.
        capacity with ml. Missing/mixed quantities fail, no guessed size scale.
        Stable ties retain backend order. No natural-language parsing or writes.
        """
        with self._lock:
            return compare(self.get_product_variants(product_id), filters, sort_by, descending, numeric_unit)

    @is_tool(ToolType.READ)
    def calculate(self, operation: Literal["sum", "difference", "price_difference", "round"], values: list[float]) -> dict:
        """Calculate finite values with an auditable input/output trace.

        difference takes [left,right]; price_difference takes [new,old,...],
        accumulates float differences in order then Python round(total,2).
        Never evaluate expressions or treat malformed money as zero.
        """
        return calculate_values(operation, values)

    @is_tool(ToolType.READ)
    def prepare_change(self, kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"], parameters: dict) -> dict:
        """Prepare exactly one complete change, without performing any write.

        parameters: address kinds need the complete address object; order kinds
        need order_id. cancel needs canonical reason; payment needs saved
        payment_method_id. modify_items/exchange need ordered replacements
        [{existing_item_id,replacement_item_id}], one payment_method_id and
        preserve_options (option keys the user has NOT requested to change).
        return needs repeated item_ids and refund_payment_method_id. Optional
        specific_instance=true rejects ambiguous duplicate-ID instance selection.
        Collect the complete one-time item list before preparation. Identity,
        ownership, status, catalog, quantities and finances are checked here.
        New preparation supersedes any unattempted proposal for the same target.
        """
        with self._lock:
            facts = prepare(self, kind, deepcopy(parameters))
            identifier = f"proposal-{len(self._proposals) + 1}"
            for previous in self._proposals.values():
                if previous["state"] == "prepared" and previous["facts"]["order_id"] == facts["order_id"]:
                    previous["state"] = "superseded"
            proposal = {"proposal_id": identifier, "state": "prepared", "facts": facts,
                        "parameters": deepcopy(parameters)}
            self._proposals[identifier] = proposal
            return deepcopy(proposal)

    def _proposal(self, identifier):
        self._customer_id()
        if identifier not in self._proposals:
            raise ReadError("unknown_proposal")
        return self._proposals[identifier]

    @is_tool(ToolType.READ)
    def review_change(self, proposal_id: str) -> dict:
        """Retrieve the exact prepared proposal for the Agent's full recap.

        The Agent displays these fixed facts and asks for confirmation; this tool
        itself does not imply that a customer has seen or approved the proposal.
        For one-time item changes the recap also asks if this is the complete list.
        """
        with self._lock:
            proposal = self._proposal(proposal_id)
            if proposal["state"] != "prepared":
                raise ReadError("proposal_not_prepared")
            return {"review": deepcopy(proposal)}

    @is_tool(ToolType.READ)
    def invalidate_change(self, proposal_id: str) -> dict:
        """Invalidate an unattempted proposal on correction, pause or withdrawal.

        The model interprets the customer's words. This tool never cancels or
        rolls back an already attempted/completed backend operation.
        """
        with self._lock:
            proposal = self._proposal(proposal_id)
            if proposal["state"] == "prepared":
                proposal["state"] = "withdrawn"
            return {"proposal_id": proposal_id, "state": proposal["state"]}

    def _execute(self, proposal_id, kind):
        with self._lock:
            proposal = self._proposal(proposal_id)
            if proposal["facts"]["kind"] != kind:
                raise ReadError("wrong_execution_kind")
            if proposal["state"] in {"attempted", "completed", "accepted", "failed", "unknown"}:
                return deepcopy(proposal.get("outcome", {"state": proposal["state"]}))
            if proposal["state"] != "prepared":
                raise ReadError("proposal_not_prepared")
            current = prepare(self, kind, proposal["parameters"])
            if current != proposal["facts"]:
                proposal["state"] = "stale"
                raise ReadError("facts_changed_prepare_and_confirm_again")
            # Agent protocol independently enforces actual recap + later user
            # turn. Toolkit has no dialogue channel and cannot prove consent.
            proposal["state"] = "attempted"
            outcome = write_once(self.client_api, proposal["facts"])
            proposal["outcome"] = {"proposal_id": proposal_id, **outcome}
            proposal["state"] = outcome["state"]
            return deepcopy(proposal["outcome"])

    @is_tool(ToolType.WRITE)
    def execute_default_address(self, proposal_id: str) -> dict:
        """Execute a reviewed, subsequently approved default-address proposal once.

        Does not alter any order address. Never supplies new address fields here.
        Revalidates current facts; unknown outcomes must not be retried.
        """
        return self._execute(proposal_id, "default_address")

    @is_tool(ToolType.WRITE)
    def execute_order_address(self, proposal_id: str) -> dict:
        """Execute a reviewed, subsequently approved pending order address once.

        Does not alter the profile default. Rechecks ownership and pending status.
        """
        return self._execute(proposal_id, "order_address")

    @is_tool(ToolType.WRITE)
    def execute_cancellation(self, proposal_id: str) -> dict:
        """Cancel the reviewed whole pending order once after customer approval.

        Only the prepared permitted reason is sent. Partial cancellation is not
        supported. Explain original-charge refunds: card/PayPal 3–6 working days,
        gift card immediately. Failed/unknown is not a successful cancellation.
        """
        return self._execute(proposal_id, "cancel")
