# -*- coding: utf-8 -*-
"""ASCII-only JSON for both runtimes.

The standard encoder IronPython ships cannot write a string holding a
character above \\x7f - it tries to decode a unicode string as UTF-8 and
fails with "'unknown' codec can't decode byte 0xe9".  A room called "Café"
found that in Revit.  These tests pin that ``json_text`` never emits such a
character, reads back exactly, and otherwise formats like the standard
library so files it writes look like files it did not.
"""

import importlib.util
import json
import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib" / "easybim"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, str(LIB_DIR / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


json_text = _load("json_text")


SAMPLE = {"b": [1, {"c": None, "a": True}], "a": u"q", "d": 1.5, "k": [], "e": {}, "n": -3,
          "f": False, "t": (1, 2)}


class EscapingTests(unittest.TestCase):
    def test_an_accented_name_is_escaped_never_passed_through(self):
        out = json_text.dumps({"room_name": u"Café"})
        self.assertTrue(all(ord(char) < 128 for char in out), out)
        self.assertIn(u"\\u00e9", out)
        self.assertEqual(json.loads(out)["room_name"], u"Café")

    def test_cjk_and_astral_characters_round_trip(self):
        value = {"name": u"会議室 \U0001F600", "note": u"\u2022 dot"}
        out = json_text.dumps(value)
        self.assertTrue(all(ord(char) < 128 for char in out), out)
        self.assertEqual(json.loads(out), value)
        # The astral character leaves as the surrogate pair JSON expects.
        self.assertIn(u"\\ud83d\\ude00", out)

    def test_quotes_backslashes_and_control_characters_are_escaped(self):
        value = {"s": u'say "hi"\\now\n\t\r\b\f\x01'}
        out = json_text.dumps(value)
        self.assertEqual(json.loads(out), value)
        self.assertNotIn(u"\n", out)


class ParityTests(unittest.TestCase):
    """For ASCII input the text is byte-for-byte what json.dumps writes."""

    def test_compact_default_indent_and_separators_match_the_standard_library(self):
        for kwargs in ({}, {"indent": 2}, {"indent": 1}, {"separators": (",", ":")},
                       {"indent": 4, "separators": (",", ": ")}):
            self.assertEqual(json_text.dumps(SAMPLE, **kwargs),
                             json.dumps(SAMPLE, sort_keys=True, **kwargs), kwargs)

    def test_unsorted_keys_keep_insertion_order(self):
        value = {"z": 1, "a": 2}
        self.assertEqual(json_text.dumps(value, sort_keys=False), json.dumps(value))

    def test_numbers_booleans_and_none(self):
        self.assertEqual(json_text.dumps([0, -1, 2.5, 1e-07, True, False, None]),
                         json.dumps([0, -1, 2.5, 1e-07, True, False, None]))

    def test_non_string_keys_are_written_the_way_the_standard_library_writes_them(self):
        value = {1: "a", 2.5: "b", True: "c", None: "d"}
        self.assertEqual(json.loads(json_text.dumps(value)), json.loads(json.dumps(value)))


class RefusalTests(unittest.TestCase):
    def test_nan_is_refused_rather_than_written(self):
        with self.assertRaises(ValueError):
            json_text.dumps({"x": float("nan")})

    def test_an_object_that_is_not_json_is_a_type_error(self):
        with self.assertRaises(TypeError):
            json_text.dumps({"x": object()})

    def test_loads_is_the_standard_decoder(self):
        self.assertEqual(json_text.loads(u'{"a": "Caf\\u00e9"}'), {"a": u"Café"})


if __name__ == "__main__":
    unittest.main()
