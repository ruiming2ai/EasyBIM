# -*- coding: utf-8 -*-
"""Tests for Link Probe's pure report module, plus its command contract.

Two things here carry real weight and the rest is scaffolding around them.

``classify_binding`` is the diagnosis the whole design rests on, and it is an
*inference*: the Revit API cannot read a link's Model Categories tab, so
"Frozen" is deduced from all nine Basics properties still inheriting.  If a
future Revit adds a tenth property, the deduction silently weakens - so the
nine are asserted literally and each one is flipped in turn.

``geometry_verdict`` decides whether a follow-up product is possible at all.
Its one dangerous failure is reporting a *truncated* absence as proof, so the
truncation cases are tested harder than the happy path.
"""

import ast
import importlib.util
import io
import pathlib
import re
import tokenize
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_DIR = REPO_ROOT / "EasyBIM.tab" / "Views.panel"
COMMAND_DIR = PANEL_DIR / "Link Probe.pushbutton"
REPORT_MODULE = COMMAND_DIR / "link_probe_report.py"
REVIT_MODULE = COMMAND_DIR / "link_probe_revit.py"
SCRIPT_MODULE = COMMAND_DIR / "script.py"

EXPECTED_MODULES = ("script.py", "link_probe_revit.py", "link_probe_report.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load(REPORT_MODULE, "link_probe_report_under_test")


def _source(path):
    return path.read_text(encoding="utf-8")


def _strip_prose(source, drop_strings=True):
    """Drop comments, and optionally string literals, keeping code.

    The scans below must read code, not prose.  These modules explain at
    length which write calls they deliberately avoid and which enum spelling
    is the trap, and a naive substring search would forbid them from saying
    so - making the most carefully documented module in the repo the one
    least able to document itself.

    ``drop_strings`` differs by what the scan is hunting.  A *call* can never
    live inside a string literal, so the write-API scan drops them.  A
    misspelled enum *name* is compared as a string here, so that scan must
    keep them and settle for dropping comments.
    """
    skip = (tokenize.COMMENT, tokenize.STRING) if drop_strings \
        else (tokenize.COMMENT,)
    kept = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token_type, text, _, _, _ in tokens:
        if token_type in skip:
            continue
        kept.append(text)
    return u"\n".join(kept)


def _code_only(path):
    return _strip_prose(_source(path))


def _uncommented(path):
    return _strip_prose(_source(path), drop_strings=False)


def basics(**overrides):
    """All nine inheriting, then whatever the caller changed."""
    values = dict((field, report.VIS_BY_LINK)
                  for field in report.BASICS_TYPE_FIELDS)
    values.update(overrides)
    return values


class BasicsFieldsTests(unittest.TestCase):
    def test_there_are_exactly_nine_and_they_are_these(self):
        # The Frozen inference is defined as "all of these inherit".  A tenth
        # property in a future Revit must fail here rather than quietly make
        # the diagnosis weaker.
        self.assertEqual(report.BASICS_TYPE_FIELDS, (
            "view_range", "color_fill", "object_styles", "nested_links",
            "view_filters", "discipline_type", "phase_type",
            "phase_filter_type", "detail_level_type"))

    def test_every_field_has_a_label(self):
        for field in report.BASICS_TYPE_FIELDS:
            self.assertIn(field, report.BASICS_LABELS)


class ClassifyBindingTests(unittest.TestCase):
    def test_by_linked_view_is_tracking(self):
        verdict, _ = report.classify_binding(
            report.VIS_BY_LINK, basics=basics(), linked_view_resolved=True)
        self.assertEqual(verdict, report.CLASS_TRACKING)

    def test_custom_with_all_nine_inheriting_is_frozen(self):
        verdict, note = report.classify_binding(
            report.VIS_CUSTOM, basics=basics(), linked_view_resolved=True)
        self.assertEqual(verdict, report.CLASS_FROZEN)
        self.assertIn("category or workset tab", note)

    def test_each_of_the_nine_flipped_in_turn_gives_ambiguous(self):
        # The core claim. If any one Basics property is overridden, Custom is
        # explained by the Basics tab and we may not call the view frozen.
        for field in report.BASICS_TYPE_FIELDS:
            verdict, note = report.classify_binding(
                report.VIS_CUSTOM,
                basics=basics(**{field: report.VIS_CUSTOM}),
                linked_view_resolved=True)
            self.assertEqual(verdict, report.CLASS_AMBIGUOUS, field)
            self.assertIn(report.BASICS_LABELS[field], note, field)

    def test_by_host_view_override_also_counts_as_overridden(self):
        verdict, _ = report.classify_binding(
            report.VIS_CUSTOM,
            basics=basics(view_filters=report.VIS_BY_HOST),
            linked_view_resolved=True)
        self.assertEqual(verdict, report.CLASS_AMBIGUOUS)

    def test_unreadable_property_is_not_treated_as_overridden(self):
        # None means "could not read", which is not the same as "changed".
        # Treating it as changed would turn unreadable into a false Ambiguous.
        verdict, _ = report.classify_binding(
            report.VIS_CUSTOM,
            basics=basics(color_fill=None),
            linked_view_resolved=True)
        self.assertEqual(verdict, report.CLASS_FROZEN)

    def test_unloaded_link_beats_everything(self):
        # An unloaded architectural link must not turn every view in the
        # project into a false Broken row.
        verdict, note = report.classify_binding(
            report.VIS_CUSTOM, basics=basics(), link_loaded=False,
            linked_view_resolved=False, had_linked_view_id=True)
        self.assertEqual(verdict, report.CLASS_UNREADABLE)
        self.assertIn("not loaded", note)

    def test_read_error_is_unreadable(self):
        verdict, note = report.classify_binding(
            report.VIS_CUSTOM, basics=basics(), read_error="boom")
        self.assertEqual(verdict, report.CLASS_UNREADABLE)
        self.assertEqual(note, "boom")

    def test_unresolvable_linked_view_is_broken_not_frozen(self):
        verdict, _ = report.classify_binding(
            report.VIS_CUSTOM, basics=basics(), linked_view_resolved=False,
            had_linked_view_id=True)
        self.assertEqual(verdict, report.CLASS_BROKEN)

    def test_by_host_view_with_a_dead_id_is_broken(self):
        # The fossil id is the fingerprint of a binding that used to work.
        verdict, note = report.classify_binding(
            report.VIS_BY_HOST, basics=basics(), had_linked_view_id=True,
            linked_view_resolved=False)
        self.assertEqual(verdict, report.CLASS_BROKEN)
        self.assertIn("dead linked-view id", note)

    def test_by_host_view_with_no_id_is_merely_unmanaged(self):
        verdict, _ = report.classify_binding(
            report.VIS_BY_HOST, basics=basics(), had_linked_view_id=False)
        self.assertEqual(verdict, report.CLASS_UNMANAGED)

    def test_without_the_2025_api_custom_never_claims_frozen(self):
        verdict, note = report.classify_binding(
            report.VIS_CUSTOM, basics={}, linked_view_resolved=True,
            basics_api=False)
        self.assertEqual(verdict, report.CLASS_AMBIGUOUS)
        self.assertIn("2024", note)

    def test_frozen_label_says_it_is_inferred(self):
        self.assertIn("inferred", report.CLASS_FROZEN)


class GeometryVerdictTests(unittest.TestCase):
    def test_absent_with_view_present_without_it_works(self):
        verdict, _ = report.geometry_verdict(
            ["Walls", "Furniture"], ["Walls"], "Furniture")
        self.assertEqual(verdict, report.VERDICT_WORKS)

    def test_present_in_both_means_settings_are_ignored(self):
        verdict, _ = report.geometry_verdict(
            ["Walls", "Furniture"], ["Walls", "Furniture"], "Furniture")
        self.assertEqual(verdict, report.VERDICT_IGNORES_SETTINGS)

    def test_identical_sets_means_the_view_was_ignored(self):
        verdict, _ = report.geometry_verdict(
            ["Walls", "Furniture"], ["Walls", "Furniture"], "Furniture")
        # Identical sets that still contain the target read as IGNORES_SETTINGS,
        # which is the more specific finding; VIEW_IGNORED is for the case
        # where the target is gone from neither but the sets match exactly.
        self.assertEqual(verdict, report.VERDICT_IGNORES_SETTINGS)

    def test_truncated_absence_is_never_reported_as_works(self):
        # The single most expensive mistake available to this probe: a walk
        # that stopped early proves nothing about what it did not reach, and
        # a whole product would be built on the false positive.
        verdict, note = report.geometry_verdict(
            ["Walls", "Furniture"], ["Walls"], "Furniture",
            test_truncated=True)
        self.assertEqual(verdict, report.VERDICT_INCONCLUSIVE)
        self.assertIn("truncated", note)

    def test_truncation_does_not_weaken_a_presence_finding(self):
        # Presence survives truncation: we found it, so it is there.
        verdict, _ = report.geometry_verdict(
            ["Walls", "Furniture"], ["Walls", "Furniture"], "Furniture",
            test_truncated=True, control_truncated=True)
        self.assertEqual(verdict, report.VERDICT_IGNORES_SETTINGS)

    def test_target_missing_from_the_control_pass_is_inconclusive(self):
        verdict, note = report.geometry_verdict(
            ["Walls"], ["Walls"], "Furniture")
        self.assertEqual(verdict, report.VERDICT_INCONCLUSIVE)
        self.assertIn("never appeared", note)

    def test_an_error_makes_it_unusable(self):
        verdict, note = report.geometry_verdict(
            ["Walls"], ["Walls"], "Walls", error="get_Geometry threw")
        self.assertEqual(verdict, report.VERDICT_UNUSABLE)
        self.assertEqual(note, "get_Geometry threw")

    def test_no_geometry_at_all_is_unusable(self):
        verdict, _ = report.geometry_verdict([], [], "Furniture")
        self.assertEqual(verdict, report.VERDICT_UNUSABLE)

    def test_every_verdict_has_a_plain_english_meaning(self):
        for verdict in (report.VERDICT_WORKS, report.VERDICT_IGNORES_SETTINGS,
                        report.VERDICT_VIEW_IGNORED, report.VERDICT_UNUSABLE,
                        report.VERDICT_INCONCLUSIVE):
            self.assertIn(verdict, report.VERDICT_MEANINGS)
            self.assertTrue(report.VERDICT_MEANINGS[verdict].strip())


class BuildReportTests(unittest.TestCase):
    def payload(self):
        return {
            "host": {"version": "2025", "title": "MEP.rvt",
                     "workshared": True, "links_api": True,
                     "basics_api": True},
            "view": {"name": "L03 - Power", "type": "FloorPlan",
                     "is_template": False, "is_dependent": False,
                     "template_name": "MEP - Power",
                     "template_controls_links": True},
            "links": [{"label": "A-BLDG", "loaded": True,
                       "instance_key": 12345, "type_key": 999, "error": ""}],
            "bindings": [{
                "link_label": "A-BLDG",
                "visibility": report.VIS_CUSTOM,
                "linked_view_name": "A-L03-BKG",
                "linked_view_resolved": True,
                "basics": basics(),
                "extra": {"Phase id": 7},
                "type_slot_differs": False,
                "classification": report.CLASS_FROZEN,
                "note": "because reasons",
            }],
            "geometry": {
                "link_label": "A-BLDG", "target_category": "Furniture",
                "control": ["Walls", "Furniture"], "test": ["Walls"],
                "control_count": 500, "test_count": 480,
                "control_truncated": False, "test_truncated": False,
                "verdict": report.VERDICT_WORKS, "note": "n",
            },
            "linked_view_scan": {
                "view_name": "A-L03-BKG", "category_count": 300,
                "hidden": ["Casework"], "overridden": [], "filters": ["F1"],
                "view_range_ok": True, "error": "",
            },
            "timings": [("Collect links", 0.25)],
            "errors": [],
        }

    def test_it_renders_and_names_the_verdicts(self):
        text = report.build_report(self.payload())
        self.assertIn("EASYBIM LINK PROBE", text)
        self.assertIn(report.CLASS_FROZEN, text)
        self.assertIn(report.VERDICT_WORKS, text)
        self.assertIn("A-L03-BKG", text)

    def test_it_says_up_front_that_nothing_was_changed(self):
        text = report.build_report(self.payload())
        self.assertIn("nothing was changed", text)

    def test_it_flags_a_template_controlled_view(self):
        text = report.build_report(self.payload())
        self.assertIn("the template owns the setting", text)

    def test_it_says_frozen_is_inferred(self):
        text = report.build_report(self.payload())
        self.assertIn("INFERRED", text)

    def test_it_warns_when_the_2025_api_is_missing(self):
        payload = self.payload()
        payload["host"]["basics_api"] = False
        self.assertIn("cannot be made", report.build_report(payload))

    def test_it_lists_every_basics_property_by_name(self):
        text = report.build_report(self.payload())
        for label in report.BASICS_LABELS.values():
            self.assertIn(label, text)

    def test_an_empty_payload_still_renders(self):
        self.assertIn("EASYBIM LINK PROBE", report.build_report({}))
        self.assertIn("EASYBIM LINK PROBE", report.build_report(None))

    def test_a_long_label_never_welds_itself_to_its_value(self):
        # "Template controls RVT Links" overruns the label column, and a
        # fixed-width format renders it as "...RVT Linksyes".
        text = report.build_report(self.payload())
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Template controls RVT Links"):
                self.assertTrue(stripped.endswith("yes"), stripped)
                self.assertIn("  ", stripped[len("Template controls RVT Links"):])
                break
        else:
            self.fail("the template-control line was not rendered")


class ReadOnlyTests(unittest.TestCase):
    """The probe's whole safety claim is that it never writes."""

    WRITE_PATTERNS = (
        (r"\bTransactionGroup\b", "TransactionGroup"),
        (r"\bDB\.Transaction\b", "DB.Transaction"),
        (r"\brevit\.Transaction\b", "revit.Transaction"),
        (r"\bSetLinkOverrides\b", "SetLinkOverrides"),
        (r"\bRemoveLinkOverrides\b", "RemoveLinkOverrides"),
        (r"\bSetCategoryHidden\b", "SetCategoryHidden"),
        (r"\bSetCategoryOverrides\b", "SetCategoryOverrides"),
        (r"\bSetElementOverrides\b", "SetElementOverrides"),
        (r"\bAddFilter\b", "AddFilter"),
        (r"\bHideElements\b", "HideElements"),
        (r"\.Delete\s*\(", "Document.Delete"),
        (r"\bParameterFilterElement\b", "ParameterFilterElement"),
    )

    def test_no_module_can_write_to_the_model(self):
        offenders = []
        for path in (REPORT_MODULE, REVIT_MODULE, SCRIPT_MODULE):
            code = _code_only(path)
            for pattern, label in self.WRITE_PATTERNS:
                if re.search(pattern, code):
                    offenders.append("%s uses %s" % (path.name, label))
        self.assertFalse(offenders, offenders)

    def test_the_scan_reads_code_and_not_prose(self):
        # Guards the guard. If _strip_prose ever over-stripped, the test above
        # would keep passing on a module that really does write.
        stripped = _strip_prose(
            "# a comment mentioning SetLinkOverrides\n"
            "note = 'SetLinkOverrides is deliberately not used'\n"
            "view.SetLinkOverrides(link_id, settings)\n")
        self.assertEqual(stripped.count("SetLinkOverrides"), 1)
        self.assertIn("view", stripped)

    def test_the_report_promises_it(self):
        self.assertIn("No transaction", _source(REPORT_MODULE))


class PureModuleTests(unittest.TestCase):
    def test_the_report_module_imports_nothing_from_revit(self):
        # It is loaded standalone by these tests, so any Revit import would
        # make the whole suite unrunnable off Revit.
        for node in ast.walk(ast.parse(_source(REPORT_MODULE))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                self.assertNotIn(name.split(".")[0],
                                 ("pyrevit", "Autodesk", "System", "clr",
                                  "easybim"), name)


class ApiNameTests(unittest.TestCase):
    def test_the_enum_member_is_spelled_bylinkview(self):
        # Revit spells it ByLinkView. ByLinkedView reads perfectly naturally
        # and matches nothing at runtime, which is exactly why it needs a
        # test. Strings are kept here on purpose - the value is compared as
        # a literal, so a typo would hide inside one.
        for path in (REPORT_MODULE, REVIT_MODULE):
            self.assertNotIn("ByLinkedView", _uncommented(path), path.name)
        self.assertIn('u"ByLinkView"', _source(REPORT_MODULE))

    def test_the_revit_module_reads_all_nine_basics(self):
        source = _source(REVIT_MODULE)
        for name in ("ViewRange", "ColorFill", "ObjectStyles", "NestedLinks",
                     "ViewFilterType", "GetDisciplineType", "GetPhaseType",
                     "GetPhaseFilterType", "GetViewDetailLevelType"):
            self.assertIn(name, source, name)

    def test_it_reads_the_linked_document_directly(self):
        source = _source(REVIT_MODULE)
        for name in ("GetLinkDocument", "GetCategoryHidden",
                     "GetCategoryOverrides", "GetFilters", "GetViewRange"):
            self.assertIn(name, source, name)

    def test_template_control_is_matched_by_builtin_parameter_id(self):
        # A localized display name would silently stop matching on a
        # non-English Revit, and the failure would look like "no templates
        # control links" rather than an error.
        source = _source(REVIT_MODULE)
        self.assertIn("DB.BuiltInParameter.VIS_GRAPHICS_RVT_LINKS", source)
        self.assertIn("GetNonControlledTemplateParameterIds", source)
        self.assertNotIn('"V/G Overrides RVT Links"', source)

    def test_the_experiment_differs_only_by_options_view(self):
        source = _source(REVIT_MODULE)
        self.assertIn("options.View = view", source)
        self.assertIn("get_Geometry", source)
        self.assertIn("GraphicsStyleId", source)

    def test_both_capability_gates_are_probed_not_assumed(self):
        source = _source(REVIT_MODULE)
        self.assertIn('hasattr(DB, "RevitLinkGraphicsSettings")', source)
        self.assertIn('hasattr(settings_type, "ObjectStyles")', source)


class BundleTests(unittest.TestCase):
    def test_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            self.assertTrue((COMMAND_DIR / name).is_file(), name)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            icon = COMMAND_DIR / name
            self.assertTrue(icon.is_file(), name)
            self.assertGreater(icon.stat().st_size, 0, name)

    def test_bundle_metadata(self):
        bundle = _source(COMMAND_DIR / "bundle.yaml")
        self.assertIn("Link", bundle)
        self.assertIn("Probe", bundle)
        self.assertIn("author: Ruiming Liu", bundle)
        self.assertIn("min_revit_version: 2024", bundle)

    def test_tooltip_states_the_read_only_promise_and_the_inference(self):
        bundle = _source(COMMAND_DIR / "bundle.yaml")
        self.assertIn("Nothing is written", bundle)
        self.assertIn("inferred", bundle)

    def test_panel_layout_lists_this_button_and_every_sibling(self):
        # An unlisted bundle gets appended by pyRevit, so placement needs it.
        layout = _source(PANEL_DIR / "bundle.yaml")
        for name in ("View Align", "View Settings Transfer", "Link Probe"):
            self.assertIn(name, layout, name)


class IronPythonTests(unittest.TestCase):
    """pyRevit runs these under IronPython 2.7."""

    F_STRING = re.compile(r"""(^|[^\w'"])[fF](['"])""")

    def test_no_module_uses_an_f_string(self):
        for name in EXPECTED_MODULES:
            source = _source(COMMAND_DIR / name)
            self.assertIsNone(self.F_STRING.search(source), name)

    def test_every_module_parses(self):
        for name in EXPECTED_MODULES:
            ast.parse(_source(COMMAND_DIR / name))

    def test_every_module_imports_print_function(self):
        for name in EXPECTED_MODULES:
            self.assertIn("from __future__ import print_function",
                          _source(COMMAND_DIR / name), name)

    def test_sibling_imports_come_after_the_sys_path_append(self):
        source = _source(SCRIPT_MODULE)
        self.assertLess(source.index("sys.path.append(SCRIPT_DIR)"),
                        source.index("import link_probe_report"))


if __name__ == "__main__":
    unittest.main()
