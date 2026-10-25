"""REST writes and actual receipt validation, without proposals or executors."""
from collections import Counter
from copy import deepcopy
from urllib.parse import quote
from typing import Literal
from pydantic import Field,ValidationError
from support_agent.adapters.read_models import Record,Address,ItemReplacement,NonEmpty,OrderCancellation,OrderPayment,OrderItem,OrderReturn,OrderExchange
from support_agent.adapters.response_values import normalize_response,ResponseValueError
from support_agent.adapters.workflow_api import accepted_workflow,poll_workflow

class DefaultAddressResult(Record):
    customer_id:str
    default_shipping_address:Address
class OrderAddressResult(Record):
    order_id:str
    shipping_address:Address
class CancellationResult(Record):
    order_id:str
    status:Literal["cancelled"]
    cancellation:OrderCancellation
    payments:list[OrderPayment]
class PaymentResult(Record):
    order_id:str
    payments:list[OrderPayment]
class ItemModificationResult(Record):
    order_id:str
    status:Literal["pending (items modified)"]
    items:list[OrderItem]
    payments:list[OrderPayment]
class ReturnResult(Record):
    order_id:str
    status:Literal["return requested"]
    return_request:OrderReturn
class ExchangeResult(Record):
    order_id:str
    status:Literal["exchange requested"]
    exchange:OrderExchange
OPERATIONS={
    "default_address":("PUT","/v1/customers/{}/default-shipping-address",DefaultAddressResult),
    "order_address":("PUT","/v1/orders/{}/shipping-address",OrderAddressResult),
    "cancel":("POST","/v1/orders/{}/cancellations",CancellationResult),
    "payment":("PUT","/v1/orders/{}/payment-method",PaymentResult),
    "modify_items":("POST","/v1/orders/{}/item-modifications",ItemModificationResult),
    "return":("POST","/v1/orders/{}/returns",ReturnResult),
    "exchange":("POST","/v1/orders/{}/exchanges",ExchangeResult),
}

# These are existing OpenAPI body constraints, not new business restrictions.
class PaymentRequest(Record):
    payment_method_id:NonEmpty
class ReplacementRequest(PaymentRequest):
    replacements:list[ItemReplacement]=Field(min_length=1)
class ReturnRequest(Record):
    item_ids:list[NonEmpty]=Field(min_length=1)
    refund_payment_method_id:NonEmpty
REQUESTS={"default_address":Address,"order_address":Address,"cancel":OrderCancellation,
    "payment":PaymentRequest,"modify_items":ReplacementRequest,"return":ReturnRequest,"exchange":ReplacementRequest}

def validate_receipt(facts,body):
    kind=facts["kind"]
    try:
        actual,warnings=normalize_response(body)
        OPERATIONS[kind][2].model_validate(actual)
        key="customer_id" if kind=="default_address" else "order_id"
        if actual[key]!=facts[key]:
            raise ValueError("Wrong resource")
        requested=facts["body"]
        if kind in {"default_address","order_address"}:
            address=actual["default_shipping_address" if kind=="default_address" else "shipping_address"]
            for name in ("address_line_1","city","region","country","postal_code"):
                if address[name]!=requested[name]:
                    raise ValueError("Wrong address")
            if (address.get("address_line_2") or "")!=(requested.get("address_line_2") or ""):
                raise ValueError("Wrong suite")
        elif kind=="cancel" and actual["cancellation"]!=requested:
            raise ValueError("Wrong reason")
        elif kind=="return":
            result=actual["return_request"]
            if Counter(result["item_ids"])!=Counter(requested["item_ids"]) or result["refund_payment_method_id"]!=requested["refund_payment_method_id"]:
                raise ValueError("Wrong return")
        elif kind=="exchange":
            result=actual["exchange"]
            if result["payment_method_id"]!=requested["payment_method_id"]:
                raise ValueError("Wrong method")
            for name in ("existing_item_id","replacement_item_id"):
                if Counter(r[name] for r in result["replacements"])!=Counter(r[name] for r in requested["replacements"]):
                    raise ValueError("Wrong items")
    except (ValidationError,ResponseValueError,ValueError,TypeError,KeyError):
        return {"state":"unknown","error":"invalid_write_receipt"}
    return {"state":"accepted" if kind in {"return","exchange"} else "completed","receipt":actual,
            **({"response_warnings":warnings} if warnings else {})}

def write_once(api,facts):
    try:
        REQUESTS[facts["kind"]].model_validate(facts["body"])
    except ValidationError:
        return {"state":"failed","error":"invalid_write_parameters"}
    method,path,_=OPERATIONS[facts["kind"]]
    identifier=facts["customer_id"] if facts["kind"]=="default_address" else facts["order_id"]
    try:
        response=api.request(method,path.format(quote(identifier,safe="")),body=deepcopy(facts["body"]))
    except Exception:
        return {"state":"unknown","error":"write_transport_failure"}
    if response.status_code==202:
        try:
            workflow=accepted_workflow(response,facts)
        except (ValueError,TypeError,OverflowError):
            return {"state":"unknown","error":"invalid_async_receipt"}
        return resume_workflow(api,facts,workflow)
    if response.status_code!=200:
        return {"state":"failed" if response.status_code in {400,404,405,409,413,422} else "unknown",
                "http_status":response.status_code,"error":response.body.get("error",{}) if isinstance(response.body,dict) else {}}
    return validate_receipt(facts,response.body)

def resume_workflow(api,facts,workflow):
    progress=poll_workflow(api,workflow)
    if progress["state"]=="succeeded":
        return {**validate_receipt(facts,progress["result"]),"workflow":progress["workflow"]}
    return progress
