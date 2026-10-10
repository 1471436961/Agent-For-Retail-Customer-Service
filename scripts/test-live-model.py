"""Opt-in real-model diagnostics. Credentials stay in the local trusted host.

Run from the repository root using its venv. No production config is changed.
Only synthetic API data is sent. Results and the attempt ledger are ignored by Git.
"""

import argparse
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://api.moonshot.cn/v1"
MODEL = "openai/kimi-k2.6"
MAX_OUTPUT = 1024
CONTEXT_WINDOW = 262144
# Official CN pricing, checked 2026-10-10. Treat all input as uncached.
INPUT_RATE = Decimal("6.50")
OUTPUT_RATE = Decimal("27.00")
MAX_CALLS = 16
MAX_CNY = Decimal("10")
RESERVE = (CONTEXT_WINDOW * INPUT_RATE + MAX_OUTPUT * OUTPUT_RATE) / 1_000_000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Authorize real billable requests")
    parser.add_argument("--suite", choices=("identity", "post-fix"), default="post-fix")
    args = parser.parse_args()
    key_path = args.key_file.resolve()
    if key_path.is_relative_to(ROOT.resolve()) or key_path.name.lower() == "enterprise-ai.json":
        parser.error("Use a separate model key file outside the repository")
    if not args.run:
        print(json.dumps({"ready": True, "base_url": BASE, "model": MODEL,
                          "max_calls": MAX_CALLS, "max_cny": str(MAX_CNY),
                          "max_tokens": MAX_OUTPUT, "suite": args.suite, "credentials_read": False}))
        return

    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LOGURU_LEVEL"] = "CRITICAL"
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()  # Do not let SDK logging emit keys in exception text.
    import httpx
    from openai import OpenAI
    from tau2.hyper import agent_context
    from tau2.hyper.agent_context import activate_agent_context, build_agent_context
    from tau2.hyper.client_api import ClientAPI, ClientAPIContext
    from tau2.hyper.sandbox.candidate_server import _brokered_generate, _MODEL_REQUEST_PREFIX
    from tau2.hyper.sandbox.sealed_runner import CandidateProcess
    from tau2.data_model.message import UserMessage, MultiToolMessage, ToolMessage
    from tau2.utils import llm_utils

    # Only this host program reads the key. It is never passed to the Agent.
    secret = key_path.read_text(encoding="utf-8-sig").strip()
    if not secret or any(character.isspace() for character in secret):
        raise SystemExit("The key file must contain one nonempty API key")

    run_dir = ROOT / "evaluation-results" / ("kimi-local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    run_dir.mkdir(parents=True)
    report_path = run_dir / "report.json"
    report = {"model": MODEL, "base_url": BASE, "max_calls": MAX_CALLS,
              "max_cny": str(MAX_CNY), "max_tokens": MAX_OUTPUT,
              "pricing_source": "https://platform.kimi.com/docs/pricing/chat",
              "input_cny_per_million_uncached": str(INPUT_RATE),
              "output_cny_per_million": str(OUTPUT_RATE),
              "network_calls": [], "cases": [], "aborted": False,
              "suite": args.suite,
              "test_scope": "Real model, local synthetic API and history-recovery fixtures; not platform evaluation"}
    charged_upper = Decimal("0")
    active_case = None

    def safe(value):
        if isinstance(value, str):
            value = value.replace(secret, "[REDACTED]")
            value = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", value)
            value = re.sub(r"\b(?:sk|ghp|gho)-[A-Za-z0-9_-]+", "[REDACTED]", value)
            return value
        if isinstance(value, dict):
            return {name: safe(item) for name, item in value.items()
                    if name.lower() not in {"api_key", "authorization", "headers", "reasoning_content"}}
        if isinstance(value, list):
            return [safe(item) for item in value]
        return value

    def save():
        report["estimated_cny_upper"] = str(charged_upper)
        text = json.dumps(safe(report), ensure_ascii=False, indent=2)
        if secret in text:
            raise RuntimeError("Credential detected; diagnostic output blocked")
        report_path.write_text(text, encoding="utf-8")

    class BudgetTransport(httpx.BaseTransport):
        def __init__(self):
            self.delegate = httpx.HTTPTransport(retries=0, proxy=os.environ.get("HTTPS_PROXY"))

        def handle_request(self, request):
            nonlocal charged_upper
            if (request.url.scheme, request.url.host, request.url.path) != (
                    "https", "api.moonshot.cn", "/v1/chat/completions"):
                raise RuntimeError("Unexpected endpoint blocked")
            if report["aborted"]:
                raise RuntimeError("Further calls blocked after an HTTP/transport failure")
            if len(report["network_calls"]) >= MAX_CALLS or charged_upper + RESERVE > MAX_CNY:
                # Record the local cause before the HTTP SDK wraps this exception
                # as a connection/provider error. No request or charge occurs.
                report["budget_stop"] = {
                    "case": active_case,
                    "reason": "call_limit" if len(report["network_calls"]) >= MAX_CALLS else "fee_limit",
                    "calls_sent": len(report["network_calls"]),
                    "estimated_cny_upper": str(charged_upper),
                }
                save()
                raise RuntimeError("Authorized local call/fee budget would be exceeded")
            body = json.loads(request.content)
            if body["model"] != "kimi-k2.6" or body["max_tokens"] != MAX_OUTPUT:
                raise RuntimeError("Unexpected local model/output limit")
            charged_upper += RESERVE  # Reserve before sending, including failed attempts.
            record = {"number": len(report["network_calls"]) + 1, "case": active_case,
                      "request": {name: body.get(name) for name in
                                  ("model", "max_tokens", "thinking", "tool_choice", "messages", "tools")},
                      "reserved_cny": str(RESERVE)}
            report["network_calls"].append(record)
            save()
            try:
                response = self.delegate.handle_request(request)
                response.read()
                record["http_status"] = response.status_code
                try:
                    result = response.json()
                except ValueError:
                    result = {}
                if response.status_code != 200:
                    record["error"] = safe(result.get("error", {"type": "non_json_http_error"}))
                    report["aborted"] = True
                else:
                    usage = result.get("usage") or {}
                    record["usage"] = usage
                    record["response"] = [
                        {"finish_reason": choice.get("finish_reason"),
                         "content": choice.get("message", {}).get("content"),
                         "tool_calls": choice.get("message", {}).get("tool_calls"),
                         "has_reasoning": bool(choice.get("message", {}).get("reasoning_content"))}
                        for choice in result.get("choices", [])]
                    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0:
                        amount = (prompt * INPUT_RATE + completion * OUTPUT_RATE) / 1_000_000
                        charged_upper += amount - RESERVE
                        record["estimated_cny_upper"] = str(amount)
                save()
                return response
            except Exception as error:
                report["aborted"] = True
                record["transport_error_type"] = type(error).__name__
                save()
                raise

        def close(self):
            self.delegate.close()

    http_client = httpx.Client(transport=BudgetTransport(), timeout=60, follow_redirects=False)
    client = OpenAI(api_key=secret, base_url=BASE, max_retries=0, http_client=http_client)
    original_completion = llm_utils.completion

    def actual_completion(**kwargs):
        # Passthrough observer, not a fake completion. The real LiteLLM and
        # OpenAI clients perform every generation; the transport counts attempts.
        kwargs["client"] = client
        return original_completion(**kwargs)

    snapshot_roots = {}

    def factory(revision):
        if revision == "workspace":
            source_root = ROOT / "agent"
        else:
            if revision not in snapshot_roots:
                directory = Path(tempfile.mkdtemp(prefix=revision + "-", dir=run_dir))
                archive = directory / "agent.zip"
                subprocess.run(["git", "archive", "--format=zip", "--output=" + str(archive), revision, "agent"],
                               cwd=ROOT, check=True, capture_output=True)
                with zipfile.ZipFile(archive) as files:
                    for entry in files.infolist():
                        if not entry.filename.startswith("agent/") or not (directory / entry.filename).resolve().is_relative_to(directory.resolve()):
                            raise RuntimeError("Unsafe historical archive")
                    files.extractall(directory)
                snapshot_roots[revision] = directory / "agent"
            source_root = snapshot_roots[revision]
        for name in list(sys.modules):
            if name in {"agent", "tools"} or name == "support_agent" or name.startswith("support_agent."):
                del sys.modules[name]
        sys.path.insert(0, str(source_root))
        sys.path.insert(1, str(ROOT / "tests"))
        from agent import create_agent
        from tools import Tools
        from fixtures.p2_backend import SyntheticBackend
        backend = SyntheticBackend()
        kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id=active_case)))
        configs = [{"model": MODEL, "constraints": {"max_tokens": MAX_OUTPUT}}]
        context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
                                      resource_root=source_root, model_configs=configs)
        with activate_agent_context(context):
            agent = create_agent()
        return agent, kit, backend

    def run_case(name, revision, messages, thinking, replay=None, read_failure=False):
        nonlocal active_case
        active_case = name
        agent, kit, backend = factory(revision)
        if read_failure:
            backend.overrides[("GET", "/v1/customers/p0_customer_a")] = backend.response(
                {"error": {"code": "business_rule_violation", "message": "Synthetic service failure."}}, 503)
        host_constraints = {"max_tokens": MAX_OUTPUT, "api_base": BASE,
                            "num_retries": 0, "timeout": 60}
        if thinking is not None:
            host_constraints["extra_body"] = {"thinking": {"type": thinking}}
        host = SimpleNamespace(model_gateway=build_agent_context(domain="retail_plus", tools=[],
            resource_root=ROOT / "agent", model_configs=[{"model": MODEL, "constraints": host_constraints}]).model_gateway)
        row = {"name": name, "revision": revision, "thinking": thinking or "provider_default",
               "transcript": [], "host_errors": [], "completed": False, "history_replay": replay}
        report["cases"].append(row)
        output = io.StringIO()

        class BrokerInput:
            def readline(inner):
                line = [line for line in output.getvalue().splitlines()
                        if line.startswith(_MODEL_REQUEST_PREFIX)][-1]
                request = json.loads(line[len(_MODEL_REQUEST_PREFIX):])
                if any(name in request["kwargs"] for name in ("api_key", "api_base")):
                    raise RuntimeError("Credentials/endpoint unexpectedly present in candidate request")
                with patch.object(agent_context, "generate", llm_utils.generate):
                    response = CandidateProcess._model_response(host, request)
                if not response["ok"]:
                    row["host_errors"].append(safe(response["error"]))
                return json.dumps(response) + "\n"

        state = agent.get_init_state()
        try:
            with patch.object(agent_context, "generate", _brokered_generate), \
                    patch.object(llm_utils, "completion", actual_completion), \
                    patch.object(llm_utils, "EMPTY_COMPLETION_MAX_ATTEMPTS", 1), \
                    patch("sys.stdout", output), patch("sys.stdin", BrokerInput()):
                for text in messages:
                    row["transcript"].append({"role": "user", "content": text})
                    message = UserMessage(role="user", content=text)
                    while True:
                        reply, state = agent.generate_next_message(message, state)
                        row["transcript"].append({"role": "assistant", "content": reply.content,
                            "tool_calls": [call.model_dump(mode="json") for call in (reply.tool_calls or [])]})
                        if not reply.tool_calls:
                            break
                        results = []
                        for call in reply.tool_calls:
                            try:
                                value = kit.get_tools()[call.name](**call.arguments)
                                result = ToolMessage(role="tool", id=call.id, content=json.dumps(value), error=False)
                            except Exception as error:
                                result = ToolMessage(role="tool", id=call.id, content=safe(str(error)), error=True)
                            row["transcript"].append({"role": "tool", "id": result.id,
                                                     "content": result.content, "error": result.error})
                            results.append(result)
                        message = MultiToolMessage(role="tool", tool_messages=results)
                        if replay and not row.get("restoration"):
                            # Calls and results above are real. Add a synthetic
                            # narration only when the provider supplied none, to
                            # exercise the legitimate mixed historical shape.
                            history = json.loads(json.dumps(state["history"]))
                            narration = report["network_calls"][-1]["response"][0].get("content")
                            added_narration = not bool(narration and narration.strip())
                            history[-1]["content"] = narration if not added_narration else (
                                "I will use the requested tool and wait for its actual result.")
                            if replay == "completed":
                                history.append(message.model_dump(mode="json"))
                            with activate_agent_context(agent.context):
                                agent = sys.modules["agent"].create_agent()
                            state = agent.get_init_state(history)
                            row["restoration"] = {"kind": replay,
                                "synthetic_narration_added": added_narration,
                                "call_ids": [call.id for call in reply.tool_calls],
                                "pending_call_ids_after_restore": state["pending_call_ids"],
                                "history": history}
                            if replay == "completed":
                                message = UserMessage(role="user", content=(
                                    "Please answer my original email question using the tool result already obtained."))
                                row["transcript"].append({"role": "user", "content": message.content})
                    if state.get("halt_reason") or report["aborted"]:
                        break
            row["halt_reason"] = state.get("halt_reason")
            row["completed"] = not state.get("halt_reason") and not report["aborted"]
        except Exception as error:
            row["driver_error"] = {"type": type(error).__name__, "message": safe(str(error))[:2000]}
        row["api_calls"] = safe(backend.calls)
        save()
        print(json.dumps({"case": name, "completed": row["completed"], "halt_reason": row.get("halt_reason"),
                          "host_errors": row["host_errors"], "api_paths": [item["path"] for item in backend.calls],
                          "calls_used": len(report["network_calls"]), "estimated_cny_upper": str(charged_upper)}, ensure_ascii=False))
        return row

    query = "Please look up customer p0_customer_a and tell me their email address. My email is alex.p0@example.test."
    try:
        cases = [
            ("current_default_thinking", "workspace", [query], None),
            ("current_non_thinking", "workspace", [query], "disabled"),
            ("name_and_postal", "workspace", ["Could you check the email on my account? You can identify me as Alex Li, with billing postal code 10001. I don't have my customer number handy."], "disabled"),
            ("claimed_id_mismatch", "workspace", ["Look up p0_customer_b and tell me the email. My email address is alex.p0@example.test."], "disabled"),
            ("multi_turn_identity", "workspace", ["你好", "请告诉我账户 p0_customer_a 登记的邮箱。", "账户验证可以使用 alex.p0@example.test。"], "disabled"),
            ("p1_non_thinking", "dc4dd74", [query], "disabled"),
        ]
        if args.suite == "post-fix":
            cases = [
                ("restored_pending_narrated_call", "workspace", [query], "disabled", "pending"),
                ("restored_completed_narrated_call", "workspace", [query], "disabled", "completed"),
                ("current_default_thinking", "workspace", [query], None),
                ("name_and_postal", "workspace", ["Could you check the email on my account? You can identify me as Alex Li, with billing postal code 10001. I don't have my customer number handy."], "disabled"),
                ("claimed_id_mismatch", "workspace", ["Look up p0_customer_b and tell me the email. My email address is alex.p0@example.test."], "disabled"),
                ("multi_turn_identity", "workspace", ["你好", "请告诉我账户 p0_customer_a 登记的邮箱。", "账户验证可以使用 alex.p0@example.test。"], "disabled"),
                ("customer_read_failure", "workspace", [query], "disabled", None, True),
            ]
        for case in cases:
            if report["aborted"] or len(report["network_calls"]) >= MAX_CALLS or charged_upper + RESERVE > MAX_CNY:
                break
            run_case(*case)
    finally:
        save()
        client.close()
    print(json.dumps({"report": str(report_path), "calls": len(report["network_calls"]),
                      "estimated_cny_upper": str(charged_upper), "aborted": report["aborted"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
