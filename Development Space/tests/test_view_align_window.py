"""Window behaviour for View Align, which had no tests of its own.

The tool's logic lives in a pushbutton `script.py` rather than a `lib/easybim`
module, so it is loaded here behind stubbed `clr`, `System.Windows` and
`pyrevit` modules.  Two things this pins were shipped broken and caught by
throwaway harnesses: Collapse All silently doing nothing once the tree stopped
realizing every row, and a failed re-pin either vanishing without a word or
rolling back a run that had already succeeded.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "EasyBIM.tab" / "Views.panel" / "View Align.pushbutton" / "script.py"
)
LIB = pathlib.Path(__file__).resolve().parents[2] / "lib"


class CheckBox(object):
    """Stands in for a WPF CheckBox."""

    def __init__(self, data_context=None, is_checked=False):
        self.DataContext = data_context
        self.IsChecked = is_checked


class ToggleButton(object):
    """A tree expander: it bubbles to the same handler and must be ignored."""

    def __init__(self, data_context=None):
        self.DataContext = data_context


_STUB_NAMES = (
    "clr", "System", "System.Windows", "System.Windows.Controls",
    "System.Windows.Controls.Primitives",
    "pyrevit", "pyrevit.DB", "pyrevit.forms", "pyrevit.revit",
    "pyrevit.script", "pyrevit.compat",
    "easybim", "easybim.sheet_geometry", "easybim.sheet_titleblocks",
    "easybim.compat",
)


def _build_stubs():
    clr = types.ModuleType("clr")
    clr.AddReference = lambda *a, **k: None

    controls = types.ModuleType("System.Windows.Controls")
    controls.CheckBox = CheckBox
    primitives = types.ModuleType("System.Windows.Controls.Primitives")
    primitives.ButtonBase = type("ButtonBase", (), {"ClickEvent": object()})
    controls.Primitives = primitives
    windows = types.ModuleType("System.Windows")
    windows.RoutedEventHandler = lambda handler: handler
    windows.Controls = controls
    system = types.ModuleType("System")
    system.Windows = windows

    DB = types.ModuleType("DB")
    DB.XYZ = type("XYZ", (), {"Zero": None})
    DB.ElementId = type("ElementId", (), {"InvalidElementId": None})
    DB.BuiltInParameter = types.SimpleNamespace(
        VIEWER_VOLUME_OF_INTEREST_CROP=None)
    DB.BuiltInCategory = types.SimpleNamespace(OST_TitleBlocks=1)

    pyrevit = types.ModuleType("pyrevit")
    pyrevit.DB = DB
    pyrevit.forms = types.SimpleNamespace(
        WPFWindow=object, alert=lambda *a, **k: None)
    pyrevit.revit = types.SimpleNamespace(doc=None, uidoc=None)
    pyrevit.script = types.SimpleNamespace(
        get_logger=lambda: types.SimpleNamespace(debug=lambda *a, **k: None))
    pyrevit.compat = types.SimpleNamespace(
        get_elementid_value_func=lambda: (lambda eid: eid))

    # The real easybim modules: script.py imports sheet_geometry and
    # sheet_titleblocks, and their maths is what it is being tested against.
    easybim = types.ModuleType("easybim")
    easybim.__path__ = [str(LIB / "easybim")]

    return {
        "clr": clr,
        "System": system,
        "System.Windows": windows,
        "System.Windows.Controls": controls,
        "System.Windows.Controls.Primitives": primitives,
        "pyrevit": pyrevit,
        "pyrevit.DB": DB,
        "pyrevit.forms": pyrevit.forms,
        "pyrevit.revit": pyrevit.revit,
        "pyrevit.script": pyrevit.script,
        "pyrevit.compat": pyrevit.compat,
        "easybim": easybim,
    }


def _load_module():
    """Load the pushbutton script behind stubs, leaving sys.modules as found.

    The stubs must not outlive this call. Other suites install their own
    `easybim` and `pyrevit` doubles, and a leftover stub here silently becomes
    theirs - which breaks them, or this, depending on discovery order.
    """
    saved = dict((name, sys.modules.get(name)) for name in _STUB_NAMES)
    sys.modules.update(_build_stubs())
    try:
        spec = importlib.util.spec_from_file_location("view_align_script",
                                                      str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name in _STUB_NAMES:
            previous = saved.get(name)
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


view_align = _load_module()
Window = view_align.ViewAlignWindow


class _Pinnable(object):
    """An element whose pin can be made to refuse."""

    def __init__(self, refuses=False):
        self.Pinned = False
        self._refuses = refuses

    def __setattr__(self, name, value):
        if name == "Pinned" and getattr(self, "_refuses", False) and value:
            raise Exception("element is workset-locked")
        object.__setattr__(self, name, value)


class RestorePinsTests(unittest.TestCase):
    """Restoring a pin is housekeeping: never silent, never destructive."""

    def setUp(self):
        self.stats = view_align.RunStats()

    def test_a_restored_pin_is_counted(self):
        element = _Pinnable()
        view_align._restore_pins([(element, "A-101")], self.stats)
        self.assertTrue(element.Pinned)
        self.assertEqual(1, self.stats.pinned_restored)
        self.assertEqual([], self.stats.notes)

    def test_a_refused_pin_is_reported_rather_than_swallowed(self):
        view_align._restore_pins([(_Pinnable(refuses=True), "A-101")], self.stats)
        self.assertEqual(1, len(self.stats.notes))
        self.assertIn("A-101", self.stats.notes[0])
        self.assertIn("pin could not be restored", self.stats.notes[0])

    def test_a_refused_pin_never_raises(self):
        # Raising here would roll back an alignment that already succeeded.
        try:
            view_align._restore_pins(
                [(_Pinnable(refuses=True), "A-101")], self.stats)
        except Exception as error:
            self.fail("restoring a pin must not raise: {}".format(error))

    def test_a_refused_pin_is_not_counted_as_restored(self):
        view_align._restore_pins([(_Pinnable(refuses=True), "A-101")], self.stats)
        self.assertEqual(0, self.stats.pinned_restored)

    def test_one_refusal_does_not_stop_the_others(self):
        good_before, bad, good_after = _Pinnable(), _Pinnable(refuses=True), _Pinnable()
        view_align._restore_pins(
            [(good_before, "A-101"), (bad, "A-102"), (good_after, "A-103")],
            self.stats)
        self.assertTrue(good_before.Pinned)
        self.assertTrue(good_after.Pinned)
        self.assertEqual(2, self.stats.pinned_restored)
        self.assertEqual(1, len(self.stats.notes))

    def test_a_refusal_is_a_note_and_never_an_issue(self):
        # stats.issues aborts the whole run; by this point the writes are done.
        view_align._restore_pins([(_Pinnable(refuses=True), "A-101")], self.stats)
        self.assertEqual([], self.stats.issues)

    def test_nothing_pinned_is_not_an_error(self):
        view_align._restore_pins([], self.stats)
        self.assertEqual(0, self.stats.pinned_restored)
        self.assertEqual([], self.stats.notes)


class _Box(object):
    def __init__(self):
        self.Text = ""
        self.ItemsSource = None


def _window(sheets=4, views=3):
    window = object.__new__(Window)
    window.target_search_tb = _Box()
    window.target_tv = _Box()
    window.target_count_tb = _Box()
    window.status_tb = _Box()
    window._checked_viewport_ids = set()
    window._collapsed_sheet_ids = set()
    window._target_rows = []
    window._target_sheet_records = []

    viewport_id = 100
    for sheet in range(sheets):
        rows = []
        for view in range(views):
            row = types.SimpleNamespace(
                viewport_id_int=viewport_id,
                sheet_id_int=sheet,
                view_name="View {}-{}".format(sheet, view),
                view_type_badge_text="Floor Plan",
                search_blob="a-{:03d} view {}-{}".format(sheet, sheet, view))
            viewport_id += 1
            rows.append(row)
            window._target_rows.append(row)
        window._target_sheet_records.append({
            "sheet_id_int": sheet,
            "sheet_number": "A-{:03d}".format(sheet),
            "sheet_name": "Sheet {}".format(sheet),
            "rows": rows,
        })
    return window


def _expansion(window):
    return [node.is_expanded for node in window.target_tv.ItemsSource]


class TreeExpansionTests(unittest.TestCase):
    """Expansion lives on the data because a virtualized row has no container.

    Reaching for containers could only ever move the rows WPF had realized, so
    these assert the whole tree responds, not just what would be on screen.
    """

    def setUp(self):
        self.window = _window()
        Window._refresh_target_tree(self.window)

    def test_sheets_are_expanded_by_default(self):
        self.assertEqual([True] * 4, _expansion(self.window))

    def test_collapse_all_reaches_every_sheet(self):
        Window._set_tree_expanded(self.window, False)
        self.assertEqual([False] * 4, _expansion(self.window))

    def test_expand_all_reaches_every_sheet(self):
        Window._set_tree_expanded(self.window, False)
        Window._set_tree_expanded(self.window, True)
        self.assertEqual([True] * 4, _expansion(self.window))

    def test_a_sheet_closed_by_hand_survives_a_rebuild(self):
        # What the TwoWay IsExpanded binding writes when a user clicks the arrow.
        self.window.target_tv.ItemsSource[1].is_expanded = False
        Window._refresh_target_tree(self.window)
        self.assertEqual([True, False, True, True], _expansion(self.window))

    def test_a_sheet_closed_by_hand_survives_a_search(self):
        self.window.target_tv.ItemsSource[1].is_expanded = False
        Window._refresh_target_tree(self.window)
        self.window.target_search_tb.Text = "view 2"
        Window._refresh_target_tree(self.window)
        self.window.target_search_tb.Text = ""
        Window._refresh_target_tree(self.window)
        self.assertEqual([True, False, True, True], _expansion(self.window))

    def test_search_filters_to_the_matching_sheet(self):
        self.window.target_search_tb.Text = "view 2-"
        Window._refresh_target_tree(self.window)
        self.assertEqual(
            ["A-002 - Sheet 2"],
            [node.display_name for node in self.window.target_tv.ItemsSource])


class TreeClickTests(unittest.TestCase):
    """One handler on the tree catches every click that bubbles to it."""

    def setUp(self):
        self.window = _window()
        Window._refresh_target_tree(self.window)

    def _click(self, source):
        args = types.SimpleNamespace(OriginalSource=source)
        Window.target_checkbox_click(self.window, None, args)

    def test_an_expander_click_changes_nothing(self):
        sheet_node = self.window.target_tv.ItemsSource[0]
        self._click(ToggleButton(sheet_node))
        self.assertEqual(set(), self.window._checked_viewport_ids)

    def test_a_click_from_nowhere_is_ignored(self):
        self._click(None)
        self.assertEqual(set(), self.window._checked_viewport_ids)

    def test_ticking_one_view_checks_only_it(self):
        leaf = self.window.target_tv.ItemsSource[0].children[1]
        self._click(CheckBox(leaf, True))
        self.assertEqual(set([leaf.key]), self.window._checked_viewport_ids)

    def test_ticking_a_sheet_checks_all_its_views(self):
        sheet_node = self.window.target_tv.ItemsSource[0]
        self._click(CheckBox(sheet_node, True))
        self.assertEqual(3, len(self.window._checked_viewport_ids))

    def test_unticking_a_sheet_clears_its_views(self):
        sheet_node = self.window.target_tv.ItemsSource[0]
        self._click(CheckBox(sheet_node, True))
        self._click(CheckBox(self.window.target_tv.ItemsSource[0], False))
        self.assertEqual(set(), self.window._checked_viewport_ids)


if __name__ == "__main__":
    unittest.main()
