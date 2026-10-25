"""Independent arithmetic expectations and bounded expression language."""
import unittest
import test_reference_runtime
from support_agent.domain.money import calculate_expression
from support_agent.domain.identity import ReadError


class ExpressionCalculatorTests(unittest.TestCase):
    def test_full_formulas_percentages_negatives_and_rounding(self):
        for expression, precision, expected in (
            ("(31.4 + 22.9) - (12.5 + 19.2)",2,22.6),
            ("75 * (1 + 12 / 100)",2,84),
            ("120 / (3 * 8)",None,5),
            ("-(-7) + +3",None,10),
            ("round(1250, -2)",None,1200),
            ("round(2.675, 2)",None,2.67),
            ("round(2.5)",None,2),
            ("1 / 3",4,0.3333),
        ):
            with self.subTest(expression=expression):
                value=calculate_expression(expression,precision)
                self.assertEqual(value["result"],expected)
                self.assertEqual(value["expression"],expression)

    def test_no_identifiers_code_containers_or_unbounded_computation(self):
        for expression in ("__import__('os')", "1 .__class__", "[1,2]", "True+1",
                           "2**10000", "sum([1,2])", "round(1, x=2)",
                           "round(1, 0.5)", "1e999", "1e308*1e308", "1+"*1000):
            with self.subTest(expression=expression),self.assertRaises(ReadError):
                calculate_expression(expression)
        with self.assertRaisesRegex(ReadError,"division_by_zero"):
            calculate_expression("4/(2-2)")
        for precision in (True,13,-13,"2"):
            with self.subTest(precision=precision),self.assertRaises(ReadError):
                calculate_expression("1+2",precision)

    def test_tool_keeps_legacy_history_without_mixing_two_interfaces(self):
        kit,_,requests=test_reference_runtime.reference_kit()
        self.assertEqual(kit.calculate(expression="3*(7-2)")["result"],15)
        self.assertEqual(kit.calculate("multiply",[3,5])["result"],15)
        with self.assertRaisesRegex(ReadError,"choose_expression_or_legacy_operands"):
            kit.calculate("add",[1,2],expression="1+2")
        self.assertEqual(requests,[])
