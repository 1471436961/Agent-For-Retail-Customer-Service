"""Real candidate-server process, JSON brokers and native REST effects, no model."""
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from fixtures.native_runtime import reference_kit,parameters,apply_native_reference,ROOT,SEED
from support_agent.tools_impl import ACTIONS
from tau2.data_model.message import AssistantMessage,ToolCall,ToolMessage,UserMessage
from tau2.environment.environment import Environment


class SandboxWireTests(unittest.TestCase):
    def test_real_candidate_server_loads_all_tools_and_roundtrips_verified_exchange(self):
        kit,rt,requests=reference_kit();case=SEED['cases'][1]
        args=parameters('exchange',case,rt)
        with tempfile.TemporaryDirectory(prefix='wire-',dir=ROOT/'evaluation-results') as folder:
            root=Path(folder).resolve()
            self.assertTrue(root.is_relative_to((ROOT/'evaluation-results').resolve()))
            shutil.copytree(ROOT/'agent',root/'workspace',ignore=shutil.ignore_patterns('__pycache__'))
            with (root/'stderr.log').open('w',encoding='utf-8') as error_log:
                proc=subprocess.Popen([sys.executable,'-m','tau2.hyper.sandbox.candidate_server'],cwd=root,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error_log,text=True,encoding='utf-8',
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                lines=queue.Queue()
                def reader():
                    for line in proc.stdout:lines.put(line)
                worker=threading.Thread(target=reader,daemon=True);worker.start()
                counter=0;chosen=None;model_requests=[]
                def send(value):
                    proc.stdin.write(json.dumps(value)+'\n');proc.stdin.flush()
                def rpc(method,payload):
                    nonlocal counter
                    counter+=1;identifier=str(counter)
                    send({'id':identifier,'method':method,'payload':payload})
                    while True:
                        try:line=lines.get(timeout=30)
                        except queue.Empty:self.fail('Candidate RPC timed out; inspect local sandbox wiring')
                        if line.startswith('__TAU2_CLIENT_API_REQUEST__'):
                            request=json.loads(line[len('__TAU2_CLIENT_API_REQUEST__'):]);request_id=request.pop('id')
                            send({'id':request_id,'ok':True,'result':rt.dispatch(request)})
                        elif line.startswith('__TAU2_MODEL_REQUEST__'):
                            request=json.loads(line[len('__TAU2_MODEL_REQUEST__'):]);model_requests.append(request)
                            self.assertIsNotNone(chosen)
                            self.assertEqual({t['function']['name'] for t in request['tools']},set(ACTIONS))
                            self.assertEqual(request['kwargs']['max_tokens'],1024)
                            send({'id':request['id'],'ok':True,'result':chosen.model_dump(mode='json')})
                        elif line.startswith('__TAU2_CANDIDATE_RPC__'):
                            response=json.loads(line[len('__TAU2_CANDIDATE_RPC__'):])
                            self.assertEqual(response['id'],identifier)
                            self.assertTrue(response['ok'],response.get('error'))
                            return response['result']
                def turn(message,reply):
                    nonlocal chosen
                    chosen=reply
                    return rpc('agent_turn',{'message':message})
                def tool_result(call):
                    # Candidate RPC returns the raw tool value; the host wraps
                    # it with the original call ID using the SDK serializer.
                    value=rpc('tool_call',{'name':call.name,'arguments':call.arguments})
                    return ToolMessage(role='tool',id=call.id,content=Environment.to_json_str(value)).model_dump(mode='json')
                try:
                    rpc('configure',{'domain':'retail_plus','client_api_mode':'rest'})
                    rpc('reset',{'client_api_context':rt.context.model_dump(mode='json')})
                    metadata=rpc('metadata',{})
                    for name in ('rank_preferred','search_products','get_all_todo_orders','verify_operation'):
                        self.assertFalse(metadata['tools'][name]['mutates_state'])
                    rpc('initialize_agent',{'model_configs':[{'model':'offline-wire','constraints':{'max_tokens':1024}}]})
                    verify=ToolCall(id='identity',name='verify_customer',arguments={'email':case['email'],
                        'order_id':case['order_id'],'skill_names':['delivered-exchanges']})
                    turn(UserMessage(role='user',content='Please check my exchange options.').model_dump(mode='json'),
                         AssistantMessage(role='assistant',tool_calls=[verify]))
                    observed=tool_result(verify)
                    product=rt.environment.tools.db.orders[case['order_id']].items[0].product_id
                    rank=ToolCall(id='rank',name='rank_preferred',arguments={'product_id':product,'filters':{},
                        'numeric_filters':[],'preferences':[],'descending_price':True})
                    turn(observed,AssistantMessage(role='assistant',tool_calls=[rank]))
                    ranked=tool_result(rank)
                    self.assertFalse(ranked.get('error'),ranked)
                    turn(ranked,AssistantMessage(role='assistant',content='Full chosen exchange and settlement shown. Do you approve?'))
                    write=ToolCall(id='exchange',name='request_exchange',arguments=args)
                    turn(UserMessage(role='user',content='Yes, submit that complete exchange.').model_dump(mode='json'),
                         AssistantMessage(role='assistant',tool_calls=[write]))
                    actual=tool_result(write)
                    self.assertFalse(actual.get('error'),actual)
                    result=json.loads(actual['content'])
                    self.assertEqual(result['state'],'accepted');self.assertEqual(result['verified'],'True')
                    turn(actual,AssistantMessage(role='assistant',content='Your exchange application was accepted.'))
                    checked=tool_result(ToolCall(id='check',name='verify_operation',arguments={'order_id':case['order_id']}))
                    self.assertEqual(json.loads(checked['content'])['operation_id'],result['operation_id'])
                    _,gold,_=reference_kit();apply_native_reference(gold.environment.tools,'exchange',case['customer_id'],args)
                    self.assertEqual(rt.environment.get_db_hash(),gold.environment.get_db_hash())
                    self.assertEqual(len(model_requests),5)
                finally:
                    proc.terminate();proc.wait(timeout=10)
                    proc.stdin.close();proc.stdout.close();worker.join(timeout=2)
