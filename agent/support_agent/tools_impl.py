"""Small stable customer tools; no dialogue phase or proposal state machine."""
from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock
from typing import Literal
from tau2.environment.toolkit import ToolType,is_tool
from tau2.hyper.client_api import ClientAPIToolKitBase
from support_agent.adapters.read_api import read_customer,read_order,read_product,read_products,search_customer
from support_agent.adapters.read_models import Address,ItemReplacement
from support_agent.adapters.conversation_api import transfer_once
from support_agent.api_writes import OPERATIONS,validate_receipt,write_once,resume_workflow
from support_agent.business_service import check_effect,eligible_operations,expected_effect
from support_agent.catalog import option_values,rank_variants,search_index
from support_agent.domain.identity import identity_selector,resource_id,ReadError,verification_failed
from support_agent.domain.money import calculate_expression
from support_agent.skills import SkillName,skill_bundle

ACTIONS=("verify_customer","get_customer","get_orders","get_all_todo_orders","get_products","search_products","rank_preferred","load_skills","calculate",
    "update_default_address","update_order_address","cancel_order","change_order_payment",
    "modify_order_items","request_return","request_exchange","inspect_operation","verify_operation","transfer_to_human")


class RetailTools(ClientAPIToolKitBase):
    def __init__(self,client_api):
        super().__init__(client_api)
        self._verified=None
        self._conversation=None
        self._pending={}
        self._operations={}
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
            self._operations={}
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

        Include a known order_id, or include_orders for discovery. Selected
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

    @is_tool(ToolType.READ)
    def get_all_todo_orders(self,operations:list[str],order_ids:list[str]|None=None)->dict:
        """Read every owned order in the selected scope and its action eligibility.

        Operations: order_address, cancel, payment, modify_items, return, exchange.
        Returns all rows, including ineligible orders and known operation outcomes;
        eligibility does not imply that the customer requested or approved a write.
        """
        with self._lock:
            if not operations or set(operations)-{'order_address','cancel','payment','modify_items','return','exchange'}:
                raise ReadError('invalid_operation_selection')
            orders=self.get_orders(order_ids)['orders'];rows=[]
            for order in orders:
                pending=self._pending.get(order['order_id'])
                allowed=[k for k in operations if k in eligible_operations(order)]
                previous=[r['outcome'] for r in self._operations.values() if r['facts']['order_id']==order['order_id']]
                rows.append({'order':order,'eligible_operations':allowed,'needs_inspection':bool(pending),
                    'outcomes':deepcopy(previous),'reason':None if allowed else 'current_status_does_not_allow_selected_operations'})
            return {'orders':rows,'total_count':len(rows),'actionable_count':sum(bool(r['eligible_operations']) and not r['needs_inspection'] for r in rows),
                'complete':True,'notice':'Review every requested order; availability is not approval or proof of task completion.'}

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

    @is_tool(ToolType.READ)
    def search_products(self,name_contains:str="")->dict:
        """Search the complete product index, returning every matching product.

        The REST catalog is unpaginated. No first-page or top-k truncation.
        """
        with self._lock:
            self._customer()
            return search_index(read_products(self.client_api)['products'],name_contains)

    @is_tool(ToolType.READ)
    def rank_preferred(self,product_id:str,filters:dict[str,str]|None=None,
                       numeric_filters:list[dict]|None=None,preferences:list[dict]|None=None,
                       descending_price:bool=False)->dict:
        """Filter and rank all available variants using explicit customer criteria.

        filters maps option to exact catalog value. numeric_filters rows contain
        option, operator (lt/lte/eq/gte/gt), value and explicit unit. preferences
        rows contain option and values in preferred order. Price breaks ties.
        Returns every match and counts, never writes or chooses for the customer.
        """
        with self._lock:
            self._customer()
            product=read_product(self.client_api,product_id)
            try:
                return rank_variants(product,filters,numeric_filters,preferences,descending_price)
            except ReadError as error:
                return {'state':'invalid_criteria','error':error.code,'option_values':option_values(product),'complete':False}

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
            order=self._order(resource_id(order_id),profile) if order_id is not None else None
            # A completed identical request is reused only while its actual
            # effect still exists; a later address restoration permits a new edit.
            for retained in reversed(list(self._operations.values())):
                facts=retained['facts']
                if facts['kind']==kind and facts['order_id']==order_id and facts['body']==body:
                    if retained['outcome']['state'] in {'accepted','completed'} and check_effect(kind,facts['expected'],order or profile,profile):
                        return deepcopy(retained['outcome'])
                    break
            try:
                expected=expected_effect(kind,body,profile,order,lambda pid:read_product(self.client_api,pid))
            except ReadError as error:
                return {'state':'failed','error':error.code}
            facts={"kind":kind,"body":deepcopy(body),"customer_id":self._verified,"order_id":order_id,'expected':expected}
            identity={'conversation':self._conversation,'sequence':len(self._operations),'facts':facts}
            operation_id=sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
            outcome=write_once(self.client_api,facts)
            outcome['operation_id']=operation_id
            retained={'facts':facts,'outcome':deepcopy(outcome)}
            self._operations[operation_id]=retained
            if outcome['state'] in {'accepted','completed','unknown'}:
                outcome=self._verify(retained)
            if outcome["state"] in {"pending","unknown"}:
                self._pending[target]=retained
            return outcome

    def _verify(self,retained):
        facts=retained['facts'];previous=retained['outcome']
        try:
            profile=self._customer()
            actual=self._order(facts['order_id'],profile) if facts['order_id'] is not None else profile
            if not check_effect(facts['kind'],facts['expected'],actual,profile):raise ReadError('postcondition_mismatch')
            model=OPERATIONS[facts['kind']][2]
            receipt={key:actual[key] for key in model.model_fields}
            result=validate_receipt(facts,receipt)
            if result['state']=='unknown':raise ReadError('invalid_verified_receipt')
            outcome={**result,'operation_id':previous['operation_id'],'verified':True}
        except (ReadError,KeyError):
            outcome={**{k:previous[k] for k in ('operation_id','workflow') if k in previous},
                'state':'unknown','error':'postcondition_not_verified','verified':False,'retry_safe':False,
                'next_action':'Inspect again or transfer for unresolved effects; no automatic business retry or separate refund endpoint.'}
        retained['outcome']=deepcopy(outcome)
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
    def change_order_payment(self,order_id:str,payment_method_id:str)->dict:
        """Replace the approved pending order's full payment with one saved method."""
        return self._write("payment",{"payment_method_id":payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def modify_order_items(self,order_id:str,replacements:list[ItemReplacement],payment_method_id:str)->dict:
        """Submit the complete approved pending-order variant changes once."""
        rows=[ItemReplacement.model_validate(r).model_dump() for r in replacements]
        return self._write("modify_items",{"replacements":rows,"payment_method_id":payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def request_return(self,order_id:str,item_ids:list[str],refund_payment_method_id:str)->dict:
        """Register the complete approved delivered return list and eligible refund method."""
        return self._write("return",{"item_ids":item_ids,"refund_payment_method_id":refund_payment_method_id},order_id)

    @is_tool(ToolType.WRITE)
    def request_exchange(self,order_id:str,replacements:list[ItemReplacement],payment_method_id:str)->dict:
        """Submit the complete approved replacement LIST in one delivered-order exchange.

        Collect all requested items before this single write. Never submit the
        first item while other choices or customer approval remain unresolved.
        """
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
                outcome=self._verify(retained)
                if outcome['state'] in {'accepted','completed'}:del self._pending[target]
                return outcome
            outcome=resume_workflow(self.client_api,retained["facts"],previous["workflow"])
            outcome['operation_id']=previous['operation_id']
            retained["outcome"]=deepcopy(outcome)
            if outcome['state'] in {'accepted','completed'}:outcome=self._verify(retained)
            if outcome["state"] in {"accepted","completed","failed"}:
                del self._pending[target]
            return outcome

    @is_tool(ToolType.READ)
    def verify_operation(self,order_id:str="")->dict:
        """Verify an actual write against fresh status, items and payment/refund records.

        Resume pending status inspection or recheck the last known operation on
        this target. Never repeats a business write or creates a refund endpoint.
        """
        with self._lock:
            self._customer();target=order_id or self._verified
            if target in self._pending:return self.inspect_operation(order_id)
            retained=next((r for r in reversed(list(self._operations.values()))
                           if (r['facts']['order_id'] or r['facts']['customer_id'])==target),None)
            if retained is None:raise ReadError('no_recorded_operation')
            if retained['outcome']['state']=='failed':return deepcopy(retained['outcome'])
            outcome=self._verify(retained)
            if outcome['state']=='unknown':self._pending[target]=retained
            return outcome

    @is_tool(ToolType.GENERIC)
    def transfer_to_human(self,summary:str)->dict:
        """Transfer an explicit human request or policy escalation with a factual summary."""
        with self._lock:
            self._active()
            outcome=transfer_once(self.client_api,summary)
            self._handoff=outcome["state"]
            return outcome
