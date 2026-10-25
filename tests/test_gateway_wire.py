"""Exercise tau2's real wire conversion; only the final provider is simulated.

No real model comprehension or remote evaluation is claimed by these tests.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from litellm import ModelResponse
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage


def completion(*, arguments=None, content=None, calls=None, raw_text=False, explicit_claim=True):
    message = {"role": "assistant", "content": content}
    if arguments is not None:
        arguments = {"claimed_customer_id": None, **arguments}
        calls = [{"id": "wire-verify", "type": "function", "function": {
            "name": "verify_customer", "arguments": json.dumps(arguments)}}]
    if calls is not None:
        calls = [dict(call, function=dict(call["function"], arguments=json.dumps({
                    "claimed_customer_id": None, **json.loads(call["function"]["arguments"])})))
                 if explicit_claim and call["function"]["name"] == "verify_customer" else call
                 for call in calls]
        message["tool_calls"] = calls
    return ModelResponse(model="openai/contract-test-model", choices=[{
        "finish_reason": "tool_calls" if calls is not None else "stop", "message": message,
    }], usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})


class GatewayWireTests(unittest.TestCase):
    def setUp(self):
        self.backend = SyntheticBackend()
        self.kit = Tools(ClientAPI(self.backend, context=ClientAPIContext(conversation_id="offline_wire")))
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=[{
                "model": "openai/contract-test-model", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(context):
            self.agent = create_agent()
        self.user = UserMessage(role="user", content="请核对我的账户，邮箱 alex.p0@example.test，编号 p0_customer_a。")
        self.provider = self.enterContext(patch("tau2.utils.llm_utils.completion"))
        self.enterContext(patch("tau2.utils.llm_utils.get_response_cost", return_value=0))

    def test_unverified_request_is_small_and_business_capabilities_require_real_result(self):
        customer = self.backend.records["customers"][0]
        self.provider.side_effect = [completion(arguments={"email": customer["email"]}),
                                    completion(content="Your verified email is " + customer["email"])]
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        first = self.provider.call_args.kwargs
        names = {t["function"]["name"] for t in first["tools"]}
        self.assertEqual(names, {"verify_customer", "load_skills", "transfer_to_human"})
        self.assertIsNone(state["verified_customer_id"])
        # Includes optional owned-order/catalog reads after verification, but
        # still excludes the full business action schema set. This is a test
        # size sanity check, not a runtime/provider request limit.
        self.assertLess(state["gateway_request"]["request_bytes"], 8000)
        self.assertEqual(state["gateway_request"]["fixed_token_settings"], {"max_tokens": 1024})
        self.assertEqual(state["gateway_request"]["model_call"], 1)
        call = reply.tool_calls[0]
        actual = self.kit.get_tools()[call.name](**call.arguments)
        reply, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=call.id, content=json.dumps(actual))]), state)
        self.assertEqual(state["verified_customer_id"], customer["customer_id"])
        self.assertIn("prepare_change", {t["function"]["name"]
                                        for t in self.provider.call_args.kwargs["tools"]})
        self.assertIn(actual["email"], reply.content)

    def test_failed_verification_does_not_unlock_business_tools(self):
        self.provider.side_effect = [completion(arguments={"email": "missing@example.test"}),
                                    completion(content="Please check the identifying information.")]
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        call = reply.tool_calls[0]
        _, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=call.id, content='{"customer_id":"untrusted"}', error=True)]), state)
        self.assertIsNone(state["verified_customer_id"])
        self.assertEqual({t["function"]["name"] for t in self.provider.call_args.kwargs["tools"]},
                         {"verify_customer", "load_skills", "transfer_to_human"})

    def test_reused_provider_id_has_matching_results_in_both_wire_rounds(self):
        customer = self.backend.records["customers"][0]
        self.provider.side_effect = [completion(arguments={"email": customer["email"]}),
                                    completion(calls=[{"id":"wire-verify", "type":"function", "function":{
                                        "name":"get_customer_summary", "arguments":"{}"}}]),
                                    completion(content="The account email is " + customer["email"])]
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        ids = []
        for _ in range(2):
            call = reply.tool_calls[0]
            ids.append(call.id)
            output = self.kit.get_tools()[call.name](**call.arguments)
            reply, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
                ToolMessage(role="tool", id=call.id, content=json.dumps(output))]), state)
            self.assertIsNone(state["halt_reason"])
        self.assertEqual(len(set(ids)), 2)
        wire = self.provider.call_args.kwargs["messages"]
        call_ids = [call["id"] for message in wire for call in message.get("tool_calls", [])]
        result_ids = [message["tool_call_id"] for message in wire if message["role"] == "tool"]
        self.assertEqual(call_ids, ids)
        self.assertEqual(result_ids, ids)

    def test_preparation_and_plan_wire_schemas_include_complete_field_shapes(self):
        self.agent.gateway.live_actions = self.agent.gateway.compatibility_actions
        customer = self.backend.records["customers"][0]
        self.provider.side_effect = [completion(arguments={"email": customer["email"]}),
                                    completion(content="How can I help with your account?")]
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        call = reply.tool_calls[0]
        result = self.kit.get_tools()[call.name](**call.arguments)
        self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=call.id, content=json.dumps(result))]), state)
        schemas = {t["function"]["name"]: t["function"]["parameters"]
                   for t in self.provider.call_args.kwargs["tools"]}
        for name in ("prepare_change", "plan_changes"):
            schema = schemas[name]
            definitions = schema["$defs"]
            self.assertEqual(definitions["Address"]["required"],
                             ["address_line_1", "city", "region", "country", "postal_code"])
            prefix = "Owned" if name == "prepare_change" else ""
            self.assertTrue(definitions[prefix + "CancellationParameters"]["additionalProperties"] is False)
            self.assertEqual(definitions[prefix + "ReplacementParameters"]["required"],
                             ["order_id", "payment_method_id", "replacements"])
            self.assertEqual(definitions["ReplacementChoice"]["required"],
                             ["existing_item_id", "replacement_item_id"])
            self.assertEqual(definitions["ReplacementChoice"]["properties"]["preserve_options"]["type"], "array")
            self.assertIn("refund_payment_method_id", definitions[prefix + "ReturnParameters"]["required"])

    def test_real_wire_host_verification_then_customer_facts(self):
        customer = self.backend.records["customers"][0]
        gift = next(m for m in customer["payment_methods"] if m["source"] == "gift_card")
        gift["balance"] = "500.0"
        self.provider.side_effect = [completion(arguments={
            "email": customer["email"], "claimed_customer_id": customer["customer_id"]}),
            completion(content="The account email is " + customer["email"] + ".")]
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        self.assertIsNone(state["halt_reason"])
        self.assertEqual(self.backend.calls, [])
        request = self.provider.call_args.kwargs
        self.assertEqual(request["messages"][-1]["content"], self.user.content)
        self.assertEqual(request["max_tokens"], 1024)
        self.assertIn("verify_customer", [t["function"]["name"] for t in request["tools"]])
        call = reply.tool_calls[0]
        output = self.kit.get_tools()[call.name](**call.arguments)
        self.assertEqual([row["path"] for row in self.backend.calls],
                         ["/v1/customers/search", "/v1/customers/p0_customer_a"])
        self.assertEqual(next(m for m in output["payment_methods"]
                              if m["id"] == gift["id"])["balance"], 500.0)
        reply, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=call.id, content=json.dumps(output), error=False)]), state)
        self.assertIsNone(state["halt_reason"])
        self.assertIn(customer["email"], reply.content)
        wire_result = next(m for m in reversed(self.provider.call_args.kwargs["messages"]) if m["role"] == "tool")
        self.assertEqual(wire_result["tool_call_id"], call.id)
        projected = json.loads(wire_result["content"])
        self.assertTrue(projected["ok"])
        self.assertEqual(projected["result"]["response_warnings"][0]["code"], "numeric_string_balance")
        self.assertEqual(self.provider.call_count, 2)

    def test_model_supplied_wrong_claim_never_reaches_profile(self):
        self.provider.return_value = completion(arguments={
            "email": "alex.p0@example.test", "claimed_customer_id": "p0_customer_b"})
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        from support_agent.domain.identity import ReadError
        with self.assertRaises(ReadError):
            call = reply.tool_calls[0]
            self.kit.get_tools()[call.name](**call.arguments)
        self.assertEqual([row["path"] for row in self.backend.calls], ["/v1/customers/search"])

    def test_restored_narrated_call_and_actual_result_reach_provider_with_matching_id(self):
        assistant = AssistantMessage(role="assistant", content="I will verify the account.", tool_calls=[
            ToolCall(id="restored-verify", name="verify_customer", arguments={
                "email": "alex.p0@example.test", "claimed_customer_id": "p0_customer_a"})])
        history = [self.user, assistant]
        serialized = [row.model_dump(mode="json") for row in history]
        original = json.dumps(serialized)
        state = self.agent.get_init_state(serialized)
        self.assertEqual(self.provider.call_count, 0)
        self.assertEqual(self.backend.calls, [])
        call = assistant.tool_calls[0]
        output = self.kit.get_tools()[call.name](**call.arguments)
        self.provider.return_value = completion(content="The account email is " + output["email"] + ".")
        reply, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=call.id, content=json.dumps(output))]), state)
        self.assertIsNone(state["halt_reason"])
        self.assertIn(output["email"], reply.content)
        wire = self.provider.call_args.kwargs["messages"]
        self.assertEqual([row["role"] for row in wire], ["system", "user", "assistant", "tool"])
        self.assertEqual(wire[-2]["content"], assistant.content)
        self.assertEqual(wire[-2]["tool_calls"][0]["id"], call.id)
        self.assertEqual(wire[-1]["tool_call_id"], call.id)
        self.assertTrue(json.loads(wire[-1]["content"])["ok"])
        self.assertEqual(original, json.dumps(serialized))
        self.assertEqual([row["path"] for row in self.backend.calls],
                         ["/v1/customers/search", "/v1/customers/p0_customer_a"])

    def test_completed_narrated_multi_call_history_preserves_reversed_results_and_errors(self):
        assistant = AssistantMessage(role="assistant", content="I will check these two read results.",
            tool_calls=[ToolCall(id="past-profile", name="get_customer_summary", arguments={}),
                        ToolCall(id="past-catalog", name="list_products", arguments={})])
        history = [self.user, assistant,
                   ToolMessage(role="tool", id="past-catalog", content="catalog_unavailable", error=True),
                   ToolMessage(role="tool", id="past-profile", content='{"email":"actual@example.test"}'),
                   AssistantMessage(role="assistant", content="The catalog lookup failed.")]
        state = self.agent.get_init_state([row.model_dump(mode="json") for row in history])
        self.provider.return_value = completion(content="I can continue with the available account result.")
        _, state = self.agent.generate_next_message(UserMessage(role="user", content="What did succeed?"), state)
        self.assertIsNone(state["halt_reason"])
        wire = self.provider.call_args.kwargs["messages"]
        self.assertEqual(wire[2]["content"], assistant.content)
        self.assertEqual([row["tool_call_id"] for row in wire[3:5]], ["past-catalog", "past-profile"])
        self.assertFalse(json.loads(wire[3]["content"])["ok"])
        self.assertTrue(json.loads(wire[4]["content"])["ok"])
        self.assertEqual(state["pending_call_ids"], [])

    def test_real_wire_keeps_each_result_in_reversed_multi_tool_batch(self):
        calls = [{"id": "wire-profile", "type": "function", "function": {
                    "name": "get_customer_summary", "arguments": "{}"}},
                 {"id": "wire-catalog", "type": "function", "function": {
                    "name": "list_products", "arguments": "{}"}}]
        self.provider.return_value = completion(arguments={"email": "alex.p0@example.test"})
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        verify_call = reply.tool_calls[0]
        verified = self.kit.get_tools()[verify_call.name](**verify_call.arguments)
        # A historical pending batch must remain readable even though live
        # retail policy now permits only one call at a time.
        historical = AssistantMessage(role="assistant", tool_calls=[ToolCall(
            id=row["id"], name=row["function"]["name"], arguments={}) for row in calls])
        state = self.agent.get_init_state([self.user, reply, MultiToolMessage(role="tool", tool_messages=[
            ToolMessage(role="tool", id=verify_call.id, content=json.dumps(verified))]), historical])
        reply = historical
        self.provider.return_value = completion(content="The catalog request failed.")
        self.backend.overrides[("GET", "/v1/catalog/products")] = self.backend.missing()
        from support_agent.domain.identity import ReadError
        results = []
        for call in reversed(reply.tool_calls):
            try:
                output = self.kit.get_tools()[call.name](**call.arguments)
                results.append(ToolMessage(role="tool", id=call.id, content=json.dumps(output)))
            except ReadError as error:
                results.append(ToolMessage(role="tool", id=call.id, content=str(error), error=True))
        _, state = self.agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=results), state)
        self.assertIsNone(state["halt_reason"])
        wire = [m for m in self.provider.call_args.kwargs["messages"] if m["role"] == "tool"][-2:]
        self.assertEqual([m["tool_call_id"] for m in wire], ["wire-catalog", "wire-profile"])
        self.assertFalse(json.loads(wire[0]["content"])["ok"])
        self.assertTrue(json.loads(wire[1]["content"])["ok"])
        raw_batch = state["history"][-2]["tool_messages"]
        self.assertEqual([m["id"] for m in raw_batch], ["wire-catalog", "wire-profile"])
        self.assertTrue(raw_batch[0]["error"])

    def test_safe_error_type_survives_state_restore_without_raw_message_or_retry(self):
        # Inject at the public gateway: no dependency on a provider's logging policy.
        with patch.object(type(self.agent.context.model_gateway), "generate",
                          side_effect=TypeError("credential=synthetic-sensitive-payload")) as gateway:
            reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
            self.assertEqual(state["gateway_diagnostic"], {
                "stage": "gateway_generate", "exception_type": "TypeError", "http_status": None,
                "reported_exception_type": None, "provider_error_code": None, "rate_limit_kind": None,
                "provider_message_terms": [], "provider_message_words": [],
                "origin": None, "failure_code": None})
            self.assertIn('"exception_type": "TypeError"', reply.content)
            self.assertNotIn("synthetic-sensitive", reply.content + json.dumps(state))
            again, restored = self.agent.generate_next_message(self.user, json.loads(json.dumps(state)))
            self.assertEqual(again.content, reply.content)
            self.assertEqual(restored["gateway_diagnostic"], state["gateway_diagnostic"])
            gateway.assert_called_once()
            self.assertEqual(self.backend.calls, [])

    def test_unknown_exception_names_and_payloads_are_not_retained(self):
        SensitiveError = type("sensitive-secret-in-class-name", (Exception,), {})
        with patch.object(type(self.agent.context.model_gateway), "generate",
                          side_effect=SensitiveError("sensitive-secret-in-body")):
            reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        self.assertEqual(state["gateway_diagnostic"]["exception_type"], "OtherError")
        self.assertNotIn("sensitive-secret", reply.content + json.dumps(state))

    def test_only_valid_integer_http_status_is_retained(self):
        for status, expected in ((401, 401), (403, 403), (503, 503), (True, None), ("401", None), (999, None)):
            with self.subTest(status=status):
                error = RuntimeError("do-not-copy-provider-response")
                error.status_code = status
                with patch.object(type(self.agent.context.model_gateway), "generate", side_effect=error):
                    reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
                self.assertEqual(state["gateway_diagnostic"]["http_status"], expected)
                self.assertNotIn("do-not-copy", reply.content + json.dumps(state))


if __name__ == "__main__":
    unittest.main()
