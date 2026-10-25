"""Measure actual SDK model inputs offline; never call a paid provider.

UTF-8 bytes are exact. cl100k counts, when cached locally, are a comparison
proxy, NOT Kimi billing tokens. Includes skill selection/loading in the trace.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LOGURU_LEVEL"] = "CRITICAL"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_task_completion as completion
from tau2.data_model.message import UserMessage


def measure():
    try:
        import tiktoken
        with patch("urllib.request.urlopen", side_effect=RuntimeError("offline only")), \
             patch("requests.get", side_effect=RuntimeError("offline only")):
            encoding = tiktoken.get_encoding("cl100k_base")
    except (ImportError, OSError, RuntimeError, ValueError):
        encoding = None
    samples = {}
    skill_names = {"order_address": ["address-changes"], "return": ["delivered-returns"],
                   "exchange": ["delivered-exchanges"]}
    for kind in skill_names:
        t = completion.TaskCompletionTests()
        try:
            t.build(kind, compatibility=False)
            def capture(label):
                kw = t.model.call_args.kwargs
                payload = {"messages": [m.model_dump(mode="json", exclude_none=True) for m in kw["messages"]],
                           "tools": [a.openai_schema for a in kw["tools"]]}
                # Exclude SDK timestamps; they are irrelevant variable metadata.
                for row in payload["messages"]:
                    row.pop("timestamp", None)
                parts = {"messages": payload["messages"], "tools": payload["tools"]}
                row = {key + "_bytes": len(json.dumps(value, ensure_ascii=False).encode()) for key,value in parts.items()}
                row["request_bytes"] = len(json.dumps(payload, ensure_ascii=False).encode())
                row["system_bytes"] = sum(len(m["content"].encode()) for m in payload["messages"] if m["role"] == "system")
                row["tool_count"] = len(payload["tools"])
                row["raw_tool_result_bytes_so_far"] = sum(
                    len((m.get("content") or "").encode()) for m in t.state["history"] if m["role"] == "tool")
                if encoding:
                    row["cl100k_proxy_tokens"] = len(encoding.encode(json.dumps(payload, ensure_ascii=False)))
                samples[kind + "/" + label] = row
            args = {"email":t.case["email"], "claimed_customer_id":None, "order_id":t.case["order_id"]}
            if "skill_names" in t.agent.gateway.actions_by_name["verify_customer"].params.model_fields:
                args["skill_names"] = skill_names[kind]
            call = t.decision(UserMessage(role="user", content="Help with my requested " + kind + "."), "verify_customer", args)
            capture("intake")
            verified = t.result(call)
            if kind == "exchange":
                quote_call = t.decision(verified, "prepare_change", {"kind":kind,"parameters":t.params})
                capture("verified")
                quoted = t.result(quote_call)
                t.decision(quoted, text="The exact proposal is " + json.dumps(t.params) + ". Do you approve?")
                capture("quote")
            else:
                t.decision(verified, text="The exact proposal uses these actual facts: " +
                    json.dumps(t.params) + ". Do you approve?")
                capture("verified")
            t.decision(UserMessage(role="user", content="Yes, submit that exact change."), "submit_change", {"kind":kind,"parameters":t.params})
            capture("approval")
        finally:
            t.doCleanups()
    return {"paid_provider_called":False, "sampled_decisions":len(samples),
            "scope":"Three controlled workflows through the write decision. Current address/return paths omit optional quotes. Raw tool content is measured separately; no judge or user model is called.",
            "token_measure":"cl100k proxy; not Kimi billing" if encoding else "bytes only; no local tokenizer cache",
            "samples":samples, "total_request_bytes":sum(s["request_bytes"] for s in samples.values()),
            **({"total_cl100k_proxy_tokens":sum(s["cl100k_proxy_tokens"] for s in samples.values())} if encoding else {})}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = measure()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
