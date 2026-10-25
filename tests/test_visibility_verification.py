"""Regression for authoritative writes with delayed or optional read projections."""
from copy import deepcopy
from unittest.mock import patch
import unittest
from fixtures.native_runtime import reference_kit,parameters,SEED
from test_simple_tools import Clock
from tools import Tools
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from tau2.hyper.client_api.defects import AsyncCompletionDefect,DefectProfile,ProjectionLagDefect,ProjectionLagReadSurface


def writes(requests):
    return [r for r in requests if r['method']!='GET' and r['path']!='/v1/customers/search']


class VisibilityVerificationTests(unittest.TestCase):
    def test_operation_identity_is_stable_when_host_replay_uses_a_fresh_conversation_id(self):
        outcomes=[]
        for conversation in ('synthetic-original','synthetic-replay'):
            _,source,_=reference_kit()
            rt=ClientAPIRuntime(source.environment,conversation_id=conversation)
            kit=Tools(ClientAPI(rt.dispatch,context=rt.context));case=SEED['cases'][0]
            kit.verify_customer(email=case['email'])
            outcomes.append(kit.update_order_address(**parameters('order_address',case,rt)))
        self.assertEqual(outcomes[0],outcomes[1])

    def test_native_projection_lag_recovers_inside_the_same_exchange_tool_without_repost(self):
        _,source,_=reference_kit();clock=Clock();requests=[]
        profile=DefectProfile(manifest_id='synthetic-read-lag',manifest_sha256='0'*64,manifest_version=1,domain='retail_plus',defects=(
            ProjectionLagDefect(id='lag',operation_id='getOrder',trigger_operation_ids=('createOrderExchange',),
                capture_timing='before_trigger',start_condition='after_trigger',resource_id_argument='order_id',
                read_surfaces=(ProjectionLagReadSurface(operation_id='getOrder',resource_id_field='order_id'),),
                min_delay_seconds=2,max_delay_seconds=2),))
        rt=ClientAPIRuntime(source.environment,conversation_id='synthetic-lag',defect_profile=profile,monotonic_clock=clock)
        def transport(req):requests.append(deepcopy(req));return rt.dispatch(req)
        kit=Tools(ClientAPI(transport,context=rt.context));case=SEED['cases'][1]
        kit.verify_customer(email=case['email'])
        with patch('support_agent.tools_impl.sleep',clock.sleep,create=True):
            outcome=kit.request_exchange(**parameters('exchange',case,rt))
        self.assertEqual(outcome['state'],'accepted',outcome)
        self.assertTrue(outcome['verified']);self.assertGreaterEqual(clock.now,2)
        self.assertEqual(len(writes(requests)),1)

    def test_native_async_return_then_projection_lag_needs_no_model_inspection_round(self):
        _,source,_=reference_kit();clock=Clock();requests=[]
        profile=DefectProfile(manifest_id='synthetic-async-lag',manifest_sha256='0'*64,manifest_version=1,domain='retail_plus',defects=(
            AsyncCompletionDefect(id='async',operation_id='createOrderReturn',status_path='/v1/return-workflows/{workflow_id}',
                min_delay_seconds=3,max_delay_seconds=3,retry_after_seconds=1),
            ProjectionLagDefect(id='lag',operation_id='getOrder',trigger_operation_ids=('createOrderReturn',),
                capture_timing='before_trigger',start_condition='after_trigger',resource_id_argument='order_id',
                read_surfaces=(ProjectionLagReadSurface(operation_id='getOrder',resource_id_field='order_id'),),
                min_delay_seconds=2,max_delay_seconds=2)))
        rt=ClientAPIRuntime(source.environment,conversation_id='synthetic-async-lag',defect_profile=profile,monotonic_clock=clock)
        def transport(req):requests.append(deepcopy(req));return rt.dispatch(req)
        kit=Tools(ClientAPI(transport,context=rt.context));case=SEED['cases'][1]
        kit.verify_customer(email=case['email'])
        with patch('support_agent.adapters.workflow_api.time.sleep',clock.sleep),patch('support_agent.tools_impl.sleep',clock.sleep,create=True):
            outcome=kit.request_return(**parameters('return',case,rt))
        self.assertEqual(outcome['state'],'accepted',outcome)
        self.assertTrue(outcome['verified']);self.assertEqual(len(writes(requests)),1)

    def test_optional_exchange_details_in_get_order_can_use_the_validated_write_receipt(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1];transport=kit.client_api._transport
        def sparse(req):
            response=transport(req)
            if req['method']=='GET' and req['path'].startswith('/v1/orders/'):
                response['body'].pop('exchange',None)
            return response
        kit.client_api._transport=sparse;kit.verify_customer(email=case['email'])
        with patch('support_agent.tools_impl.sleep',return_value=None,create=True):
            outcome=kit.request_exchange(**parameters('exchange',case,rt))
        self.assertEqual(outcome['state'],'accepted',outcome)
        self.assertTrue(outcome['verified']);self.assertEqual(len(writes(requests)),1)

    def test_numeric_representation_is_not_a_different_payment_or_item(self):
        from support_agent.business_service import multiset,payment_multiset
        self.assertEqual(multiset([{'price':30,'item_id':'same'}]),multiset([{'price':30.0,'item_id':'same'}]))
        self.assertEqual(payment_multiset([{'transaction_type':'refund','amount':-30,'payment_method_id':'same'}]),
                         payment_multiset([{'transaction_type':'refund','amount':30.0,'payment_method_id':'same'}]))

    def test_present_conflicting_exchange_details_are_never_overwritten_by_receipt(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1];transport=kit.client_api._transport
        def conflicting(req):
            response=transport(req)
            if req['method']=='GET' and req['path'].startswith('/v1/orders/') and response['body'].get('exchange'):
                response['body']['exchange']['price_difference']+=1
            return response
        kit.client_api._transport=conflicting;kit.verify_customer(email=case['email']);clock=Clock()
        with patch('support_agent.tools_impl.sleep',clock.sleep):
            outcome=kit.request_exchange(**parameters('exchange',case,rt))
        self.assertEqual(outcome['state'],'unknown');self.assertEqual(outcome['verification_checks'],['exchange'])
        self.assertEqual(outcome['submission_state'],'accepted');self.assertLessEqual(clock.now,20)
        self.assertEqual(len(writes(requests)),1)
        retained=next(iter(kit._operations.values()))
        self.assertIn('exchange',retained['receipt'])

    def test_lost_receipt_and_missing_optional_detail_are_not_fabricated_as_success(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1];transport=kit.client_api._transport
        def lost(req):
            response=transport(req)
            if req['path'].endswith('/exchanges'):raise TimeoutError('Synthetic lost receipt')
            if req['method']=='GET' and req['path'].startswith('/v1/orders/'):response['body'].pop('exchange',None)
            return response
        kit.client_api._transport=lost;kit.verify_customer(email=case['email'])
        with patch('support_agent.tools_impl.sleep',return_value=None):
            outcome=kit.request_exchange(**parameters('exchange',case,rt))
        self.assertEqual(outcome['state'],'unknown');self.assertFalse(outcome['retry_safe'])
        self.assertEqual(outcome['submission_state'],'unknown')
        self.assertEqual(outcome['verification_checks'],['exchange']);self.assertEqual(len(writes(requests)),1)
