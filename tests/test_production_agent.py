"""Current model-facing Agent, real SDK/backend; provider choices are controlled.

This validates the canonical execution and completion contract, not real model
understanding. Older executor/plan tests separately exercise compatibility.
"""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
import test_task_completion as completion
from test_reference_runtime import reference_kit, apply_native_reference, legacy_address
from tau2.data_model.message import UserMessage, AssistantMessage
from support_agent.state import SessionState
from support_agent.domain.reply import response_content, output_was_truncated
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.data_model.tasks import Task, UserScenario
from tau2.data_model.message import ToolCall


class ProductionAgentTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def test_tool_transport_excludes_provider_dump_and_keeps_native_usage(self):
        self.build("return", compatibility=False)
        self.model.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(
            id="metadata-boundary", name="verify_customer", arguments={"email":self.case["email"],
            "claimed_customer_id":None,"skill_names":["delivered-returns"]})], usage={"prompt_tokens":18,"completion_tokens":9}, cost=0.01,
            raw_data={"debug_object":object(),"repeated_provider_dump":"x"*100000})
        call,self.state = self.agent.generate_next_message(UserMessage(role="user",content="Help with a return."),self.state)
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(call.usage,{"prompt_tokens":18,"completion_tokens":9})
        self.assertEqual(call.cost,0.01)
        self.assertLess(len(json.dumps(self.state)),3000)
        self.assertEqual(call.tool_calls[0].id,"metadata-boundary")
        received = self.result(call)
        self.assertEqual(json.loads(received.content)["customer_id"],self.case["customer_id"])

    def test_invalid_model_reply_still_finalizes_a_readable_native_trajectory(self):
        self.build("return",compatibility=False)
        self.model.return_value=AssistantMessage(role="assistant",content=" ")
        class Customer:
            def get_init_state(self,message_history=None): return 0
            def generate_next_message(self,message,state):
                return UserMessage(role="user",content="Please help." if state==0 else "###STOP###"),state+1
            def stop(self,*args): pass
        task=Task(id="synthetic-invalid-reply-finalization",user_scenario=UserScenario(instructions="Synthetic protocol-failure trajectory"))
        simulation=Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,
            task=task,max_steps=10,validate_communication=True).run()
        self.assertEqual(simulation.termination_reason.value,"user_stop")
        self.assertTrue(all(isinstance(m.timestamp,str) for m in simulation.messages))
        self.assertIn("invalid_model_reply",simulation.messages[-2].content)
        self.assertEqual(self.requests,[])

    def test_unexpected_error_after_real_write_preserves_receipt_and_stops_automation(self):
        received = self.read_request("order_address")
        self.decision(received,text="Update only this order to "+json.dumps(self.params["address"])+". Approve this complete change?")
        call = self.decision(UserMessage(role="user",content="I approve this exact change; submit it."),
                             "update_order_address",self.params)
        receipt = self.result(call)
        self.assertEqual(json.loads(receipt.content)["state"],"completed")
        with patch("support_agent.application.normalize_call_ids",side_effect=KeyError("private-debug-text")):
            reply,self.state = self.agent.generate_next_message(receipt,self.state)
        self.assertEqual(self.state["halt_reason"],"internal_error")
        self.assertEqual(self.state["runtime_diagnostic"],{"stage":"validate_model_reply","exception_type":"KeyError"})
        self.assertIn('"state": "completed"',reply.content)
        self.assertNotIn("private-debug-text",json.dumps(self.state))
        before = len(self.requests)
        reply,self.state = self.agent.generate_next_message(UserMessage(role="user",content="Please continue."),self.state)
        self.assertFalse(reply.tool_calls)
        self.assertEqual(len(self.requests),before)

    def read_request(self, kind):
        self.build(kind, compatibility=False)
        args = {"email": self.case["email"], "claimed_customer_id": self.case["customer_id"]}
        if kind != "default_address":
            args["order_id"] = self.case["order_id"]
        incoming = self.result(self.decision(UserMessage(role="user",content="Please help with my requested change."),
            "verify_customer",args))
        self.assertEqual(self.requests[0]["path"],"/v1/customers/search")
        self.assertEqual(len([r for r in self.requests if r["path"].startswith("/v1/customers/") and r["method"]=="GET"]),1)
        self.assertFalse(any("/catalog/" in r["path"] for r in self.requests))
        if kind in {"modify_items","exchange"}:
            order=self.kit._observed_orders[self.case["order_id"]]
            selected={row["existing_item_id"] for row in self.params["replacements"]}
            ids=list(dict.fromkeys(row["product_id"] for row in order["items"] if row["item_id"] in selected))
            incoming=self.result(self.decision(incoming,"read_products",{"product_ids":ids}))
        return incoming

    def test_all_operations_use_one_submission_and_native_completion(self):
        for kind in ("default_address","order_address","cancel","payment","modify_items","return","exchange"):
            with self.subTest(kind=kind):
                incoming=self.read_request(kind)
                before=len(self.requests)
                if kind in {"modify_items", "exchange"}:
                    incoming=self.result(self.decision(incoming,"prepare_change",{"kind":kind,"parameters":self.params}))
                    p=json.loads(incoming.content)
                    details={"change":p["facts"]["body"],"calculations":p["facts"]["calculations"]}
                else:
                    details={"change":self.params,"actual_records":json.loads(incoming.content)}
                self.assertEqual(len(self.requests),before)  # Reuse genuine already-read facts.
                text="The complete requested change is "+json.dumps(details)+". Is this the complete list and do you approve this submission?"
                recap=self.decision(incoming,text=text)
                self.assertFalse(self.agent.is_stop(recap))
                self.assertEqual(self.writes(),[])
                actions={a.name for a in self.agent.gateway.live_actions}
                expected_write = next(name for name,value in completion.DIRECT_WRITE_KINDS.items() if value == kind)
                self.assertIn(expected_write,actions)
                self.assertNotIn("submit_change",actions)
                self.assertFalse(any(name.startswith("execute_") for name in actions))
                self.assertFalse({"plan_changes","review_change","prepare_plan_step"}&actions)
                receipt=self.result(self.decision(UserMessage(role="user",content="I approve those exact complete details. Submit now."),
                    "submit_change",{"kind":kind,"parameters":self.params}))
                outcome=json.loads(receipt.content)
                self.assertEqual(outcome["state"],"accepted" if kind in {"return","exchange"} else "completed")
                final=self.decision(receipt,text="The actual receipt confirms the requested operation. No later settlement or shipping is implied. ###STOP###")
                self.assertFalse(self.agent.is_stop(final))  # The customer can still follow up.
                self.assertNotIn("###STOP###",final.content)
                self.assertEqual(len(self.writes()),1)
                fresh=[r for r in self.requests[before:] if r["method"]=="GET"]
                self.assertEqual(len(fresh),len({r["path"] for r in fresh}))
                _,gold,_=reference_kit()
                apply_native_reference(gold.environment.tools,kind,self.case["customer_id"],self.params)
                self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())
                restored=self.agent.get_init_state(deepcopy(self.state["history"]))
                self.assertEqual(restored["submissions"],self.state["submissions"])

    def test_completion_is_not_parsed_from_customer_language_or_assumed_from_text(self):
        self.build(compatibility=False)
        reply=self.decision(UserMessage(role="user",content="What does ###STOP### mean?"),text="What can I help you with?")
        self.assertFalse(self.agent.is_stop(reply))
        self.assertFalse(self.agent.is_stop(UserMessage(role="user",content="###STOP###")))
        self.assertEqual(response_content("A normal reply without any marker."),("A normal reply without any marker.",False))

    def test_completion_controls_are_model_decisions_and_never_visible(self):
        self.assertEqual(response_content("###COMPLETE### Your request was accepted."),
                         ("Your request was accepted.",True))
        self.assertEqual(response_content("###CONTINUE### Do you approve this change?"),
                         ("Do you approve this change?",False))
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            response_content("###CONTINUE### A draft ###STOP###")
        self.assertFalse(output_was_truncated(AssistantMessage(role="assistant",content="It ends mid-sentence")))

    def test_metadata_confirmed_text_truncation_is_repaired_before_display(self):
        self.build(compatibility=False)
        incomplete=AssistantMessage(role="assistant",content="Here are your options: You would then",
            raw_data={"choices":[{"finish_reason":"length"}]})
        fixed=AssistantMessage(role="assistant",content="Please provide your email or full name and postal code.")
        self.model.side_effect=[incomplete,fixed]
        reply,self.state=self.agent.generate_next_message(UserMessage(role="user",content="Please help with my order."),self.state)
        self.assertEqual(reply.content,fixed.content)
        self.assertFalse(reply.tool_calls)
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(self.state["model_calls"],2)
        self.assertEqual(self.requests,[])
        self.assertEqual(len(self.state["history"]),2)

    def test_complete_validated_call_is_not_discarded_only_for_length_metadata(self):
        self.build(compatibility=False)
        complete=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="complete-at-boundary",name="verify_customer",
            arguments={"email":self.case["email"],"claimed_customer_id":None,"skill_names":["service-workflow"]})],
            raw_data={"choices":[{"finish_reason":"length"}]})
        self.model.side_effect=[complete]
        reply,self.state=self.agent.generate_next_message(UserMessage(role="user",content="My email is "+self.case["email"]),self.state)
        self.assertEqual(reply.tool_calls[0].name,"verify_customer")
        self.assertEqual(self.state["model_calls"],1)
        self.assertEqual(json.loads(self.result(reply).content)["customer_id"],self.case["customer_id"])

    def test_two_truncated_drafts_do_not_permanently_lock_verified_customer(self):
        incoming=self.read_request("order_address")
        truncated=AssistantMessage(role="assistant",content="The details are",
            raw_data={"choices":[{"finish_reason":"length"}]})
        self.model.side_effect=[truncated,truncated]
        failed,self.state=self.agent.generate_next_message(incoming,self.state)
        self.assertFalse(failed.tool_calls)
        self.assertIsNone(self.state["halt_reason"])
        self.assertIn("truncation",self.state["protocol_diagnostic"])
        self.model.side_effect=None
        reply=self.decision(UserMessage(role="user",content="Continue with the same verified order, please."),
            text="I have the verified order. Which address field should change?")
        self.assertEqual(reply.content,"I have the verified order. Which address field should change?")
        self.assertIsNone(self.state["protocol_diagnostic"])
        self.assertEqual(len([r for r in self.requests if r["path"]=="/v1/customers/search"]),1)

    def test_receipt_reply_uses_one_decision_and_leaves_the_customer_turn_open(self):
        incoming=self.read_request("exchange")
        quote=self.result(self.decision(incoming,"prepare_change",{"kind":"exchange","parameters":self.params}))
        p=json.loads(quote.content)
        self.decision(quote,text="The complete exchange is "+json.dumps(p["facts"]["body"])+
                      "; settlement "+json.dumps(p["facts"]["calculations"])+". Do you approve submission?")
        receipt=self.result(self.decision(UserMessage(role="user",content="I approve that exact exchange and settlement. Submit it."),
            "submit_change",{"kind":"exchange","parameters":self.params}))
        calls=self.state["model_calls"]
        original_history=deepcopy(self.state["history"])
        self.model.side_effect=[AssistantMessage(role="assistant",content="Your exchange request was accepted. Later settlement or shipping is not confirmed.")]
        reply,self.state=self.agent.generate_next_message(receipt,self.state)
        self.assertFalse(self.agent.is_stop(reply))
        self.assertEqual(self.state["model_calls"],calls+1)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.state["history"][:-2],original_history)
        self.assertNotIn("Anything else",json.dumps(self.state["history"]))

    def test_receipt_can_continue_another_goal_without_repeating_the_write(self):
        incoming=self.read_request("default_address")
        self.decision(incoming,text="I will change only your profile default to the confirmed full address. Do you approve this exact update?")
        receipt=self.result(self.decision(UserMessage(role="user",content="Yes, submit the address update. Then read my order status."),
            "submit_change",{"kind":"default_address","parameters":self.params}))
        self.model.side_effect=[AssistantMessage(role="assistant",tool_calls=[ToolCall(id="remaining-read",name="get_owned_order",
                arguments={"order_id":self.case["order_id"]})])]
        reply,self.state=self.agent.generate_next_message(receipt,self.state)
        self.assertEqual(reply.tool_calls[0].name,"get_owned_order")
        self.assertFalse(self.agent.is_stop(reply))
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.result(reply).error,False)

    def test_subset_information_and_separate_profile_update_preserve_order(self):
        # The provider selects the subset and judges consent. The test checks
        # general arithmetic + observation reuse + scoped native API effects,
        # not the model's understanding of any benchmark category or answer.
        for adjustment in (0.0, 3.17):
            with self.subTest(adjustment=adjustment):
                self.build("default_address",compatibility=False)
                native_order=self.runtime.environment.tools.db.orders[self.case["order_id"]]
                native_order.address=type(native_order.address).model_validate(legacy_address(self.params["address"]))
                for row in native_order.items:
                    row.price += adjustment
                order_before=native_order.model_dump()
                verified=self.result(self.decision(UserMessage(role="user",content="Please value the requested subset and explain whether a partial cancellation is possible. My email is "+self.case["email"]),
                    "verify_customer",{"email":self.case["email"],"claimed_customer_id":None,"order_id":self.case["order_id"]}))
                observed=json.loads(verified.content)["requested_order"]
                self.assertGreater(len(observed["items"]),1)
                subset=observed["items"][:-1]
                prices=[float(row["price"]) for row in subset]
                calculated=self.result(self.decision(verified,"calculate",{"operation":"add","values":prices,"decimal_places":2}))
                amount=float(json.loads(calculated.content)["result"])
                self.assertEqual(amount,round(sum(prices),2))
                self.assertNotEqual(amount,round(sum(float(row["price"]) for row in observed["items"]),2))
                self.decision(calculated,text=f"The selected items have recorded value {amount:.2f}. Partial cancellation is unavailable for this pending order; that value is not an approved partial refund. Would you like to keep the order?")
                self.assertEqual(self.writes(),[])
                address={"address":observed["shipping_address"]}
                self.decision(UserMessage(role="user",content="Keep the order. Change only my profile default to the address on this order, without spelling out my street address."),
                    text="Change only your profile default to the existing address on order "+self.case["order_id"]+". This does not change the order. Do you approve this exact profile update?")
                receipt=self.result(self.decision(UserMessage(role="user",content="Yes, submit that exact profile update using the order's address."),
                    "submit_change",{"kind":"default_address","parameters":address}))
                final=self.decision(receipt,text="###COMPLETE### Your profile default now matches that order's address. The order remains unchanged.")
                self.assertFalse(self.agent.is_stop(final))
                self.assertNotIn(address["address"]["address_line_1"],final.content)
                self.assertEqual(len(self.writes()),1)
                self.assertEqual(self.writes()[0]["method"],"PUT")
                self.assertTrue(self.writes()[0]["path"].endswith("/default-shipping-address"))
                self.assertEqual(self.runtime.environment.tools.db.orders[self.case["order_id"]].model_dump(),order_before)
                profile=self.kit.get_customer_summary()
                self.assertEqual(profile["default_shipping_address"],address["address"])

    def test_uncertain_write_prevents_completion_without_becoming_success(self):
        incoming=self.read_request("exchange")
        self.decision(incoming,text="I have read the requested records.")
        self.state["submissions"]={"real-attempt":{"kind":"exchange","order_id":self.case["order_id"],"state":"unknown"}}
        self.model.side_effect=[AssistantMessage(role="assistant",content="Everything is handled. ###STOP###"),
                                AssistantMessage(role="assistant",content="The write outcome remains uncertain; I cannot claim completion.")]
        reply,self.state=self.agent.generate_next_message(UserMessage(role="user",content="Is it done?"),self.state)
        self.assertFalse(self.agent.is_stop(reply))
        self.assertIn("uncertain",reply.content)
        self.assertEqual(self.writes(),[])

    def test_live_schemas_keep_batch_scope_explicit_and_disabled_by_default(self):
        self.build(compatibility=False)
        actions={a.name:a for a in self.agent.gateway.live_actions}
        for name in ("verify_customer","get_owned_order"):
            self.assertIs(actions[name].params.model_fields["include_product_variants"].default,False)
        self.assertIn("read_products",actions)
        self.assertIn("read_context",actions)
        self.assertNotIn("get_service_policy",actions)

    def test_native_orchestrator_observes_normal_completion_and_actual_database_change(self):
        self.build("exchange",compatibility=False)
        case,params=self.case,self.params
        reader,_,_=reference_kit()
        reader.verify_customer(email=case["email"])
        order=reader.get_owned_order(case["order_id"])
        chosen={row["existing_item_id"] for row in params["replacements"]}
        products=list(dict.fromkeys(row["product_id"] for row in order["items"] if row["item_id"] in chosen))

        class Customer:
            def get_init_state(self,message_history=None):
                return 0

            def generate_next_message(self,message,state):
                messages=["Please handle my requested exchange. My email is "+case["email"],
                          "I approve the exact complete exchange and settlement you described. Submit it."]
                if state>=len(messages):
                    return UserMessage(role="user",content="Thank you. ###STOP###"),state+1
                return UserMessage(role="user",content=messages[state]),state+1

            def stop(self,*_args):
                pass

        calls=[("verify_customer",{"email":case["email"],"claimed_customer_id":case["customer_id"],"order_id":case["order_id"]}),
               ("read_products",{"product_ids":products}),
               ("prepare_change",{"kind":"exchange","parameters":params}),
               None,("submit_change",{"kind":"exchange","parameters":params}),None]

        def provider(**kwargs):
            index=self.model.call_count-1
            choice=calls[index]
            if choice:
                name,args=completion.live_call(self.agent,choice[0],choice[1])
                return AssistantMessage(role="assistant",tool_calls=[ToolCall(id=f"native-{index}",name=name,arguments=args)])
            actual=json.loads(next(m.content for m in reversed(kwargs["messages"]) if m.role == "tool"))["result"]
            if index==3:
                return AssistantMessage(role="assistant",content="The complete exchange is "+json.dumps(actual["body"])+" with calculations "+json.dumps(actual["calculations"])+". Do you approve this full submission?")
            self.assertEqual(actual["state"],"accepted")
            return AssistantMessage(role="assistant",content="The exchange request was accepted with the confirmed replacements and settlement; later refund arrival or shipping is not confirmed. ###STOP###")

        self.model.side_effect=provider
        task=Task(id="synthetic-native-dialogue",user_scenario=UserScenario(instructions="Synthetic exchange fixture only"))
        runner=Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,task=task,
                            validate_communication=True)
        simulation=runner.run()
        self.assertEqual(simulation.termination_reason.value,"user_stop")
        self.assertEqual(self.model.call_count,len(calls))
        self.assertEqual(len(self.writes()),1)
        self.assertFalse(self.agent.is_stop(simulation.messages[-2]))
        # The native sealed boundary retains this legitimate message metadata.
        from tau2.hyper.sandbox.sealed_runner import _candidate_visible_message
        self.assertTrue(_candidate_visible_message(simulation.messages[-2])["raw_data"]["retail_service_complete"])
        _,gold,_=reference_kit()
        apply_native_reference(gold.environment.tools,"exchange",case["customer_id"],params)
        self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())

    def test_independent_changes_on_same_order_share_approval_without_extra_quotes(self):
        incoming=self.read_request("order_address")
        address=deepcopy(self.params)
        payment={"order_id":self.case["order_id"],"payment_method_id":self.case["payment_method_ids"]["paypal"]}
        self.decision(incoming,text="Apply the complete new order address "+json.dumps(address["address"])+
                      " and replace payment using "+payment["payment_method_id"]+" for the full charge, refunding the original. Do you approve both changes?")
        self.assertEqual(self.state["proposals"],{})
        approval=UserMessage(role="user",content="I approve both exact changes. Apply the address and then replace payment.")
        result=self.result(self.decision(approval,"submit_change",{"kind":"order_address","parameters":address}))
        result=self.result(self.decision(result,"submit_change",{"kind":"payment","parameters":payment}))
        reply=self.decision(result,text="Both requested operations completed with separate actual receipts. ###STOP###")
        self.assertFalse(self.agent.is_stop(reply))
        self.assertEqual(len(self.writes()),2)
        self.assertEqual({p["state"] for p in self.state["submissions"].values()},{"completed"})
        _,gold,_=reference_kit()
        apply_native_reference(gold.environment.tools,"order_address",self.case["customer_id"],address)
        apply_native_reference(gold.environment.tools,"payment",self.case["customer_id"],payment)
        self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())
