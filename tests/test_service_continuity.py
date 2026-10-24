"""Controlled model choices, real SDK writes: revisions and customer follow-ups.

These test contracts and effects, not real-model semantic understanding. Fixture
records and prices are synthetic; no evaluation-case answer is hardcoded.
"""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from test_reference_runtime import apply_native_reference
from support_agent.adapters.change_parameters import PlanRequest
from support_agent.domain.identity import ReadError
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.data_model.tasks import Task, UserScenario
from tau2.domains.retail_plus.tools import RetailPlusTools
from tau2.orchestrator.orchestrator import Orchestrator


class ServiceContinuityTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def verify(self, kind="exchange"):
        self.build(kind, compatibility=False)
        return self.result(self.decision(UserMessage(role="user",content="Please help with my request."),
            "verify_customer",{"email":self.case["email"],"claimed_customer_id":None,
                "order_id":self.case["order_id"],"include_product_variants":True}))

    def two_orders(self):
        self.build("return", compatibility=False)
        db = self.runtime.environment.tools.db
        original = db.orders[self.case["order_id"]]
        other = original.model_copy(deep=True)
        other.order_id = "#SYNTHETIC-SECOND-ORDER"
        for payment in other.payment_history:
            payment.payment_method_id = self.case["payment_method_ids"]["paypal"]
        db.orders[other.order_id] = other
        db.users[self.case["customer_id"]].orders.append(other.order_id)
        received = self.result(self.decision(UserMessage(role="user",content="Return everything in both delivered orders."),
            "verify_customer",{"email":self.case["email"],"claimed_customer_id":None,"include_order_details":True}))
        observed = json.loads(received.content)["requested_context"]["orders"]
        rows = [{"kind":"return","parameters":{"order_id":order["order_id"],
                 "item_ids":[item["item_id"] for item in order["items"]],
                 "refund_payment_method_id":next(p["payment_method_id"] for p in order["payments"]
                                                  if p["transaction_type"] == "payment")}}
                for order in observed if order["status"] == "delivered"]
        self.assertEqual(len(rows),2)
        return received, rows

    def test_mismatched_kind_is_corrected_before_host_execution(self):
        received = self.verify("order_address")
        wrong = {"order_id":self.case["order_id"],"replacements":[],
                 "payment_method_id":self.case["payment_method_ids"]["credit_card"]}
        before = len(self.requests)
        self.model.side_effect = [
            AssistantMessage(role="assistant",tool_calls=[ToolCall(id="bad-address-shape",name="prepare_change",
                arguments={"kind":"order_address","parameters":wrong})]),
            AssistantMessage(role="assistant",tool_calls=[ToolCall(id="real-address-shape",name="prepare_change",
                arguments={"kind":"order_address","parameters":self.params})]),
        ]
        reply,self.state = self.agent.generate_next_message(received,self.state)
        self.assertEqual(reply.tool_calls[0].id,"real-address-shape")
        feedback = self.model.call_args.kwargs["messages"][-1].content
        self.assertIn("order_address",feedback)
        self.assertIn("address, order_id",feedback)
        self.assertEqual(len(self.requests),before)
        self.assertEqual(json.loads(self.result(reply).content)["state"],"prepared")
        self.assertEqual(self.writes(),[])
        with self.assertRaises(ValueError):
            PlanRequest.model_validate({"kind":"order_address","parameters":wrong})

    def test_variant_query_is_model_visible_and_cannot_bypass_fresh_write_checks(self):
        received = self.verify("exchange")
        order = self.kit._observed_orders[self.case["order_id"]]
        queries = []
        targets = []
        for row in self.params["replacements"]:
            item = next(i for i in order["items"] if i["item_id"]==row["existing_item_id"])
            product = self.runtime.environment.tools.db.products[item["product_id"]]
            target = product.variants[row["replacement_item_id"]]
            targets.append(target)
            queries.append({"product_id":product.product_id,"filters":dict(target.options)})
        before = len(self.requests)
        filtered = self.result(self.decision(received,"query_product_variants",{"queries":queries}))
        self.assertEqual(len(self.requests),before)  # Actual prior catalogs are reused.
        matches = json.loads(filtered.content)["queries"]
        self.assertTrue(all(row["state"]=="complete" for row in matches))
        for result,requested in zip(matches,self.params["replacements"]):
            self.assertIn(requested["replacement_item_id"],[item["item_id"] for item in result["candidates"]])
        quoted = self.result(self.decision(filtered,"prepare_change",{"kind":"exchange","parameters":self.params}))
        facts = json.loads(quoted.content)["facts"]
        self.decision(quoted,text="The complete exchange is "+json.dumps(facts["body"])+" with calculations "+json.dumps(facts["calculations"])+". Do you approve?")
        targets[0].available = False
        call = self.decision(UserMessage(role="user",content="I approve that complete exchange and settlement. Submit it."),
                             "request_exchange",self.params)
        outcome = json.loads(self.result(call).content)
        self.assertEqual(outcome["state"],"failed")
        self.assertEqual(self.writes(),[])

    def test_specific_address_schema_and_resume_after_two_invalid_drafts(self):
        received = self.verify("order_address")
        actual = next(action for action in self.agent.gateway.live_actions if action.name == "update_order_address")
        self.assertEqual(set(actual.params.model_fields),{"order_id","address","request_id"})
        self.assertNotIn("parameters",actual.params.model_fields)
        self.decision(received,text="Update only this order to "+json.dumps(self.params["address"])+". Do you approve this complete address change?")
        wrong = AssistantMessage(role="assistant",tool_calls=[ToolCall(id="missing-address",name="update_order_address",
            arguments={"order_id":self.case["order_id"]})])
        self.model.side_effect=[wrong,wrong]
        rejected,self.state = self.agent.generate_next_message(UserMessage(role="user",content="Yes, I approve that exact address; update it."),self.state)
        self.assertFalse(rejected.tool_calls)
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(self.writes(),[])
        self.model.side_effect=None
        call = self.decision(UserMessage(role="user",content="Continue with the same approved order address."),
            "update_order_address",self.params)
        receipt=self.result(call)
        self.assertEqual(json.loads(receipt.content)["state"],"completed")
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.writes()[0]["body"],self.params["address"])
        self.assertEqual(len([r for r in self.requests if r["path"]=="/v1/customers/search"]),1)

    def test_complete_write_with_length_metadata_still_passes_full_preflight_once(self):
        received=self.verify("order_address")
        self.decision(received,text="Update only this order to "+json.dumps(self.params["address"])+". Approve this exact change?")
        self.model.side_effect=[AssistantMessage(role="assistant",tool_calls=[ToolCall(id="valid-write-at-limit",name="update_order_address",arguments=self.params)],
            raw_data={"choices":[{"finish_reason":"length"}]})]
        call,self.state=self.agent.generate_next_message(UserMessage(role="user",content="Yes, submit that exact full address update."),self.state)
        receipt=self.result(call)
        self.assertEqual(json.loads(receipt.content)["state"],"completed")
        self.assertEqual(len(self.writes()),1)

    def test_typed_write_reuses_quote_and_batch_replay_without_reposting(self):
        received = self.verify("return")
        quoted = self.result(self.decision(received,"prepare_change",{"kind":"return","parameters":self.params}))
        proposal_id = json.loads(quoted.content)["proposal_id"]
        self.decision(quoted,text="Return exactly these recorded items to their original method. Do you approve this complete request?")
        call = self.decision(UserMessage(role="user",content="I approve that exact complete return. Submit it."),
                             "request_return",self.params)
        receipt = self.result(call)
        outcome = json.loads(receipt.content)
        self.assertEqual(outcome["proposal_id"],proposal_id)
        row = {"kind":"return","parameters":deepcopy(self.params),
               "request_id":call.tool_calls[0].arguments["request_id"]}
        before = len(self.requests)
        replay = self.kit.submit_changes([row])
        self.assertEqual(json.loads(self.host.to_json_str(replay))["results"][0]["outcome"],outcome)
        self.assertEqual(len(self.requests),before)
        self.assertEqual(len(self.writes()),1)
        row["parameters"]["specific_instance"] = True
        with self.assertRaisesRegex(ReadError,"submission_reference_reused_for_different_change"):
            self.kit.submit_changes([row])
        self.assertEqual(len(self.requests),before)

    def test_malformed_generation_is_corrected_without_a_permanent_gateway_lock(self):
        received=self.verify("order_address")
        self.model.side_effect=[json.JSONDecodeError("Malformed function arguments","{",1),
            AssistantMessage(role="assistant",content="I have the verified order. What address field should change?")]
        reply,self.state=self.agent.generate_next_message(received,self.state)
        self.assertFalse(reply.tool_calls)
        self.assertIsNone(self.state["halt_reason"])
        self.assertIsNone(self.state["protocol_diagnostic"])
        self.assertIsNone(self.state["gateway_diagnostic"])
        self.assertEqual(self.writes(),[])

    def test_historical_rejected_shape_is_preserved_without_reexecution(self):
        received = self.verify("order_address")
        self.decision(received,text="Which order address field should change?")
        history = deepcopy(self.state["history"])
        history.extend([UserMessage(role="user",content="Only change the suite.").model_dump(),
            AssistantMessage(role="assistant",tool_calls=[ToolCall(id="historical-shape-error",name="prepare_change",arguments={
                "kind":"order_address","parameters":{"order_id":self.case["order_id"],"replacements":[],
                    "payment_method_id":self.case["payment_method_ids"]["credit_card"]}})]).model_dump(),
            ToolMessage(role="tool",id="historical-shape-error",content="Error: ReadError: unexpected_change_parameter",error=True).model_dump()])
        before = len(self.requests)
        restored = self.agent.get_init_state(history)
        self.assertEqual(len(self.requests),before)
        self.assertEqual(restored["proposals"],{})
        self.assertFalse(restored["pending_call_ids"])

    def test_identical_delivered_replacement_matches_native_backend(self):
        received = self.verify()
        observed = json.loads(received.content)["requested_order"]
        original = next(item for item in observed["items"] if any(
            variant["item_id"] == item["item_id"] and variant["available"]
            for product in observed["product_variants"] for variant in product["items"]))
        params = {"order_id":self.case["order_id"],"replacements":[{
            "existing_item_id":original["item_id"],"replacement_item_id":original["item_id"]}],
            "payment_method_id":self.case["payment_method_ids"]["credit_card"]}
        quote = self.result(self.decision(received,"prepare_change",{"kind":"exchange","parameters":params}))
        self.assertEqual(float(json.loads(quote.content)["facts"]["calculations"][0]["result"]),0)
        gold = RetailPlusTools(self.runtime.environment.tools.db.model_copy(deep=True))
        self.decision(quote,text="Replace this delivered damaged unit with the identical available variant, zero price difference, using your saved card. Is that the complete exchange you approve?")
        receipt = self.result(self.decision(UserMessage(role="user",content="Yes, submit that exact replacement."),
            "submit_change",{"kind":"exchange","parameters":params}))
        self.assertEqual(json.loads(receipt.content)["state"],"accepted")
        apply_native_reference(gold,"exchange",self.case["customer_id"],params)
        self.assertEqual(self.runtime.environment.tools.db.model_dump(),gold.db.model_dump())
        self.assertEqual(len(self.writes()),1)

    def test_switching_operation_retires_draft_in_the_new_quote(self):
        received = self.verify()
        returns = {"order_id":self.case["order_id"],
                   "item_ids":[self.params["replacements"][0]["existing_item_id"]],
                   "refund_payment_method_id":self.case["payment_method_ids"]["credit_card"]}
        first = self.result(self.decision(received,"prepare_change",{"kind":"return","parameters":returns}))
        self.decision(first,text="Return the selected delivered item to the original saved card. Do you approve this complete return?")
        second = self.result(self.decision(UserMessage(role="user",content="Do not return it. I want an exchange instead."),
            "prepare_change",{"kind":"exchange","parameters":self.params,"supersedes":["proposal-1"]}))
        self.assertEqual(json.loads(second.content)["superseded_proposal_ids"],["proposal-1"])
        self.decision(second,text="Here is the replacement and actual quoted difference for the complete exchange. Do you approve this exchange instead?")
        self.assertEqual(self.state["proposals"]["proposal-1"]["state"],"superseded")
        self.assertNotIn("proposal-1",self.state["quote_response_turns"])
        self.assertEqual(self.writes(),[])
        self.assertNotIn("invalidate_change",[call["name"] for message in self.state["history"] for call in message.get("tool_calls",[])])

    def test_reduced_scope_can_reuse_row_facts_then_customer_can_choose_return(self):
        received = self.verify()
        quoted = self.result(self.decision(received,"prepare_change",{"kind":"exchange","parameters":self.params}))
        facts = json.loads(quoted.content)["facts"]
        component = next(row for row in facts["calculations"] if row.get("purpose") == "replacement_row" and int(row["row_index"]) == 0)
        original = facts["snapshot"]["order"]["items"][0]
        returns = {"order_id":self.case["order_id"],"item_ids":[original["item_id"]],
                   "refund_payment_method_id":self.case["payment_method_ids"]["credit_card"]}
        self.decision(quoted,text="Here are all quoted replacements and the full settlement. Do you approve all exchanges?")
        reduced = self.decision(UserMessage(role="user",content="Only keep the first quoted exchange, drop the others."),
            text="Only the first quoted replacement remains, with its verified difference "+str(component["result"])+" to the quoted saved card. Do you approve that revised complete scope?")
        self.assertFalse(reduced.tool_calls)
        self.decision(UserMessage(role="user",content="Actually, no exchanges. Return this original item instead."),
            text="Return only "+original["name"]+" at its recorded sale price "+str(original["price"])+" to the original card. This registers a return, not refund arrival. Do you approve this complete return instead?")
        receipt = self.result(self.decision(UserMessage(role="user",content="Yes, submit only that return."),
            "submit_change",{"kind":"return","parameters":returns}))
        self.decision(receipt,text="Only the confirmed return application was accepted; no exchanges were submitted.")
        self.assertEqual(self.state["proposals"]["proposal-1"]["state"],"superseded")
        self.assertEqual(self.kit._proposals["proposal-1"]["state"],"superseded")
        self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.writes()[0]["path"].endswith("/returns"))
        calls = [call["name"] for message in self.state["history"] for call in message.get("tool_calls",[])]
        self.assertEqual(calls.count("prepare_change"),1)
        self.assertNotIn("invalidate_change",calls)

    def test_two_returns_share_confirmation_and_match_native_database(self):
        received,rows = self.two_orders()
        quote = self.result(self.decision(received,"prepare_changes",{"requests":rows}))
        self.assertEqual(len(json.loads(quote.content)["changes"]),2)
        gold = RetailPlusTools(self.runtime.environment.tools.db.model_copy(deep=True))
        self.decision(quote,text="Return all listed items in both delivered orders, each to its own original method. These are two separate applications. Do you approve both full requests?")
        call = self.decision(UserMessage(role="user",content="Yes, submit both complete returns to their original methods."),
            "submit_changes",{"requests":rows})
        bound = deepcopy(call.tool_calls[0].arguments["requests"])
        receipt = self.result(call)
        outcome = json.loads(receipt.content)
        self.assertEqual([r["outcome"]["state"] for r in outcome["results"]],["accepted","accepted"])
        self.assertEqual(int(outcome["remaining"]),0)
        self.assertEqual(len(self.writes()),2)
        for row in rows:
            apply_native_reference(gold,row["kind"],self.case["customer_id"],row["parameters"])
        self.assertEqual(self.runtime.environment.tools.db.model_dump(),gold.db.model_dump())
        # Actual host replay returns recorded results, never a second POST.
        self.assertEqual(json.loads(self.host.to_json_str(self.kit.submit_changes(bound))),outcome)
        self.assertEqual(len(self.writes()),2)
        reply = self.decision(receipt,text="Both return applications were accepted. Later refund arrival is not confirmed.")
        self.assertFalse(self.agent.is_stop(reply))
        self.assertEqual({r["state"] for r in self.state["submissions"].values()},{"accepted"})
        # A later complaint remains a real customer turn, and real handoff is terminal.
        transfer = self.result(self.decision(UserMessage(role="user",content="Now that both are confirmed, transfer me to a human to complain."),
            "transfer_to_human",{"basis":"explicit_request","summary":"Customer requests a human complaint review after both returns were accepted."}))
        reply,self.state = self.agent.generate_next_message(transfer,self.state)
        self.assertTrue(self.agent.is_stop(reply))
        self.assertIn("BEING TRANSFERRED",reply.content)

    def test_invalid_destination_in_batch_writes_nothing(self):
        received,rows = self.two_orders()
        rows[0]["parameters"]["refund_payment_method_id"],rows[1]["parameters"]["refund_payment_method_id"] = (
            rows[1]["parameters"]["refund_payment_method_id"],rows[0]["parameters"]["refund_payment_method_id"])
        self.decision(received,text="Refunds cannot be swapped between unrelated original card/PayPal methods.")
        with self.assertRaisesRegex(ReadError,"ineligible_refund_destination"):
            self.kit.prepare_changes(rows)
        self.assertEqual(self.writes(),[])
        self.assertEqual(self.kit._proposals,{})

    def test_batch_validates_all_ownership_before_first_write(self):
        self.build("return",compatibility=False)
        with self.assertRaisesRegex(ReadError,"identity_required"):
            self.kit.submit_changes([])
        self.assertEqual(self.requests,[])
        received,rows = self.two_orders()
        rows[1]["parameters"]["order_id"] = "foreign-not-observed"
        for index,row in enumerate(rows):
            row["request_id"] = f"synthetic-scope-{index}"
        with self.assertRaisesRegex(ReadError,"read_order_facts_before_submission"):
            self.kit.submit_changes(rows)
        self.assertEqual(self.writes(),[])
        self.assertEqual(self.kit._submissions,{})

    def test_batch_unknown_result_preserves_prior_success_and_never_reposts(self):
        received,rows = self.two_orders()
        quote = self.result(self.decision(received,"prepare_changes",{"requests":rows}))
        self.decision(quote,text="Submit both complete returns to each original method. Do you approve?")
        original_transport = self.kit.client_api._transport
        uncertain_path = "/v1/orders/%23SYNTHETIC-SECOND-ORDER/returns"
        def transport(request):
            if request["path"] == uncertain_path and request["method"] == "POST":
                self.requests.append(deepcopy(request))
                return {"status_code":503,"body":{}}
            return original_transport(request)
        self.kit.client_api._transport = transport
        call = self.decision(UserMessage(role="user",content="I approve both full returns; submit."),"submit_changes",{"requests":rows})
        receipt = self.result(call)
        outcome = json.loads(receipt.content)
        self.assertEqual([r["outcome"]["state"] for r in outcome["results"]],["accepted","unknown"])
        replay = self.kit.submit_changes(call.tool_calls[0].arguments["requests"])
        self.assertEqual(json.loads(self.host.to_json_str(replay)),outcome)
        self.assertEqual(len(self.writes()),2)
        self.decision(receipt,text="The first return was accepted; the second result is unknown and I will not resubmit it.")
        self.assertEqual({r["state"] for r in self.state["submissions"].values()},{"accepted","unknown"})

    def test_native_customer_can_revert_profile_after_address_batch(self):
        self.build("default_address",compatibility=False)
        db = self.runtime.environment.tools.db
        original_profile = self.kit.client_api._transport({"method":"GET","path":"/v1/customers/"+self.case["customer_id"]})["body"]["default_shipping_address"]
        self.requests.clear()
        original_order = deepcopy(db.orders[self.case["order_id"]].model_dump())
        rows = [{"kind":"default_address","parameters":self.params},
                {"kind":"order_address","parameters":{"order_id":self.case["order_id"],**self.params}}]
        restore = {"address":original_profile}
        schedule = [("verify_customer",{"email":self.case["email"],"claimed_customer_id":None,"order_id":self.case["order_id"]}),
                    "review-both",("submit_changes",{"requests":rows}),"both-receipts","review-restore",
                    ("submit_change",{"kind":"default_address","parameters":restore}),"restored"]
        script = ["Change my profile and pending order to the complete new address "+json.dumps(self.params["address"])+". My email is "+self.case["email"],
                  "I approve both exact address updates.","Now restore only my profile address to its original value, leaving the order at the new address.",
                  "Yes, I approve that exact profile-only restoration.","Thank you. ###STOP###"]
        class Customer:
            def get_init_state(self,message_history=None): return 0
            def generate_next_message(self,message,state):
                return UserMessage(role="user",content=script[state]),state+1
            def stop(self,*args): pass
        def provider(**kwargs):
            choice = schedule[self.model.call_count-1]
            if isinstance(choice,tuple):
                name,args = completion.live_call(self.agent,choice[0],choice[1])
                return AssistantMessage(role="assistant",tool_calls=[ToolCall(id=f"continuity-{self.model.call_count}",name=name,arguments=args)])
            if choice == "review-both":
                text = "Change only the profile default and this pending order to "+json.dumps(self.params["address"])+". Do you approve both?"
            elif choice == "review-restore":
                text = "Restore only the profile default to "+json.dumps(original_profile)+"; keep the order at the new address. Do you approve?"
            else:
                text = "The actual receipts confirm those specific updates."
            return AssistantMessage(role="assistant",content=text)
        self.model.side_effect = provider
        task = Task(id="synthetic-address-follow-up",user_scenario=UserScenario(instructions="Synthetic record changes and a later correction"))
        simulation = Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,task=task,
                                  max_steps=16,validate_communication=True).run()
        self.assertEqual(simulation.termination_reason.value,"user_stop")
        self.assertLessEqual(len(simulation.messages),16)
        self.assertEqual(len(self.writes()),3)
        calls = [call.name for message in simulation.messages for call in (getattr(message,"tool_calls",None) or [])]
        self.assertEqual(calls,["verify_customer","submit_changes","update_default_address"])
        self.assertEqual(self.kit.get_customer_summary()["default_shipping_address"],original_profile)
        order = db.orders[self.case["order_id"]].model_dump()
        self.assertNotEqual(order["address"],original_order["address"])
        self.assertEqual({k:v for k,v in order.items() if k!="address"},{k:v for k,v in original_order.items() if k!="address"})

    def test_native_routing_and_handoff_boundary_counts_required_messages(self):
        # The local limit is a diagnostic input, not an assertion about the
        # remote configuration. No read/quote/confirmation round is invented.
        for identity_up_front,limit,expected in ((True,16,"agent_stop"),
                                                  (False,16,"max_steps"),
                                                  (False,17,"agent_stop")):
            with self.subTest(identity_up_front=identity_up_front,limit=limit):
                self.build("return",compatibility=False)
                db = self.runtime.environment.tools.db
                original = db.orders[self.case["order_id"]]
                other = original.model_copy(deep=True)
                other.order_id = "#SYNTHETIC-SECOND-ORDER"
                for payment in other.payment_history:
                    payment.payment_method_id = self.case["payment_method_ids"]["paypal"]
                db.orders[other.order_id] = other
                db.users[self.case["customer_id"]].orders.append(other.order_id)
                requests = [{"kind":"return","parameters":{"order_id":o.order_id,
                    "item_ids":[i.item_id for i in o.items],
                    "refund_payment_method_id":o.payment_history[0].payment_method_id}}
                    for o in (original,other)]
                schedule = ([] if identity_up_front else ["identity"]) + [
                    ("verify_customer",{"email":self.case["email"],"claimed_customer_id":None,"include_order_details":True}),
                    "recap","routing",("submit_changes",{"requests":requests}),"receipts",
                    ("transfer_to_human",{"basis":"explicit_request","summary":"Customer requests a human complaint after both returns were accepted."})]
                initial = "Return both delivered orders."
                script = ([initial+" My email is "+self.case["email"]] if identity_up_front else
                          [initial,self.case["email"]]) + [
                    "Please cross the refund destinations instead.",
                    "Then submit both complete returns to the stated original methods. I approve.",
                    "Now get me a human to complain."]
                class Customer:
                    def get_init_state(self,message_history=None): return 0
                    def generate_next_message(self,message,state):
                        return UserMessage(role="user",content=script[state]),state+1
                    def stop(self,*args): pass
                totals = {}
                def provider(**kwargs):
                    choice = schedule[self.model.call_count-1]
                    if isinstance(choice,tuple):
                        return AssistantMessage(role="assistant",tool_calls=[ToolCall(
                            id=f"routing-{self.model.call_count}",name=choice[0],arguments=choice[1])])
                    if choice == "identity":
                        text = "Please provide your account email or full name and postal code."
                    elif choice == "recap":
                        body=json.loads(next(m.content for m in reversed(kwargs["messages"]) if m.role=="tool"))["result"]
                        totals.update({o["order_id"]:o["item_price_total"]["result"]
                                       for o in body["requested_context"]["orders"]})
                        text = "Return these complete selected item lists to their original methods: "+json.dumps(requests)+"; recorded item totals "+json.dumps(totals)+". Instructions are provided by email. Request acceptance is not refund arrival. Do you approve?"
                    elif choice == "routing":
                        text = "Cross-routing these refunds is unsupported. The complete return lists and totals are unchanged; each must use its stated original method. Do you approve those returns?"
                    else:
                        body=json.loads(next(m.content for m in reversed(kwargs["messages"]) if m.role=="tool"))["result"]
                        self.assertTrue(all(r["outcome"]["state"]=="accepted" for r in body["results"]))
                        text = "Both return requests were accepted to their confirmed original methods. Instructions are provided by email; refund arrival is not confirmed."
                    return AssistantMessage(role="assistant",content=text)
                self.model.side_effect=provider
                task=Task(id="synthetic-return-routing-boundary",user_scenario=UserScenario(instructions="Synthetic integration and lifecycle test"))
                simulation=Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,
                    task=task,max_steps=limit,validate_communication=True).run()
                self.assertEqual(simulation.termination_reason.value,expected)
                self.assertEqual(simulation.messages[-1].content,"YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.")
                self.assertEqual([o.status for o in (original,other)],["return requested","return requested"])
                self.assertEqual(len(self.writes()),3)
                calls=[c.name for m in simulation.messages for c in (getattr(m,"tool_calls",None) or [])]
                self.assertEqual(calls,["verify_customer","submit_changes","transfer_to_human"])
