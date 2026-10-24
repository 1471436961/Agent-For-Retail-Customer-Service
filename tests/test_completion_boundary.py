"""Native lifecycle diagnostics on synthetic data, with controlled provider choices.

This reproduces a possible truncation mechanism, not the undisclosed remote
termination reason. No platform limit is changed or inferred as fact here.
"""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from test_reference_runtime import reference_kit, apply_native_reference
from support_agent.domain.changes import prepare
from support_agent.domain.observations import ObservedFacts
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.tasks import Task, UserScenario
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.evaluator.evaluator import evaluate_simulation, EvaluationType


class CompletionBoundaryTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build

    def run_dialogue(self, kind, *, efficient, max_steps, short_reference=False, review_final=False):
        self.build(kind, compatibility=not efficient)
        case, params = self.case, self.params
        order = self.runtime.environment.tools.db.orders[case["order_id"]]
        selected = {row["existing_item_id"] for row in params["replacements"]}
        products = list(dict.fromkeys(row.product_id for row in order.items if row.item_id in selected))
        names = [self.runtime.environment.tools.db.products[value].name for value in products]
        verification = {"email":case["email"], "claimed_customer_id":None}
        if kind == "exchange":
            verification["order_id"] = case["order_id"][1:] if short_reference else case["order_id"]
            if efficient:
                verification["include_product_variants"] = True
        elif efficient:
            verification.update(include_order_details=True, product_names=names)
        submit = ("submit_change", {"kind":kind, "parameters":params})
        quote = ("prepare_change", {"kind":kind, "parameters":params})
        if efficient:
            schedule = ["identity", ("verify_customer",verification), "choices", quote, "recap", submit, "final"]
        elif kind == "exchange":
            schedule = ["identity", ("verify_customer",verification), "choices",
                ("read_products",{"product_ids":products}), "preparation_question",
                ("get_service_policy",{"operation":"exchange"}), "recap", submit, "final"]
        else:
            schedule = ["identity", ("verify_customer",verification),
                ("list_customer_orders",{"include_details":True}),
                ("get_catalog_summary",{"product_id":products[0]}), "choices", quote, "recap", submit, "final"]
        script = ["Please help with my requested order change and relevant catalog information.",
                  "My independently supplied email is " + case["email"],
                  "I choose these exact replacement specifications: " + json.dumps(params["replacements"])]
        if not efficient and kind == "exchange":
            script.append("Before approving, please explain the supplied exchange process and any real limits.")
        script.append("I approve this exact complete change and settlement. Submit it.")
        if review_final:
            schedule[-1:] = ["unmarked_final", "final"]

        class Customer:
            def get_init_state(self, message_history=None):
                return 0

            def generate_next_message(self, message, state):
                if state >= len(script):
                    raise AssertionError("The Agent did not close the resolved request")
                return UserMessage(role="user",content=script[state]),state+1

            def stop(self, *_args):
                pass

        def provider(**kwargs):
            index = self.model.call_count - 1
            choice = schedule[index]
            if isinstance(choice, tuple):
                return AssistantMessage(role="assistant",tool_calls=[ToolCall(
                    id=f"boundary-{index}",name=choice[0],arguments=deepcopy(choice[1]))])
            if choice == "identity":
                text = "Please provide your email or full first/last name and postal code for independent verification."
            elif choice == "choices":
                text = "Which exact specifications do you want to change?"
                if kind == "modify_items":
                    product = self.kit._observed_products[products[0]]
                    text = f"There are {sum(row['available'] for row in product['items'])} available variants. " + text
            elif choice == "preparation_question":
                text = "May I prepare this exchange for approval?"
            elif choice == "recap":
                facts = prepare(ObservedFacts(self.kit),kind,params)
                text = "The complete change is " + json.dumps(facts["body"]) + "; actual calculations " + json.dumps(facts["calculations"]) + "; applicable process " + json.dumps(facts["policy"]) + ". Do you approve this full submission?"
            else:
                actual = json.loads(next(m.content for m in reversed(kwargs["messages"]) if m.role == "tool"))["result"]
                self.assertEqual(actual["state"], "accepted" if kind == "exchange" else "completed")
                text = "The actual receipt confirms your requested change: " + json.dumps(actual["customer_facts"]) + ". Later refund arrival or shipping is not confirmed."
                text = text + " Anything else?" if choice == "unmarked_final" else "###COMPLETE### " + text
            return AssistantMessage(role="assistant",content=text)

        self.model.side_effect = provider
        task = Task(id="synthetic-lifecycle-boundary", user_scenario=UserScenario(instructions="Synthetic dialogue; no benchmark case copied"))
        runner = Orchestrator(domain="retail_plus", agent=self.agent, user=Customer(), environment=self.host,
                              task=task,max_steps=max_steps,validate_communication=True)
        simulation = runner.run()
        writes = [r for r in self.requests if r["method"] != "GET" and r["path"] != "/v1/customers/search"]
        self.assertEqual(len(writes),1)
        _, native, _ = reference_kit()
        apply_native_reference(native.environment.tools,kind,case["customer_id"],params)
        self.assertEqual(self.runtime.environment.get_db_hash(),native.environment.get_db_hash())
        return simulation, task

    def test_successful_write_can_still_get_zero_when_dialogue_is_truncated(self):
        for kind in ("exchange","modify_items"):
            for limit in (16,17):
                with self.subTest(kind=kind,limit=limit):
                    simulation,task = self.run_dialogue(kind,efficient=False,max_steps=limit)
                    self.assertEqual(simulation.termination_reason.value,"max_steps")
                    self.assertEqual(len(simulation.messages),18)
                    self.assertEqual(simulation.messages[-1].role,"tool")
                    reward = evaluate_simulation(simulation,task,EvaluationType.ENV,False,"retail_plus")
                    self.assertEqual(reward.reward,0)
                    self.assertIsNone(reward.reward_basis)
                    self.assertIn("max_steps",reward.info["note"])

    def test_same_longer_dialogues_end_normally_when_the_host_allows_the_final_response(self):
        for kind in ("exchange","modify_items"):
            with self.subTest(kind=kind):
                simulation,_ = self.run_dialogue(kind,efficient=False,max_steps=100)
                self.assertEqual(simulation.termination_reason.value,"agent_stop")
                self.assertTrue(self.agent.is_stop(simulation.messages[-1]))

    def test_explicit_read_batches_finish_both_goals_without_a_preparation_permission_stage(self):
        for kind in ("exchange","modify_items"):
            with self.subTest(kind=kind):
                simulation,_ = self.run_dialogue(kind,efficient=True,max_steps=16)
                self.assertEqual(simulation.termination_reason.value,"agent_stop")
                self.assertEqual(len(simulation.messages),15)
                self.assertTrue(self.agent.is_stop(simulation.messages[-1]))
                calls = [call.name for m in simulation.messages for call in (getattr(m,"tool_calls",None) or [])]
                self.assertEqual(calls,["verify_customer","prepare_change","submit_change"])
                # Identity search remains first; all requested reads follow it.
                self.assertEqual(self.requests[0]["path"],"/v1/customers/search")

    def test_context_batch_preserves_authorization_and_avoids_repeated_profile_reads(self):
        self.build("exchange",compatibility=False)
        with self.assertRaisesRegex(Exception,"identity_required"):
            self.kit.read_context(include_order_details=True)
        self.assertEqual(self.requests,[])
        with self.assertRaisesRegex(Exception,"identity_verification_failed"):
            self.kit.verify_customer(email=self.case["email"],claimed_customer_id="mismatched-claim",
                include_order_details=True,product_names=["unreached-catalog-selection"])
        self.assertEqual([r["path"] for r in self.requests],["/v1/customers/search"])
        self.requests.clear()
        verified = self.kit.verify_customer(email=self.case["email"])
        start = len(self.requests)
        context = self.kit.read_context(order_ids=[self.case["order_id"]])
        self.assertEqual([r["path"] for r in self.requests[start:]].count("/v1/customers/" + self.case["customer_id"]),1)
        self.assertEqual(context["orders"][0]["order_id"],self.case["order_id"])
        start = len(self.requests)
        with self.assertRaisesRegex(Exception,"order_access_denied"):
            self.kit.read_context(order_ids=["foreign-or-unknown-record"])
        self.assertFalse(any(r["path"].startswith("/v1/orders/") for r in self.requests[start:]))
        self.assertEqual(verified["customer_id"],context["customer_id"])

    def test_initial_display_reference_and_internal_review_do_not_spend_host_turns(self):
        simulation,_ = self.run_dialogue("exchange",efficient=True,max_steps=16,
                                        short_reference=True,review_final=True)
        self.assertEqual(simulation.termination_reason.value,"agent_stop")
        self.assertEqual(len(simulation.messages),15)
        self.assertTrue(self.agent.is_stop(simulation.messages[-1]))
        self.assertEqual(self.model.call_count,8)  # Seven decisions, one unshown-draft review.
        self.assertFalse(any("requested_order_error" in (m.content or "") for m in simulation.messages))
