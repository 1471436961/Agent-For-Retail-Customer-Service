"""Current model-facing Agent, real SDK/backend; provider choices are controlled.

This validates the canonical execution and completion contract, not real model
understanding. Older executor/plan tests separately exercise compatibility.
"""
import json
import unittest
from copy import deepcopy
import test_task_completion as completion
from test_reference_runtime import reference_kit, apply_native_reference
from tau2.data_model.message import UserMessage, AssistantMessage
from support_agent.state import SessionState
from support_agent.domain.reply import response_content
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.data_model.tasks import Task, UserScenario
from tau2.data_model.message import ToolCall


class ProductionAgentTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

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
                quote=self.result(self.decision(incoming,"prepare_change",{"kind":kind,"parameters":self.params}))
                self.assertEqual(len(self.requests),before)  # Reuse genuine already-read facts.
                p=json.loads(quote.content)
                text="The complete requested change is "+json.dumps(p["facts"]["body"])+"; calculations "+json.dumps(p["facts"]["calculations"])+". Do you approve this complete submission?"
                recap=self.decision(quote,text=text)
                self.assertFalse(self.agent.is_stop(recap))
                self.assertEqual(self.writes(),[])
                actions={a.name for a in self.agent.gateway.live_actions}
                self.assertIn("submit_change",actions)
                self.assertFalse(any(name.startswith("execute_") for name in actions))
                self.assertFalse({"plan_changes","review_change","prepare_plan_step"}&actions)
                receipt=self.result(self.decision(UserMessage(role="user",content="I approve those exact complete details. Submit now."),
                    "submit_change",{"kind":kind,"parameters":self.params}))
                outcome=json.loads(receipt.content)
                self.assertEqual(outcome["state"],"accepted" if kind in {"return","exchange"} else "completed")
                final=self.decision(receipt,text="The actual receipt confirms the requested operation. No later settlement or shipping is implied. ###STOP###")
                self.assertTrue(self.agent.is_stop(final))
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

    def test_live_schemas_do_not_request_unfocused_catalog_loading(self):
        self.build(compatibility=False)
        actions={a.name:a for a in self.agent.gateway.live_actions}
        for name in ("verify_customer","get_owned_order"):
            self.assertNotIn("include_product_variants",actions[name].params.model_fields)
        self.assertIn("read_products",actions)

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
                    raise AssertionError("The Agent failed to end after the resolved request")
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
                return AssistantMessage(role="assistant",tool_calls=[ToolCall(id=f"native-{index}",name=choice[0],arguments=choice[1])])
            actual=json.loads(kwargs["messages"][-1].content)["result"]
            if index==3:
                return AssistantMessage(role="assistant",content="The complete exchange is "+json.dumps(actual["body"])+" with calculations "+json.dumps(actual["calculations"])+". Do you approve this full submission?")
            self.assertEqual(actual["state"],"accepted")
            return AssistantMessage(role="assistant",content="The exchange request was accepted with the confirmed replacements and settlement; later refund arrival or shipping is not confirmed. ###STOP###")

        self.model.side_effect=provider
        task=Task(id="synthetic-native-dialogue",user_scenario=UserScenario(instructions="Synthetic exchange fixture only"))
        runner=Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,task=task,
                            validate_communication=True)
        simulation=runner.run()
        self.assertEqual(simulation.termination_reason.value,"agent_stop")
        self.assertEqual(self.model.call_count,len(calls))
        self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.agent.is_stop(simulation.messages[-1]))
        # The native sealed boundary retains this legitimate message metadata.
        from tau2.hyper.sandbox.sealed_runner import _candidate_visible_message
        self.assertTrue(_candidate_visible_message(simulation.messages[-1])["raw_data"]["retail_service_complete"])
        _,gold,_=reference_kit()
        apply_native_reference(gold.environment.tools,"exchange",case["customer_id"],params)
        self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())

    def test_independent_changes_on_same_order_keep_their_quotes_and_share_approval(self):
        incoming=self.read_request("order_address")
        address=deepcopy(self.params)
        payment={"order_id":self.case["order_id"],"payment_method_id":self.case["payment_method_ids"]["paypal"]}
        first=self.result(self.decision(incoming,"prepare_change",{"kind":"order_address","parameters":address}))
        second=self.result(self.decision(first,"prepare_change",{"kind":"payment","parameters":payment}))
        self.decision(second,text="Apply the complete new order address "+json.dumps(address["address"])+
                      " and replace payment using "+payment["payment_method_id"]+" for the full charge, refunding the original. Do you approve both changes?")
        self.assertEqual({p["state"] for p in self.state["proposals"].values()},{"prepared"})
        approval=UserMessage(role="user",content="I approve both exact changes. Apply the address and then replace payment.")
        result=self.result(self.decision(approval,"submit_change",{"kind":"order_address","parameters":address}))
        result=self.result(self.decision(result,"submit_change",{"kind":"payment","parameters":payment}))
        reply=self.decision(result,text="Both requested operations completed with separate actual receipts. ###STOP###")
        self.assertTrue(self.agent.is_stop(reply))
        self.assertEqual(len(self.writes()),2)
        self.assertEqual({p["state"] for p in self.state["proposals"].values()},{"completed"})
        _,gold,_=reference_kit()
        apply_native_reference(gold.environment.tools,"order_address",self.case["customer_id"],address)
        apply_native_reference(gold.environment.tools,"payment",self.case["customer_id"],payment)
        self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())
