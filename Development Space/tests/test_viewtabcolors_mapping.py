"""Caption matching regressions; these do not require Revit or its UI."""

import importlib.util
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "tabcolor_mapping_under_test", ROOT / "lib" / "viewtabcolors" / "mapping.py"
)
mapping = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mapping)

PLAN = "#99CC77"
SHEET = "#CC88AA"
VIEW_NAME = "MECHANICAL PIPING ROOF LEVEL"
SHEET_TITLE = "M101 - " + VIEW_NAME


def entry(color, *captions):
    return {"color": color, "captions": list(captions)}


def rules_for(entries, max_pattern_length=16000):
    assignments, unused_conflicts = mapping.build_caption_assignments(entries)
    return mapping.build_filter_rules(
        assignments, re.escape, max_pattern_length=max_pattern_length,
        entries=entries,
    )


def matching_colors(rules, title):
    # Assert every matching color, not just the first: rule ordering must not
    # decide which category wins when a sheet contains a view's name.
    return set(rule["color"] for rule in rules if re.search(rule["pattern"], title))


class TabColorCaptionTests(unittest.TestCase):
    def test_view_keeps_color_when_activation_adds_host_sheet(self):
        views = [entry(PLAN, VIEW_NAME)]
        self.assertEqual({PLAN}, matching_colors(rules_for(views), VIEW_NAME))
        views.append(entry(SHEET, "M101", SHEET_TITLE))
        rules = rules_for(views)
        self.assertEqual({PLAN}, matching_colors(rules, VIEW_NAME))
        self.assertEqual({SHEET}, matching_colors(rules, SHEET_TITLE))

    def test_internal_document_prefix_and_view_modifiers(self):
        rules = rules_for([entry(PLAN, VIEW_NAME), entry(SHEET, SHEET_TITLE)])
        for prefix in ("Project - ", "Project\n"):
            self.assertEqual({PLAN}, matching_colors(rules, prefix + VIEW_NAME + " *"))
            self.assertEqual({SHEET}, matching_colors(rules, prefix + SHEET_TITLE))

    def test_disabled_sheet_does_not_disable_standalone_view(self):
        rules = rules_for([entry(PLAN, VIEW_NAME), entry(None, SHEET_TITLE)])
        self.assertEqual({PLAN}, matching_colors(rules, VIEW_NAME))
        self.assertEqual(set(), matching_colors(rules, SHEET_TITLE))

    def test_ambiguous_sheet_title_stays_uncolored(self):
        rules = rules_for([
            entry(PLAN, VIEW_NAME), entry(SHEET, SHEET_TITLE),
            entry("#FFFFFF", SHEET_TITLE),
        ])
        self.assertEqual({PLAN}, matching_colors(rules, VIEW_NAME))
        self.assertEqual(set(), matching_colors(rules, SHEET_TITLE))

    def test_identical_caption_with_different_colors_stays_uncolored(self):
        for other in (None, SHEET):
            rules = rules_for([entry(PLAN, VIEW_NAME), entry(other, VIEW_NAME)])
            self.assertEqual(set(), matching_colors(rules, VIEW_NAME))

    def test_longer_caption_can_surround_shorter_caption(self):
        for longer in ("Level 1 - Sheet", "Sheet - Level 1", "Sheet - Level 1 - A"):
            rules = rules_for([entry(PLAN, "Level 1"), entry(SHEET, longer)])
            self.assertEqual({PLAN}, matching_colors(rules, "Level 1"))
            self.assertEqual({SHEET}, matching_colors(rules, longer))

    def test_same_color_nested_names_and_word_boundaries(self):
        rules = rules_for([entry(PLAN, "Level 1", "Level 1 - Copy")])
        self.assertEqual({PLAN}, matching_colors(rules, "Level 1"))
        self.assertEqual({PLAN}, matching_colors(rules, "Level 1 - Copy"))
        self.assertEqual(set(), matching_colors(rules, "Level 10"))

    def test_caption_and_competitors_are_literal(self):
        name = "Level [1] (East)+"
        longer = "S.101 - " + name
        rules = rules_for([entry(PLAN, name), entry(SHEET, longer)])
        self.assertEqual({PLAN}, matching_colors(rules, name))
        self.assertEqual({SHEET}, matching_colors(rules, longer))
        self.assertEqual({PLAN}, matching_colors(rules, "Sx101 - " + name))
        self.assertEqual(set(), matching_colors(rules, "Level 1 East"))

    def test_chunking_preserves_guards_for_each_caption(self):
        entries = [entry(PLAN, "Level 1", "Level 2"), entry(SHEET, "S - Level 1")]
        rules = rules_for(entries, max_pattern_length=1)
        self.assertGreater(len(rules), 1)
        for title in ("Level 1", "Level 2"):
            self.assertEqual({PLAN}, matching_colors(rules, title))
        self.assertEqual({SHEET}, matching_colors(rules, "S - Level 1"))

    def test_assignments_only_call_also_protects_nested_captions(self):
        assignments, unused = mapping.build_caption_assignments([
            entry(PLAN, VIEW_NAME), entry(SHEET, SHEET_TITLE)
        ])
        rules = mapping.build_filter_rules(assignments, re.escape)
        self.assertEqual({PLAN}, matching_colors(rules, VIEW_NAME))
        self.assertEqual({SHEET}, matching_colors(rules, SHEET_TITLE))


if __name__ == "__main__":
    unittest.main()
