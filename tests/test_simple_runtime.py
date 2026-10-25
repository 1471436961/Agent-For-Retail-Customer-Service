"""Native SDK message/termination checks; controlled decisions, no paid provider."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from fixtures.native_runtime import reference_kit,parameters,SEED,ROOT
from agent import create_agent
from support_agent.tools_impl import ACTIONS
from tau2.hyper.agent_context import build_agent_context,activate_agent_context
from tau2.data_model.message import AssistantMessage,UserMessage,ToolMessage,ToolCall
from tau2.data_model.tasks import Task,UserScenario
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.environment.environment import Environment

class SimpleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.kit,self.runtime,self.requests=reference_kit()
        ctx=build_agent_context(domain="retail_plus",tools=list(self.kit.get_tools().values()),resource_root=ROOT/"agent",
            model_configs=[{"model":"offline-native"}])
        with activate_agent_context(ctx):self.agent=create_agent()
        self.model=self.enterContext(patch("tau2.hyper.agent_context.generate"))

    def test_stable_tools_and_verbatim_history_in_both_directions(self):
        state=self.agent.get_init_state()
        original=UserMessage(role="user",content="Please preserve this exact correction and my conditions.")
        self.model.return_value=AssistantMessage(role="assistant",content="Which order do you mean?")
        answer,state=self.agent.generate_next_message(original,state)
        self.assertEqual(set(a.name for a in self.model.call_args.kwargs["tools"]),set(ACTIONS))
        self.assertEqual(self.model.call_args.kwargs["messages"][-1],original)
        self.assertEqual(set(state),{"messages"})
        self.model.return_value=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="read",name="load_skills",arguments={"names":["delivered-exchanges"]})])
        call,state=self.agent.generate_next_message(UserMessage(role="user",content="Read the exchange procedure."),state)
        result=Environment("retail_plus",policy="",tools=self.kit).get_response(call.tool_calls[0])
        self.model.return_value=AssistantMessage(role="assistant",content="Please provide your account email.")
        _,state=self.agent.generate_next_message(result,state)
        self.assertEqual(set(a.name for a in self.model.call_args.kwargs["tools"]),set(ACTIONS))
        system=self.model.call_args.kwargs["messages"][0].content
        self.assertIn("DIFFERENT options",system)
        self.assertEqual(self.model.call_args.kwargs["messages"][-1].content,result.content)
        self.assertEqual(self.requests,[])
        self.assertEqual(self.agent.get_init_state(state["messages"]),state)

    def test_native_error_result_keeps_error_and_call_id(self):
        state=self.agent.get_init_state([AssistantMessage(role="assistant",tool_calls=[ToolCall(id="bad-read",name="get_orders",arguments={})])])
        result=ToolMessage(role="tool",id="bad-read",content="Error: identity_required",error=True)
        self.model.return_value=AssistantMessage(role="assistant",content="Please provide your email or full name and postal code.")
        _,state=self.agent.generate_next_message(result,state)
        actual=self.model.call_args.kwargs["messages"][-1]
        self.assertTrue(actual.error);self.assertEqual(actual.id,"bad-read")
        self.assertEqual(actual.content,result.content)

    def test_invalid_arguments_get_one_repair_without_host_side_effect(self):
        self.model.side_effect=[AssistantMessage(role="assistant",tool_calls=[ToolCall(id="bad",name="update_order_address",arguments={"order_id":"unknown"})]),
            AssistantMessage(role="assistant",content="Please provide your account email.")]
        reply,state=self.agent.generate_next_message(UserMessage(role="user",content="Help with an address."),self.agent.get_init_state())
        self.assertEqual(reply.content,"Please provide your account email.")
        self.assertEqual(self.model.call_count,2);self.assertEqual(self.requests,[])
        self.assertEqual(len(state["messages"]),2)

    def test_incomplete_identity_selector_is_repaired_before_a_host_round(self):
        self.model.side_effect=[AssistantMessage(role='assistant',tool_calls=[ToolCall(id='incomplete',name='verify_customer',
            arguments={'first_name':'synthetic_account_42','postal_code':'12345'})]),
            AssistantMessage(role='assistant',content='Please provide your full first and last name and your desired changes.')]
        reply,state=self.agent.generate_next_message(UserMessage(role='user',content='My account ID is synthetic_account_42; postal code12345.'),self.agent.get_init_state())
        self.assertFalse(reply.tool_calls);self.assertEqual(self.model.call_count,2)
        self.assertEqual(self.requests,[])
        self.assertIn('first_name, last_name and postal_code together',self.model.call_args.kwargs['messages'][-1].content)

    def test_explicitly_truncated_provider_reply_never_becomes_a_partial_customer_recap(self):
        self.model.side_effect=[AssistantMessage(role='assistant',content='The complete price difference is $',
            raw_data={'choices':[{'finish_reason':'length'}]}),
            AssistantMessage(role='assistant',content='Here is the complete proposal. Please review it before submission.')]
        reply,state=self.agent.generate_next_message(UserMessage(role='user',content='Please explain the proposed change.'),self.agent.get_init_state())
        self.assertEqual(self.model.call_count,2)
        self.assertNotIn('price difference is $',reply.content)
        self.assertFalse(any(row.get('content')=='The complete price difference is $' for row in state['messages']))
        self.assertEqual(self.requests,[])

    def test_direct_write_does_not_require_local_proposal_or_approval_clock(self):
        case=SEED["cases"][0];args=parameters("order_address",case,self.runtime)
        self.kit.verify_customer(email=case["email"])
        previous=[UserMessage(role="user",content="Use this complete address: "+json.dumps(args)),
            AssistantMessage(role="assistant",content="I will update only that pending order to the complete address above. Do you approve?")]
        self.model.return_value=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="approved-write",name="update_order_address",arguments=args)])
        call,state=self.agent.generate_next_message(UserMessage(role="user",content="Yes. Submit it."),self.agent.get_init_state(previous))
        actual=Environment("retail_plus",policy="",tools=self.kit).get_response(call.tool_calls[0])
        self.assertFalse(actual.error,actual.content)
        self.assertEqual(json.loads(actual.content)["state"],"completed")

    def test_native_consecutive_revisions_only_register_confirmed_return_and_user_stop(self):
        case=SEED["cases"][1];params=parameters("exchange",case,self.runtime)
        original=self.runtime.environment.tools.db.orders[case["order_id"]].items[0]
        returns={"order_id":case["order_id"],"item_ids":[original.item_id],"refund_payment_method_id":case["payment_method_ids"]["credit_card"]}
        schedule=[("verify_customer",{"email":case["email"],"order_id":case["order_id"],"skill_names":["delivered-exchanges"]}),
            "Full selected replacement list and settlement shown. Is this the complete list and do you approve?",
            "Revised scope: only the first replacement and its stated difference. Approve?",
            "Return only the original item for its recorded price to the original card; no exchange. Instructions by email, no refund arrival promised. Approve?",
            ("request_return",returns),"The actual receipt confirms only the return application was accepted."]
        customer=["Exchange these items. My email is "+case["email"],"Actually only exchange the first item.",
            "No exchanges. Return that original item instead.","Yes, submit only that complete return.","Thank you. ###STOP###"]
        class Customer:
            def get_init_state(self,message_history=None):return 0
            def generate_next_message(self,message,state):return UserMessage(role="user",content=customer[state]),state+1
            def stop(self,*args):pass
        def provider(**kwargs):
            choice=schedule[self.model.call_count-1]
            if isinstance(choice,tuple):return AssistantMessage(role="assistant",tool_calls=[ToolCall(id=f"call-{self.model.call_count}",name=choice[0],arguments=choice[1])])
            return AssistantMessage(role="assistant",content=choice)
        self.model.side_effect=provider
        run=Orchestrator(domain="retail_plus",agent=self.agent,user=Customer(),
            environment=Environment("retail_plus",policy="",tools=self.kit),
            task=Task(id="synthetic-revision",user_scenario=UserScenario(instructions="Synthetic lifecycle")),max_steps=16,validate_communication=True).run()
        self.assertEqual(run.termination_reason.value,"user_stop")
        writes=[r for r in self.requests if r["method"]!="GET" and r["path"]!="/v1/customers/search"]
        self.assertEqual(len(writes),1);self.assertTrue(writes[0]["path"].endswith("/returns"))
        self.assertEqual(self.runtime.environment.tools.db.orders[case["order_id"]].status,"return requested")

    def test_actual_handoff_is_terminal_and_ordinary_success_is_not(self):
        self.assertFalse(self.agent.is_stop(AssistantMessage(role="assistant",content="Your return was accepted.")))
        call=AssistantMessage(role="assistant",tool_calls=[ToolCall(id="handoff",name="transfer_to_human",arguments={"summary":"Customer explicitly requests a human."})])
        state=self.agent.get_init_state([call])
        result=Environment("retail_plus",policy="",tools=self.kit).get_response(call.tool_calls[0])
        reply,state=self.agent.generate_next_message(result,state)
        self.assertTrue(self.agent.is_stop(reply));self.assertIsInstance(reply.timestamp,str)
        self.model.assert_not_called()


class NativeWireTests(unittest.TestCase):
    def test_real_sdk_conversion_keeps_fixed_settings_and_matching_tool_result(self):
        from litellm import ModelResponse
        kit,runtime,_=reference_kit();case=SEED["cases"][0]
        context=build_agent_context(domain="retail_plus",tools=list(kit.get_tools().values()),
            resource_root=ROOT/"agent",model_configs=[{"model":"openai/contract-test-model","constraints":{"max_tokens":1024}}])
        with activate_agent_context(context):agent=create_agent()
        def response(message,reason):
            return ModelResponse(model="openai/contract-test-model",choices=[{"finish_reason":reason,"message":message}],
                usage={"prompt_tokens":10,"completion_tokens":10,"total_tokens":20})
        with patch("tau2.utils.llm_utils.completion") as provider,patch("tau2.utils.llm_utils.get_response_cost",return_value=0):
            provider.return_value=response({"role":"assistant","content":None,"tool_calls":[{
                "id":"native-wire","type":"function","function":{"name":"verify_customer",
                    "arguments":json.dumps({"email":case["email"],"skill_names":["address-changes"]})}}]},"tool_calls")
            call,state=agent.generate_next_message(UserMessage(role="user",content="My email is "+case["email"]),agent.get_init_state())
            self.assertEqual(provider.call_args.kwargs["max_tokens"],1024)
            self.assertEqual({t["function"]["name"] for t in provider.call_args.kwargs["tools"]},set(ACTIONS))
            result=Environment("retail_plus",policy="",tools=kit).get_response(call.tool_calls[0])
            self.assertFalse(result.error,result.content)
            provider.return_value=response({"role":"assistant","content":"What address would you like to use?"},"stop")
            reply,state=agent.generate_next_message(result,state)
            actual=provider.call_args.kwargs["messages"][-1]
            self.assertEqual(actual["role"],"tool")
            self.assertEqual(actual["tool_call_id"],call.tool_calls[0].id)
            self.assertEqual(actual["content"],result.content)
            self.assertEqual(reply.content,"What address would you like to use?")
