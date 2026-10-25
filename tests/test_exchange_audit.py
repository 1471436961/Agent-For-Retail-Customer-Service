"""Exchange boundary audit with native REST and the real sandbox broker function."""
from contextlib import contextmanager,redirect_stdout
from copy import deepcopy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fixtures.native_runtime import reference_kit,parameters,SEED,ROOT
from agent import create_agent
from tau2.data_model.message import AssistantMessage,ToolCall,UserMessage
from tau2.environment.environment import Environment
from tau2.hyper.agent_context import activate_agent_context,build_agent_context
from tau2.hyper.sandbox.candidate_server import _brokered_generate


class ExchangeAuditTests(unittest.TestCase):
    def setUp(self):
        self.kit,self.runtime,self.requests=reference_kit()

    def agent(self):
        context=build_agent_context(domain="retail_plus",tools=list(self.kit.get_tools().values()),
            resource_root=ROOT/"agent",model_configs=[{"model":"offline-broker-audit"}])
        with activate_agent_context(context):return create_agent()

    @contextmanager
    def broken_broker(self):
        # The installed trusted shim wraps an upstream error in RuntimeError.
        # Only the RPC response is synthetic; no external model is called.
        upstream={"id":"audit-rpc","ok":False,"error":{"type":"JSONDecodeError",
            "message":"Expecting property name enclosed in double quotes: line 1 column 2 (char 1); private upstream draft"}}
        captured=io.StringIO()
        with patch("tau2.hyper.agent_context.generate",_brokered_generate), \
             patch("tau2.hyper.sandbox.candidate_server.uuid.uuid4",return_value=SimpleNamespace(hex="audit-rpc")), \
             patch("sys.stdin",io.StringIO((json.dumps(upstream)+"\n")*2)),redirect_stdout(captured):
            yield captured

    def test_real_broker_format_error_gets_one_repair_without_crashing(self):
        agent=self.agent()
        with self.broken_broker() as captured:
            reply,state=agent.generate_next_message(UserMessage(role="user",content="Please review the requested variants."),agent.get_init_state())
        self.assertFalse(reply.tool_calls)
        self.assertNotIn("private upstream",reply.content)
        self.assertEqual(state["diagnostic"],{"stage":"model_generation","exception_type":"RuntimeError"})
        self.assertEqual(captured.getvalue().count("__TAU2_MODEL_REQUEST__"),2)
        self.assertEqual(self.requests,[])

    def test_service_and_unknown_runtime_errors_keep_host_classification(self):
        for message in ("HTTP503 service unavailable","rate limit exceeded","Model broker returned a mismatched response"):
            with self.subTest(message=message):
                agent=self.agent();failure=RuntimeError(message)
                with patch("tau2.hyper.agent_context.generate",side_effect=failure) as provider:
                    with self.assertRaises(RuntimeError) as caught:
                        agent.generate_next_message(UserMessage(role="user",content="Continue."),agent.get_init_state())
                    self.assertIs(caught.exception,failure)
                    self.assertEqual(provider.call_count,1)
        self.assertEqual(self.requests,[])

    def test_broker_error_after_exchange_keeps_actual_receipt_and_never_reposts(self):
        case=SEED["cases"][1];args=parameters("exchange",case,self.runtime)
        self.kit.verify_customer(email=case["email"])
        call=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="actual-exchange",name="request_exchange",arguments=args)])
        receipt=Environment("retail_plus",policy="",tools=self.kit).get_response(call.tool_calls[0])
        self.assertFalse(receipt.error,receipt.content)
        self.assertEqual(json.loads(receipt.content)["state"],"accepted")
        agent=self.agent();before=deepcopy(self.requests)
        with self.broken_broker():
            reply,state=agent.generate_next_message(receipt,agent.get_init_state([call]))
        self.assertEqual(self.requests,before)
        self.assertEqual(state["messages"][-2]["content"],receipt.content)
        self.assertFalse(agent.is_stop(reply))

    def test_backend_business_refusals_are_controlled_results_not_candidate_crashes(self):
        for condition in ("processed","insufficient_gift","wrong_product","unavailable","too_many_occurrences"):
            with self.subTest(condition=condition):
                kit,runtime,requests=reference_kit()
                kind="modify_items" if condition in {"processed","insufficient_gift"} else "exchange"
                case=SEED["cases"][0 if kind=="modify_items" else 1]
                args=parameters(kind,case,runtime);db=runtime.environment.tools.db
                order=db.orders[case["order_id"]]
                if condition=="processed":order.status="processed"
                elif condition=="insufficient_gift":
                    method=case["payment_method_ids"]["gift_card"]
                    db.users[case["customer_id"]].payment_methods[method].balance=0
                    args["payment_method_id"]=method
                elif condition=="wrong_product":
                    original=next(i for i in order.items if i.item_id==args["replacements"][0]["existing_item_id"])
                    other=next(p for p in db.products.values() if p.product_id!=original.product_id)
                    args["replacements"][0]["replacement_item_id"]=next(iter(other.variants))
                elif condition=="unavailable":
                    identifier=args["replacements"][0]["replacement_item_id"]
                    next(v for p in db.products.values() for v in p.variants.values() if v.item_id==identifier).available=False
                else:args["replacements"]*=len(order.items)+1
                kit.verify_customer(email=case["email"])
                before=runtime.environment.get_db_hash()
                name="modify_order_items" if kind=="modify_items" else "request_exchange"
                reply=Environment("retail_plus",policy="",tools=kit).get_response(ToolCall(id="refusal",name=name,arguments=args))
                self.assertFalse(reply.error,reply.content)
                result=json.loads(reply.content)
                self.assertEqual(result["state"],"failed",result)
                self.assertIn(int(result["http_status"]),{400,404,409,422})
                self.assertEqual(runtime.environment.get_db_hash(),before)

    def test_return_and_exchange_share_backend_opportunity_without_crashing(self):
        case=SEED["cases"][1];exchange=parameters("exchange",case,self.runtime)
        self.kit.verify_customer(email=case["email"])
        returning={"order_id":case["order_id"],"item_ids":[exchange["replacements"][0]["existing_item_id"]],
            "refund_payment_method_id":case["payment_method_ids"]["credit_card"]}
        accepted=self.kit.request_return(**returning)
        self.assertEqual(accepted["state"],"accepted")
        before=self.runtime.environment.get_db_hash()
        refused=self.kit.request_exchange(**exchange)
        self.assertEqual(refused["state"],"failed")
        self.assertEqual(self.runtime.environment.get_db_hash(),before)

    def test_empty_write_lists_are_rejected_before_any_rest_write(self):
        case=SEED["cases"][1]
        self.kit.verify_customer(email=case["email"])
        for name,args in (("request_exchange",{"order_id":case["order_id"],"replacements":[],"payment_method_id":case["payment_method_ids"]["paypal"]}),
                          ("request_return",{"order_id":case["order_id"],"item_ids":[],"refund_payment_method_id":case["payment_method_ids"]["credit_card"]})):
            with self.subTest(name=name):
                result=getattr(self.kit,name)(**args)
                self.assertEqual(result["state"],"failed")
                self.assertEqual(result["error"],"invalid_write_parameters")
        self.assertFalse(any(r["method"]!="GET" and r["path"]!="/v1/customers/search" for r in self.requests))

    def test_tool_minimum_lengths_match_original_openapi(self):
        contract=json.loads((ROOT/"materials/client_api/openapi.yaml").read_text(encoding="utf-8"))
        for tool,path,list_field,payment_field in (
            ("request_exchange","exchanges","replacements","payment_method_id"),
            ("modify_order_items","item-modifications","replacements","payment_method_id"),
            ("request_return","returns","item_ids","refund_payment_method_id")):
            with self.subTest(tool=tool):
                expected=contract["paths"]["/v1/orders/{order_id}/"+path]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
                actual=self.kit.get_tools()[tool].params.model_json_schema()["properties"]
                self.assertEqual(actual[list_field]["minItems"],expected[list_field]["minItems"])
                self.assertEqual(actual[payment_field]["minLength"],expected[payment_field]["minLength"])

    def test_incomplete_structured_identity_is_corrected_before_host_tool_round(self):
        agent=self.agent()
        invalid=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="incomplete-identity",name="verify_customer",
            arguments={"first_name":"synthetic_account_42","postal_code":"12345"})])
        question=AssistantMessage(role="assistant",content="Please provide your full first and last name, plus your desired item changes.")
        with patch("tau2.hyper.agent_context.generate",side_effect=[invalid,question]) as provider:
            reply,state=agent.generate_next_message(UserMessage(role="user",content="My account name is synthetic_account_42, postal code12345."),agent.get_init_state())
            self.assertEqual(provider.call_count,2)
            self.assertFalse(reply.tool_calls)
            self.assertIn("first_name, last_name and postal_code together",provider.call_args.kwargs["messages"][-1].content)
        self.assertEqual(self.requests,[])

    def test_multi_order_upgrade_can_recover_from_gift_refusal_without_changing_shoe_size(self):
        case=SEED["cases"][0];db=self.runtime.environment.tools.db
        original=db.orders[case["order_id"]]
        product_type=type(next(iter(db.products.values())))
        shoe=product_type.model_validate({"product_id":"synthetic-shoe","name":"Synthetic Shoe","variants":{
            "shoe-8-base":{"item_id":"shoe-8-base","options":{"size":"8","material":"mesh"},"available":True,"price":70},
            "shoe-8-top":{"item_id":"shoe-8-top","options":{"size":"8","material":"leather"},"available":True,"price":160},
            "shoe-9-top":{"item_id":"shoe-9-top","options":{"size":"9","material":"leather"},"available":True,"price":999},
            "shoe-8-unavailable":{"item_id":"shoe-8-unavailable","options":{"size":"8","material":"premium"},"available":False,"price":5000}}})
        db.products[shoe.product_id]=shoe
        original.items.append(type(original.items[0]).model_validate({"name":shoe.name,"product_id":shoe.product_id,
            "item_id":"shoe-8-base","options":{"size":"8","material":"mesh"},"price":70}))
        original.payment_history[0].amount+=70
        for suffix,status in (("OTHER","pending"),("LOCKED","processed"),("RECEIVED","delivered")):
            clone=original.model_copy(deep=True);clone.order_id="#SYNTHETIC-UPGRADE-"+suffix;clone.status=status
            db.orders[clone.order_id]=clone;db.users[case["customer_id"]].orders.append(clone.order_id)
        gift=case["payment_method_ids"]["gift_card"]
        db.users[case["customer_id"]].payment_methods[gift].balance=0
        from tau2.domains.retail_plus.tools import RetailPlusTools
        gold=RetailPlusTools(db.model_copy(deep=True))
        observed=self.kit.verify_customer(email=case["email"],include_orders=True)["orders"]
        eligible=[o for o in observed if o["status"]=="pending"]
        self.assertEqual(len(eligible),2)
        for order in eligible:
            products=self.kit.get_products(product_ids=list(dict.fromkeys(i["product_id"] for i in order["items"])))
            catalog={p["product_id"]:p for p in products["products"]}
            rows=[]
            for old in order["items"]:
                choices=[v for v in catalog[old["product_id"]]["items"] if v["available"] and
                    (old["product_id"]!=shoe.product_id or v["options"]["size"]==old["options"]["size"])]
                target=max(choices,key=lambda v:v["price"])
                if target["item_id"]!=old["item_id"]:
                    rows.append({"existing_item_id":old["item_id"],"replacement_item_id":target["item_id"]})
            args={"order_id":order["order_id"],"replacements":rows,"payment_method_id":gift}
            before=self.runtime.environment.get_db_hash()
            self.assertEqual(self.kit.modify_order_items(**args)["state"],"failed")
            self.assertEqual(self.runtime.environment.get_db_hash(),before)
            args["payment_method_id"]=case["payment_method_ids"]["paypal"]
            self.assertEqual(self.kit.modify_order_items(**args)["state"],"completed")
            from fixtures.native_runtime import apply_native_reference
            apply_native_reference(gold,"modify_items",case["customer_id"],args)
        self.assertEqual(self.runtime.environment.tools.db.model_dump(),gold.db.model_dump())
        for identifier in (o["order_id"] for o in eligible):
            self.assertEqual(next(i for i in db.orders[identifier].items if i.product_id==shoe.product_id).options["size"],"8")

    def test_exchange_priority_keeps_other_items_and_allows_negative_paypal_difference(self):
        case=SEED["cases"][1];db=self.runtime.environment.tools.db;order=db.orders[case["order_id"]]
        product_type=type(next(iter(db.products.values())))
        product=product_type.model_validate({"product_id":"synthetic-boot","name":"Synthetic Boot","variants":{
            "boot-dry":{"item_id":"boot-dry","options":{"size":"8","material":"leather","waterproof":"no"},"available":True,"price":180},
            "boot-wet":{"item_id":"boot-wet","options":{"size":"8","material":"leather","waterproof":"yes"},"available":True,"price":110}}})
        db.products[product.product_id]=product
        order.items.append(type(order.items[0]).model_validate({"name":product.name,"product_id":product.product_id,
            "item_id":"boot-dry","options":product.variants["boot-dry"].options,"price":180}))
        order.payment_history[0].amount+=180
        originals=deepcopy(order.items)
        self.kit.verify_customer(email=case["email"],include_orders=True)
        args={"order_id":order.order_id,"replacements":[{"existing_item_id":"boot-dry","replacement_item_id":"boot-wet"}],
            "payment_method_id":case["payment_method_ids"]["paypal"]}
        outcome=self.kit.request_exchange(**args)
        self.assertEqual(outcome["state"],"accepted")
        self.assertEqual(outcome["receipt"]["exchange"]["price_difference"],-70)
        self.assertEqual(order.items,originals,"Application does not prematurely replace or return unrelated originals")
        writes=[r for r in self.requests if r["method"]!="GET" and r["path"]!="/v1/customers/search"]
        self.assertEqual(len(writes),1)
        self.assertTrue(writes[0]["path"].endswith("/exchanges"))
