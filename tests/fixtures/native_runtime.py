"""Synthetic public seed through the installed real REST runtime and database."""
from copy import deepcopy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"agent"))
from fixtures.p2_backend import SyntheticBackend
from tools import Tools
from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail_plus.tools import RetailPlusTools
from tau2.environment.environment import Environment
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.development import apply_development_seed
from tau2.hyper.client_api.runtime import ClientAPIRuntime
SEED=json.loads((ROOT/"materials/client_api/development_seed.json").read_text())

def legacy_address(a):
    return {"address1":a["address_line_1"],"address2":a.get("address_line_2") or "",
            "city":a["city"],"state":a["region"],"country":a["country"],"zip":a["postal_code"]}

def reference_kit():
    records=deepcopy(SyntheticBackend().records)
    db=RetailDB.model_validate({
        "users":{c["customer_id"]:{"user_id":c["customer_id"],"name":c["name"],"email":c["email"],
            "address":legacy_address(c["default_shipping_address"]),"payment_methods":{m["id"]:m for m in c["payment_methods"]},"orders":c["order_ids"]} for c in records["customers"]},
        "products":{p["product_id"]:{"product_id":p["product_id"],"name":p["name"],"variants":{i["item_id"]:i for i in p["items"]}} for p in records["products"]},
        "orders":{o["order_id"]:{"order_id":o["order_id"],"user_id":o["customer_id"],"address":legacy_address(o["shipping_address"]),
            "items":o["items"],"status":o["status"],"fulfillments":o["fulfillments"],"payment_history":o["payments"]} for o in records["orders"]}})
    env=Environment("retail_plus",policy="",tools=RetailPlusTools(db))
    apply_development_seed(env)
    runtime=ClientAPIRuntime(env,conversation_id="synthetic-native-runtime")
    requests=[]
    def transport(request):
        requests.append(deepcopy(request))
        return runtime.dispatch(request)
    return Tools(ClientAPI(transport,context=runtime.context)),runtime,requests

def parameters(kind,case,runtime):
    order=runtime.environment.tools.db.orders[case["order_id"]]
    if kind in {"default_address","order_address"}:
        params={"address":{"address_line_1":"90 New Road","city":"Boston","region":"MA","country":"USA","postal_code":"02101"}}
        if kind=="order_address":params["order_id"]=order.order_id
        return params
    params={"order_id":order.order_id}
    if kind=="cancel":params["reason"]="no longer needed"
    elif kind=="payment":params["payment_method_id"]=case["payment_method_ids"]["paypal"]
    elif kind=="return":params.update(item_ids=[i.item_id for i in order.items],refund_payment_method_id=case["payment_method_ids"]["credit_card"])
    else:
        rows=[]
        for old in order.items:
            target=next(v for v in runtime.environment.tools.db.products[old.product_id].variants.values() if v.available and v.item_id!=old.item_id)
            rows.append({"existing_item_id":old.item_id,"replacement_item_id":target.item_id})
        params.update(replacements=rows,payment_method_id=case["payment_method_ids"]["paypal"])
    return params

def serialized_reference_kit():
    kit,runtime,requests=reference_kit()
    transport=kit.client_api._transport
    def serialized(request):
        response=deepcopy(transport(request))
        response["body"]=json.loads(Environment.to_json_str(response["body"]))
        return response
    kit.client_api=ClientAPI(serialized,context=runtime.context)
    return kit,runtime,requests

def apply_native_reference(tools,kind,customer,params):
    if kind in {"default_address","order_address"}:
        a=legacy_address(params["address"])
        if kind=="default_address":return tools.modify_user_address(user_id=customer,**a)
        return tools.modify_pending_order_address(order_id=params["order_id"],**a)
    if kind=="cancel":return tools.cancel_pending_order(params["order_id"],params["reason"])
    if kind=="payment":return tools.modify_pending_order_payment(params["order_id"],params["payment_method_id"])
    if kind=="return":return tools.return_delivered_order_items(params["order_id"],params["item_ids"],params["refund_payment_method_id"])
    fn=tools.modify_pending_order_items if kind=="modify_items" else tools.exchange_delivered_order_items
    return fn(params["order_id"],[r["existing_item_id"] for r in params["replacements"]],
              [r["replacement_item_id"] for r in params["replacements"]],params["payment_method_id"])
