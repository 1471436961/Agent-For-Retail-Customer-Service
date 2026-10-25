"""Measure six actual native SDK input shapes offline; no paid model calls."""
import argparse,json,os,sys
from pathlib import Path
from unittest.mock import patch
os.environ['PYTHON_DOTENV_DISABLED']='1'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
os.environ['LOGURU_LEVEL']='CRITICAL'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from fixtures.native_runtime import reference_kit,SEED
from agent import create_agent
from tau2.hyper.agent_context import build_agent_context,activate_agent_context
from tau2.environment.environment import Environment
from tau2.data_model.message import AssistantMessage,UserMessage,ToolCall

def measure():
    samples={}
    for skill,index in (('address-changes',0),('delivered-returns',1),('delivered-exchanges',1)):
        kit,runtime,_=reference_kit();case=SEED['cases'][index]
        context=build_agent_context(domain='retail_plus',tools=list(kit.get_tools().values()),resource_root=ROOT/'agent',model_configs=[{'model':'offline-measurement'}])
        with activate_agent_context(context):agent=create_agent()
        state=agent.get_init_state();host=Environment('retail_plus',policy='',tools=kit)
        with patch('tau2.hyper.agent_context.generate') as model:
            model.return_value=AssistantMessage(role='assistant',tool_calls=[ToolCall(id='verify',name='verify_customer',arguments={'email':case['email'],'order_id':case['order_id'],'skill_names':[skill]})])
            incoming=UserMessage(role='user',content='Help with my requested change. My email is '+case['email'])
            call,state=agent.generate_next_message(incoming,state)
            def capture(label):
                kw=model.call_args.kwargs
                payload={'messages':[m.model_dump(mode='json',exclude_none=True) for m in kw['messages']],'tools':[a.openai_schema for a in kw['tools']]}
                for row in payload['messages']:row.pop('timestamp',None)
                samples[skill+'/'+label]={'request_bytes':len(json.dumps(payload,ensure_ascii=False).encode()),'tool_count':len(payload['tools']),'system_bytes':len(payload['messages'][0]['content'].encode())}
            capture('intake')
            actual=host.get_response(call.tool_calls[0])
            model.return_value=AssistantMessage(role='assistant',content='What would you like changed?')
            agent.generate_next_message(actual,state);capture('verified')
    return {'paid_provider_called':False,'scope':'Six controlled SDK input shapes, not a full conversation or Kimi billing tokens','samples':samples,'total_request_bytes':sum(s['request_bytes'] for s in samples.values())}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=measure();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))