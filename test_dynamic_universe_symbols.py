import unittest

from dynamic_universe_symbols import normalize_symbol


class DynamicUniverseSymbolTests(unittest.TestCase):
    def test_numeric_and_alphanumeric_krx_symbols(self):
        self.assertEqual(normalize_symbol("005930"), "005930")
        self.assertEqual(normalize_symbol("0197X0"), "0197X0")

    def test_ambiguous_values_are_rejected(self):
        for value in (" 005930", "005930 ", "0197x0", "12345", "1234567", "ABCD12", "not-a-code", None):
            self.assertIsNone(normalize_symbol(value))
