"""Governance for Circuit Schedule.

The pane is wired by name, twice over: once for the docked ``UserControl`` and
once for the modeless ``Window`` that stands in when the host pyRevit build has
no dockable-pane support.  A name that exists in one file and not the other, or
a handler named in XAML with no method behind it, only shows up in Revit - and
only on the machine that took the fallback path.  So both are checked here.
"""

import ast
import importlib.util
import pathlib
import re
import sys
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib" / "easybim"

# The panel module imports its own siblings through the easybim package, so the
# extension lib folder has to be importable the way pyRevit makes it.
if str(REPO_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
UI_DIR = LIB_DIR / "ui"
PULLDOWN_DIR = (REPO_ROOT / "EasyBIM.tab" / "Misc Tools.panel" /
                "Circuiting.pulldown")
COMMAND_DIR = PULLDOWN_DIR / "Circuit Schedule.pushbutton"

PANEL_MODULE = LIB_DIR / "circuit_schedule_panel.py"
STATE_MODULE = LIB_DIR / "circuit_schedule_state.py"
REVIT_MODULE = LIB_DIR / "circuit_schedule_revit.py"
SCRIPT_MODULE = COMMAND_DIR / "script.py"

PANEL_XAML = UI_DIR / "circuit_schedule_panel.xaml"
WINDOW_XAML = UI_DIR / "circuit_schedule_panel_window.xaml"

X_NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
HANDLER_ATTRS = ("Click", "Checked", "Unchecked", "SelectionChanged",
                 "TextChanged", "SelectedItemChanged", "MouseDoubleClick",
                 "PreviewKeyDown")

CONTROL_ATTRIBUTE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

#: Names declared inside the row DataTemplate.  WPF scopes those to the
#: template, not the window, so the view never exposes them as attributes.
TEMPLATE_NAMES = frozenset(
    ["RowBorder", "GlyphText", "NumberText", "TitleText", "SubtitleText",
     "DetailText"])

#: The controls the panel module drives by name.
REQUIRED_NAMES = frozenset(
    [
        "TreeRoot", "SearchBox", "ClearSearchButton", "PathText", "StatusText",
        "EmptyText", "HeaderText", "LegendText",
        "RefreshButton", "ShowButton", "ExpandAllButton", "CollapseAllButton",
    ]
)


def _import_panel_module():
    """Import the pane module against a real ``easybim`` package.

    ``test_external_events`` installs a hand-built stub under that name, and a
    stub has no submodule search locations - so the pane's own
    ``from easybim import circuit_schedule_state`` fails depending on which
    test ran first.  Snapshot, clear, import, restore.
    """
    saved = dict((name, module) for name, module in sys.modules.items()
                 if name == "easybim" or name.startswith("easybim."))
    for name in saved:
        del sys.modules[name]
    try:
        return importlib.import_module("easybim.circuit_schedule_panel")
    finally:
        for name in list(sys.modules):
            if name == "easybim" or name.startswith("easybim."):
                del sys.modules[name]
        sys.modules.update(saved)


def _root(path):
    return ET.parse(str(path)).getroot()


def _xaml_names(path):
    return set(element.attrib[X_NAME] for element in _root(path).iter()
               if X_NAME in element.attrib)


def _xaml_handlers(path):
    handlers = set()
    for element in _root(path).iter():
        for attr in HANDLER_ATTRS:
            value = element.attrib.get(attr)
            if value:
                handlers.add(value)
    return handlers


def _all_classes(path):
    """Every class in the file, nested ones included.

    The view classes are built inside ``_panel_type()`` and
    ``_fallback_window_type()``, so a scan of ``tree.body`` alone would walk
    straight past them and assert nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return dict((node.name, node) for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef))


def _module_control_attributes(path):
    """Every ``<view>.Name`` the module reads, plus its by-name lookups."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    attributes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and CONTROL_ATTRIBUTE.match(node.attr):
            value = node.value
            if isinstance(value, ast.Name) and value.id in ("view", "self"):
                attributes.add(node.attr)
    # _set_text("StatusText", ...) and friends reach controls by string.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name not in ("_set_text", "_set_visible"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            attributes.add(node.args[0].value)
    return attributes


class BundleTests(unittest.TestCase):
    def test_the_button_sits_in_the_circuiting_pulldown(self):
        self.assertTrue(COMMAND_DIR.is_dir(), str(COMMAND_DIR))

    def test_bundle_carries_the_ribbon_title(self):
        bundle = (COMMAND_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("Circuit", bundle)
        self.assertIn("Schedule", bundle)
        self.assertIn("author: Ruiming Liu", bundle)

    def test_the_pulldown_pins_the_button_order(self):
        # Without a layout the pulldown orders by discovery, so a new folder
        # would silently reshuffle a menu the user already knows.
        bundle = (PULLDOWN_DIR / "bundle.yaml").read_text(encoding="utf-8")
        self.assertIn("layout:", bundle)
        self.assertIn("- Update Circuit Rating", bundle)
        self.assertIn("- Circuit Schedule", bundle)

    def test_both_icon_variants_exist(self):
        for name in ("icon.png", "icon.dark.png"):
            path = COMMAND_DIR / name
            self.assertTrue(path.exists(), str(path))
            self.assertGreater(path.stat().st_size, 0, str(path))

    def test_expected_modules_exist(self):
        for path in (SCRIPT_MODULE, PANEL_MODULE, STATE_MODULE, REVIT_MODULE,
                     PANEL_XAML, WINDOW_XAML):
            self.assertTrue(path.exists(), str(path))


class PlacementTests(unittest.TestCase):
    """Why the pane cannot live in the pushbutton folder."""

    def test_the_pane_lives_in_lib_so_startup_can_import_it(self):
        # A .pushbutton folder is only on sys.path while its own script.py
        # runs, and RegisterDockablePane is only accepted during app init.
        self.assertTrue(PANEL_MODULE.exists())
        startup = (REPO_ROOT / "startup.py").read_text(encoding="utf-8")
        self.assertIn("from easybim import circuit_schedule_panel", startup)
        self.assertIn("circuit_schedule_panel.register()", startup)

    def test_startup_registration_is_guarded(self):
        # A raising import here would take out every later startup job.
        startup = (REPO_ROOT / "startup.py").read_text(encoding="utf-8")
        block = startup.split("from easybim import circuit_schedule_panel")[0]
        self.assertTrue(block.rstrip().endswith("try:"))

    def test_the_pane_id_is_not_the_clash_pane_id(self):
        panel = PANEL_MODULE.read_text(encoding="utf-8")
        clash = (LIB_DIR / "clash_detection_panel.py").read_text(encoding="utf-8")
        panel_id = re.search(r'PANEL_ID = "([^"]+)"', panel).group(1)
        clash_id = re.search(r'PANEL_ID = "([^"]+)"', clash).group(1)
        self.assertNotEqual(panel_id, clash_id)

    def test_the_script_does_not_claim_a_persistent_engine(self):
        # The pane holds no Revit event delegates, only an ExternalEvent that
        # the next button click re-creates.  A persistent engine would buy
        # nothing and bring back the stale-sys.modules problem.
        source = SCRIPT_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("__persistentengine__", source)


class XamlTests(unittest.TestCase):
    def test_both_files_parse(self):
        self.assertTrue(_root(PANEL_XAML) is not None)
        self.assertTrue(_root(WINDOW_XAML) is not None)

    def test_the_docked_pane_is_a_usercontrol_and_the_fallback_a_window(self):
        self.assertTrue(_root(PANEL_XAML).tag.endswith("UserControl"))
        self.assertTrue(_root(WINDOW_XAML).tag.endswith("Window"))

    def test_the_two_views_expose_the_same_controls(self):
        # The fallback is only ever exercised on hosts without dockable panes,
        # so drift between the pair would go unnoticed until it broke there.
        self.assertEqual(_xaml_names(PANEL_XAML), _xaml_names(WINDOW_XAML))

    def test_the_two_views_wire_the_same_handlers(self):
        self.assertEqual(_xaml_handlers(PANEL_XAML), _xaml_handlers(WINDOW_XAML))

    def test_every_required_control_is_named(self):
        names = _xaml_names(PANEL_XAML)
        self.assertTrue(REQUIRED_NAMES.issubset(names),
                        sorted(REQUIRED_NAMES - names))

    def test_the_tree_recurses_through_children(self):
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn('HierarchicalDataTemplate ItemsSource="{Binding children}"',
                      source)

    def test_is_expanded_is_bound_two_way_on_the_container(self):
        # Containers are virtualized, so ContainerFromItem returns null for an
        # unrealized row; this binding is the only way code can open the tree.
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn("TreeViewItem", source)
        self.assertIn("{Binding is_expanded, Mode=TwoWay}", source)

    def test_the_tree_virtualizes(self):
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn('VirtualizingPanel.IsVirtualizing="True"', source)
        # TreeView virtualization only engages with item scrolling on.
        self.assertIn('ScrollViewer.CanContentScroll="True"', source)

    def test_each_node_kind_has_its_own_appearance(self):
        # A board, a circuit and a device must not read alike: the whole
        # upstream/downstream story is carried by that contrast.
        source = PANEL_XAML.read_text(encoding="utf-8")
        for kind in ("equipment", "circuit", "load"):
            self.assertIn('Value="{0}"'.format(kind), source)

    def test_the_circuit_number_has_its_own_column(self):
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn("{Binding number_text}", source)

    def test_the_loop_and_reference_states_are_styled(self):
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn("{Binding is_cycle}", source)
        self.assertIn("{Binding is_reference}", source)

    def test_hidden_rows_collapse(self):
        source = PANEL_XAML.read_text(encoding="utf-8")
        self.assertIn("{Binding is_visible}", source)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.classes = _all_classes(PANEL_MODULE)

    def test_the_view_classes_are_found(self):
        # Guards the test itself: both are nested in factory functions, so a
        # top-level-only scan would pass while checking nothing.
        self.assertIn("CircuitSchedulePanel", self.classes)
        self.assertIn("CircuitScheduleWindow", self.classes)
        self.assertIn("_Handlers", self.classes)

    def test_every_xaml_handler_exists_on_the_shared_mixin(self):
        methods = set(child.name for child in self.classes["_Handlers"].body
                      if isinstance(child, ast.FunctionDef))
        missing = _xaml_handlers(PANEL_XAML) - methods
        self.assertFalse(missing, sorted(missing))

    def test_both_views_inherit_the_handler_mixin(self):
        for name in ("CircuitSchedulePanel", "CircuitScheduleWindow"):
            bases = [getattr(base, "id", None)
                     for base in self.classes[name].bases]
            self.assertIn("_Handlers", bases, name)

    def test_the_docked_pane_declares_its_identity(self):
        source = PANEL_MODULE.read_text(encoding="utf-8")
        for attr in ("panel_title", "panel_id", "panel_source"):
            self.assertIn(attr, source)
        self.assertIn("setup_dockablepane", source)
        self.assertIn("DockPosition.Right", source)

    def test_every_control_the_module_touches_is_named_in_the_xaml(self):
        names = _xaml_names(PANEL_XAML) - TEMPLATE_NAMES
        touched = _module_control_attributes(PANEL_MODULE)
        # Only judge attributes that look like control names and are not
        # WPF/pyRevit members the view already inherits.
        inherited = set(["Text", "Visibility", "IsEnabled", "ItemsSource",
                         "SelectedItem", "Show", "Close", "Activate", "Left",
                         "Top", "Width", "Document", "ActiveUIDocument", "Key",
                         "Escape", "Enter", "Owner", "InitialState",
                         "DockPosition", "Visible", "Collapsed"])
        unknown = set(name for name in touched
                      if name in REQUIRED_NAMES or name in TEMPLATE_NAMES)
        self.assertTrue(unknown.issubset(names), sorted(unknown - names))
        del inherited


class ModulePurityTests(unittest.TestCase):
    def test_the_state_module_never_reaches_for_revit_or_clr(self):
        # It is loaded standalone by the desktop suite; one CLR import would
        # make the whole hierarchy untestable off Revit.
        source = STATE_MODULE.read_text(encoding="utf-8")
        for banned in ("import clr", "from pyrevit", "Autodesk.Revit",
                       "from easybim"):
            self.assertNotIn(banned, source, banned)

    def test_the_panel_module_leaves_the_api_to_the_revit_module(self):
        source = PANEL_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("FilteredElementCollector", source)
        self.assertNotIn("from pyrevit import DB", source)

    def test_the_panel_module_imports_off_revit(self):
        # Every Revit/pyRevit import in it is guarded, which is what lets the
        # tree be exercised here rather than only in Revit.
        module = _import_panel_module()
        self.assertTrue(module.PANEL_ID)
        self.assertTrue(issubclass(module.PanelNode, object))

    def test_no_python_3_only_syntax(self):
        # pyRevit's default engine is IronPython 2.7.  Checked on the parse
        # tree rather than by text search, which cannot tell an f-string from
        # a word ending in "f" before a quote.
        for path in (STATE_MODULE, PANEL_MODULE, REVIT_MODULE, SCRIPT_MODULE):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("dataclass", source, str(path))
            for node in ast.walk(ast.parse(source)):
                self.assertNotIn(type(node).__name__,
                                 ("JoinedStr", "AnnAssign"), str(path))

    def test_every_module_declares_utf8(self):
        for path in (STATE_MODULE, PANEL_MODULE, REVIT_MODULE, SCRIPT_MODULE):
            first = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("coding: utf-8", first, str(path))


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT_MODULE.read_text(encoding="utf-8")

    def test_the_script_guards_the_document(self):
        self.assertIn("forms.check_modeldoc", self.source)
        self.assertIn("IsFamilyDocument", self.source)

    def test_the_script_creates_the_external_event_in_api_context(self):
        # bridge.create() is only valid inside a command run; the pane that
        # outlives it can never make one for itself.
        self.assertIn("ExternalEventBridge(", self.source)
        self.assertIn("panel.attach_bridge(", self.source)

    def test_the_script_reuses_a_live_bridge(self):
        self.assertIn("has_ready_bridge()", self.source)

    def test_the_script_opens_the_pane_with_a_scan(self):
        self.assertIn("crevit.scan_model(", self.source)
        self.assertIn("panel.open_panel(", self.source)


if __name__ == "__main__":
    unittest.main()
