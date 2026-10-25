"""Native messages in, native messages out, with a stable tool set."""
from itertools import product
import json
from pydantic import ValidationError
from tau2.data_model.message import AssistantMessage,MultiToolMessage,SystemMessage,ToolMessage,UserMessage
from support_agent.adapters.conversation_api import TRANSFER_NOTICE
from support_agent.prompts import BASE_PROMPT
from support_agent.skills import active_skill_names,instructions_for,skill_index
from support_agent.tools_impl import ACTIONS
from support_agent.domain.identity import ReadError,identity_selector

# The sandbox shim erases the upstream exception class. Recognize only the
# standard JSON decoder messages; never swallow service/quota/transport errors.
JSON_DECODER_MESSAGES=("Expecting value:","Expecting property name enclosed in double quotes:",
    "Expecting ',' delimiter:","Expecting ':' delimiter:","Unterminated string starting at:",
    "Extra data:","Invalid \\escape:","Invalid control character at:")


def message_object(value):
    if not isinstance(value,dict):
        return value
    model={"user":UserMessage,"assistant":AssistantMessage,"tool":ToolMessage}[value["role"]]
    return model.model_validate(value)


class RetailAgent:
    def __init__(self,context):
        self.gateway=context.model_gateway
        self.actions=context.action_interface.select(ACTIONS)
        self.by_name={a.name:a for a in self.actions}
        self.model,self.options=self._configuration()

    def _configuration(self):
        # Use only injected choices; preserve every fixed generation setting.
        models=sorted(self.gateway.available_models,key=lambda m:m.rsplit("/",1)[-1]!="kimi-k2.6")
        for model in models:
            for config in self.gateway.models:
                if config.model!=model:
                    continue
                choices={k:v["one_of"] for k,v in config.constrained_args.items()
                         if isinstance(v,dict) and set(v)=={"one_of"}}
                for values in product(*choices.values()):
                    options=dict(zip(choices,values))
                    try:
                        self.gateway.validate_request(model,options)
                    except ValueError:
                        continue
                    return model,options
        raise ValueError("No valid injected model configuration")

    def get_init_state(self,message_history=None):
        rows=[]
        for row in message_history or []:
            if isinstance(row,MultiToolMessage):
                rows.extend(row.tool_messages)
            elif isinstance(row,dict) and "tool_messages" in row:
                rows.extend(row["tool_messages"])
            else:
                rows.append(row)
        return {"messages":[message_object(m).model_dump(mode="json",exclude={"raw_data"}) for m in rows]}

    def generate_next_message(self,message,state):
        history=list(state["messages"])
        incoming=message.tool_messages if isinstance(message,MultiToolMessage) else [message]
        history.extend(message_object(m).model_dump(mode="json",exclude={"raw_data"}) for m in incoming)
        calls={c["id"]:c["name"] for row in history for c in row.get("tool_calls") or []}
        for row in reversed(history):
            if row["role"]!="tool" or calls.get(row.get("id"))!="transfer_to_human":
                continue
            try:
                accepted=not row.get("error") and json.loads(row["content"]).get("state")=="accepted"
            except (ValueError,AttributeError):
                accepted=False
            if accepted:
                return self._finish(history,AssistantMessage(role="assistant",content=TRANSFER_NOTICE,
                    raw_data={"handoff_accepted":True}))
            break
        names=list(dict.fromkeys(["service-workflow",*active_skill_names(history)]))
        messages=[SystemMessage(role="system",content=BASE_PROMPT+skill_index()+"\n\n"+instructions_for(names)),
                  *map(message_object,history)]
        for attempt in range(2):
            try:
                try:
                    reply=self.gateway.generate(model=self.model,actions=self.actions,messages=messages,
                        call_name="retail_response",**self.options)
                except RuntimeError as error:
                    if not str(error).startswith(JSON_DECODER_MESSAGES):
                        raise  # Preserve the host's infrastructure classification.
                    if attempt==0:
                        messages.append(SystemMessage(role="system",content=
                            "The previous model reply could not be decoded; no tool was executed. Return a shorter complete text reply or native tool call using the displayed schema and existing approvals."))
                        continue
                    return self._finish(history,AssistantMessage(role="assistant",content=
                        "I encountered a technical problem decoding the next response. No new tool call was executed; previous recorded results are unchanged."),
                        {"stage":"model_generation","exception_type":type(error).__name__})
                if not isinstance(reply,AssistantMessage) or (not reply.content and not reply.tool_calls):
                    raise ValueError("Empty assistant reply")
                for call in reply.tool_calls or []:
                    if call.name not in self.by_name:
                        raise ValueError("Unknown tool")
                    self.by_name[call.name].params.model_validate(call.arguments)
                    if call.name=="verify_customer":
                        identity_selector(**{key:call.arguments.get(key,"") for key in
                            ("email","first_name","last_name","postal_code")})
                # Provider debug objects are not dialogue or serialized state.
                return self._finish(history,reply.model_copy(update={"raw_data":None}))
            except (ValueError,ValidationError,json.JSONDecodeError) as error:
                if attempt:
                    return self._finish(history,AssistantMessage(role="assistant",content=
                        "I encountered a technical problem generating the next reply. No proposed tool call was executed."))
                detail=(" Verification needs email alone OR first_name, last_name and postal_code together. An account ID is not a first/last name; ask for missing identity fields without guessing."
                    if isinstance(error,ReadError) and error.code=="incomplete_identity_selector" else "")
                messages.append(SystemMessage(role="system",content=
                    "The draft was not executed. Return concise text or valid native tool arguments using the displayed schema. Keep the customer's actual request and approvals."+detail))

    @staticmethod
    def _finish(history,reply,diagnostic=None):
        history.append(reply.model_dump(mode="json",exclude={"raw_data"}))
        return reply,{"messages":history,**({"diagnostic":diagnostic} if diagnostic else {})}

    @staticmethod
    def is_stop(message):
        return bool(isinstance(message,AssistantMessage) and message.raw_data and message.raw_data.get("handoff_accepted"))
