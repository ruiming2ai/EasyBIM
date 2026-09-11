# -*- coding: utf-8 -*-
"""Space Boundary's bundle, XAML wiring, and the pins that keep the layers apart.

This tool writes model graphics and keeps a relationship inside the .rvt, so
the boundaries between its layers matter more than usual: the state module
must stay drivable on a laptop, the UI must never reach the model except
through the callables the launcher hands it, and exactly one module may open
a transaction.  Every one of those is asserted here rather than trusted.
"""

import ast
import pathlib
import re
import struct
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMAND_DIR = REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" / "Space Boundary.pushbutton"
UI_MODULE = COMMAND_DIR / "space_boundary_ui.py"
STATE_MODULE = COMMAND_DIR / "space_boundary_state.py"
SETTINGS_MODULE = COMMAND_DIR / "space_boundary_settings.py"
REVIT_MODULE = COMMAND_DIR / "space_boundary_revit.py"
STORAGE_MODULE = COMMAND_DIR / "space_boundary_storage.py"
SCRIPT = COMMAND_DIR / "script.py"
LIB_DIR = REPO_ROOT / "lib" / "easybim"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged", "TextChanged")

WINDOW_CLASSES = {
    "SpaceBoundaryWindow.xaml": "SetupWindow",
    "SpaceBoundaryPlanWindow.xaml": "PlanWindow",
    "SpaceBoundaryReportWindow.xaml": "ReportWindow",
}

# The shared bridged window reaches these by name on the report XAML.
REQUIRED_REPORT_CONTROLS = ("StatusText", "RefreshButton", "EmptyText", "ContentScroll")

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# Inherited from System.Windows.Window / forms.WPFWindow, so they are not
# x:Name lookups and must not be demanded of the XAML.
WINDOW_MEMBERS = frozenset([
    "Activate", "Close", "Closed", "Closing", "Content", "Cursor", "DialogResult",
    "Dispatcher", "Focus", "Height", "Hide", "Icon", "IsEnabled", "IsVisible",
    "Left", "Owner", "Show", "ShowActivated", "ShowDialog", "Tag", "Title",
    "Top", "Topmost", "Width", "WindowState", "OnPropertyChanged",
])


def _xaml_root(name):
    return ET.parse(str(COMMAND_DIR / name)).getroot()


def _xaml_names(root):
    names = set()
    for element in root.iter():
        name = element.attrib.get(X_NAME)
        if name:
            names.add(name)
    return names


def _xaml_handlers(root):
    handlers = set()
    for element in root.iter():
        for attr in HANDLER_ATTRS:
            value = element.attrib.get(attr)
            if value:
                handlers.add(value)
    return handlers


def _class_control_attributes(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    attributes = set()
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Attribute):
                continue
            value = child.value
            if isinstance(value, ast.Name) and value.id == "self":
                if CONTROL_ATTRIBUTE.match(child.attr) and child.attr not in WINDOW_MEMBERS:
                    attributes.add(child.attr)
    return attributes


def _class_methods(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return set(child.name for child in node.body
                       if isinstance(child, ast.FunctionDef))
    return None


def _png_size(path):
    with open(str(path), "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", header[16:24])


def _top_level_names(path):
    """Everything a module exports: its functions, classes and constants."""
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _module_constant(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    return None


class BundleTests(unittest.TestCase):
    def test_the_button_sits_on_the_misc_tools_panel(self):
        self.assertTrue(COMMAND_DIR.is_dir())
        self.assertTrue(SCRIPT.is_file())

    def test_bundle_carries_the_two_line_title_and_author(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn('title: "Space\\nBoundary"', bundle)
        self.assertIn("tooltip:", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_the_bundle_asks_for_revit_2022(self):
        """``FilledRegion.GetBoundaries`` arrived in 2022, and reading a
        region's own outline is the only way to notice a hand edit - the whole
        review lane rests on it, so there is no degraded branch to fall back
        to and the bundle says so up front."""
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("min_revit_version: 2022", bundle)
        self.assertIn("GetBoundaries", REVIT_MODULE.read_text(encoding="utf-8"))

    def test_the_tooltip_states_what_is_written(self):
        """The tool draws elements and keeps a record in the .rvt. A tooltip
        that implied otherwise would be the tool lying about its own writes."""
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("written into this model", bundle)
        self.assertIn("one undo step", bundle)
        self.assertIn("preview", bundle)
        self.assertNotIn("nothing in the model is changed", bundle)
        self.assertNotIn("changes nothing", bundle)

    def test_both_icon_variants_exist_at_96_by_96(self):
        for name in ("icon.png", "icon.dark.png"):
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), str(path))
            self.assertEqual(_png_size(path), (96, 96), name)

    def test_expected_modules_exist(self):
        for name in ("script.py", "space_boundary_state.py", "space_boundary_settings.py",
                     "space_boundary_revit.py", "space_boundary_storage.py",
                     "space_boundary_ui.py"):
            self.assertTrue((COMMAND_DIR / name).exists(), name)

    def test_the_readme_documents_the_tool(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Space Boundary (Misc Tools, Revit 2022+)", readme)


class XamlTests(unittest.TestCase):
    def test_every_xaml_parses(self):
        for name in WINDOW_CLASSES:
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), name)
            ET.parse(str(path))

    def test_no_stray_xaml_files(self):
        found = set(path.name for path in COMMAND_DIR.glob("*.xaml"))
        self.assertEqual(set(WINDOW_CLASSES), found)

    def test_every_handler_resolves_on_its_own_window_class(self):
        for xaml_name, class_name in WINDOW_CLASSES.items():
            methods = _class_methods(UI_MODULE, class_name)
            self.assertIsNotNone(methods, "no class %s in space_boundary_ui" % class_name)
            missing = _xaml_handlers(_xaml_root(xaml_name)) - methods
            self.assertFalse(missing,
                             "%s: %s is missing %s" % (xaml_name, class_name, missing))

    def test_every_control_the_code_touches_exists_in_the_xaml(self):
        """``self.SomeButton`` with no x:Name is an AttributeError on open."""
        for xaml_name, class_name in WINDOW_CLASSES.items():
            names = _xaml_names(_xaml_root(xaml_name))
            for attribute in _class_control_attributes(UI_MODULE, class_name):
                self.assertIn(attribute, names,
                              "%s uses self.%s but %s has no such x:Name"
                              % (class_name, attribute, xaml_name))

    def test_the_setup_carries_the_agreed_cards(self):
        """Source, What, Where and How - the four questions, in that order."""
        names = _xaml_names(_xaml_root("SpaceBoundaryWindow.xaml"))
        required = {
            "KindCombo", "SourcePanel", "SourceCountText", "SourceSearchBox",
            "SourceAllButton", "SourceNoneButton",
            "ScopeActiveViewRadio", "ScopeSelectedViewsRadio", "ViewsPanel", "ViewsCard",
            "ViewCountText", "ViewSearchBox", "ViewAllButton", "ViewNoneButton",
            "SubjectAllRadio", "SubjectOneRadio", "PickButton", "PickedText",
            "BoundaryDocumentRadio", "BoundaryOverrideRadio", "BoundaryOverrideCombo",
            "BoundaryResolvedText", "RegionTypeCombo", "ReplaceExistingCheck",
            "IncludeDesignOptionCheck", "StatusText", "PreviewButton", "CheckButton",
            "CancelButton",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_the_setup_names_the_two_combos_and_the_selected_views_radio(self):
        """The first Revit run showed one blank combo labelled "Draw with":
        nobody could tell whether it meant the fill or the edge. Now there are
        two, each named for what it is."""
        source = (COMMAND_DIR / "SpaceBoundaryWindow.xaml").read_text(encoding="utf-8")
        self.assertIn("Filled region type", source)
        self.assertIn("Boundary line style", source)
        self.assertIn('x:Name="LineStyleCombo"', source)
        self.assertNotIn("Draw with", source)
        self.assertIn('Content="Selected Views Below"', source)
        self.assertNotIn("The views I tick below", source)
        self.assertNotIn("no region yet", source)

    def test_the_report_offers_update_and_update_all_and_nothing_it_removed(self):
        source = (COMMAND_DIR / "SpaceBoundaryReportWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('x:Name="UpdateAllButton"', source)
        ui = UI_MODULE.read_text(encoding="utf-8")
        self.assertIn(u'u"Update"', ui)
        self.assertNotIn("Redraw", ui)
        self.assertNotIn("missing_region", ui)
        self.assertNotIn("can_create", ui)
        self.assertNotIn("Not this view", ui)

    def test_the_setup_has_both_jobs_as_tabs(self):
        source = (COMMAND_DIR / "SpaceBoundaryWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('x:Name="CreateTab"', source)
        self.assertIn('x:Name="CheckTab"', source)
        self.assertIn("Create Boundaries", source)
        self.assertIn("Check Differences", source)

    def test_the_plan_window_carries_the_dry_run_controls(self):
        names = _xaml_names(_xaml_root("SpaceBoundaryPlanWindow.xaml"))
        required = {
            "SummaryText", "RefusalCard", "RefusalText", "SearchBox", "CountText",
            "SelectAllButton", "SelectNoneButton", "ExpandAllButton", "CollapseAllButton",
            "EmptyText", "ContentScroll", "ContentPanel", "AckCard", "AckPanel",
            "StatusText", "DrawButton", "BackButton", "CancelButton",
        }
        self.assertFalse(required - names, "missing x:Name(s): %s" % (required - names))

    def test_the_report_carries_what_the_shared_window_touches(self):
        names = _xaml_names(_xaml_root("SpaceBoundaryReportWindow.xaml"))
        for name in REQUIRED_REPORT_CONTROLS:
            self.assertIn(name, names)

    def test_preview_is_the_default_and_cancel_is_the_escape(self):
        """Draw is never the default action on the setup: the dry run comes
        first, which is the whole house rule for a tool that writes."""
        source = (COMMAND_DIR / "SpaceBoundaryWindow.xaml").read_text(encoding="utf-8")
        preview = source.split('x:Name="PreviewButton"')[1].split("/>")[0]
        self.assertIn('IsDefault="True"', preview)
        cancel = source.split('x:Name="CancelButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', cancel)

    def test_the_plan_windows_cancel_is_the_escape(self):
        source = (COMMAND_DIR / "SpaceBoundaryPlanWindow.xaml").read_text(encoding="utf-8")
        cancel = source.split('x:Name="CancelButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', cancel)

    def test_the_report_close_button_is_the_escape(self):
        source = (COMMAND_DIR / "SpaceBoundaryReportWindow.xaml").read_text(encoding="utf-8")
        close = source.split('x:Name="CloseButton"')[1].split("/>")[0]
        self.assertIn('IsCancel="True"', close)

    def test_star_rows_carry_a_min_height(self):
        for name in WINDOW_CLASSES:
            root = _xaml_root(name)
            for element in root.iter():
                if element.tag.endswith("RowDefinition") and element.attrib.get("Height") == "*":
                    self.assertIn("MinHeight", element.attrib, name)


class IronPythonTests(unittest.TestCase):
    def test_command_scripts_avoid_python3_only_constructs(self):
        failures = []
        for path in sorted(COMMAND_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))

            if re.search(r"(?<![A-Za-z0-9_])[fF]['\"]", source):
                failures.append("{0} appears to contain an f-string".format(path.name))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "dataclasses":
                    failures.append("{0} imports dataclasses".format(path.name))

                if isinstance(node, ast.ClassDef):
                    for decorator in node.decorator_list:
                        if getattr(decorator, "id", None) == "dataclass":
                            failures.append("{0} uses @dataclass on {1}".format(
                                path.name, node.name))

                if isinstance(node, ast.AnnAssign):
                    failures.append("{0} uses variable annotations".format(path.name))

                if isinstance(node, ast.FunctionDef):
                    if node.returns is not None:
                        failures.append("{0} uses return annotations".format(path.name))
                    for arg in list(node.args.args) + list(node.args.kwonlyargs):
                        if arg.annotation is not None:
                            failures.append("{0} uses argument annotations".format(path.name))
                    if node.args.kwonlyargs:
                        failures.append("{0} uses keyword-only arguments".format(path.name))

                if isinstance(node, ast.JoinedStr):
                    failures.append("{0} uses f-strings".format(path.name))

                if isinstance(node, ast.Nonlocal):
                    failures.append("{0} uses nonlocal".format(path.name))

        self.assertEqual([], failures)

    def test_state_and_settings_modules_stay_free_of_revit(self):
        for path in (STATE_MODULE, SETTINGS_MODULE):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import clr", "Autodesk.Revit", "pyrevit"):
                self.assertNotIn(forbidden, source, path.name)

    def test_the_state_module_never_imports_the_adapter(self):
        """The dependency runs one way only: the adapter reads the model and
        hands plain values over. A state module that could import the adapter
        would stop being drivable on a laptop the first time somebody used it.

        Named in the docstring is fine - imported is not, so this reads the
        import statements rather than the file's words."""
        tree = ast.parse(STATE_MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        for name in ("space_boundary_revit", "space_boundary_storage"):
            self.assertNotIn(name, imported)

    def test_ui_module_stays_free_of_revit_api(self):
        source = UI_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("Autodesk.Revit", source)
        self.assertNotIn("import clr", source)
        # Every model read and write belongs to the adapter, reached only
        # through the callables the launcher hands in.
        self.assertNotIn("space_boundary_revit", source)
        self.assertNotIn("space_boundary_storage", source)
        self.assertNotIn("Transaction", source)


class WriteRuleTests(unittest.TestCase):
    def test_only_the_adapter_opens_transactions(self):
        for path in (STATE_MODULE, SETTINGS_MODULE, UI_MODULE, STORAGE_MODULE):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("Transaction(", source, path.name)

    def test_the_write_is_one_undo_step_nested_three_deep(self):
        """A TransactionGroup for the run, a Transaction per view, a
        SubTransaction per room: one bad room rolls back alone and cheaply,
        and the whole run is still a single undo."""
        source = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("TransactionGroup(", source)
        self.assertIn("Assimilate()", source)
        self.assertIn("Transaction(doc", source)
        self.assertIn("SubTransaction(doc", source)

    def test_the_store_joins_the_callers_transaction(self):
        """A region and its record have to commit together, or an undo would
        leave one without the other."""
        source = STORAGE_MODULE.read_text(encoding="utf-8")
        self.assertIn("IsModifiable", source)
        self.assertNotIn("Transaction(", source)

    def test_the_schema_guid_is_this_tools_own(self):
        guid = _module_constant(STORAGE_MODULE, "SCHEMA_GUID")
        self.assertTrue(guid)
        others = []
        for name in ("model_store.py", "copy_monitor_storage.py"):
            path = LIB_DIR / name
            if path.exists():
                others.append(path.read_text(encoding="utf-8"))
        for source in others:
            self.assertNotIn(guid, source)

    def test_the_accepted_list_is_kept_apart_from_the_relationship(self):
        """A region owned by another user cannot be written, but its
        difference still has to be acceptable - so the accepted list goes in
        the shared store, not on the region."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("model_store", source)
        self.assertIn('TOOL_KEY = "space_boundary"', source)
        self.assertIn("accept_difference", source)
        self.assertNotIn("accept_regions", REVIT_MODULE.read_text(encoding="utf-8"))

    def test_accept_difference_is_the_one_review_action(self):
        ui = UI_MODULE.read_text(encoding="utf-8")
        self.assertIn(u'u"Accept Difference"', ui)
        self.assertIn(u'u"Reopen"', ui)
        for gone in (u'u"Ignore"', u'u"Restore"', u'u"Accept",', u"can_accept", u"is_ignored"):
            self.assertNotIn(gone, ui, gone)
        xaml = (COMMAND_DIR / "SpaceBoundaryWindow.xaml").read_text(encoding="utf-8")
        self.assertIn("Accept Difference", xaml)
        self.assertIn('Content="All rooms or spaces in views"', xaml)
        self.assertIn("rooms or spaces in views", xaml)
        self.assertNotIn("Only the ones I pick", xaml)

    def test_a_refused_room_is_told_to_the_user_not_to_the_log(self):
        """Draw used to report refusals as a debug line only: the preview
        listed the room, Draw ran without complaint, and the region was not
        there. Now every refusal is named before the report opens."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("forms.alert", source.split("def _report_write")[1].split("def _open_report")[0])
        self.assertIn("extra_notes", source)

    def test_every_named_skip_has_a_sentence(self):
        """Never silently drop: every code the adapter can emit has words."""
        state_source = STATE_MODULE.read_text(encoding="utf-8")
        sentences = set(re.findall(r'^\s{4}"([a-z_]+)":\s*u"', state_source, re.MULTILINE))
        used = set(re.findall(r'"code":\s*"([a-z_]+)"',
                              REVIT_MODULE.read_text(encoding="utf-8")))
        missing = used - sentences
        self.assertFalse(missing, "codes with no sentence: %s" % sorted(missing))


class LauncherTests(unittest.TestCase):
    def test_the_launcher_keeps_its_engine_and_drops_stale_modules(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("__persistentengine__ = True", source)
        self.assertIn("_drop_stale_modules", source)

    def test_the_active_flag_matches_the_ui_module(self):
        self.assertEqual(_module_constant(SCRIPT, "ACTIVE_ENVVAR"),
                         _module_constant(UI_MODULE, "ACTIVE_ENVVAR"))

    def test_this_tools_envvars_differ_from_the_checkers(self):
        """Each tool keeps its own report open; sharing a name would have one
        tool close another's window."""
        mine = {_module_constant(UI_MODULE, "ACTIVE_ENVVAR"),
                _module_constant(UI_MODULE, "WINDOW_ENVVAR")}
        for other in ("Damper Check", "Fire Damper Check"):
            path = (REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" /
                    (other + ".pushbutton") /
                    (other.lower().replace(" ", "_") + "_ui.py"))
            if not path.exists():
                continue
            theirs = {_module_constant(path, "ACTIVE_ENVVAR"),
                      _module_constant(path, "WINDOW_ENVVAR")}
            self.assertFalse(mine & theirs, other)

    def test_every_stale_module_the_launcher_drops_is_real(self):
        for name in _module_constant(SCRIPT, "STALE_MODULES"):
            if name.startswith("easybim."):
                path = LIB_DIR / (name.split(".", 1)[1] + ".py")
            else:
                path = COMMAND_DIR / (name + ".py")
            self.assertTrue(path.exists(), name)

    def test_the_launcher_refuses_a_document_that_cannot_carry_the_record(self):
        """Fail closed: if the relationship cannot be stored, drawing regions
        that nothing tracks would be worse than not starting."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("availability(doc)", source)
        self.assertIn("exitscript=True", source)

    def test_every_name_the_launcher_reaches_for_exists(self):
        """script.py cannot be imported on a laptop - it reaches pyRevit at
        the top - so a typo in ``brevit.read_boundary_map`` would only turn up
        in Revit, in front of a user. This walks the calls instead."""
        modules = {
            "brevit": REVIT_MODULE, "bstate": STATE_MODULE, "bstorage": STORAGE_MODULE,
            "bui": UI_MODULE, "bsettings": SETTINGS_MODULE,
        }
        # The same modules again, as the Run object holds them.
        held = {"revit": REVIT_MODULE, "state": STATE_MODULE, "storage": STORAGE_MODULE}
        exported = dict((alias, _top_level_names(path))
                        for alias, path in modules.items())
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        missing = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            alias = None
            if isinstance(value, ast.Name) and value.id in modules:
                alias = value.id
            elif (isinstance(value, ast.Attribute) and value.attr in held
                  and isinstance(value.value, ast.Name) and value.value.id == "self"):
                alias = {"revit": "brevit", "state": "bstate",
                         "storage": "bstorage"}[value.attr]
            if alias is None:
                continue
            if node.attr not in exported[alias]:
                missing.append("{0}.{1}".format(alias, node.attr))
        self.assertEqual([], sorted(set(missing)))

    def test_every_name_read_anywhere_in_the_bundle_is_bound_somewhere(self):
        """The second Revit run died on ``global name 'delete' is not
        defined``: an edit cut a local function out and left the call that
        used it. None of these modules can be imported on a laptop, so this
        is the pyflakes the suite did not have - every name a module reads
        must be bound somewhere in that module, or be a builtin."""
        import builtins
        known = set(dir(builtins)) | {"__file__", "__name__", "__revit__", "unicode",
                                      "basestring", "long", "xrange"}
        failures = []
        for path in sorted(COMMAND_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            bound = set(known)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, ast.arg):
                    bound.add(node.arg)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    bound.add(node.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        bound.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    bound.add(node.name)
                elif isinstance(node, ast.Global):
                    bound.update(node.names)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id not in bound:
                        failures.append("{0}:{1} reads '{2}', bound nowhere".format(
                            path.name, node.lineno, node.id))
        self.assertEqual([], failures)

    def test_the_pick_path_reopens_the_setup_and_takes_many(self):
        """PickObjects cannot run while a modal window is up, so the setup
        hands its choices back and the launcher reopens it."""
        self.assertIn('self.result = "pick"', UI_MODULE.read_text(encoding="utf-8"))
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if result == "pick":', source)
        self.assertIn("pick_spatial", source)
        adapter = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("PickObjects(", adapter)
        self.assertNotIn("PickObject(", adapter)

    def test_the_write_regenerates_before_the_record_is_trusted(self):
        """The record's region digest was written from a sketch Revit had not
        finished; now the view is regenerated once and the records re-read."""
        adapter = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("_regenerate(doc)", adapter)
        self.assertIn("_refresh_records(", adapter)

    def test_names_are_read_the_way_ironpython_can(self):
        """The blank combo: ``FilledRegionType.Name`` does not bind under
        IronPython, and the repo's fallback is ``Element.Name.GetValue``."""
        adapter = REVIT_MODULE.read_text(encoding="utf-8")
        self.assertIn("Name.GetValue", adapter)
        self.assertNotIn('getattr(element, "Name"', adapter)


if __name__ == "__main__":
    unittest.main()
