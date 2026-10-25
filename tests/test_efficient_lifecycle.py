"""Controlled decisions, independent native lifecycle and database checks.

These prove execution/termination of generic workflows, not LLM comprehension
or a remote step limit. No benchmark identities or expected answers are used.
"""
import json
import unittest
from copy import deepcopy

import test_task_completion as c
from test_reference_runtime import reference_kit, apply_native_reference
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.tasks import Task, UserScenario
from tau2.orchestrator.orchestrator import Orchestrator


class EfficientLifecycleTests(unittest.TestCase):
    build=c.TaskCompletionTests.build
    writes=c.TaskCompletionTests.writes

    def run_flow(self,schedule,customer,limit):
        outer=self
        class Customer:
            def get_init_state(self,message_history=None): return 0
            def generate_next_message(self,message,state):
                return UserMessage(role="user",content=customer[state]),state+1
            def stop(self,*args): pass
        def provider(**kwargs):
            choice=schedule[outer.model.call_count-1]
            if callable(choice): choice=choice()
            if isinstance(choice,tuple):
                return AssistantMessage(role="assistant",tool_calls=[ToolCall(
                    id=f"efficient-{outer.model.call_count}",name=choice[0],arguments=choice[1])])
            return AssistantMessage(role="assistant",content=choice)
        self.model.side_effect=provider
        task=Task(id="synthetic-service-lifecycle",user_scenario=UserScenario(instructions="Generic controlled lifecycle test"))
        return Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),environment=self.host,
            task=task,max_steps=limit,validate_communication=True).run()

    def intake(self,skills):
        db=self.runtime.environment.tools.db
        order=db.orders[self.case["order_id"]]
        selected={row["existing_item_id"] for row in self.params["replacements"]}
        names=list(dict.fromkeys(item.name for item in order.items if item.item_id in selected))
        return {"email":self.case["email"],"claimed_customer_id":None,"skill_names":skills,
                "order_id":order.order_id,"product_names":names}

    def test_named_order_batched_catalog_and_one_formula_end_with_user_stop(self):
        self.build("exchange",compatibility=False)
        intake=self.intake(["delivered-exchanges"])
        values=[]
        def calculation():
            order=self.kit._observed_orders[self.case["order_id"]]
            for row in self.params["replacements"]:
                old=next(i for i in order["items"] if i["item_id"]==row["existing_item_id"])
                target=next(i for i in self.kit._observed_products[old["product_id"]]["items"]
                            if i["item_id"]==row["replacement_item_id"])
                values.append((target["price"],old["price"]))
            return "calculate",{"expression":"+".join(f"({new!r}-{old!r})" for new,old in values),"decimal_places":2}
        schedule=["Please provide your email or full name and postal code.",
            ("verify_customer",intake),"Which replacement specifications do you want?",calculation,
            lambda:"Complete replacements "+json.dumps(self.params)+"; difference "+str(round(sum(n-o for n,o in values),2))+". Is this the complete list and do you approve?",
            ("request_exchange",self.params),"The receipt confirms exchange requested. Instructions are provided by email; settlement/shipping are not confirmed."]
        run=self.run_flow(schedule,["Exchange the selected items.",self.case["email"],
            "Use the specified replacements and show the full difference.","That is the complete list. I approve those exact changes and settlement.","Thanks. ###STOP###"],16)
        self.assertEqual(run.termination_reason.value,"user_stop")
        calls=[c.name for m in run.messages for c in (getattr(m,"tool_calls",None) or [])]
        self.assertEqual(calls,["verify_customer","calculate","request_exchange"])
        self.assertEqual(len(self.writes()),1)
        before_write=self.requests[:next(i for i,r in enumerate(self.requests) if r["path"].endswith("/exchanges"))]
        # Write preflight legitimately re-reads current facts; verification itself
        # should not discover unrelated orders or repeat a customer read.
        self.assertEqual(set(r["path"] for r in before_write if r["path"].startswith("/v1/orders/")),
                         {"/v1/orders/"+self.case["order_id"].replace("#","%23")})
        _,gold,_=reference_kit()
        apply_native_reference(gold.environment.tools,"exchange",self.case["customer_id"],self.params)
        self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())

    def test_two_scope_revisions_keep_only_confirmed_return_and_allow_natural_close(self):
        for identity_first,limit in ((True,16),(False,18)):
            with self.subTest(identity_first=identity_first):
                self.build("exchange",compatibility=False)
                db=self.runtime.environment.tools.db
                order=db.orders[self.case["order_id"]]
                first_id=self.params["replacements"][0]["existing_item_id"]
                other=next(i for i in order.items if i.item_id!=first_id)
                target=next(v for v in db.products[other.product_id].variants.values()
                            if v.available and v.item_id!=other.item_id and v.options!=other.options)
                self.params["replacements"].append({"existing_item_id":other.item_id,
                    "replacement_item_id":target.item_id})
                self.assertEqual(len(self.params["replacements"]),2)
                intake=self.intake(["delivered-exchanges"])
                item=self.params["replacements"][0]["existing_item_id"]
                returning={"order_id":self.case["order_id"],"item_ids":[item],
                    "refund_payment_method_id":self.case["payment_method_ids"]["credit_card"]}
                def recap_all():
                    p=self.kit._proposals["proposal-1"]
                    return "Full requested replacements "+json.dumps(p["facts"]["body"])+" and amounts "+json.dumps(p["facts"]["calculations"])+". Is this the complete list and do you approve?"
                def recap_retained():
                    p=self.kit._proposals["proposal-1"]
                    row=next(r for r in p["facts"]["calculations"] if r.get("purpose")=="replacement_row" and r["row_index"]==0)
                    return "Revised proposal: only the first displayed replacement; others retained. Same saved method, difference "+str(row["result"])+". Approve this revised complete list?"
                def recap_return():
                    old=next(i for i in self.kit._observed_orders[self.case["order_id"]]["items"] if i["item_id"]==item)
                    return "No exchange will be submitted. Return only "+json.dumps(old)+" for its recorded price to the original method "+returning["refund_payment_method_id"]+". Instructions by email; no arrival date is promised. Approve this complete return?"
                schedule=(["Please provide your email or full name and postal code."] if not identity_first else [])+[
                    ("verify_customer",intake),("prepare_change",{"kind":"exchange","parameters":self.params}),
                    recap_all,recap_retained,recap_return,("request_return",returning),
                    "The actual receipt confirms the requested return only; no exchange or refund arrival is asserted."]
                customer=["Exchange the specified items."+(" My email is "+self.case["email"] if identity_first else "")]
                if not identity_first: customer.append(self.case["email"])
                customer += ["Actually exchange only the first item; retain the others.",
                    "I changed my mind again. No exchange; return that original instead.",
                    "I approve only that complete return to the stated original method.","Thanks. ###STOP###"]
                run=self.run_flow(schedule,customer,limit)
                self.assertEqual(run.termination_reason.value,"user_stop")
                calls=[c.name for m in run.messages for c in (getattr(m,"tool_calls",None) or [])]
                self.assertEqual(calls,["verify_customer","prepare_change","request_return"])
                self.assertEqual(len(self.writes()),1)
                _,gold,_=reference_kit()
                apply_native_reference(gold.environment.tools,"return",self.case["customer_id"],returning)
                self.assertEqual(self.runtime.environment.get_db_hash(),gold.environment.get_db_hash())
