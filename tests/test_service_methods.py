"""Seven-method repair contracts with native effects and injected failures."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from fixtures.native_runtime import reference_kit,parameters,SEED,ROOT
from support_agent.catalog import rank_variants,search_index
from support_agent.domain.identity import ReadError
from support_agent.skills import active_skill_names
from tau2.data_model.message import AssistantMessage,ToolCall
from tau2.environment.environment import Environment


def business_writes(requests):
    return [r for r in requests if r['method']!='GET' and r['path']!='/v1/customers/search']


class SelectionMethods(unittest.TestCase):
    def product(self):
        return {'product_id':'synthetic','items':[
            {'item_id':'a','options':{'size':'8','length':'31 inch','power':'battery'},'available':True,'price':140.0},
            {'item_id':'b','options':{'size':'8','length':'28 inch','power':'USB'},'available':True,'price':80.0},
            {'item_id':'c','options':{'size':'9','length':'28 inch','power':'battery'},'available':True,'price':999.0},
            {'item_id':'d','options':{'size':'8','length':'28 inch','power':'battery'},'available':False,'price':5000.0}]}

    def test_preference_priority_hard_constraints_numeric_units_and_full_results(self):
        p=self.product()
        result=rank_variants(p,{'size':'8'},[],[{'option':'power','values':['battery','USB']}],True)
        self.assertEqual([i['item_id'] for i in result['candidates']],['a','b'])
        self.assertEqual(result['unavailable_matching_count'],1)
        self.assertTrue(result['complete'])
        shorter=rank_variants(p,{'size':'8'},[{'option':'length','operator':'lt','value':31,'unit':'inch'}])
        self.assertEqual([i['item_id'] for i in shorter['candidates']],['b'])
        most_expensive=rank_variants(p,{'size':'8'},descending=True)
        self.assertEqual(most_expensive['candidates'][0]['item_id'],'a')

    def test_bad_criteria_are_errors_not_fake_no_stock(self):
        p=self.product()
        for args in ({'filters':{'size':'200'}},
                     {'numeric_filters':[{'option':'length','operator':'lt','value':31,'unit':'ml'}]},
                     {'preferences':[{'option':['power'],'values':['battery']}]},
                     {'numeric_filters':[{'option':'length','operator':'lt','value':float('nan'),'unit':'inch'}]}):
            with self.subTest(args=args),self.assertRaises(ReadError):rank_variants(p,**args)

    def test_search_does_not_truncate_a_large_index_to_a_page(self):
        rows=[{'product_id':str(i),'name':'Match '+str(i)} for i in range(237)]
        result=search_index(rows,'match')
        self.assertEqual(result['matching_count'],237)
        self.assertEqual(result['products'][-1]['product_id'],'236')
        self.assertTrue(result['complete'])

    def test_search_contract_has_no_paging_and_unexpected_paging_is_not_silently_ignored(self):
        spec=json.loads((ROOT/'materials/client_api/openapi.yaml').read_text(encoding='utf-8'))
        self.assertEqual(spec['paths']['/v1/catalog/products']['get']['x-api-pagination'],'none')
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def partial(req):
            result=transport(req)
            if req['path']=='/v1/catalog/products':result['body']['next_cursor']='unexpected'
            return result
        kit.client_api._transport=partial
        with self.assertRaises(ReadError):kit.search_products('Fixture')
        self.assertFalse(business_writes(requests))

    def test_new_selection_tools_use_real_records_and_are_read_only(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email'])
        product=next(iter(rt.environment.tools.db.products.values()))
        env=Environment('retail_plus',policy='',tools=kit)
        for name,args in [('rank_preferred',{'product_id':product.product_id}),('search_products',{'name_contains':''})]:
            reply=env.get_response(ToolCall(id=name,name=name,arguments=args))
            self.assertFalse(reply.error,reply.content)
            self.assertEqual(json.loads(reply.content)['complete'],'True')
        invalid=kit.rank_preferred(product.product_id,filters={'unknown-option':'unknown-value'})
        self.assertEqual(invalid['state'],'invalid_criteria');self.assertFalse(invalid['complete'])
        self.assertTrue(invalid['option_values'])
        self.assertFalse(business_writes(requests))

    def test_loading_a_second_skill_preserves_first_topic(self):
        kit,_,_=reference_kit();history=[]
        for index,name in enumerate(['delivered-exchanges','order-cancellation']):
            call=ToolCall(id=str(index),name='load_skills',arguments={'names':[name]})
            result=Environment('retail_plus',policy='',tools=kit).get_response(call)
            history.extend([AssistantMessage(role='assistant',tool_calls=[call]).model_dump(mode='json'),result.model_dump(mode='json')])
        self.assertTrue({'delivered-exchanges','delivered-service','order-cancellation'}<=set(active_skill_names(history)))


class ServiceMethods(unittest.TestCase):
    def test_complete_todo_list_retains_ineligible_orders_and_remaining_work(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0];db=rt.environment.tools.db
        original=db.orders[case['order_id']]
        for suffix,status in [('SECOND','pending'),('LOCKED','processed')]:
            clone=original.model_copy(deep=True);clone.order_id='#SYNTHETIC-'+suffix;clone.status=status
            db.orders[clone.order_id]=clone;db.users[case['customer_id']].orders.append(clone.order_id)
        kit.verify_customer(email=case['email'])
        outcome=kit.modify_order_items(**parameters('modify_items',case,rt))
        self.assertTrue(outcome['verified'])
        result=kit.get_all_todo_orders(['modify_items'])
        self.assertEqual(result['total_count'],3)
        self.assertEqual(result['actionable_count'],1)
        self.assertTrue(result['complete'])
        finished=next(r for r in result['orders'] if r['order']['order_id']==case['order_id'])
        self.assertTrue(finished['outcomes']);self.assertFalse(finished['eligible_operations'])

    def test_batch_exchange_is_one_write_and_invalid_late_row_is_atomic(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1]
        kit.verify_customer(email=case['email']);args=parameters('exchange',case,rt)
        bad=deepcopy(args);bad['replacements'][-1]['replacement_item_id']='not-in-the-product'
        before=rt.environment.get_db_hash()
        self.assertEqual(kit.request_exchange(**bad)['state'],'failed')
        self.assertFalse(business_writes(requests));self.assertEqual(before,rt.environment.get_db_hash())
        result=kit.request_exchange(**args)
        self.assertTrue(result['verified']);self.assertEqual(len(business_writes(requests)),1)
        self.assertEqual(len(result['receipt']['exchange']['replacements']),len(args['replacements']))

    def test_business_service_rejects_status_stock_quantity_method_and_same_options_before_write(self):
        for condition in ['status','stock','quantity','method','same_options','gift']:
            with self.subTest(condition=condition):
                kit,rt,requests=reference_kit();case=SEED['cases'][1];db=rt.environment.tools.db
                args=parameters('exchange',case,rt);order=db.orders[case['order_id']]
                if condition=='status':order.status='processed'
                elif condition=='stock':
                    identifier=args['replacements'][0]['replacement_item_id']
                    next(v for p in db.products.values() for v in p.variants.values() if v.item_id==identifier).available=False
                elif condition=='quantity':args['replacements']*=len(order.items)+1
                elif condition=='method':args['payment_method_id']='not-saved'
                elif condition=='same_options':args['replacements'][0]['replacement_item_id']=args['replacements'][0]['existing_item_id']
                else:
                    gift=case['payment_method_ids']['gift_card'];db.users[case['customer_id']].payment_methods[gift].balance=0
                    args['payment_method_id']=gift
                kit.verify_customer(email=case['email']);before=rt.environment.get_db_hash()
                result=kit.request_exchange(**args)
                self.assertEqual(result['state'],'failed',result)
                self.assertFalse(business_writes(requests));self.assertEqual(before,rt.environment.get_db_hash())

    def test_cross_refund_destination_is_rejected_without_probing_server(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1]
        kit.verify_customer(email=case['email']);args=parameters('return',case,rt)
        args['refund_payment_method_id']=case['payment_method_ids']['paypal']
        self.assertEqual(kit.request_return(**args)['error'],'ineligible_refund_destination')
        self.assertFalse(business_writes(requests))

    def test_timeout_after_effect_recovers_and_duplicate_keeps_stable_id_without_repost(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def lost(req):
            response=transport(req)
            if req['path'].endswith('/cancellations'):raise TimeoutError('Synthetic response lost after native effect')
            return response
        kit.client_api._transport=lost;args=parameters('cancel',case,rt)
        first=kit.cancel_order(**args);second=kit.cancel_order(**args)
        self.assertTrue(first['verified']);self.assertEqual(first,second)
        self.assertEqual(len(business_writes(requests)),1)
        self.assertEqual(kit.verify_operation(case['order_id'])['operation_id'],first['operation_id'])

    def test_unknown_before_effect_never_reposts_and_later_read_can_recover(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def lost(req):
            if req['path'].endswith('/cancellations'):
                requests.append(deepcopy(req));raise TimeoutError('Synthetic unknown in-flight request')
            return transport(req)
        kit.client_api._transport=lost;args=parameters('cancel',case,rt)
        result=kit.cancel_order(**args)
        self.assertEqual(result['state'],'unknown');self.assertFalse(result['retry_safe'])
        with self.assertRaises(ReadError):kit.cancel_order(**args)
        self.assertEqual(kit.verify_operation(case['order_id'])['state'],'unknown')
        rt.environment.tools.cancel_pending_order(case['order_id'],args['reason']) # late completion of the original write
        recovered=kit.verify_operation(case['order_id'])
        self.assertTrue(recovered['verified']);self.assertEqual(recovered['operation_id'],result['operation_id'])
        self.assertEqual(len(business_writes(requests)),1)

    def test_cancellation_without_refund_is_unknown_not_false_success_and_no_fake_repair_api(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        original=kit.verify_customer(email=case['email'],order_id=case['order_id'])['order']
        transport=kit.client_api._transport
        def broken(req):
            if req['path'].endswith('/cancellations'):
                requests.append(deepcopy(req));order=rt.environment.tools.db.orders[case['order_id']]
                order.status='cancelled';order.cancel_reason=req['body']['reason']
                return {'status_code':200,'body':{'order_id':case['order_id'],'status':'cancelled',
                    'cancellation':req['body'],'payments':original['payments']}}
            return transport(req)
        kit.client_api._transport=broken
        result=kit.cancel_order(**parameters('cancel',case,rt))
        self.assertEqual(result['state'],'unknown');self.assertFalse(result['verified'])
        self.assertEqual(kit.verify_operation(case['order_id'])['state'],'unknown')
        self.assertEqual(len(business_writes(requests)),1)

    def test_missing_refund_in_receipt_is_recovered_only_from_actual_records(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def incomplete(req):
            response=transport(req)
            if req['path'].endswith('/cancellations'):
                response['body']['payments']=[p for p in response['body']['payments'] if p['transaction_type']!='refund']
            return response
        kit.client_api._transport=incomplete
        result=kit.cancel_order(**parameters('cancel',case,rt))
        self.assertTrue(result['verified']);self.assertEqual(result['state'],'completed')
        self.assertTrue(any(p['transaction_type']=='refund' for p in result['receipt']['payments']))
        self.assertEqual(len(business_writes(requests)),1)

    def test_modified_status_with_wrong_items_or_settlement_is_not_success(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        original=kit.verify_customer(email=case['email'],order_id=case['order_id'])['order'];transport=kit.client_api._transport
        def broken(req):
            if req['path'].endswith('/item-modifications'):
                requests.append(deepcopy(req));rt.environment.tools.db.orders[case['order_id']].status='pending (items modified)'
                return {'status_code':200,'body':{'order_id':case['order_id'],'status':'pending (items modified)',
                    'items':original['items'],'payments':original['payments']}}
            return transport(req)
        kit.client_api._transport=broken
        result=kit.modify_order_items(**parameters('modify_items',case,rt))
        self.assertEqual(result['state'],'unknown');self.assertFalse(result['verified'])
        self.assertEqual(len(business_writes(requests)),1)

    def test_each_charge_has_a_separate_refund_and_signed_refund_amounts_are_compatible(self):
        for signed in (False,True):
            with self.subTest(signed=signed):
                kit,rt,requests=reference_kit();case=SEED['cases'][0]
                order=rt.environment.tools.db.orders[case['order_id']]
                topup=order.payment_history[0].model_copy(deep=True);topup.amount=11.25
                order.payment_history.append(topup)
                transport=kit.client_api._transport
                def serialize(req):
                    response=transport(req)
                    if signed:
                        for payment in response.get('body',{}).get('payments',[]):
                            if payment['transaction_type']=='refund':payment['amount']=-abs(payment['amount'])
                    return response
                kit.client_api._transport=serialize;kit.verify_customer(email=case['email'])
                result=kit.cancel_order(**parameters('cancel',case,rt))
                self.assertTrue(result['verified'],result)
                self.assertEqual(len([p for p in result['receipt']['payments'] if p['transaction_type']=='refund']),2)
                self.assertEqual(len(business_writes(requests)),1)

    def test_refund_record_without_the_required_gift_balance_credit_is_unverified(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0];db=rt.environment.tools.db
        gift=case['payment_method_ids']['gift_card'];order=db.orders[case['order_id']]
        order.payment_history[0].payment_method_id=gift
        balance=db.users[case['customer_id']].payment_methods[gift].balance
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def lost_credit(req):
            response=transport(req)
            if req['path'].endswith('/cancellations'):
                db.users[case['customer_id']].payment_methods[gift].balance=balance
            return response
        kit.client_api._transport=lost_credit
        result=kit.cancel_order(**parameters('cancel',case,rt))
        self.assertEqual(result['state'],'unknown');self.assertFalse(result['verified'])
        self.assertEqual(len(business_writes(requests)),1)

    def test_verifying_a_definite_business_decline_does_not_turn_it_into_an_unknown_write(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][0]
        kit.verify_customer(email=case['email']);transport=kit.client_api._transport
        def decline(req):
            if req['path'].endswith('/cancellations'):
                requests.append(deepcopy(req));return {'status_code':422,'body':{'error':{'code':'business_rule_violation'}}}
            return transport(req)
        kit.client_api._transport=decline;before=rt.environment.get_db_hash()
        result=kit.cancel_order(**parameters('cancel',case,rt))
        self.assertEqual(result['state'],'failed')
        self.assertEqual(kit.verify_operation(case['order_id']),result)
        self.assertFalse(kit._pending);self.assertEqual(before,rt.environment.get_db_hash())
        self.assertEqual(len(business_writes(requests)),1)
