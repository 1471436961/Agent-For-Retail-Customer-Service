"""Real SDK async mutations on synthetic data; no benchmark deployments/tasks.

The generic public runtime supplies 202/Location/Retry-After and terminal
receipts. A fake clock honors those delays without paid models or real waits.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_reference_runtime import (
    reference_kit, apply_native_reference, SEED,
)
import test_reference_runtime as reference
from tools import Tools
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.defects import AsyncCompletionDefect, DefectProfile
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from support_agent.adapters.write_api import OPERATIONS
from support_agent.domain.identity import ReadError


EXECUTORS = {
    "default_address": "execute_default_address", "order_address": "execute_order_address",
    "cancel": "execute_cancellation", "payment": "execute_payment",
    "modify_items": "execute_item_modification", "return": "execute_return",
    "exchange": "execute_exchange",
}


class Clock:
    def __init__(self):
        self.now = 0
        self.delays = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


def async_kit(kind, delay=2.5):
    _, original, _ = reference_kit()
    clock = Clock()
    profile = DefectProfile(manifest_id="synthetic-async-contract", manifest_sha256="0" * 64,
        manifest_version=1, domain="retail_plus", defects=(AsyncCompletionDefect(
            id="synthetic-async-operation", operation_id=OPERATIONS[kind][0],
            status_path="/v1/operation-status/{workflow_id}",
            min_delay_seconds=delay, max_delay_seconds=delay, retry_after_seconds=1),))
    runtime = ClientAPIRuntime(original.environment, conversation_id="synthetic-async",
                               defect_profile=profile, monotonic_clock=clock)
    requests, responses, database_after_responses = [], [], []

    def transport(request):
        requests.append(deepcopy(request))
        response = runtime.dispatch(request)
        responses.append(deepcopy(response))
        database_after_responses.append(runtime.environment.get_db_hash())
        return response

    return Tools(ClientAPI(transport, context=runtime.context)), runtime, requests, responses, database_after_responses, clock


class AsyncOperationTests(unittest.TestCase):
    def setup_change(self, kind, delay=2.5):
        kit, runtime, requests, responses, hashes, clock = async_kit(kind, delay)
        case = SEED["cases"][1 if kind in {"return", "exchange"} else 0]
        kit.verify_customer(email=case["email"])
        params = reference.ReferenceRuntimeTests.change_parameters(kit, kind, case)
        proposal = kit.prepare_change(kind, params)
        return kit, runtime, requests, responses, hashes, clock, case, params, proposal

    def test_all_seven_async_operations_wait_and_match_native_database(self):
        for kind, executor in EXECUTORS.items():
            with self.subTest(kind=kind):
                kit, runtime, requests, responses, hashes, clock, case, params, proposal = self.setup_change(kind)
                before = runtime.environment.get_db_hash()
                with patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
                    outcome = getattr(kit, executor)(proposal["proposal_id"])
                self.assertEqual(outcome["state"], "accepted" if kind in {"return", "exchange"} else "completed", outcome)
                accepted = next(index for index, response in enumerate(responses) if response["status_code"] == 202)
                self.assertEqual(hashes[accepted], before, "202 alone has not performed the mutation")
                self.assertNotEqual(runtime.environment.get_db_hash(), before)
                writes = [row for row in requests if row["method"] != "GET" and row["path"] != "/v1/customers/search"]
                self.assertEqual(len(writes), 1)
                location = responses[accepted]["headers"]["Location"]
                checks = [row for row in requests if row["path"] == location]
                self.assertEqual(len(checks), 3)
                self.assertTrue(all(row["method"] == "GET" and not row.get("body") for row in checks))
                self.assertEqual(clock.delays, [1, 1, 1])
                self.assertEqual(getattr(kit, executor)(proposal["proposal_id"]), outcome)
                self.assertEqual(len([row for row in requests if row["path"] == location]), 3)
                _, native, _ = reference_kit()
                apply_native_reference(native.environment.tools, kind, case["customer_id"], params)
                self.assertEqual(runtime.environment.get_db_hash(), native.environment.get_db_hash())

    def test_pending_job_resumes_by_read_without_approval_or_another_write(self):
        kit, runtime, requests, _, _, clock, _, params, proposal = self.setup_change("return", delay=30)
        before = runtime.environment.get_db_hash()
        with patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
            outcome = kit.execute_return(proposal["proposal_id"])
            self.assertEqual(outcome["state"], "pending")
            self.assertEqual(runtime.environment.get_db_hash(), before)
            self.assertIn("not yet confirmed", outcome["customer_facts"]["notice"])
            self.assertEqual(kit.execute_return(proposal["proposal_id"]), outcome)
            with self.assertRaisesRegex(ReadError, "unresolved_write"):
                kit.prepare_change("return", params)
            resolved = kit.inspect_change_result(proposal["proposal_id"])
        self.assertEqual(resolved["state"], "accepted", resolved)
        self.assertEqual(kit._proposals[proposal["proposal_id"]]["initial_outcome"], outcome)
        self.assertEqual(len([row for row in requests if row["path"].endswith("/returns")]), 1)
        self.assertNotEqual(runtime.environment.get_db_hash(), before)

    def test_terminal_workflow_failure_does_not_claim_success_or_retry(self):
        kit, _, requests, _, _, clock, _, _, proposal = self.setup_change("return")
        transport = kit.client_api._transport

        def fail_status(request):
            if request["path"].startswith("/v1/operation-status/"):
                requests.append(deepcopy(request))
                return {"status_code": 200, "body": {"workflow_id": request["path"].split("/")[-1],
                    "status": "failed", "error": {"code": "resource_conflict", "message": "private-provider-detail"}}}
            return transport(request)

        kit.client_api = ClientAPI(fail_status, context=kit.client_api.context)
        with patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
            outcome = kit.execute_return(proposal["proposal_id"])
        self.assertEqual(outcome["state"], "failed")
        self.assertNotIn("private-provider-detail", str(outcome))
        self.assertEqual(kit.execute_return(proposal["proposal_id"]), outcome)
        self.assertEqual(len([row for row in requests if row["path"].endswith("/returns")]), 1)

    def test_malformed_or_external_202_receipts_do_not_trigger_guessed_status_reads(self):
        examples = [({}, {}), ({"workflow_id": "wf_test", "status": "pending"}, {}),
            ({"workflow_id": "wf_test", "status": "pending"}, {"Location": "https://external.test/wf_test", "Retry-After": "1"}),
            ({"workflow_id": "wf_test", "status": "pending"}, {"Location": "/v1/status/other_id", "Retry-After": "1"}),
            ({"workflow_id": "wf_test", "status": "pending"}, {"Location": "/v1/status/../wf_test", "Retry-After": "1"}),
            ({"workflow_id": "wf_test", "status": "pending"}, {"Location": "/v1/customers/other_customer/jobs/wf_test", "Retry-After": "1"})]
        for body, headers in examples:
            with self.subTest(body=body, headers=headers):
                kit, runtime, requests, _, _, _, _, _, proposal = self.setup_change("return")
                transport = kit.client_api._transport
                before = runtime.environment.get_db_hash()

                def malformed(request):
                    if request["path"].endswith("/returns"):
                        requests.append(deepcopy(request))
                        return {"status_code": 202, "body": body, "headers": headers}
                    return transport(request)

                kit.client_api = ClientAPI(malformed, context=kit.client_api.context)
                outcome = kit.execute_return(proposal["proposal_id"])
                self.assertEqual(outcome["state"], "unknown")
                self.assertEqual(outcome["error"], "invalid_async_write_receipt")
                self.assertEqual(runtime.environment.get_db_hash(), before)
                self.assertFalse(any(row["path"].startswith("/v1/status/") for row in requests))
                self.assertEqual(len([row for row in requests if row["path"].endswith("/returns")]), 1)

    def test_lost_sync_response_can_confirm_matching_record_for_all_seven_kinds(self):
        for kind, executor in EXECUTORS.items():
            with self.subTest(kind=kind):
                kit, runtime, requests = reference_kit()
                case = SEED["cases"][1 if kind in {"return", "exchange"} else 0]
                kit.verify_customer(email=case["email"])
                params = reference.ReferenceRuntimeTests.change_parameters(kit, kind, case)
                proposal = kit.prepare_change(kind, params)
                transport = kit.client_api._transport
                method, template = OPERATIONS[kind][1:3]
                from urllib.parse import quote
                target = case["customer_id"] if kind == "default_address" else case["order_id"]
                path = template.format(quote(target, safe=""))

                def lose_receipt(request):
                    response = transport(request)
                    if (request["method"], request["path"]) == (method, path):
                        return {"status_code": 504, "body": {"error": {"code": "timeout"}}}
                    return response

                kit.client_api = ClientAPI(lose_receipt, context=kit.client_api.context)
                outcome = getattr(kit, executor)(proposal["proposal_id"])
                self.assertEqual(outcome["state"], "unknown")
                inspected = kit.inspect_change_result(proposal["proposal_id"])
                self.assertEqual(inspected["state"], "accepted" if kind in {"return", "exchange"} else "completed", inspected)
                self.assertEqual(inspected["write_outcome"]["verification"], "observed_matching_business_record")
                self.assertEqual(getattr(kit, executor)(proposal["proposal_id"]), inspected["write_outcome"])
                self.assertEqual(len([row for row in requests if (row["method"], row["path"]) == (method, path)]), 1)

    def test_replay_with_fresh_toolkit_and_backend_keeps_requests_and_results(self):
        runs = []
        for _ in range(2):
            kit, runtime, requests, _, _, clock, _, _, proposal = self.setup_change("return")
            with patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
                outcome = kit.execute_return(proposal["proposal_id"])
            runs.append((requests, outcome, runtime.environment.get_db_hash()))
        self.assertEqual(runs[0], runs[1])

    def test_terminal_receipt_still_checks_exact_approved_return_destination(self):
        kit, runtime, requests, _, _, clock, _, _, proposal = self.setup_change("return")
        transport = kit.client_api._transport

        def corrupt_receipt(request):
            response = transport(request)
            if response["body"].get("status") == "succeeded":
                response["body"]["result"]["return_request"]["refund_payment_method_id"] = "unapproved-method"
            return response

        kit.client_api = ClientAPI(corrupt_receipt, context=kit.client_api.context)
        with patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
            outcome = kit.execute_return(proposal["proposal_id"])
        self.assertEqual(outcome["state"], "unknown")
        self.assertEqual(outcome["error"], "invalid_write_receipt")
        # The real SDK database was changed correctly, so a separate owned-record
        # inspection can confirm it without trusting the corrupt receipt.
        inspected = kit.inspect_change_result(proposal["proposal_id"])
        self.assertEqual(inspected["state"], "accepted")
        self.assertEqual(inspected["write_outcome"]["verification"], "observed_matching_business_record")
        self.assertEqual(len([row for row in requests if row["path"].endswith("/returns")]), 1)
