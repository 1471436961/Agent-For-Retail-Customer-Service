"""Failure diagnostics through the real SDK broker; no provider/network calls."""

import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from support_agent.domain.gateway_failure import GatewayDiagnostic, diagnose_gateway_failure
from support_agent.gateway import GatewayAdapter
from tau2.data_model.message import UserMessage
from tau2.hyper import agent_context
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext
from tau2.hyper.sandbox.candidate_server import _brokered_generate, _MODEL_REQUEST_PREFIX
from tau2.hyper.sandbox.sealed_runner import CandidateProcess


class GatewayFailureTests(unittest.TestCase):
    def setUp(self):
        self.backend = SyntheticBackend()
        kit = Tools(ClientAPI(self.backend, context=ClientAPIContext(conversation_id="broker_failure")))
        self.context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=[{
                "model": "openai/contract-test-model", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(self.context):
            self.agent = create_agent()

    def through_broker(self, host_error):
        output = io.StringIO()
        envelopes = []
        host = SimpleNamespace(model_gateway=self.context.model_gateway)

        class BrokerInput:
            def readline(inner):
                line = [line for line in output.getvalue().splitlines()
                        if line.startswith(_MODEL_REQUEST_PREFIX)][-1]
                request = json.loads(line[len(_MODEL_REQUEST_PREFIX):])
                with patch.object(agent_context, "generate", side_effect=host_error):
                    response = CandidateProcess._model_response(host, request)
                envelopes.append(response)
                return json.dumps(response) + "\n"

        with patch.object(agent_context, "generate", _brokered_generate), \
                patch("sys.stdout", output), patch("sys.stdin", BrokerInput()):
            reply, state = self.agent.generate_next_message(
                UserMessage(role="user", content="Hello"), self.agent.get_init_state())
        self.assertEqual(len(envelopes), 1)
        self.assertEqual(state["model_calls"], 1)
        self.assertEqual(state["halt_reason"], "gateway_failure")
        self.assertEqual(state["gateway_diagnostic"]["origin"], "model_broker")
        self.assertEqual(state["gateway_diagnostic"]["exception_type"], "RuntimeError")
        self.assertEqual(self.backend.calls, [])
        return reply, state, envelopes[0]

    def test_broker_retained_provider_type_and_status_are_classified(self):
        reply, state, envelope = self.through_broker(ValueError(
            "litellm.BadRequestError: OpenAIException - Error code: 400 - "
            "{secret: sensitive-fixture, customer: sensitive-customer, url: https://private.test}"))
        self.assertEqual(envelope["error"]["type"], "ValueError")
        diagnostic = state["gateway_diagnostic"]
        self.assertEqual(diagnostic["reported_exception_type"], "BadRequestError")
        self.assertEqual(diagnostic["http_status"], 400)
        self.assertEqual(diagnostic["failure_code"], "provider_bad_request")
        self.assertNotIn("sensitive-", reply.content + json.dumps(state))
        self.assertNotIn("private.test", reply.content + json.dumps(state))
        # Restore retains the diagnostic and does not attempt another model call.
        with patch.object(type(self.context.model_gateway), "generate") as gateway:
            again, restored = self.agent.generate_next_message(
                UserMessage(role="user", content="Try again"), json.loads(json.dumps(state)))
        gateway.assert_not_called()
        self.assertEqual(again.content, reply.content)
        self.assertEqual(restored["gateway_diagnostic"], diagnostic)

    def test_broker_sdk_parameter_failure_retains_cause_without_parameter_values(self):
        reply, state, _ = self.through_broker(ValueError(
            "Candidate model request contains unsupported arguments: sensitive-parameter"))
        self.assertEqual(state["gateway_diagnostic"]["failure_code"], "unsupported_request_arguments")
        self.assertIsNone(state["gateway_diagnostic"]["reported_exception_type"])
        self.assertNotIn("sensitive-parameter", reply.content + json.dumps(state))

    def test_broker_missing_provider_configuration_is_identified_without_key_or_name(self):
        reply, state, _ = self.through_broker(RuntimeError(
            "SENSITIVE_VARIABLE is not set, but model 'sensitive-model' is routed to the "
            "'sensitive-provider' provider. Export it in .env, or point model_routing.toml "
            "at a provider whose key you have."))
        self.assertEqual(state["gateway_diagnostic"]["failure_code"], "missing_provider_key")
        self.assertNotIn("SENSITIVE_VARIABLE", reply.content + json.dumps(state))
        self.assertNotIn("sensitive-", reply.content + json.dumps(state))

    def test_unknown_broker_text_and_embedded_error_names_are_never_disclosed_or_inferred(self):
        reply, state, _ = self.through_broker(RuntimeError(
            "sensitive-payload says litellm.AuthenticationError: Error code: 401"))
        self.assertEqual(state["gateway_diagnostic"]["failure_code"], "unclassified_broker_error")
        self.assertIsNone(state["gateway_diagnostic"]["reported_exception_type"])
        self.assertIsNone(state["gateway_diagnostic"]["http_status"])
        self.assertNotIn("sensitive-payload", reply.content + json.dumps(state))

    def test_history_projection_is_separate_and_does_not_count_as_a_model_attempt(self):
        with patch.object(GatewayAdapter, "project_history", side_effect=ValueError("sensitive-history")), \
                patch.object(type(self.context.model_gateway), "generate") as gateway:
            reply, state = self.agent.generate_next_message(
                UserMessage(role="user", content="Hello"), self.agent.get_init_state())
        gateway.assert_not_called()
        self.assertEqual(state["gateway_diagnostic"]["stage"], "history_projection")
        self.assertEqual(state["model_calls"], 0)
        self.assertNotIn("sensitive-history", reply.content + json.dumps(state))
        self.assertIn("conversation history could not be prepared", reply.content)
        self.assertNotIn("model request failed", reply.content)
        self.assertNotIn("limit", reply.content)

    def test_old_diagnostic_state_is_still_valid(self):
        old = {"stage": "gateway_generate", "exception_type": "RuntimeError", "http_status": None}
        diagnostic = GatewayDiagnostic.model_validate(old)
        self.assertIsNone(diagnostic.failure_code)
        self.assertIsNone(diagnostic.origin)

    def test_known_broker_and_constraint_errors_have_fixed_safe_labels(self):
        for message, code in (
            ("Model broker returned a mismatched response", "broker_response_mismatch"),
            ("Model broker call failed", "broker_unspecified_failure"),
            ("Candidate model gateway is not initialized", "model_gateway_uninitialized"),
            ("Model call violates configured constraints: sensitive-value", "model_constraint_violation"),
            ("Model call is ambiguous across 2 allowed configurations for sensitive-model", "model_configuration_ambiguous"),
            ("Model 'sensitive-model' is not allowed; choose one of 'sensitive-choice'", "model_not_allowed"),
            ("Model call does not match any allowed configuration for 'sensitive-model'", "model_configuration_unmatched"),
            ("Model 'sensitive-model' allows sensitive-option in ['sensitive-value']; the call must choose one explicitly", "model_option_choice_required"),
            ("Model 'sensitive-model' returned no token usage; credit accounting cannot continue", "missing_token_usage"),
        ):
            with self.subTest(code=code):
                diagnostic = diagnose_gateway_failure(RuntimeError(message))
                self.assertEqual(diagnostic.failure_code, code)
                self.assertNotIn("sensitive-", diagnostic.model_dump_json())


if __name__ == "__main__":
    unittest.main()
