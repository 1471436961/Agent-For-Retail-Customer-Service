"""T1 safeguards through actual tau2 messages, tools and public REST runtime.

Only model outputs/fault injection are controlled. No provider calls or secrets.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "tests")]
from agent import create_agent
from test_reference_runtime import reference_kit, SEED
from test_gateway_wire import completion
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage, MultiToolMessage
from tau2.environment.environment import Environment
from tau2.hyper.agent_context import activate_agent_context, build_agent_context


class T1RepairTests(unittest.TestCase):
    def setUp(self):
        self.kit, self.runtime, self.requests = reference_kit()
        self.case = SEED["cases"][0]
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=json.loads((ROOT / "materials/framework/deployment_manifest.json").read_text())["allowed_agent_models"])
        with activate_agent_context(context):
            self.agent = create_agent()
        self.state = self.agent.get_init_state()
        self.environment = Environment("retail_plus", policy="", tools=self.kit)
        self.provider = self.enterContext(patch("tau2.utils.llm_utils.completion"))
        self.enterContext(patch("tau2.utils.llm_utils.get_response_cost", return_value=0))
        self.user = UserMessage(role="user", content=f"Please find {self.case['customer_id']}; "
                                f"my email is {self.case['email']}.")

    def decide(self, message, response):
        self.provider.side_effect = None
        self.provider.return_value = response
        reply, self.state = self.agent.generate_next_message(message, self.state)
        return reply

    def execute(self, reply):
        return MultiToolMessage(role="tool", tool_messages=[
            self.environment.get_response(call) for call in reply.tool_calls])

    def verify_call(self):
        return self.decide(self.user, completion(arguments={"email": self.case["email"],
                                      "claimed_customer_id": self.case["customer_id"]}))

    @staticmethod
    def envelope(**values):
        return completion(content=json.dumps(values), raw_text=True)

    def test_missing_claim_is_rejected_then_corrected_before_any_api(self):
        missing = completion(calls=[{"id": "missing-claim", "type": "function", "function": {
            "name": "verify_customer", "arguments": json.dumps({"email": self.case["email"]})}}], explicit_claim=False)
        correct = completion(arguments={"email": self.case["email"], "claimed_customer_id": self.case["customer_id"]})
        self.provider.side_effect = [missing, correct]
        reply, self.state = self.agent.generate_next_message(self.user, self.state)
        self.assertEqual(self.provider.call_count, 2)
        self.assertEqual(self.requests, [])
        self.assertEqual(reply.tool_calls[0].arguments["claimed_customer_id"], self.case["customer_id"])
        schema = next(t for t in self.provider.call_args.kwargs["tools"] if t["function"]["name"] == "verify_customer")
        self.assertIn("claimed_customer_id", schema["function"]["parameters"]["required"])

    def test_explicit_null_claim_still_supports_email_without_an_id(self):
        reply = self.decide(UserMessage(role="user", content="Please check the email I supplied."),
            completion(arguments={"email": self.case["email"], "claimed_customer_id": None}))
        result = self.execute(reply)
        self.assertFalse(result.tool_messages[0].error)
        self.assertEqual([r["path"] for r in self.requests],
                         ["/v1/customers/search", "/v1/customers/" + self.case["customer_id"]])

    def test_plain_echo_and_fabricated_factual_reference_are_not_forwarded(self):
        self.provider.side_effect = [completion(content="The account email is " + self.case["email"], raw_text=True),
            self.envelope(kind="facts", text="The account email is " + self.case["email"], evidence_call_ids=["invented"])]
        reply, self.state = self.agent.generate_next_message(self.user, self.state)
        self.assertEqual(self.state["halt_reason"], "invalid_model_reply")
        self.assertNotIn(self.case["email"], reply.content)
        self.assertEqual(self.requests, [])

    def test_semantic_clarification_remains_possible_without_verification(self):
        reply = self.decide(UserMessage(role="user", content="您好"),
            self.envelope(kind="clarification", text="您好，请提供账户邮箱或完整姓名和邮编。"))
        self.assertEqual(reply.content, "您好，请提供账户邮箱或完整姓名和邮编。")
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(self.requests, [])

    def test_email_is_rendered_from_actual_receipt_with_matching_call_id(self):
        verification = self.verify_call()
        actual = self.execute(verification)
        source_id = verification.tool_calls[0].id
        reply = self.decide(actual, self.envelope(kind="customer_fields", fields=["email"], evidence_call_ids=[source_id]))
        self.assertEqual(reply.content, "email: " + self.case["email"])
        self.assertTrue(self.state["customer_profile_loaded"])
        wire_result = self.provider.call_args.kwargs["messages"][-1]
        self.assertEqual(wire_result["tool_call_id"], source_id)
        self.assertEqual(json.loads(wire_result["content"])["evidence_call_id"], source_id)
        self.assertEqual(json.loads(wire_result["content"])["result"]["email"], self.case["email"])

    def test_profile_field_cannot_include_model_supplied_fake_value(self):
        result = self.execute(self.verify_call())
        self.provider.side_effect = [self.envelope(kind="customer_fields", fields=["email"],
            text="fake@example.test", evidence_call_ids=[result.tool_messages[0].id]),
            self.envelope(kind="customer_fields", fields=["email"], evidence_call_ids=[result.tool_messages[0].id])]
        reply, self.state = self.agent.generate_next_message(result, self.state)
        self.assertEqual(reply.content, "email: " + self.case["email"])
        self.assertNotIn("fake@example.test", reply.content)

    def test_wrong_claim_never_reads_details_and_failed_result_is_not_evidence(self):
        reply = self.decide(self.user, completion(arguments={"email": self.case["email"],
            "claimed_customer_id": SEED["cases"][1]["customer_id"]}))
        failed = self.execute(reply)
        self.assertTrue(failed.tool_messages[0].error)
        self.assertEqual([r["path"] for r in self.requests], ["/v1/customers/search"])
        bad = self.envelope(kind="facts", text="Verification succeeded.",
                            evidence_call_ids=[failed.tool_messages[0].id])
        self.provider.side_effect = [bad, bad]
        _, self.state = self.agent.generate_next_message(failed, self.state)
        self.assertEqual(self.state["evidence_call_ids"], [])
        self.assertEqual(self.state["halt_reason"], "invalid_model_reply")

    def test_unadvertised_tool_is_corrected_before_host_execution(self):
        unexpected = completion(calls=[{"id": "unexpected", "type": "function", "function": {
            "name": "get_customer_summary", "arguments": "{}"}}])
        self.provider.side_effect = [unexpected, completion(arguments={"email": self.case["email"],
                                                        "claimed_customer_id": self.case["customer_id"]})]
        reply, self.state = self.agent.generate_next_message(self.user, self.state)
        self.assertEqual([call.name for call in reply.tool_calls], ["verify_customer"])
        self.assertEqual(self.requests, [])

    def test_verified_identity_survives_detail_failure_and_recovers_without_search(self):
        verification = self.verify_call()
        dispatch = self.runtime.dispatch
        def fail_details(request):
            if request["method"] == "GET":
                return {"status_code": 503, "body": {"error": {"code": "resource_conflict", "message": "Synthetic outage"}}}
            return dispatch(request)
        with patch.object(self.runtime, "dispatch", side_effect=fail_details):
            receipt = self.execute(verification)
        body = json.loads(receipt.tool_messages[0].content)
        self.assertFalse(receipt.tool_messages[0].error)
        self.assertEqual(body["verification"], {"state": "verified", "profile_state": "unavailable"})
        self.assertNotIn("email", body)
        self.provider.side_effect = [self.envelope(kind="customer_fields", fields=["email"],
            evidence_call_ids=[verification.tool_calls[0].id]),
            completion(calls=[{"id": "recovered-profile", "type": "function",
                "function": {"name": "get_customer_summary", "arguments": "{}"}}])]
        recovery, self.state = self.agent.generate_next_message(receipt, self.state)
        self.assertEqual(self.state["verified_customer_id"], self.kit._identity.customer_id)
        self.assertFalse(self.state["customer_profile_loaded"])
        self.assertEqual({t["function"]["name"] for t in self.provider.call_args.kwargs["tools"]},
                         {"get_customer_summary", "transfer_to_human"})
        profile = self.execute(recovery)
        reply = self.decide(profile, self.envelope(kind="customer_fields", fields=["email"],
                                                 evidence_call_ids=["recovered-profile"]))
        self.assertEqual(reply.content, "email: " + self.case["email"])
        self.assertEqual([r["path"] for r in self.requests], ["/v1/customers/search",
            "/v1/customers/" + self.case["customer_id"], "/v1/customers/" + self.case["customer_id"]])

    def test_legacy_history_restores_evidence_without_requiring_new_live_fields(self):
        profile = self.kit.verify_customer(email=self.case["email"])
        history = [self.user, AssistantMessage(role="assistant", tool_calls=[
            ToolCall(id="historical-verify", name="verify_customer", arguments={"email": self.case["email"]})]),
            MultiToolMessage(role="tool", tool_messages=[ToolMessage(
                role="tool", id="historical-verify", content=json.dumps(profile))])]
        self.state = self.agent.get_init_state(history)
        before = len(self.requests)
        reply = self.decide(UserMessage(role="user", content="Please repeat the verified email."),
            self.envelope(kind="customer_fields", fields=["email"], evidence_call_ids=["historical-verify"]))
        self.assertEqual(reply.content, "email: " + self.case["email"])
        self.assertEqual(len(self.requests), before)


if __name__ == "__main__":
    unittest.main()
