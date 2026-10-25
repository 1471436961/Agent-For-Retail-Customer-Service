"""Small stable customer tools; no dialogue phase or proposal state machine."""
from copy import deepcopy
from threading import RLock
from typing import Annotated,Literal
from pydantic import Field
from tau2.environment.toolkit import ToolType,is_tool
from tau2.hyper.client_api import ClientAPIToolKitBase
from support_agent.adapters.read_api import read_customer,read_order,read_product,read_products,search_customer
from support_agent.adapters.read_models import Address,ItemReplacement,NonEmpty
from support_agent.adapters.conversation_api import transfer_once
from support_agent.api_writes import write_once,resume_workflow
from support_agent.domain.identity import identity_selector,resource_id,ReadError,verification_failed
from support_agent.domain.money import calculate_expression
from support_agent.skills import SkillName,skill_bundle

ACTIONS=("verify_customer","get_customer","get_orders","get_products","load_skills","calculate",
    "update_default_address","update_order_address","cancel_order","change_order_payment",
    "modify_order_items","request_return","request_exchange","inspect_operation","transfer_to_human")
Replacements=Annotated[list[ItemReplacement],Field(min_length=1)]
ItemIDs=Annotated[list[NonEmpty],Field(min_length=1)]


class RetailTools(ClientAPIToolKitBase):
    def __init__(self,client_api):
        super().__init__(client_api)
        self._verified=None
        self._conversation=None
        self._pending={}
        self._handoff=None
        self._lock=RLock()

    def _active(self):
        try:
            conversation=self.client_api.context.conversation_id
        except RuntimeError:
            conversation=None
        if conversation!=self._conversation:
            self._verified=None
            self._pending={}
            self._handoff=None
            self._conversation=conversation
        if self._handoff in {"accepted","unknown"}:
            raise ReadError("automated_service_stopped_after_handoff")

    def _customer(self):
        self._active()
        if self._verified is None:
            raise ReadError("identity_required")
        return read_customer(self.client_api,self._verified)

    def _order(self,identifier,profile):
        if identifier not in profile["order_ids"]:
            raise ReadError("order_access_denied")
        order=read_order(self.client_api,identifier)
        if order["customer_id"]!=self._verified:
            raise ReadError("order_access_denied")
        return order

    @is_tool(ToolType.READ)
    def load_skills(self,names:list[SkillName])->dict:
        """Read relevant general business procedures. No customer access or write."""
        return {"business_skills":skill_bundle(names)}

    @is_tool(ToolType.READ)
    def verify_customer(self,email:str="",first_name:str="",last_name:str="",postal_code:str="",
                        claimed_customer_id:str|None=None,order_id:str="",include_orders:bool=False,
                        product_names:list[str]|None=None,skill_names:list[SkillName]|None=None)->dict:
        """Verify email OR full name+postal code. An optional claimed ID must match.

        For one known order use order_id and leave include_orders false.
        Use include_orders only for discovery or explicit multi-order requests. Selected
        product_names and skill_names can be read in this same action.
        """
        with self._lock:
            self._active()
            skills=skill_bundle(skill_names) if skill_names else None
            selector=identity_selector(email,first_name,last_name,postal_code)
            found=search_customer(self.client_api,selector)["customer_id"]
            if (claimed_customer_id and found!=claimed_customer_id) or (self._verified and found!=self._verified):
                raise verification_failed()
            self._verified=found
            profile=self._customer()
            result=deepcopy(profile)
            if order_id:
                selected=order_id
                if selected not in profile["order_ids"]:
                    matches=[i for i in profile["order_ids"] if not order_id.startswith("#") and i.startswith("#") and i[1:]==order_id]
                    if len(matches)!=1:
                        raise ReadError("order_access_denied")
                    selected=matches[0]
                result["order"]=self._order(selected,profile)
            if include_orders:
                result["orders"]=[result["order"] if result.get("order",{}).get("order_id")==i
                    else self._order(i,profile) for i in profile["order_ids"]]
            if product_names:
                result["products"]=self._products([],product_names)
            if skills:
                result["business_skills"]=skills
            return result

    @is_tool(ToolType.READ)
    def get_customer(self)->dict:
        """Read the verified profile, saved payment methods and owned order IDs."""
        with self._lock:
            return self._customer()

    @is_tool(ToolType.READ)
    def get_orders(self,order_ids:list[str]|None=None)->dict:
        """Read selected exact owned order IDs; omit the list to discover all orders."""
        with self._lock:
            profile=self._customer()
            selected=profile["order_ids"] if order_ids is None else order_ids
            return {"orders":[self._order(resource_id(i),profile) for i in dict.fromkeys(selected)]}

    def _products(self,identifiers,names):
        if names:
            index=read_products(self.client_api)["products"]
            for name in names:
                matches=[p["product_id"] for p in index if p["name"].casefold()==name.casefold()]
                if len(matches)!=1:
                    raise ReadError("product_name_missing_or_ambiguous")
                identifiers.extend(matches)
        products=[read_product(self.client_api,resource_id(i)) for i in dict.fromkeys(identifiers)]
        for product in products:
            product["variant_count"]=len(product["items"])
            product["available_variant_count"]=sum(i["available"] for i in product["items"])
        return products

    @is_tool(ToolType.READ)
    def get_products(self,product_ids:list[str]|None=None,product_names:list[str]|None=None)->dict:
        """Read selected full catalogs in one batch; omit both lists for the product index.

        Catalog options, prices and availability are returned together. Select
        candidates from these actual records; no separate filtering tool is needed.
        """
        with self._lock:
            self._customer()
            if not product_ids and not product_names:
                return read_products(self.client_api)
            return {"products":self._products(list(product_ids or []),product_names or [])}

    @is_tool(ToolType.GENERIC)
    def calculate(self,expression:str,decimal_places:int|None=None)->dict:
        """Evaluate one complete arithmetic expression with parentheses, + - * / and round."""
        return calculate_expression(expression,decimal_places)

    def _write(self,kind,body,order_id=None):
        with self._lock:
            profile=self._customer()
            target=order_id or self._verified
            if target in self._pending:
                raise ReadError("inspect_uncertain_operation_before_another_write")
            if order_id is not None:
                self._order(resource_id(order_id),profile)
            facts={"kind":kind,"body":deepcopy(body),"customer_id":self._verified,"order_id":order_id}
            outcome=write_once(self.client_api,facts)
            if outcome["state"] in {"pending","unknown"}:
                self._pending[target]={"facts":facts,"outcome":deepcopy(outcome)}
            return outcome

    @is_tool(ToolType.WRITE)
    def update_default_address(self,address:Address)->dict:
        """Update only the approved profile default. Does not modify order addresses."""
        body=Address.model_validate(address).model_dump(exclude_unset=True)
        return self._write("default_address",body)

    @is_tool(ToolType.WRITE)
    def update_order_address(self,order_id:str,address:Address)->dict:
        """Update the approved pending order's full shipping address, including suite."""
        return self._write("order_address",Address.model_validate(address).model_dump(exclude_unset=True),order_id)

    @is_tool(ToolType.WRITE)
    def cancel_order(self,order_id:str,reason:Literal["no longer needed","ordered by mistake"])->dict:
        """Cancel the whole approved pending order; no partial cancellation."""
        return self._write("cancel",{"reason":reason},order_id)

    @is_tool(ToolType.WRITE)
    def change_order_payment(self,order_id:str,payment_method_id:NonEmpty)->dict:
        """Replace the approved pending order's full payment with one saved method."""
        return self._write("payment",{"payment_method_id":payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def modify_order_items(self,order_id:str,replacements:Replacements,payment_method_id:NonEmpty)->dict:
        """Submit the complete approved pending-order variant changes once."""
        rows=[ItemReplacement.model_validate(r).model_dump() for r in replacements]
        return self._write("modify_items",{"replacements":rows,"payment_method_id":payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def request_return(self,order_id:str,item_ids:ItemIDs,refund_payment_method_id:NonEmpty)->dict:
        """Register the complete approved delivered return list and eligible refund method."""
        return self._write("return",{"item_ids":item_ids,"refund_payment_method_id":refund_payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def request_exchange(self,order_id:str,replacements:Replacements,payment_method_id:NonEmpty)->dict:
        """Register complete approved same-product variant exchanges and saved settlement method."""
        rows=[ItemReplacement.model_validate(r).model_dump() for r in replacements]
        return self._write("exchange",{"replacements":rows,"payment_method_id":payment_method_id},order_id)

    @is_tool(ToolType.READ)
    def inspect_operation(self,order_id:str="")->dict:
        """Inspect an actual pending/unknown write; never repeat its business request.

        Omit order_id for the profile default address operation.
        """
        with self._lock:
            self._customer()
            target=order_id or self._verified
            retained=self._pending.get(target)
            if retained is None:
                raise ReadError("no_pending_operation")
            previous=retained["outcome"]
            if "workflow" not in previous:
                return deepcopy(previous)
            outcome=resume_workflow(self.client_api,retained["facts"],previous["workflow"])
            retained["outcome"]=deepcopy(outcome)
            if outcome["state"] in {"accepted","completed","failed"}:
                del self._pending[target]
            return outcome

    @is_tool(ToolType.GENERIC)
    def transfer_to_human(self,summary:str)->dict:
        """Transfer an explicit human request or policy escalation with a factual summary."""
        with self._lock:
            self._active()
            outcome=transfer_once(self.client_api,summary)
            self._handoff=outcome["state"]
            return outcome
