"""Actual tau2 factory/gateway/tool integration; only provider and API are fake."""

import json
import os
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from support_agent.state import ProtocolError
from tau2.data_model.message import (
    AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage,
)
from tau2.hyper.agent_context import (
    activate_agent_context, build_agent_context, get_agent_context,
)
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


def user(text="Please help me with my account."):
    return UserMessage(role="user", content=text, timestamp=None)


def text_reply(text="Please provide the email associated with your account."):
    return AssistantMessage(role="assistant", content=text, timestamp=None)


def call(identifier="lookup_1", **changes):
    values = {"id": identifier, "name": "lookup_customer", "requestor": "assistant",
              "arguments": {"customer_id": "", "email": "alex.p0@example.test"}}
    values.update(changes)
    return ToolCall(**values)


def calls_reply(*calls):
    return AssistantMessage(role="assistant", tool_calls=list(calls), timestamp=None)


def tool_result(identifier="lookup_1", content='{"email":"alex.p0@example.test"}', error=False):
    return ToolMessage(role="tool", id=identifier, content=content, error=error, timestamp=None)


def results(*messages):
    return MultiToolMessage(role="tool", tool_messages=list(messages))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock(side_effect=AssertionError("Backend must only be called by the simulated host"))
        self.kit = Tools(ClientAPI(self.transport, context=ClientAPIContext(conversation_id="offline_p1")))
        self.tools = self.kit.get_tools()
        self.context = self.make_context()
        self.agent = self.build(self.context)
        self.provider_patch = patch("tau2.hyper.agent_context.generate")
        self.provider = self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.provider.return_value = text_reply()

    def make_context(self, models=None, limits=None):
        context = build_agent_context(
            domain="retail_plus", tools=list(self.tools.values()), resource_root=ROOT / "agent",
            model_configs=models or [{"model": "offline_model", "constraints": {"max_tokens": 1024}}],
        )
        if limits:
            context = replace(context, runtime_config={**context.runtime_config, "agent_limits": limits})
        return context

    @staticmethod
    def build(context):
        with activate_agent_context(context):
            return create_agent()

    def pending(self, *calls):
        self.provider.return_value = calls_reply(*(calls or [call()]))
        reply, state = self.agent.generate_next_message(user(), self.agent.get_init_state())
        self.assertTrue(reply.tool_calls)
        return state

    def assert_halt(self, state, reason):
        self.assertEqual(state["halt_reason"], reason)
        self.assertNotIn("credential", json.dumps(state))

    def test_factory_retains_context_outside_activation(self):
        with self.assertRaises(RuntimeError):
            get_agent_context()
        self.assertIs(self.agent.context, self.context)
        reply, state = self.agent.generate_next_message(user(), self.agent.get_init_state())
        self.assertEqual(reply.content, self.provider.return_value.content)
        self.assertEqual(state["model_calls"], 1)
        self.provider.assert_called_once()
        self.transport.assert_not_called()

    def test_full_user_host_tool_model_cycle(self):
        fixture = json.loads((ROOT / "tests/fixtures/p0_baseline.json").read_text(encoding="utf-8"))
        customer = fixture["records"]["customers"][0]
        state = self.pending(call("email_lookup"))
        self.transport.assert_not_called()  # Agent must not execute the requested tool.
        self.transport.side_effect = None
        self.transport.return_value = {"status_code": 200, "body": {"customer_id": customer["customer_id"]}}
        self.transport.side_effect = [self.transport.return_value, {"status_code": 200, "body": customer}]
        output = self.tools["lookup_customer"](customer_id="", email=customer["email"])
        self.provider.return_value = text_reply(f"The account email is {output['email']}.")
        reply, final = self.agent.generate_next_message(
            results(tool_result("email_lookup", json.dumps(output))), json.loads(json.dumps(state)),
        )
        self.assertEqual(final["pending_call_ids"], [])
        self.assertEqual(final["model_calls"], 2)
        self.assertEqual(len(final["history"]), 4)
        self.assertEqual(self.provider.call_count, 2)
        self.assertIn(output["email"], reply.content)
        self.assertEqual([row.args[0]["path"] for row in self.transport.call_args_list],
                         ["/v1/customers/search", "/v1/customers/p0_customer_a"])

    def test_actual_gateway_actions_and_fixed_options(self):
        with patch.object(type(self.context.model_gateway), "generate", autospec=True,
                          wraps=self.context.model_gateway.generate) as gateway:
            gateway.return_value = text_reply()
            self.agent.generate_next_message(user(), self.agent.get_init_state())
        kwargs = gateway.call_args.kwargs
        self.assertEqual(kwargs["model"], "offline_model")
        self.assertEqual(kwargs["actions"], self.context.action_interface.select(["lookup_customer"]))
        self.assertNotIn("max_tokens", kwargs)
        self.agent.generate_next_message(user(), self.agent.get_init_state())
        self.assertEqual(self.provider.call_args.kwargs["max_tokens"], 1024)
        self.assertEqual(self.provider.call_args.kwargs["tools"][0].name, "lookup_customer")

    def test_one_of_is_explicit_fixed_value_is_omitted(self):
        agent = self.build(self.make_context(models=[{"model": "choice_model", "constraints": {
            "max_tokens": 321, "reasoning_effort": {"one_of": ["low", "medium"]}}}]))
        self.assertEqual(agent.gateway.options, {"reasoning_effort": "low"})
        agent.generate_next_message(user(), agent.get_init_state())
        self.assertEqual(self.provider.call_args.kwargs["reasoning_effort"], "low")
        self.assertEqual(self.provider.call_args.kwargs["max_tokens"], 321)

    def test_ambiguous_model_configuration_fails_before_provider(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.build(self.make_context(models=[
                {"model": "same_model", "constraints": {"max_tokens": 10}},
                {"model": "same_model", "constraints": {"max_tokens": 20}},
            ]))
        self.provider.assert_not_called()

    def test_multiple_results_reversed_order_and_error_preserved(self):
        state = self.pending(call("a"), call("b"))
        self.provider.return_value = text_reply("One lookup failed; no successful record is available for it.")
        _, final = self.agent.generate_next_message(
            results(tool_result("b", '{"email":"must-not-be-a-success"}', error=True), tool_result("a")), state,
        )
        messages = self.provider.call_args.kwargs["messages"]
        projected = messages[-1].tool_messages
        self.assertEqual([row.id for row in projected], ["b", "a"])
        self.assertFalse(json.loads(projected[0].content)["ok"])
        self.assertTrue(json.loads(projected[1].content)["ok"])
        self.assertTrue(final["history"][-2]["tool_messages"][0]["error"])
        self.assertEqual(final["pending_call_ids"], [])

    def test_invalid_json_is_failure_only_in_projection(self):
        state = self.pending()
        self.provider.return_value = text_reply("The service result was invalid.")
        _, final = self.agent.generate_next_message(results(tool_result(content="not-json")), state)
        projected = self.provider.call_args.kwargs["messages"][-1].tool_messages[0]
        self.assertFalse(json.loads(projected.content)["ok"])
        self.assertEqual(final["history"][-2]["tool_messages"][0]["content"], "not-json")

    def test_missing_extra_duplicate_result_ids_stop_without_model(self):
        for messages in ([tool_result("a")], [tool_result("a"), tool_result("x")],
                         [tool_result("a"), tool_result("a")]):
            with self.subTest(ids=[m.id for m in messages]):
                state = self.pending(call("a"), call("b"))
                previous_calls = self.provider.call_count
                reply, final = self.agent.generate_next_message(results(*messages), state)
                self.assert_halt(final, "invalid_host_sequence")
                self.assertEqual(self.provider.call_count, previous_calls)
                self.assertEqual(final["pending_call_ids"], ["a", "b"])
                self.assertFalse(reply.tool_calls)

    def test_unexpected_tool_and_user_interruption_are_not_silent_success(self):
        _, final = self.agent.generate_next_message(results(tool_result()), self.agent.get_init_state())
        self.assert_halt(final, "invalid_host_sequence")
        state = self.pending()
        _, final = self.agent.generate_next_message(user("Actually, stop."), state)
        self.assert_halt(final, "invalid_host_sequence")

    def test_invalid_assistant_output_cannot_reach_host_execution(self):
        outputs = [
            AssistantMessage(role="assistant", content="Working", tool_calls=[call()]),
            AssistantMessage(role="assistant", content=" "),
            calls_reply(call("")), calls_reply(call("a"), call("a")),
            calls_reply(call(name="arbitrary_refund")), calls_reply(call(requestor="user")),
            calls_reply(call(arguments={"customer_id": "", "email": 42})),
            calls_reply(call(arguments={"customer_id": "", "email": "a@example.test", "notes": "extra"})),
            user("wrong provider role"),
        ]
        for output in outputs:
            with self.subTest(output=type(output).__name__):
                self.provider.return_value = output
                reply, state = self.agent.generate_next_message(user(), self.agent.get_init_state())
                self.assert_halt(state, "invalid_model_reply")
                self.assertFalse(reply.tool_calls)
                self.transport.assert_not_called()

    def test_reused_call_id_is_rejected(self):
        state = self.pending()
        self.provider.return_value = calls_reply(call())
        _, final = self.agent.generate_next_message(results(tool_result()), state)
        self.assert_halt(final, "invalid_model_reply")

    def test_history_restores_pending_calls_and_original_user_text(self):
        history = [user("Earlier I gave a different email."), calls_reply(call("restore"))]
        before = [row.model_dump(mode="json") for row in history]
        state = self.agent.get_init_state(history)
        self.assertEqual(state["pending_call_ids"], ["restore"])
        self.provider.return_value = text_reply()
        self.agent.generate_next_message(results(tool_result("restore")), state)
        self.assertEqual(self.provider.call_args.kwargs["messages"][1].content, history[0].content)
        self.assertEqual(before, [row.model_dump(mode="json") for row in history])

    def test_history_normalization_does_not_mutate_caller_messages(self):
        assistant = calls_reply(call())
        assistant.content = ""
        state = self.agent.get_init_state([user(), assistant])
        self.assertEqual(assistant.content, "")
        self.assertNotIn("content", state["history"][-1])

    def test_wrong_state_version_or_bound_domain_is_rejected(self):
        state = self.agent.get_init_state()
        state["schema_version"] = 99
        with self.assertRaises(ValueError):
            self.agent.generate_next_message(user(), state)
        with self.assertRaisesRegex(ValueError, "retail_plus"):
            self.build(replace(self.context, runtime_config={"domain": "airline_plus"}))
        self.provider.assert_not_called()

    def test_history_individual_tool_results_are_restored_as_batch(self):
        history = [user(), calls_reply(call("a"), call("b")), tool_result("b"), tool_result("a"), text_reply()]
        state = self.agent.get_init_state(history)
        self.assertEqual(state["pending_call_ids"], [])
        self.assertEqual(state["tool_rounds"], 1)
        self.assertEqual(state["model_calls"], 2)
        with self.assertRaises(ProtocolError):
            self.agent.get_init_state(history[:3])

    def test_serialized_state_and_new_instance_resume_without_private_memory(self):
        state = self.pending(call("resume"))
        other = self.build(self.context)
        self.provider.return_value = text_reply("Resumed reply.")
        reply, final = other.generate_next_message(results(tool_result("resume")), json.loads(json.dumps(state)))
        self.assertEqual(reply.content, "Resumed reply.")
        self.assertEqual(final["pending_call_ids"], [])

    def test_sessions_and_input_state_remain_independent(self):
        first, second = self.agent.get_init_state(), self.agent.get_init_state()
        saved = deepcopy(first)
        self.agent.generate_next_message(user("Only session one."), first)
        self.assertEqual(first, saved)
        self.assertEqual(second["history"], [])
        self.agent.generate_next_message(user("Only session two."), second)
        messages = self.provider.call_args.kwargs["messages"]
        self.assertEqual([m.content for m in messages[1:]], ["Only session two."])

    def test_arbitrary_language_is_forwarded_without_parsing(self):
        for utterance in ("Li 是我的姓，名字 Alex，稍后给你邮箱。", "Call me Alex; surname Li.",
                          "Earlier address was wrong; the account uses alex.p0@example.test."):
            self.agent.generate_next_message(user(utterance), self.agent.get_init_state())
            self.assertEqual(self.provider.call_args.kwargs["messages"][-1].content, utterance)
        # This proves forwarding and gateway use, not real model comprehension.

    def test_provider_failure_is_terminal_without_exception_details_or_retry(self):
        self.provider.side_effect = RuntimeError("credential=synthetic-secret-do-not-copy")
        reply, state = self.agent.generate_next_message(user(), self.agent.get_init_state())
        self.assert_halt(state, "gateway_failure")
        self.assertEqual(state["model_calls"], 1)
        self.assertNotIn("synthetic-secret", json.dumps(state) + reply.content)
        self.agent.generate_next_message(user(), state)
        self.provider.assert_called_once()

    def test_session_model_budget_counts_attempts(self):
        agent = self.build(self.make_context(limits={"max_model_calls": 1}))
        _, state = agent.generate_next_message(user(), agent.get_init_state())
        _, final = agent.generate_next_message(user("Another request"), state)
        self.assert_halt(final, "model_call_limit")
        self.provider.assert_called_once()

    def test_tool_round_budget_allows_final_text_but_prevents_another_batch(self):
        agent = self.build(self.make_context(limits={"max_tool_rounds": 1}))
        self.provider.return_value = calls_reply(call("a"))
        _, state = agent.generate_next_message(user(), agent.get_init_state())
        self.provider.return_value = text_reply("Only the lookup completed.")
        _, completed = agent.generate_next_message(results(tool_result("a")), state)
        self.assertIsNone(completed["halt_reason"])
        self.provider.return_value = calls_reply(call("b"))
        _, limited = agent.generate_next_message(results(tool_result("a")), state)
        self.assert_halt(limited, "tool_round_limit")
        self.assertEqual(limited["tool_rounds"], 1)

    def test_new_user_request_resets_tool_round_counter(self):
        state = self.pending()
        self.provider.return_value = text_reply()
        _, state = self.agent.generate_next_message(results(tool_result()), state)
        self.provider.return_value = calls_reply(call("next_request"))
        _, state = self.agent.generate_next_message(user("Now please check again."), state)
        self.assertEqual(state["request_tool_rounds"], 1)
        self.assertEqual(state["tool_rounds"], 2)


if __name__ == "__main__":
    unittest.main()
