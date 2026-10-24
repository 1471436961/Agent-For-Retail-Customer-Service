"""Read-only comparison, calculator and deterministic seven-kind proposals."""
from copy import deepcopy
from typing import Literal
from tau2.environment.toolkit import ToolType, is_tool
from support_agent.adapters.customer_tools import CustomerTools, READ_ACTION_NAMES
from support_agent.domain.catalog import compare
from support_agent.domain.changes import prepare, execution_evidence
from support_agent.domain.identity import ReadError
from support_agent.domain.money import calculate_values
from support_agent.adapters.write_api import write_once
from support_agent.adapters.conversation_api import transfer_once
from support_agent.domain.plans import order_requests
from support_agent.domain.receipts import customer_facts
from support_agent.adapters.change_parameters import ChangeParameters, PlanRequest
from support_agent.domain.policy import service_policy
from support_agent.domain.observations import ObservedFacts

PREPARATION_ACTION_NAMES = ("compare_variants", "calculate", "get_service_policy", "prepare_change")
REVIEW_ACTION_NAMES = ("review_change", "invalidate_change")
PLAN_ACTION_NAMES = ("plan_changes", "prepare_plan_step", "abandon_plan", "get_service_status", "inspect_change_result", "transfer_to_human")
EXECUTE_ACTIONS = {"execute_default_address": "default_address", "execute_order_address": "order_address", "execute_cancellation": "cancel",
                   "execute_payment": "payment", "execute_item_modification": "modify_items", "execute_return": "return", "execute_exchange": "exchange"}
DIRECT_ACTION = "submit_change"
ACTION_NAMES = (*READ_ACTION_NAMES, *PREPARATION_ACTION_NAMES, *REVIEW_ACTION_NAMES, *PLAN_ACTION_NAMES, *EXECUTE_ACTIONS, DIRECT_ACTION)


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
    def calculate(self, operation: Literal["sum", "difference", "price_difference", "round"], values: list[float]) -> dict:
        """Calculate finite values with an auditable input/output trace.

        difference takes [left,right]; price_difference takes [new,old,...],
        accumulates float differences in order then Python round(total,2).
        Never evaluate expressions or treat malformed money as zero.
        """
        return calculate_values(operation, values)

    @is_tool(ToolType.READ)
    def get_service_policy(self, operation: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"]) -> dict:
        """Read the applicable supplied policy for an operation, including processing questions.

        These are sourced course policy facts, not live backend/fulfillment facts.
        Prepared proposals already include this policy, so do not fetch it again
        unnecessarily. Use it for policy questions before preparation or when the
        customer changes operation. No account data, inferred user consent or
        writes. Cancellation refund timing does not govern returns/exchanges.
        """
        with self._lock:
            self._customer_id()
            return service_policy(operation)

    @is_tool(ToolType.READ)
    def prepare_change(self, kind: Literal["default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"], parameters: ChangeParameters) -> dict:
        """Get the accurate full quote now, without performing any write.

        This is a READ. When choices and settlement details are known, call it
        immediately; never ask permission to prepare, calculate or show a quote.
        Use its verified prices, totals and difference in ONE complete recap,
        then ask for submission approval. Do not offer to prepare it after an
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
        Then obtain confirmation of the complete list and exact change before execute. Identity,
        ownership, status, catalog, quantities and finances are checked here.
        A changed quote supersedes an unattempted proposal for the same target.
        Reading the same unchanged quote preserves its ID and approval context.
        """
        with self._lock:
            self._customer_id()
            target = parameters.get("order_id") if kind != "default_address" else None
            if any(p["state"] in {"attempted", "unknown"} and p["facts"]["order_id"] == target for p in self._proposals.values()):
                raise ReadError("unresolved_write_requires_inspection_no_new_write")
            facts = prepare(self, kind, deepcopy(parameters))
            return deepcopy(self._new_proposal(facts, parameters))

    def _new_proposal(self, facts, parameters):
        if any(p["state"] in {"attempted", "unknown"} and p["facts"]["order_id"] == facts["order_id"] for p in self._proposals.values()):
            raise ReadError("unresolved_write_requires_inspection_no_new_write")
        for previous in self._proposals.values():
            if (previous["state"] == "prepared" and previous["parameters"] == parameters
                    and execution_evidence(previous["facts"]) == execution_evidence(facts)):
                return previous
        identifier = f"proposal-{len(self._proposals) + 1}"
        for previous in self._proposals.values():
            if previous["state"] == "prepared" and previous["facts"]["order_id"] == facts["order_id"]:
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
                if proposal["facts"]["kind"] != kind or proposal["parameters"] != parameters:
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
            if proposal["state"] in {"attempted", "completed", "accepted", "failed", "unknown"}:
                return deepcopy(proposal.get("outcome", {"state": proposal["state"]}))
            if proposal["state"] != "prepared":
                raise ReadError("proposal_not_prepared")
            current = prepare(self, kind, proposal["parameters"])
            if execution_evidence(current) != execution_evidence(proposal["facts"]):
                proposal["state"] = "stale"
                raise ReadError("facts_changed_prepare_and_confirm_again")
            # Agent protocol records response/customer chronology; the model
            # must establish the actual recap and semantic consent. The toolkit
            # has no dialogue channel and cannot prove either meaning.
            proposal["state"] = "attempted"
            outcome = write_once(self.client_api, proposal["facts"])
            outcome["customer_facts"] = customer_facts(proposal["facts"], outcome)
            proposal["outcome"] = {"proposal_id": proposal_id, **outcome}
            proposal["state"] = outcome["state"]
            if outcome["state"] in {"completed", "accepted"}:
                receipt = outcome["receipt"]
                if kind == "default_address" and self._observed_profile:
                    self._observed_profile["default_shipping_address"] = deepcopy(receipt["default_shipping_address"])
                elif proposal["facts"]["order_id"] in self._observed_orders:
                    observed = self._observed_orders[proposal["facts"]["order_id"]]
                    observed.update({key: deepcopy(value) for key, value in receipt.items() if key != "order_id"})
            if "plan_id" in proposal:
                step = self._plans[proposal["plan_id"]]["steps"][proposal["step_index"]]
                step["state"] = outcome["state"]
                step["outcome"] = deepcopy(proposal["outcome"])
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
        """Inspect with existing customer/order reads, without repeating the write.

        Preserves the original write outcome. Seeing a record is evidence, never
        proof of request identity or permission to retry an unknown write. No new
        status API, compensation, slicing or write is performed.
        """
        with self._lock:
            proposal = self._proposal(proposal_id)
            count = self._inspections.get(proposal_id, 0)
            self._inspections[proposal_id] = count + 1
            facts = proposal["facts"]
            observed = self.get_owned_order(facts["order_id"]) if facts["order_id"] else self._profile()
            return {"proposal_id": proposal_id, "state": proposal["state"],
                    "write_outcome": deepcopy(proposal.get("outcome")), "observed": observed,
                    "inspection_number": count + 1,
                    "warning": "Observation does not authorize replay of a write or prove an unknown write outcome."}

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
