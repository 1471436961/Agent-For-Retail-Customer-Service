"""Read-only comparison, calculator and deterministic seven-kind proposals."""
from copy import deepcopy
from typing import Literal
from tau2.environment.toolkit import ToolType, is_tool
from support_agent.adapters.customer_tools import CustomerTools, READ_ACTION_NAMES
from support_agent.domain.catalog import compare
from support_agent.domain.changes import prepare, execution_evidence
from support_agent.domain.identity import ReadError
from support_agent.domain.money import calculate_values
from support_agent.adapters.write_api import write_once, resume_workflow, observed_receipt
from support_agent.adapters.conversation_api import transfer_once
from support_agent.domain.plans import order_requests
from support_agent.domain.receipts import customer_facts
from support_agent.adapters.change_parameters import ChangeParameters, PlanRequest, SubmissionRequest, ReplacementChoice, validate_change_shape, equivalent_change_parameters
from support_agent.adapters.read_models import Address
from support_agent.domain.policy import service_policy
from support_agent.domain.observations import ObservedFacts, CurrentFacts

PREPARATION_ACTION_NAMES = ("compare_variants", "calculate", "get_service_policy", "prepare_change", "prepare_changes")
REVIEW_ACTION_NAMES = ("review_change", "invalidate_change")
PLAN_ACTION_NAMES = ("plan_changes", "prepare_plan_step", "abandon_plan", "get_service_status", "inspect_change_result", "transfer_to_human")
EXECUTE_ACTIONS = {"execute_default_address": "default_address", "execute_order_address": "order_address", "execute_cancellation": "cancel",
                   "execute_payment": "payment", "execute_item_modification": "modify_items", "execute_return": "return", "execute_exchange": "exchange"}
DIRECT_ACTION = "submit_change"
BATCH_DIRECT_ACTION = "submit_changes"
DIRECT_WRITE_KINDS = {
    "update_default_address": "default_address", "update_order_address": "order_address",
    "cancel_order": "cancel", "change_order_payment": "payment",
    "modify_order_items": "modify_items", "request_return": "return", "request_exchange": "exchange",
}
ACTION_NAMES = (*READ_ACTION_NAMES, *PREPARATION_ACTION_NAMES, *REVIEW_ACTION_NAMES, *PLAN_ACTION_NAMES,
                *EXECUTE_ACTIONS, DIRECT_ACTION, BATCH_DIRECT_ACTION, *DIRECT_WRITE_KINDS)


def write_arguments(name, arguments):
    """The same internal write contract, selected by a typed operation tool.

    This maps structured tool fields only. It neither interprets customer text
    nor infers consent, and preserves the caller's parameter values.
    """
    if name in DIRECT_WRITE_KINDS:
        parameters = {k: v for k, v in arguments.items() if k != "request_id"}
        if name in {"modify_order_items", "request_exchange"} and parameters.get("preserve_options") is None:
            parameters.pop("preserve_options", None)
        return {"kind": DIRECT_WRITE_KINDS[name],
                "parameters": parameters,
                "request_id": arguments.get("request_id", "")}
    return arguments


class ChangeTools(CustomerTools):
    def __init__(self, client_api):
        super().__init__(client_api)
        self._proposals = {}
        self._opening_gift_ids = None
        self._plans = {}
        self._human_requested = False
        self._transfer_outcome = None
        self._inspections = {}
        self._submissions = {}

    def _sync_session(self):
        previous = self._conversation_id
        super()._sync_session()
        if previous != self._conversation_id:
            self._proposals = {}
            self._opening_gift_ids = None
            self._plans = {}
            self._human_requested = False
            self._transfer_outcome = None
            self._inspections = {}
            self._submissions = {}

    def _assert_service_active(self):
        if self._transfer_outcome and self._transfer_outcome["state"] in {"accepted", "unknown"}:
            raise ReadError("human_handoff_terminal_no_customer_api")

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
    def calculate(self, operation: Literal["add", "subtract", "multiply", "divide", "round", "sum", "difference", "price_difference"],
                  values: list[float], decimal_places: int | None = None) -> dict:
        """Perform general addition, subtraction, multiplication, division or rounding.

        The model determines operands from the user's request and business rules.
        add sums values; subtract and divide apply values left to right (at least
        two operands); multiply multiplies all values. No implicit currency
        rounding. Optional decimal_places rounds the final result; round takes
        one value and defaults to two decimal places. Python float/round applies.
        Returns actual operands and result. Rejects non-finite numbers and zero
        divisors; never invents units or evaluates code. No business API calls.
        """
        return calculate_values(operation, values, decimal_places)

    @is_tool(ToolType.READ)
    def get_service_policy(self, operation: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"]) -> dict:
        """Compatibility reference to this operation's packaged business skills.

        New decisions use load_skills for procedures and processing questions.
        Quotes carry references, not another copy of all policy prose. No write,
        inferred consent or live fulfillment facts are established by this read.
        """
        with self._lock:
            self._customer_id()
            return service_policy(operation)

    @is_tool(ToolType.READ)
    def prepare_change(self, kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"], parameters: ChangeParameters,
                       supersedes: list[str] | None = None) -> dict:
        """Quote the selected change from actual observed records, without writing.

        This is an optional READ for missing calculations or facts. Reuse known
        row differences and recorded single-item return prices without another
        quote. Never ask permission to prepare, calculate or show a quote.
        Use its verified prices, totals and difference in ONE complete recap,
        then ask for submission approval. To refresh a record, read it explicitly;
        quotes reuse observed facts, while submission independently revalidates them. Do not offer to prepare it after an
        initial confirmation. Answer any real clarification without restarting
        an unchanged quote or inserting a separate list-completeness approval.

        parameters: address kinds need the complete address object; order kinds
        need order_id. cancel needs canonical reason; payment needs saved
        payment_method_id. modify_items/exchange need ordered replacements
        [{existing_item_id,replacement_item_id,preserve_options}], one saved
        payment_method_id. Each row's preserve_options lists only that item's
        option keys the user wants unchanged; use [] if there are none. Keys
        must exist on that original item. Different products can have different
        keys. Top-level preserve_options is a shared union: each key
        applies only to original items that have it, not to unrelated products.
        Set that top-level list to [] when using only row-specific constraints.
        These local constraints are not sent as business API request fields.
        return needs repeated item_ids and refund_payment_method_id. Optional
        specific_instance=true rejects ambiguous duplicate-ID instance selection.
        Include all requested items whose choices have been specified. This is
        a read-only draft: no approval or list-completeness affirmation is needed
        to prepare it. Its successful result is ready for the Agent to explain
        and display directly; a second review_change read is not required.
        Then obtain confirmation of the complete list and exact change before
        the matching operation-specific write tool. Identity,
        ownership, status, catalog, quantities and finances are checked here.
        A changed quote supersedes an unattempted proposal for the same target.
        Reading the same unchanged quote preserves its ID and approval context.
        supersedes explicitly retires old unsubmitted drafts for this target
        while preparing the revised quote, including a change of operation.
        Do not call invalidate_change first when this revised quote suffices.
        """
        with self._lock:
            self._customer_id()
            target = parameters.get("order_id") if kind != "default_address" else None
            if any(p["state"] in {"attempted", "pending", "unknown"} and p["facts"]["order_id"] == target for p in self._proposals.values()):
                raise ReadError("unresolved_write_requires_inspection_no_new_write")
            facts = prepare(ObservedFacts(self, load_missing=True), kind, deepcopy(parameters))
            replaced = self._replacement_drafts(supersedes, {facts["order_id"]})
            proposal = self._new_proposal(facts, parameters)
            return self._retire_drafts(replaced, [proposal], deepcopy(proposal))

    def _replacement_drafts(self, identifiers, targets):
        if identifiers is None:
            return []
        if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
            raise ReadError("invalid_superseded_proposals")
        proposals = [self._proposal(value) for value in dict.fromkeys(identifiers)]
        if any(p["state"] != "prepared" or p["facts"]["order_id"] not in targets for p in proposals):
            raise ReadError("only_unattempted_same_target_drafts_can_be_superseded")
        return proposals

    @staticmethod
    def _retire_drafts(previous, replacements, result):
        retained = {p["proposal_id"] for p in replacements}
        retired = []
        for proposal in previous:
            if proposal["proposal_id"] not in retained:
                proposal["state"] = "superseded"
                retired.append(proposal["proposal_id"])
        if retired:
            result["superseded_proposal_ids"] = retired
        return result

    @is_tool(ToolType.READ)
    def prepare_changes(self, requests: list[PlanRequest], supersedes: list[str] | None = None) -> dict:
        """Quote several explicitly selected changes in one read-only call.

        Each request is {kind, parameters}, using that kind's exact schema.
        Use for multiple orders or profile plus order changes; one later recap
        and approval can cover all. No write, execution plan or permission to
        prepare is created. Address/payment precede item changes. Incompatible
        same-order return/exchange requests require a customer choice first.
        supersedes retires specified unsubmitted drafts on these same targets.
        """
        with self._lock:
            self._customer_id()
            ordered = order_requests(requests)
            facts = [prepare(ObservedFacts(self, load_missing=True), row["kind"], deepcopy(row["parameters"]))
                     for row in ordered]
            replaced = self._replacement_drafts(supersedes, {row["order_id"] for row in facts})
            proposals = [self._new_proposal(fact, row["parameters"]) for fact, row in zip(facts, ordered)]
            result = {"state": "prepared", "changes": deepcopy(proposals)}
            return self._retire_drafts(replaced, proposals, result)

    def _new_proposal(self, facts, parameters):
        if any(p["state"] in {"attempted", "pending", "unknown"} and p["facts"]["order_id"] == facts["order_id"] for p in self._proposals.values()):
            raise ReadError("unresolved_write_requires_inspection_no_new_write")
        for previous in self._proposals.values():
            if (previous["state"] == "prepared" and previous["facts"]["kind"] == facts["kind"]
                    and equivalent_change_parameters(facts["kind"], previous["parameters"], parameters)
                    and execution_evidence(previous["facts"]) == execution_evidence(facts)):
                return previous
        identifier = f"proposal-{len(self._proposals) + 1}"
        for previous in self._proposals.values():
            if (previous["state"] == "prepared" and previous["facts"]["order_id"] == facts["order_id"]
                    and previous["facts"]["kind"] == facts["kind"]):
                previous["state"] = "superseded"
        proposal = {"proposal_id": identifier, "state": "prepared", "facts": facts,
                    "parameters": deepcopy(parameters)}
        self._proposals[identifier] = proposal
        return proposal

    @is_tool(ToolType.WRITE)
    def submit_change(self, kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"],
                      parameters: ChangeParameters, request_id: str = "") -> dict:
        """Actually submit a change the customer has already explicitly approved.

        Use this WRITE after a genuine natural-language recap of the full change
        and a later customer reply clearly approving those exact details. No
        prepared proposal ID or second confirmation is required. A question alone,
        conditional assent or request merely to check availability is not consent.
        The model interprets that meaning; the program does not match phrases.
        Parameters have the same seven-kind shapes as prepare_change. Prior
        actual customer/order/catalog reads are required so details are grounded.
        The toolkit freezes those reads, rechecks current facts, then makes one
        real API write. Changed facts require a new recap and approval. Never use
        this to bypass missing customer approval or to retry an unknown write.
        request_id is filled by the Agent from the real customer turn and target;
        it is local replay metadata, never a REST field or backend idempotency key.
        """
        with self._lock:
            self._customer_id()
            if not isinstance(request_id, str) or not request_id:
                raise ReadError("missing_submission_request_reference")
            if request_id in self._submissions:
                proposal = self._proposal(self._submissions[request_id])
                if proposal["facts"]["kind"] != kind or not equivalent_change_parameters(kind, proposal["parameters"], parameters):
                    raise ReadError("submission_reference_reused_for_different_change")
                return deepcopy(proposal.get("outcome", {"proposal_id": proposal["proposal_id"], "state": proposal["state"]}))
            facts = prepare(ObservedFacts(self), kind, deepcopy(parameters))
            proposal = self._new_proposal(facts, parameters)
            self._submissions[request_id] = proposal["proposal_id"]
            try:
                return self._execute(proposal["proposal_id"], kind)
            except ReadError as error:
                # _execute raises ReadError only in preflight, before write_once.
                # Preserve a definite non-write result for this request reference.
                proposal["state"] = "failed"
                outcome = {"proposal_id": proposal["proposal_id"], "state": "failed",
                           "error": error.code, "customer_facts": {
                               "kind": kind, "order_id": facts["order_id"], "state": "failed",
                               "notice": "The change was not submitted. Recheck facts and obtain approval if the proposal changed."}}
                proposal["outcome"] = outcome
                return deepcopy(outcome)

    @is_tool(ToolType.WRITE)
    def submit_changes(self, requests: list[SubmissionRequest]) -> dict:
        """Submit several exact changes covered by the customer's clear approval.

        Each request is {kind, parameters}; request_id is bound by the Agent.
        Use for approved returns on multiple orders or profile/order updates.
        Validate the whole observed request list first, then perform each real
        business write sequentially with fresh preflight checks. This is not an
        atomic cross-order transaction. Stop on a failed, pending or unknown
        result; report actual partial results, never repeat successful writes.
        A single recap and later consent must cover every change in this call.
        """
        with self._lock:
            self._customer_id()
            rows = [{"kind": row["kind"], "parameters": row["parameters"]} for row in requests]
            ordered = order_requests(rows)
            references = {(row["kind"], row["parameters"].get("order_id")): row.get("request_id") for row in requests}
            if any(not isinstance(value, str) or not value for value in references.values()):
                raise ReadError("missing_submission_request_reference")
            # No writes occur while verifying the complete observed batch.
            for row in ordered:
                validate_change_shape(row["kind"], row["parameters"])
                reference = references[(row["kind"], row["parameters"].get("order_id"))]
                if reference in self._submissions:
                    proposal = self._proposal(self._submissions[reference])
                    if proposal["facts"]["kind"] != row["kind"] or not equivalent_change_parameters(row["kind"], proposal["parameters"], row["parameters"]):
                        raise ReadError("submission_reference_reused_for_different_change")
                    continue
                prepare(ObservedFacts(self), row["kind"], deepcopy(row["parameters"]))
            results = []
            for row in ordered:
                target = row["parameters"].get("order_id")
                reference = references[(row["kind"], target)]
                try:
                    outcome = self.submit_change(row["kind"], row["parameters"], reference)
                except ReadError as error:
                    # submit_change can raise only before a business write;
                    # preserve prior actual outcomes instead of losing them.
                    outcome = {"state": "failed", "error": error.code,
                               "customer_facts": {"kind": row["kind"], "order_id": target,
                                   "state": "failed", "notice": "This change was not submitted."}}
                results.append({"kind": row["kind"], "order_id": target, "request_id": reference,
                                "outcome": outcome})
                if outcome["state"] not in {"completed", "accepted"}:
                    break
            return {"results": results, "remaining": len(ordered) - len(results),
                    "notice": "Separate actual outcomes; no cross-operation atomicity or unreported success."}

    @is_tool(ToolType.WRITE)
    def update_default_address(self, address: Address, request_id: str = "") -> dict:
        """Update ONLY the verified profile default to the complete address already recapped and clearly approved. Does not change any order. request_id is bound by the Agent."""
        return self.submit_change("default_address", {"address": address}, request_id)

    @is_tool(ToolType.WRITE)
    def update_order_address(self, order_id: str, address: Address, request_id: str = "") -> dict:
        """Update ONLY this owned pending order to the complete address already recapped and clearly approved. Keep unchanged address fields. Does not change the profile or other orders. request_id is bound by the Agent."""
        return self.submit_change("order_address", {"order_id": order_id, "address": address}, request_id)

    @is_tool(ToolType.WRITE)
    def cancel_order(self, order_id: str, reason: Literal["no longer needed", "ordered by mistake"], request_id: str = "") -> dict:
        """Cancel the WHOLE owned pending order after its full recap and clear approval. Refund original charges to their original methods. No partial cancellation. request_id is bound by the Agent."""
        return self.submit_change("cancel", {"order_id": order_id, "reason": reason}, request_id)

    @is_tool(ToolType.WRITE)
    def change_order_payment(self, order_id: str, payment_method_id: str, request_id: str = "") -> dict:
        """Replace the full pending-order charge using one different saved method, after recap and approval. Gift balance must cover the full charge. Original charge is refunded; a decline preserves the original. request_id is bound by the Agent."""
        return self.submit_change("payment", {"order_id": order_id, "payment_method_id": payment_method_id}, request_id)

    @is_tool(ToolType.WRITE)
    def modify_order_items(self, order_id: str, replacements: list[ReplacementChoice], payment_method_id: str,
                           preserve_options: list[str] | None = None, specific_instance: bool = False, request_id: str = "") -> dict:
        """Modify the COMPLETE approved pending-order item list once, using available same-product variants and one saved settlement method. Preserve ordered duplicate occurrences and per-row unchanged options. request_id is bound by the Agent."""
        return self.submit_change("modify_items", {"order_id": order_id, "replacements": replacements,
            "payment_method_id": payment_method_id, "preserve_options": preserve_options or [],
            "specific_instance": specific_instance}, request_id)

    @is_tool(ToolType.WRITE)
    def request_return(self, order_id: str, item_ids: list[str], refund_payment_method_id: str,
                       specific_instance: bool = False, request_id: str = "") -> dict:
        """Register the COMPLETE approved return list for an owned delivered order once. Refund to an original method or eligible preexisting gift card; no card/PayPal cross-routing. Keep duplicate item IDs. Acceptance is not refund arrival. request_id is bound by the Agent."""
        return self.submit_change("return", {"order_id": order_id, "item_ids": item_ids,
            "refund_payment_method_id": refund_payment_method_id, "specific_instance": specific_instance}, request_id)

    @is_tool(ToolType.WRITE)
    def request_exchange(self, order_id: str, replacements: list[ReplacementChoice], payment_method_id: str,
                         preserve_options: list[str] | None = None, specific_instance: bool = False, request_id: str = "") -> dict:
        """Register the COMPLETE approved delivered-order exchange once. Use available same-product replacements, including an identical variant, and one saved method for the difference. Keep ordered duplicates and per-row constraints. Acceptance is not settlement or shipment. request_id is bound by the Agent."""
        return self.submit_change("exchange", {"order_id": order_id, "replacements": replacements,
            "payment_method_id": payment_method_id, "preserve_options": preserve_options or [],
            "specific_instance": specific_instance}, request_id)

    def _proposal(self, identifier):
        self._customer_id()
        if identifier not in self._proposals:
            raise ReadError("unknown_proposal")
        return self._proposals[identifier]

    @is_tool(ToolType.READ)
    def review_change(self, proposal_id: str) -> dict:
        """Optionally retrieve an earlier prepared proposal to display it again.

        Successful preparation already supplies the proposal for immediate
        display. Do not use this as an extra mandatory step after preparation.
        Retrieval does not start another approval stage. The model preserves
        any unchanged quote already explained and interprets actual later
        consent; this tool itself proves no customer approval.
        It requires no preliminary approval to display the read-only proposal.
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
            if self._human_requested:
                raise ReadError("human_requested_business_writes_paused")
            if proposal["facts"]["kind"] != kind:
                raise ReadError("wrong_execution_kind")
            if proposal["state"] in {"attempted", "pending", "completed", "accepted", "failed", "unknown"}:
                return deepcopy(proposal.get("outcome", {"state": proposal["state"]}))
            if proposal["state"] != "prepared":
                raise ReadError("proposal_not_prepared")
            current = prepare(CurrentFacts(self), kind, proposal["parameters"])
            if execution_evidence(current) != execution_evidence(proposal["facts"]):
                proposal["state"] = "stale"
                raise ReadError("facts_changed_prepare_and_confirm_again")
            # Agent protocol records response/customer chronology; the model
            # must establish the actual recap and semantic consent. The toolkit
            # has no dialogue channel and cannot prove either meaning.
            proposal["state"] = "attempted"
            outcome = write_once(self.client_api, proposal["facts"])
            self._save_outcome(proposal, outcome)
            return deepcopy(proposal["outcome"])

    def _save_outcome(self, proposal, outcome):
        outcome = deepcopy(outcome)
        facts = proposal["facts"]
        outcome["customer_facts"] = customer_facts(facts, outcome)
        proposal["outcome"] = {"proposal_id": proposal["proposal_id"], **outcome}
        proposal["state"] = outcome["state"]
        if outcome["state"] in {"completed", "accepted"}:
            receipt = outcome["receipt"]
            if facts["kind"] in {"cancel", "modify_items", "return", "exchange"}:
                # The actual one-time write changed this order's eligibility.
                # Other unsubmitted drafts for it are now obsolete, regardless
                # of which earlier operation the customer was considering.
                for previous in self._proposals.values():
                    if previous["state"] == "prepared" and previous["facts"]["order_id"] == facts["order_id"]:
                        previous["state"] = "superseded"
            if facts["kind"] == "default_address" and self._observed_profile:
                self._observed_profile["default_shipping_address"] = deepcopy(receipt["default_shipping_address"])
            elif facts["order_id"] in self._observed_orders:
                observed = self._observed_orders[facts["order_id"]]
                observed.update({key: deepcopy(value) for key, value in receipt.items() if key != "order_id"})
        if "plan_id" in proposal:
            step = self._plans[proposal["plan_id"]]["steps"][proposal["step_index"]]
            step["state"] = outcome["state"]
            step["outcome"] = deepcopy(proposal["outcome"])

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

    @is_tool(ToolType.WRITE)
    def execute_payment(self, proposal_id: str) -> dict:
        """Execute the approved pending-order payment replacement once.

        One different saved method covers the entire original charge; successful
        replacement charges it and refunds the original charge. Decline is not
        success. Explain card/PayPal 3–6 working days or immediate gift refund.
        """
        return self._execute(proposal_id, "payment")

    @is_tool(ToolType.WRITE)
    def execute_item_modification(self, proposal_id: str) -> dict:
        """Execute the approved COMPLETE pending item list as ONE request.

        Wait for the customer's response to the recap's final completeness
        question. Never split, deduplicate or append after submission. The saved
        settlement choice pays/refunds the computed difference. No quantity edit.
        """
        return self._execute(proposal_id, "modify_items")

    @is_tool(ToolType.WRITE)
    def execute_return(self, proposal_id: str) -> dict:
        """Submit the approved COMPLETE delivered-item return list once.

        Uses original payment or eligible preexisting saved gift card as prepared.
        accepted means application received, not money already refunded. Do not
        append items, assert payment arrival or invent a refund timing guarantee.
        """
        return self._execute(proposal_id, "return")

    @is_tool(ToolType.WRITE)
    def execute_exchange(self, proposal_id: str) -> dict:
        """Submit the approved COMPLETE delivered exchange mapping once.

        One saved method settles the difference; it need not be original payment.
        accepted means application received, not settlement or shipping complete.
        Never append, change the confirmed destination or infer hidden line IDs.
        """
        return self._execute(proposal_id, "exchange")

    @is_tool(ToolType.READ)
    def plan_changes(self, requests: list[PlanRequest]) -> dict:
        """Build a serial plan from already clarified explicit customer requests.

        Each entry is {kind, parameters}. Per-order address/payment precede
        one-time items. Incompatible cancellation/return/exchange combinations
        require clarification, never silent omission or expansion. Every step is
        separately prepared, actually reviewed and approved before execution.
        """
        with self._lock:
            self._customer_id()
            ordered = order_requests(requests)
            # Validate all currently known qualifications without creating proposals.
            for row in ordered:
                prepare(self, row["kind"], row["parameters"])
            identifier = f"plan-{len(self._plans) + 1}"
            plan = {"plan_id": identifier, "steps": [{**deepcopy(row), "state": "pending"} for row in ordered]}
            self._plans[identifier] = plan
            return deepcopy(plan)

    @is_tool(ToolType.READ)
    def prepare_plan_step(self, plan_id: str) -> dict:
        """Prepare only the next pending plan step after prior real successes.

        No write or approval is implied. A failed/unknown/paused prior step blocks
        progression; report partial success and clarify how to proceed.
        """
        with self._lock:
            self._customer_id()
            if self._human_requested or plan_id not in self._plans:
                raise ReadError("plan_unavailable_or_human_requested")
            plan = self._plans[plan_id]
            for index, step in enumerate(plan["steps"]):
                if step["state"] in {"completed", "accepted"}:
                    continue
                if step["state"] != "pending":
                    raise ReadError("previous_plan_step_not_complete")
                p = self.prepare_change(step["kind"], step["parameters"])
                self._proposals[p["proposal_id"]].update(plan_id=plan_id, step_index=index)
                step.update(state="prepared", proposal_id=p["proposal_id"])
                return deepcopy(self._proposals[p["proposal_id"]])
            raise ReadError("plan_already_complete")

    @is_tool(ToolType.READ)
    def abandon_plan(self, plan_id: str) -> dict:
        """Pause unattempted steps on a changed target, withdrawal or human request.

        Keeps completed/accepted/failed/unknown receipts. Never rolls back or
        repeats completed work; create a new plan only for newly agreed remaining work.
        """
        with self._lock:
            self._sync_session()
            if plan_id not in self._plans:
                raise ReadError("unknown_plan")
            plan = self._plans[plan_id]
            for step in plan["steps"]:
                if step["state"] in {"pending", "prepared"}:
                    step["state"] = "paused"
                    if "proposal_id" in step:
                        self.invalidate_change(step["proposal_id"])
            return deepcopy(plan)

    @is_tool(ToolType.READ)
    def get_service_status(self) -> dict:
        """Read session-only plans and actual receipts; no backend request.

        Distinguishes completed, accepted, failed, unknown and pending steps.
        Report partial success accurately rather than claim an atomic transaction.
        """
        with self._lock:
            self._sync_session()
            return {"plans": deepcopy(list(self._plans.values())),
                    "changes": deepcopy(list(self._proposals.values())),
                    "human_requested": self._human_requested, "handoff": deepcopy(self._transfer_outcome)}

    @is_tool(ToolType.READ)
    def inspect_change_result(self, proposal_id: str) -> dict:
        """Resolve a pending workflow or inspect an uncertain write, using reads.

        Follow only the status Location actually returned by the write service,
        honoring Retry-After. A final receipt is checked against the original
        approved items, amounts and destination. No new approval is required to
        inspect an already submitted request. Never submit it a second time.
        Without a usable workflow receipt, read the owned record and check for
        the exact requested business state. A matching observation confirms that
        current record, not the identity of its writer. An unchanged/stale record
        never proves a write failed and never authorizes replay.
        """
        with self._lock:
            proposal = self._proposal(proposal_id)
            count = self._inspections.get(proposal_id, 0)
            self._inspections[proposal_id] = count + 1
            facts = proposal["facts"]
            original = deepcopy(proposal.get("outcome"))
            observed = None
            if proposal["state"] in {"pending", "unknown"}:
                proposal.setdefault("initial_outcome", original)
                if original and "workflow" in original and original.get("workflow_status") != "succeeded":
                    self._save_outcome(proposal, resume_workflow(self.client_api, facts, original["workflow"]))
                if proposal["state"] == "unknown":
                    observed = self.get_owned_order(facts["order_id"]) if facts["order_id"] else self._profile()
                    checked = observed_receipt(facts, observed)
                    if checked["state"] in {"accepted", "completed"}:
                        self._save_outcome(proposal, checked)
            else:
                observed = self.get_owned_order(facts["order_id"]) if facts["order_id"] else self._profile()
            return {"proposal_id": proposal_id, "state": proposal["state"],
                    "write_outcome": deepcopy(proposal.get("outcome")), "observed": observed,
                    "customer_facts": deepcopy(proposal.get("outcome", {}).get("customer_facts")),
                    "inspection_number": count + 1,
                    "warning": "Status checks and observations never authorize replay of the write."}

    @is_tool(ToolType.GENERIC, mutates_state=True)
    def transfer_to_human(self, summary: str, basis: Literal["explicit_request", "unsupported_escalation", "policy_gap"]) -> dict:
        """Transfer using the trusted active conversation ID, once, no retry.

        The model must establish one of the three bases: explicit customer request
        (no extra troubleshooting), escalation requested after explaining an
        unsupported limit, or known facts with a genuinely unaddressed policy.
        Complexity, anger or latency alone is not a transfer basis. Calling this
        pauses business writes immediately. accepted/unknown stops customer API.
        Only accepted permits the exact configured transfer notice.
        """
        with self._lock:
            self._sync_session()
            if self._transfer_outcome is not None:
                return deepcopy(self._transfer_outcome)
            if basis not in {"explicit_request", "unsupported_escalation", "policy_gap"} or not isinstance(summary, str) or not summary.strip():
                raise ReadError("invalid_transfer_basis_or_summary")
            self._human_requested = True
            for proposal in self._proposals.values():
                if proposal["state"] == "prepared":
                    proposal["state"] = "paused"
            self._transfer_outcome = transfer_once(self.client_api, summary)
            return deepcopy(self._transfer_outcome)
