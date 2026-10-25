"""General arithmetic and actual SDK wire schema, without a paid provider."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from support_agent.domain.money import calculate_values
from support_agent.domain.identity import ReadError
from tau2.data_model.message import ToolMessage, UserMessage
import test_gateway_wire as wire


class ArithmeticTests(unittest.TestCase):
    def test_general_operations_and_explicit_precision(self):
        # No currency, item pairs or retail interpretation is required.
        for operation, values, expected in (
            ("add", [3, -2, 7.25], 8.25),
            ("subtract", [100, 15, 20], 65),
            ("multiply", [2.5, 4, -3], -30),
            ("divide", [120, 3, 8], 5),
            ("multiply", [7], 7),
            ("round", [2.675], 2.67),
        ):
            with self.subTest(operation=operation):
                result = calculate_values(operation, values)
                self.assertEqual(result["result"], expected)
                self.assertEqual(result["inputs"], values)
        self.assertEqual(calculate_values("divide", [1, 3])["result"], 1 / 3)
        self.assertEqual(calculate_values("divide", [1, 3], 4)["result"], 0.3333)
        self.assertEqual(calculate_values("round", [1250], -2)["result"], 1200)
        # The model can compose a percentage without a business-specific tool.
        part = calculate_values("multiply", [75, 0.12])["result"]
        self.assertEqual(calculate_values("add", [75, part])["result"], 84)

    def test_invalid_inputs_division_by_zero_and_overflow_are_controlled(self):
        for values in ([], None, [True, 1], [None], ["1.2"], [float("nan")], [float("inf")]):
            with self.subTest(values=values), self.assertRaises(ReadError) as caught:
                calculate_values("add", values)
            self.assertEqual(caught.exception.code, "invalid_calculation_input")
        for precision in (True, "2", 2.5):
            with self.subTest(precision=precision), self.assertRaises(ReadError):
                calculate_values("add", [1, 2], precision)
        for operation, values, code in (
            ("divide", [5, 0], "division_by_zero"),
            ("divide", [1], "unsupported_calculation"),
            ("subtract", [1], "unsupported_calculation"),
            ("round", [1, 2], "unsupported_calculation"),
            ("eval", [1], "unsupported_calculation"),
            ("multiply", [1e308, 1e308], "invalid_calculation_result"),
        ):
            with self.subTest(operation=operation, values=values), self.assertRaises(ReadError) as caught:
                calculate_values(operation, values)
            self.assertEqual(caught.exception.code, code)


class ArithmeticWireTests(unittest.TestCase):
    setUp = wire.GatewayWireTests.setUp

    def test_live_generic_calls_and_legacy_history_use_real_sdk_conversion(self):
        customer = self.backend.records["customers"][0]
        self.provider.return_value = wire.completion(arguments={"email": customer["email"]})
        reply, state = self.agent.generate_next_message(self.user, self.agent.get_init_state())
        call = reply.tool_calls[0]
        actual = self.kit.get_tools()[call.name](**call.arguments)
        incoming = ToolMessage(role="tool", id=call.id, content=json.dumps(actual))
        requests_before = len(self.backend.calls)
        for index, (expression, expected) in enumerate((
            ("12 * 3.5", 42), ("42 / 2", 21),
            ("21 - 5 - 1", 15), ("15 + 2", 17),
        )):
            self.provider.return_value = wire.completion(calls=[{
                "id": f"arithmetic-{index}", "type": "function", "function": {
                    "name": "calculate", "arguments": json.dumps({"expression": expression})}}])
            reply, state = self.agent.generate_next_message(incoming, state)
            self.assertIsNone(state["halt_reason"], state)
            schemas = self.provider.call_args.kwargs["tools"]
            calculator = next(tool["function"] for tool in schemas if tool["function"]["name"] == "calculate")
            self.assertEqual(set(calculator["parameters"]["properties"]), {"expression", "decimal_places"})
            self.assertEqual(calculator["parameters"]["required"], ["expression"])
            call = reply.tool_calls[0]
            actual = self.kit.get_tools()[call.name](**call.arguments)
            self.assertEqual(actual["result"], expected)
            incoming = ToolMessage(role="tool", id=call.id, content=json.dumps(actual))
        self.provider.return_value = wire.completion(content="The result is 17.")
        _, state = self.agent.generate_next_message(incoming, state)
        self.assertEqual(len(self.backend.calls), requests_before)
        # Old legitimate traces remain restorable, but are not live choices.
        history = state["history"]
        old = next(row for row in history if row.get("tool_calls") and row["tool_calls"][0]["name"] == "calculate")
        legacy = old["tool_calls"][0]
        legacy["arguments"] = {"operation": "sum", "values": [12, 30]}
        actual_legacy = self.kit.calculate(**legacy["arguments"])
        historical_result = next(row for row in history if row["role"] == "tool" and row.get("id") == legacy["id"])
        historical_result["content"] = json.dumps(actual_legacy)
        restored = self.agent.get_init_state(history)
        self.assertEqual(restored["seen_call_ids"], state["seen_call_ids"])
        self.provider.return_value = wire.completion(content="How else can I help?")
        _, restored = self.agent.generate_next_message(UserMessage(role="user", content="Continue."), restored)
        self.assertIsNone(restored["halt_reason"])
