"""Exercise the actual sandbox broker's exception translation without a provider."""
from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fixtures.native_runtime import reference_kit,ROOT
from agent import create_agent
from tau2.data_model.message import AssistantMessage,ToolCall,UserMessage
from tau2.hyper.agent_context import build_agent_context,activate_agent_context
from tau2.hyper.sandbox.candidate_server import _brokered_generate


class ModelBrokerFormatTests(unittest.TestCase):
    def agent(self):
        kit,_,requests=reference_kit()
        context=build_agent_context(domain='retail_plus',tools=list(kit.get_tools().values()),resource_root=ROOT/'agent',
            model_configs=[{'model':'offline-broker','constraints':{'max_tokens':512}}])
        with activate_agent_context(context):agent=create_agent()
        return agent,requests

    def run_broker(self,responses):
        agent,requests=self.agent();captured=io.StringIO()
        with patch('tau2.hyper.agent_context.generate',_brokered_generate), \
             patch('tau2.hyper.sandbox.candidate_server.uuid.uuid4',return_value=SimpleNamespace(hex='rpc-audit')), \
             patch('sys.stdin',io.StringIO(''.join(json.dumps(r)+'\n' for r in responses))),redirect_stdout(captured):
            reply,state=agent.generate_next_message(UserMessage(role='user',content='Please review the request.'),agent.get_init_state())
        sent=[json.loads(row[len('__TAU2_MODEL_REQUEST__'):]) for row in captured.getvalue().splitlines() if row.startswith('__TAU2_MODEL_REQUEST__')]
        return reply,state,requests,sent

    def error(self):
        return {'id':'rpc-audit','ok':False,'error':{'type':'JSONDecodeError',
            'message':"Expecting ',' delimiter: line1column12(char11); private draft text"}}

    def test_broker_json_error_repairs_once_with_unchanged_fixed_settings(self):
        valid=AssistantMessage(role='assistant',tool_calls=[ToolCall(id='skill',name='load_skills',arguments={'names':['catalog-selection']})])
        reply,state,requests,sent=self.run_broker([self.error(),{'id':'rpc-audit','ok':True,'result':valid.model_dump(mode='json')}])
        self.assertEqual(len(sent),2)
        self.assertTrue(all(r['kwargs']['max_tokens']==512 for r in sent))
        self.assertEqual(reply.tool_calls[0].name,'load_skills');self.assertEqual(requests,[])
        self.assertNotIn('private draft',json.dumps(state))

    def test_persistent_broker_format_error_never_executes_a_partial_tool(self):
        reply,state,requests,sent=self.run_broker([self.error(),self.error()])
        self.assertEqual(len(sent),2);self.assertFalse(reply.tool_calls);self.assertEqual(requests,[])
        self.assertNotIn('private draft',reply.content)

    def test_service_limit_and_unknown_runtime_errors_keep_host_classification(self):
        for message in ('HTTP503 unavailable','rate limit exceeded','Model broker returned a mismatched response'):
            with self.subTest(message=message):
                agent,requests=self.agent();error=RuntimeError(message)
                with patch('tau2.hyper.agent_context.generate',side_effect=error) as model:
                    with self.assertRaises(RuntimeError) as caught:
                        agent.generate_next_message(UserMessage(role='user',content='Continue.'),agent.get_init_state())
                    self.assertIs(caught.exception,error);self.assertEqual(model.call_count,1)
                self.assertEqual(requests,[])
