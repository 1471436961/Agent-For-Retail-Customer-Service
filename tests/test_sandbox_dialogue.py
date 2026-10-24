"""Actual tau2 model broker and HTTP serialization against a loopback fixture.

No model provider or real credential is used. Responses are synthetic; this
checks integration, not language comprehension. Submission code is unchanged.
"""

import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from tau2.hyper import agent_context
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext
from tau2.hyper.sandbox.candidate_server import _brokered_generate, _MODEL_REQUEST_PREFIX
from tau2.hyper.sandbox.sealed_runner import CandidateProcess
from tau2.data_model.message import MultiToolMessage, ToolMessage, UserMessage
from tau2.utils import llm_utils


class SandboxDialogueTests(unittest.TestCase):
    def test_greeting_then_verified_lookup_through_broker_and_actual_http(self):
        backend = SyntheticBackend()
        kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="loopback_dialogue")))
        models = json.loads((ROOT / "materials/framework/deployment_manifest.json").read_text())[
            "allowed_agent_models"]
        context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=models)
        with activate_agent_context(context):
            agent = create_agent()

        http_requests = []
        canned = [
            {"role": "assistant", "content": "你好，请提供账户邮箱，或完整姓名和邮编。"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "loopback-verify", "type": "function", "function": {
                    "name": "verify_customer", "arguments": json.dumps({
                        "email": "alex.p0@example.test", "claimed_customer_id": "p0_customer_a"})}}]},
            {"role": "assistant", "content": "email: alex.p0@example.test ###STOP###"},
        ]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                index = len(http_requests)
                http_requests.append({"path": self.path, "body": payload})
                if index >= len(canned):
                    self.send_error(500)
                    return
                result = {"id": f"loopback-{index}", "object": "chat.completion", "created": 1,
                    "model": "enterprise-haiku", "choices": [{"index": 0,
                    "finish_reason": "tool_calls" if index == 1 else "stop", "message": canned[index]}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            # Only the test's trusted host supplies loopback transport settings.
            # These are not credentials or options added to the submitted Agent.
            host_models = [{**row, "constraints": {**row["constraints"],
                "api_base": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key": "loopback-fixture-only", "num_retries": 0}} for row in models]
            host = SimpleNamespace(model_gateway=build_agent_context(domain="retail_plus", tools=[],
                resource_root=ROOT / "agent", model_configs=host_models).model_gateway)
            output = io.StringIO()
            broker_requests = []

            class BrokerInput:
                def readline(self):
                    lines = [line for line in output.getvalue().splitlines()
                             if line.startswith(_MODEL_REQUEST_PREFIX)]
                    request = json.loads(lines[-1][len(_MODEL_REQUEST_PREFIX):])
                    broker_requests.append(request)
                    with patch.object(agent_context, "generate", llm_utils.generate):
                        result = CandidateProcess._model_response(host, request)
                    return json.dumps(result) + "\n"

            with patch.object(agent_context, "generate", _brokered_generate), \
                    patch("sys.stdout", output), patch("sys.stdin", BrokerInput()):
                reply, state = agent.generate_next_message(UserMessage(role="user", content="你好"),
                                                          agent.get_init_state())
                self.assertIsNone(state["halt_reason"])
                self.assertIn("邮箱", reply.content)
                self.assertEqual(backend.calls, [])
                text = "帮我查账户 p0_customer_a，邮箱 alex.p0@example.test。"
                reply, state = agent.generate_next_message(UserMessage(role="user", content=text), state)
                self.assertIsNone(state["halt_reason"])
                self.assertEqual(backend.calls, [])
                call = reply.tool_calls[0]
                facts = kit.get_tools()[call.name](**call.arguments)
                reply, state = agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
                    ToolMessage(role="tool", id=call.id, content=json.dumps(facts))]), state)
                self.assertIsNone(state["halt_reason"])
                self.assertIn("alex.p0@example.test", reply.content)
                self.assertTrue(agent.is_stop(reply))
                self.assertNotIn("###STOP###", reply.content)

            self.assertEqual(len(http_requests), 3)
            self.assertEqual(len(broker_requests), 3)
            self.assertTrue(all(r["path"] == "/v1/chat/completions" for r in http_requests))
            self.assertEqual([row["path"] for row in backend.calls],
                             ["/v1/customers/search", "/v1/customers/p0_customer_a"])
            self.assertEqual(http_requests[1]["body"]["messages"][-1]["content"], text)
            result = http_requests[2]["body"]["messages"][-1]
            self.assertEqual(result["tool_call_id"], "loopback-verify")
            self.assertEqual(json.loads(result["content"])["result"]["email"], "alex.p0@example.test")
            self.assertEqual(state["model_calls"], 3)
            for request in broker_requests:
                self.assertNotIn("api_key", request["kwargs"])
                self.assertNotIn("api_base", request["kwargs"])
                self.assertEqual(request["kwargs"]["max_tokens"], 1024)
            self.assertNotIn("loopback-fixture-only", json.dumps(state))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
