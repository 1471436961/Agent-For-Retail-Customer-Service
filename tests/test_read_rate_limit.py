"""Read-only Retry-After handling; writes and auth/server failures are not replayed."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_reference_runtime import reference_kit, SEED
from test_async_operations import Clock
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.defects import DefectProfile, RateLimitDefect
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from tools import Tools
from support_agent.adapters.read_api import read_products
from support_agent.domain.identity import ReadError


class ReadRateLimitTests(unittest.TestCase):
    def test_real_sdk_rate_limited_search_waits_then_verifies_before_details(self):
        _, original, _ = reference_kit()
        clock = Clock()
        profile = DefectProfile(manifest_id="synthetic-read-rate", manifest_sha256="1" * 64,
            manifest_version=1, domain="retail_plus", defects=(RateLimitDefect(
                id="synthetic-search-limit", operation_id="searchCustomers", trigger_call_ordinal=1,
                min_delay_seconds=2.5, max_delay_seconds=2.5, retry_after_seconds=2),))
        runtime = ClientAPIRuntime(original.environment, defect_profile=profile,
                                  monotonic_clock=clock)
        requests, responses = [], []

        def transport(request):
            requests.append(deepcopy(request))
            response = runtime.dispatch(request)
            responses.append(response["status_code"])
            return response

        kit = Tools(ClientAPI(transport, context=runtime.context))
        case = SEED["cases"][0]
        with patch("support_agent.adapters.read_api.time.sleep", clock.sleep):
            verified = kit.verify_customer(email=case["email"], claimed_customer_id=case["customer_id"])
        self.assertEqual(verified["customer_id"], case["customer_id"])
        self.assertEqual(responses, [429, 429, 200, 200])
        self.assertEqual(clock.delays, [2, 2])
        self.assertTrue(all(row["path"] == "/v1/customers/search" for row in requests[:-1]))
        self.assertEqual(requests[-1]["path"], "/v1/customers/" + case["customer_id"])

    def test_auth_server_errors_and_missing_retry_after_are_not_retried(self):
        for status, headers in ((401, {}), (403, {}), (503, {"Retry-After": "1"}),
                                (429, {}), (429, {"Retry-After": "0"}),
                                (429, {"Retry-After": "9999"})):
            with self.subTest(status=status, headers=headers):
                requests = []

                def transport(request):
                    requests.append(request)
                    return {"status_code": status, "body": {}, "headers": headers}

                with patch("support_agent.adapters.read_api.time.sleep") as sleep:
                    with self.assertRaises(ReadError):
                        read_products(ClientAPI(transport))
                self.assertEqual(len(requests), 1)
                sleep.assert_not_called()

    def test_persistent_read_limit_is_bounded_and_never_returns_a_fake_empty_catalog(self):
        requests = []

        def transport(request):
            requests.append(request)
            return {"status_code": 429, "body": {}, "headers": {"Retry-After": "10"}}

        clock = Clock()
        with patch("support_agent.adapters.read_api.time.sleep", clock.sleep):
            with self.assertRaises(ReadError) as caught:
                read_products(ClientAPI(transport))
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(clock.delays, [10, 10])
        self.assertEqual(len(requests), 3)
