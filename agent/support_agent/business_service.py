"""Business preconditions and expected effects from fresh owned records.

This service never interprets dialogue, invents approval, or sends writes.
Source: reviewed OpenAPI, INTERFACE-CLARIFICATIONS U5/U6 and packaged policies.
"""
from collections import Counter
from copy import deepcopy
import json
from support_agent.domain.identity import ReadError


def eligible_operations(order):
    if order['status']=='pending':return ['order_address','cancel','payment','modify_items']
    if order['status']=='delivered':return ['return','exchange']
    return []


def payment_row(kind,amount,method):
    return {'transaction_type':kind,'amount':abs(amount),'payment_method_id':method}


def multiset(rows):
    return Counter(json.dumps(r,sort_keys=True,separators=(',',':')) for r in rows)


def payment_multiset(rows):
    return multiset([{**p,'amount':abs(p['amount']) if p['transaction_type']=='refund' else p['amount']} for p in rows])


def expected_effect(kind,body,profile,order,read_product):
    def require(condition,code):
        if not condition:raise ReadError(code)
    if kind=='default_address':return {'address':deepcopy(body)}
    require(kind in eligible_operations(order),'ineligible_order_status')
    balances={m['id']:m['balance'] for m in profile['payment_methods'] if m['source']=='gift_card'}
    expected={'status':order['status'],'items':deepcopy(order['items']),
              'shipping_address':deepcopy(order['shipping_address']),'payments':deepcopy(order['payments']),
              'gift_balances':{}}
    def gift_change(key,amount):
        if key in balances:
            expected['gift_balances'][key]=round(expected['gift_balances'].get(key,balances[key])+amount,2)
    if kind=='order_address':
        expected['shipping_address']=deepcopy(body);return expected
    if kind=='cancel':
        require(body.get('reason') in {'no longer needed','ordered by mistake'},'invalid_cancellation_reason')
        expected.update(status='cancelled',cancellation=deepcopy(body))
        expected['payments'] += [payment_row('refund',p['amount'],p['payment_method_id'])
                                 for p in order['payments'] if p['transaction_type']=='payment']
        for p in order['payments']:
            if p['transaction_type']=='payment':gift_change(p['payment_method_id'],abs(p['amount']))
        return expected
    method_id=body.get('refund_payment_method_id' if kind=='return' else 'payment_method_id')
    methods={m['id']:m for m in profile['payment_methods']}
    require(method_id in methods,'payment_method_not_saved')
    method=methods[method_id]
    if kind=='return':
        selected=body.get('item_ids',[])
        require(bool(selected) and not (Counter(selected)-Counter(i['item_id'] for i in order['items'])),'invalid_item_quantities')
        original={p['payment_method_id'] for p in order['payments'] if p['transaction_type']=='payment'}
        require(method_id in original or method['source']=='gift_card','ineligible_refund_destination')
        expected.update(status='return requested',return_request=deepcopy(body));return expected
    if kind=='payment':
        payments=order['payments']
        require(len(payments)==1 and payments[0]['transaction_type']=='payment','ineligible_payment_history')
        require(payments[0]['payment_method_id']!=method_id,'unchanged_payment_method')
        amount=payments[0]['amount']
        require(method['source']!='gift_card' or method['balance']>=amount,'insufficient_gift_card_balance')
        expected['payments'] += [payment_row('payment',amount,method_id),payment_row('refund',amount,payments[0]['payment_method_id'])]
        for key,change in ((method_id,-amount),(payments[0]['payment_method_id'],amount)):
            gift_change(key,change)
        return expected
    rows=body.get('replacements',[])
    require(bool(rows) and not (Counter(r['existing_item_id'] for r in rows)-Counter(i['item_id'] for i in order['items'])),'invalid_item_quantities')
    difference=0.0;catalogs={}
    for row in rows:
        old=next(i for i in order['items'] if i['item_id']==row['existing_item_id'])
        pid=old['product_id']
        if pid not in catalogs:catalogs[pid]=read_product(pid)
        target=next((i for i in catalogs[pid]['items'] if i['item_id']==row['replacement_item_id']),None)
        require(target is not None,'replacement_product_mismatch')
        require(target['available'],'replacement_unavailable')
        require(target['options']!=old['options'],'replacement_options_unchanged')
        difference+=target['price']-old['price']
        if kind=='modify_items':
            instance=next(i for i in expected['items'] if i['item_id']==old['item_id'])
            instance.update(item_id=target['item_id'],options=deepcopy(target['options']),price=target['price'])
    difference=round(difference,2)
    require(method['source']!='gift_card' or difference<=method['balance'],'insufficient_gift_card_balance')
    if kind=='exchange':
        expected.update(status='exchange requested',exchange={**deepcopy(body),'price_difference':difference})
    else:
        expected['status']='pending (items modified)'
        expected['payments'].append(payment_row('payment' if difference>0 else 'refund',difference,method_id))
        gift_change(method_id,-difference)
    return expected


def address_equal(actual,expected):
    return all((actual.get(k) or '')==(expected.get(k) or '') for k in
               ('address_line_1','address_line_2','city','region','country','postal_code'))


def check_effect(kind,expected,actual,profile=None):
    """Full operation-specific postconditions, not an HTTP success check."""
    if kind=='default_address':return address_equal(actual['default_shipping_address'],expected['address'])
    if profile is not None and any(next((m.get('balance') for m in profile['payment_methods'] if m['id']==key),None)!=value
                                   for key,value in expected['gift_balances'].items()):return False
    if actual.get('status')!=expected['status']:return False
    if not address_equal(actual.get('shipping_address',{}),expected['shipping_address']):return False
    if multiset(actual.get('items',[]))!=multiset(expected['items']):return False
    if payment_multiset(actual.get('payments',[]))!=payment_multiset(expected['payments']):return False
    if kind=='cancel':return actual.get('cancellation')==expected['cancellation']
    if kind=='return':
        actual_return=actual.get('return_request') or {};wanted=expected['return_request']
        return (Counter(actual_return.get('item_ids',[]))==Counter(wanted['item_ids']) and
                actual_return.get('refund_payment_method_id')==wanted['refund_payment_method_id'])
    if kind=='exchange':
        exchange=actual.get('exchange') or {};wanted=expected['exchange']
        # Upstream independently sorts the two receipt columns. Preserve request
        # pairs in our facts and compare occurrence counts in the public record.
        return (exchange.get('payment_method_id')==wanted['payment_method_id'] and
                exchange.get('price_difference')==wanted['price_difference'] and
                all(Counter(r[k] for r in exchange.get('replacements',[]))==Counter(r[k] for r in wanted['replacements'])
                    for k in ('existing_item_id','replacement_item_id')))
    return True
