"""Offline tests: python3 -m unittest discover -s tests -v (no pip install)."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from support_agent.adapters.customer_api import read_customer, verify_and_read_customer
from support_agent.domain.customer import customer_reply, find_customer_id
from support_agent.state import initial_state


class CustomerTests(unittest.TestCase):
    def test_verification_precedes_details_and_rejects_mismatch(self):
        api = Mock()
        search = SimpleNamespace(body={"customer_id": "customer_a"}, raise_for_status=Mock())
        details = SimpleNamespace(body={"email": "a@example.test"}, raise_for_status=Mock())
        api.request.side_effect = [search, details]
        self.assertEqual(verify_and_read_customer(api, "customer_a", "a@example.test"), details.body)
        self.assertEqual([call.args[:2] for call in api.request.call_args_list], [("POST", "/v1/customers/search"), ("GET", "/v1/customers/customer_a")])
        api.reset_mock(side_effect=True)
        api.request.return_value = search
        with self.assertRaisesRegex(ValueError, "verification failed"):
            verify_and_read_customer(api, "customer_b", "a@example.test")
        self.assertEqual(api.request.call_count, 1)
        api.reset_mock()
        with self.assertRaisesRegex(ValueError, "Provide your email"):
            verify_and_read_customer(api, "customer_a", "")
        api.request.assert_not_called()

    def test_airline_and_retail_identifiers(self):
        for value in (
            "developer_traveler_9001",
            "developer_pending_9001",
            "developer_delivered_9001",
            "customer_example",
        ):
            self.assertEqual(find_customer_id("Please find " + value), value)
        self.assertIsNone(find_customer_id("Please cancel my booking"))

    def test_success_and_invalid_tool_responses(self):
        self.assertIn(
            "sample@example.test", customer_reply('{"email":"sample@example.test"}')
        )
        for content in ("not-json", "null", "[]"):
            self.assertIn("invalid response", customer_reply(content))
        self.assertIn("no email", customer_reply("{}"))
        self.assertIn("could not find", customer_reply("{}", error=True))

    def test_no_shared_conversation_state(self):
        first, second = initial_state(), initial_state()
        first["customer_id"] = "customer_example"
        self.assertIsNone(second["customer_id"])

    def test_transport_encodes_identifiers_and_checks_status(self):
        response = SimpleNamespace(
            body={"email": "sample@example.test"}, raise_for_status=Mock()
        )
        api = Mock()
        api.request.return_value = response
        self.assertEqual(read_customer(api, "a/b?x"), response.body)
        api.request.assert_called_once_with("GET", "/v1/customers/a%2Fb%3Fx")
        response.raise_for_status.assert_called_once()

    def test_transport_propagates_errors_without_retrying_writes(self):
        api = Mock()
        api.request.return_value.raise_for_status.side_effect = ValueError("not found")
        with self.assertRaisesRegex(ValueError, "not found"):
            read_customer(api, "customer_missing")
        self.assertEqual(api.request.call_count, 1)
        with self.assertRaises(ValueError):
            read_customer(api, "")


if __name__ == "__main__":
    unittest.main()
