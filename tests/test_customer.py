"""Offline API adapter tests; run with the repository virtual environment."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from support_agent.adapters.read_api import read_customer
from support_agent.state import initial_state
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class CustomerTests(unittest.TestCase):
    def test_verification_precedes_details_and_rejects_mismatch(self):
        backend = SyntheticBackend()
        kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="offline_customer")))
        self.assertEqual(kit.lookup_customer("p0_customer_a", "alex.p0@example.test"), backend.records["customers"][0])
        self.assertEqual([(row["method"], row["path"]) for row in backend.calls],
                         [("POST", "/v1/customers/search"), ("GET", "/v1/customers/p0_customer_a")])
        backend.calls.clear()
        with self.assertRaisesRegex(ValueError, "identity_verification_failed"):
            kit.lookup_customer("p0_customer_b", "alex.p0@example.test")
        self.assertFalse(backend.calls)
        with self.assertRaisesRegex(ValueError, "incomplete_identity_selector"):
            kit.lookup_customer("p0_customer_a", "")
        self.assertFalse(backend.calls)

    def test_no_shared_conversation_state(self):
        first, second = initial_state(), initial_state()
        first.history.append({"role": "user", "content": "First session"})
        self.assertEqual(second.history, [])

    def test_transport_encodes_identifiers_and_checks_status(self):
        customer = SyntheticBackend().records["customers"][0]
        customer["customer_id"] = "a/b?x"
        response = SimpleNamespace(status_code=200, body=customer)
        api = Mock()
        api.request.return_value = response
        self.assertEqual(read_customer(api, "a/b?x"), response.body)
        api.request.assert_called_once_with("GET", "/v1/customers/a%2Fb%3Fx")

    def test_transport_propagates_errors_without_retrying_writes(self):
        api = Mock()
        api.request.return_value = SimpleNamespace(status_code=404, body={"error":{"code":"resource_not_found"}})
        with self.assertRaisesRegex(ValueError, "HTTP=404"):
            read_customer(api, "customer_missing")
        self.assertEqual(api.request.call_count, 1)
        with self.assertRaises(ValueError):
            read_customer(api, "")


if __name__ == "__main__":
    unittest.main()
