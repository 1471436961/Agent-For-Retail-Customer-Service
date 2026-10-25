"""Contract/effect tests against the real SDK, with independent native DB oracle."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from fixtures.native_runtime import reference_kit,parameters,apply_native_reference,SEED
from tools import Tools
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.defects import AsyncCompletionDefect,DefectProfile
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from support_agent.adapters.read_api import read_order,read_products
from support_agent.domain.identity import ReadError

WRITE_TOOLS={"default_address":"update_default_address","order_address":"update_order_address",
    "cancel":"cancel_order","payment":"change_order_payment","modify_items":"modify_order_items",
    "return":"request_return","exchange":"request_exchange"}
OPERATION_IDS={"default_address":"replaceCustomerDefaultShippingAddress","order_address":"replaceOrderShippingAddress",
    "cancel":"createOrderCancellation","payment":"replaceOrderPaymentMethod","modify_items":"createOrderItemModification",
    "return":"createOrderReturn","exchange":"createOrderExchange"}

class Clock:
    def __init__(self):self.now=0;self.delays=[]
    def __call__(self):return self.now
    def sleep(self,seconds):self.now+=seconds;self.delays.append(seconds)

class SimpleToolsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('support_agent.tools_impl.sleep',return_value=None))

    def test_all_seven_writes_match_native_database_and_replay(self):
        for kind,name in WRITE_TOOLS.items():
            with self.subTest(kind=kind):
                kit,runtime,requests=reference_kit()
                case=SEED["cases"][1 if kind in {"return","exchange"} else 0]
                args=parameters(kind,case,runtime)
                kit.verify_customer(email=case["email"])
                outcome=getattr(kit,name)(**args)
                self.assertEqual(outcome["state"],"accepted" if kind in {"return","exchange"} else "completed",outcome)
                _,gold,_=reference_kit()
                apply_native_reference(gold.environment.tools,kind,case["customer_id"],args)
                self.assertEqual(runtime.environment.get_db_hash(),gold.environment.get_db_hash())
                replay,other,_=reference_kit()
                replay.verify_customer(email=case["email"])
                self.assertEqual(getattr(replay,name)(**args),outcome)
                self.assertEqual(other.environment.get_db_hash(),runtime.environment.get_db_hash())
                self.assertEqual(len([r for r in requests if r["method"]!="GET" and r["path"]!="/v1/customers/search"]),1)

    def test_all_seven_async_writes_wait_for_real_effect(self):
        for kind,name in WRITE_TOOLS.items():
            with self.subTest(kind=kind):
                _,source,_=reference_kit();clock=Clock()
                profile=DefectProfile(manifest_id="synthetic-async",manifest_sha256="0"*64,manifest_version=1,domain="retail_plus",
                    defects=(AsyncCompletionDefect(id="async",operation_id=OPERATION_IDS[kind],status_path="/v1/operation-status/{workflow_id}",
                        min_delay_seconds=3,max_delay_seconds=3,retry_after_seconds=1),))
                rt=ClientAPIRuntime(source.environment,conversation_id="synthetic-async",defect_profile=profile,monotonic_clock=clock)
                requests=[]
                def transport(req):requests.append(deepcopy(req));return rt.dispatch(req)
                kit=Tools(ClientAPI(transport,context=rt.context));case=SEED["cases"][1 if kind in {"return","exchange"} else 0]
                args=parameters(kind,case,rt);kit.verify_customer(email=case["email"])
                before=rt.environment.get_db_hash()
                with patch("support_agent.adapters.workflow_api.time.sleep",clock.sleep):outcome=getattr(kit,name)(**args)
                self.assertIn(outcome["state"],{"accepted","completed"},outcome)
                self.assertNotEqual(rt.environment.get_db_hash(),before)
                self.assertEqual(clock.delays,[1,1,1])
                self.assertEqual(len([r for r in requests if r["method"]!="GET" and r["path"]!="/v1/customers/search"]),1)

    def test_unknown_write_blocks_repost_and_other_record_is_not_globally_locked(self):
        kit,rt,requests=reference_kit();case=SEED["cases"][0]
        kit.verify_customer(email=case["email"]);original=kit.client_api._transport
        def transport(req):
            if req["method"]=="PUT":requests.append(deepcopy(req));return {"status_code":503,"body":{}}
            return original(req)
        kit.client_api._transport=transport
        args=parameters("order_address",case,rt)
        self.assertEqual(kit.update_order_address(**args)["state"],"unknown")
        with self.assertRaisesRegex(ReadError,"inspect_uncertain"):kit.update_order_address(**args)
        self.assertEqual(kit.inspect_operation(case["order_id"])["state"],"unknown")
        self.assertEqual(len([r for r in requests if r["method"]=="PUT"]),1)

    def test_identity_ownership_claim_and_context_boundaries(self):
        kit,rt,requests=reference_kit();a,b=SEED["cases"][:2]
        with self.assertRaisesRegex(ReadError,"identity_required"):kit.get_orders()
        self.assertEqual(requests,[])
        with self.assertRaises(ReadError):kit.verify_customer(email=a["email"],claimed_customer_id=b["customer_id"])
        self.assertFalse(any(r["method"]=="GET" for r in requests))
        kit.verify_customer(email=a["email"])
        before=len(requests)
        with self.assertRaisesRegex(ReadError,"order_access_denied"):kit.get_orders([b["order_id"]])
        self.assertFalse(any(r["path"].startswith("/v1/orders/") for r in requests[before:]))
        with self.assertRaises(ReadError):kit.verify_customer(email=b["email"])
        kit.client_api._set_context({"conversation_id":"another-conversation"})
        with self.assertRaisesRegex(ReadError,"identity_required"):kit.get_customer()

    def test_combined_reads_do_not_duplicate_customer_or_named_order(self):
        kit,rt,requests=reference_kit();case=SEED["cases"][0]
        value=kit.verify_customer(email=case["email"],order_id=case["order_id"][1:],include_orders=True,skill_names=["item-updates"])
        self.assertEqual(value["order"]["order_id"],case["order_id"])
        self.assertEqual(len([r for r in requests if r["path"].startswith("/v1/customers/") and r["method"]=="GET"]),1)
        self.assertEqual(len([r for r in requests if r["path"].startswith("/v1/orders/")]),len(value["orders"]))
        self.assertEqual(set(value["business_skills"]),{"names","versions"})

    def test_profile_restore_does_not_undo_order_address(self):
        kit,rt,_=reference_kit();case=SEED["cases"][0]
        old=kit.verify_customer(email=case["email"])["default_shipping_address"]
        args=parameters("order_address",case,rt)
        kit.update_default_address(args["address"]);kit.update_order_address(**args);kit.update_default_address(old)
        self.assertEqual(kit.get_customer()["default_shipping_address"],old)
        self.assertEqual(kit.get_orders([case["order_id"]])["orders"][0]["shipping_address"]["address_line_1"],args["address"]["address_line_1"])

    def test_scalar_strings_status_alias_and_schema_errors(self):
        kit,rt,_=reference_kit();case=SEED["cases"][1];transport=kit.client_api._transport
        def serialized(req):
            response=deepcopy(transport(req));body=response["body"]
            if req["path"].startswith("/v1/orders/"):body["orderStatus"]=body.pop("status")
            response["body"]=json.loads(rt.environment.to_json_str(body));return response
        kit.client_api=ClientAPI(serialized,context=rt.context)
        kit.verify_customer(email=case["email"])
        actual=kit.get_orders([case["order_id"]])["orders"][0]
        self.assertEqual(actual["status"],"delivered")
        self.assertIsInstance(actual["items"][0]["price"],float)
        bad={"status_code":200,"body":{**actual,"status":"shipped"}}
        with self.assertRaises(ReadError):read_order(ClientAPI(lambda _:bad),case["order_id"])

    def test_read_rate_limit_is_bounded(self):
        clock=Clock();calls=[]
        def transport(r):calls.append(r);return {"status_code":429,"body":{},"headers":{"Retry-After":"10"}}
        with patch("support_agent.adapters.read_api.time.sleep",clock.sleep),self.assertRaises(ReadError):read_products(ClientAPI(transport))
        self.assertEqual(clock.delays,[10,10]);self.assertEqual(len(calls),3)

    def test_transfer_is_real_and_blocks_followup_api(self):
        kit,rt,requests=reference_kit()
        result=kit.transfer_to_human("Customer explicitly requests a human complaint review.")
        self.assertEqual(result["state"],"accepted")
        with self.assertRaises(ReadError):kit.get_customer()
        self.assertEqual(len(requests),1)
