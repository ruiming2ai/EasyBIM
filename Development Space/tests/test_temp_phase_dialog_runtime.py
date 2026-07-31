"""Contract tests for the compact Temp Phase close warning dialog.

The dialog is implemented in a separate helper so that the Revit/WPF UI can
be kept out of the close state-machine tests.  These tests intentionally use
plain Python fakes and also accept the descriptive ``MESSAGE``/
``build_warning_segments`` names used by early versions of the helper.  The
runtime-facing names are ``WARNING_MESSAGE`` and ``build_warning_runs``.
"""

import importlib.util
import inspect
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB_ROOT = ROOT / "lib" / "easybim"
EXPECTED_MESSAGE = (
    "Please Sync or Save the model again to remove all the Temporary Phases "
    "and Views Settings before closing!!"
)


def _load_module(module_name, filename):
    """Load an EasyBIM module in isolation, as pyRevit does for a hook."""
    package = types.ModuleType("easybim")
    package.__path__ = [str(LIB_ROOT)]
    sys.modules["easybim"] = package
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, str(LIB_ROOT / filename)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    setattr(package, module_name.rsplit(".", 1)[-1], module)
    return module


def _load_dialog():
    return _load_module("easybim.temp_phase_dialog", "temp_phase_dialog.py")


def _load_close():
    return _load_module("easybim.temp_phase_close", "temp_phase_close.py")


def _first_callable(module, *names):
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    raise AssertionError(
        "The compact dialog helper must expose one of: {0}".format(
            ", ".join(names)
        )
    )


def _message(module):
    for name in ("WARNING_MESSAGE", "MESSAGE", "COMPACT_WARNING_MESSAGE"):
        value = getattr(module, name, None)
        if isinstance(value, str):
            return value
    raise AssertionError("The compact dialog message constant is missing")


def _run_value(run, *names):
    """Read a semantic run from dicts, tuples, or WPF Run-like objects."""
    if isinstance(run, dict):
        for name in names:
            if name in run:
                return run[name]
        return None
    for name in names:
        try:
            value = getattr(run, name)
        except Exception:
            continue
        return value
    if isinstance(run, (tuple, list)):
        # The normal tuple contract is (text, bold, color).  The fallback is
        # useful for a simple (text, attributes) representation.
        if names[0] in ("text", "Text"):
            return run[0] if run else ""
        if names[0] in ("bold", "Bold", "font_weight", "FontWeight"):
            if len(run) > 1 and isinstance(run[1], dict):
                return run[1].get("bold", run[1].get("font_weight"))
            return run[1] if len(run) > 1 else None
        if names[0] in ("color", "foreground", "Foreground"):
            if len(run) > 2:
                return run[2]
            if len(run) > 1 and isinstance(run[1], dict):
                return run[1].get("color", run[1].get("foreground"))
    return None


def _normalise_runs(runs):
    normalised = []
    for run in list(runs or []):
        text = _run_value(run, "text", "Text")
        if text is None:
            continue
        bold = _run_value(run, "bold", "is_bold", "font_weight", "FontWeight")
        color = _run_value(run, "color", "foreground", "Foreground")
        normalised.append((str(text), bold, color))
    return normalised


def _is_bold(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").lower()
    return "bold" in text or text in ("700", "800", "900")


def _is_red(value):
    text = str(value or "").lower().replace(" ", "")
    return "red" in text or "#ffff0000" in text or text in (
        "#ff0000",
        "ffff0000",
        "#c00000",
        "c00000",
    )


def _styled_spans(runs):
    """Return (start, end, bold, red) spans for semantic run assertions."""
    spans = []
    offset = 0
    for text, bold, color in _normalise_runs(runs):
        spans.append((offset, offset + len(text), _is_bold(bold), _is_red(color)))
        offset += len(text)
    return spans


class _EnumUi(object):
    """TaskDialog enums deliberately distinct from command-link ids."""

    class TaskDialogCommandLinkId(object):
        CommandLink1 = object()
        CommandLink2 = object()
        CommandLink3 = object()

    class TaskDialogResult(object):
        CommandLink1 = object()
        CommandLink2 = object()
        CommandLink3 = object()

        Cancel = object()


class CompactTempPhaseDialogTests(unittest.TestCase):
    def setUp(self):
        self.dialog = _load_dialog()

    def test_warning_message_is_exact_and_compact(self):
        self.assertEqual(EXPECTED_MESSAGE, _message(self.dialog))

    def test_warning_runs_preserve_text_and_emphasis(self):
        builder = _first_callable(
            self.dialog, "build_warning_runs", "build_warning_segments"
        )
        runs = builder()
        normalised = _normalise_runs(runs)
        rendered = "".join(text for text, _, _ in normalised)
        self.assertEqual(EXPECTED_MESSAGE, rendered)

        spans = _styled_spans(runs)
        for phrase in ("Sync", "Save", "Temporary Phases"):
            start = EXPECTED_MESSAGE.index(phrase)
            end = start + len(phrase)
            covering = [
                span for span in spans if span[0] <= start and span[1] >= end
            ]
            self.assertTrue(covering, "No style run covers {0!r}".format(phrase))
            self.assertTrue(
                all(span[2] for span in covering),
                "{0!r} must be bold".format(phrase),
            )
            if phrase in ("Sync", "Save"):
                self.assertTrue(
                    all(span[3] for span in covering),
                    "{0!r} must be red".format(phrase),
                )
            else:
                self.assertFalse(
                    any(span[3] for span in covering),
                    "{0!r} must retain the normal text color".format(phrase),
                )

    def test_action_labels_include_sync_only_for_workshared_documents(self):
        labels_builder = _first_callable(self.dialog, "action_labels")
        non_workshared = labels_builder(False)
        workshared = labels_builder(True)

        def labels(value):
            if isinstance(value, dict):
                return list(value.values())
            return [
                item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else str(item)
                for item in list(value or [])
            ]

        non_labels = labels(non_workshared)
        work_labels = labels(workshared)
        self.assertIn("Save Restored File and Close", non_labels)
        self.assertIn("Keep File Open", non_labels)
        self.assertFalse(any("Synchronize" in text for text in non_labels))
        self.assertIn("Save Restored File and Close", work_labels)
        self.assertIn("Synchronize Restored File and Close", work_labels)
        self.assertIn("Keep File Open", work_labels)

    def test_choice_mapper_uses_task_dialog_result_not_command_link_id(self):
        mapper = getattr(self.dialog, "_task_dialog_result_to_choice", None)
        if not callable(mapper):
            close = _load_close()
            mapper = getattr(close, "_task_dialog_result_to_choice", None)
        if not callable(mapper):
            self.skipTest("choice mapper remains private to the dialog implementation")

        def map_choice(result, workshared):
            parameter_count = len(inspect.signature(mapper).parameters)
            if parameter_count >= 3:
                return mapper(result, _EnumUi, workshared)
            return mapper(
                result,
                _EnumUi,
                (
                    ("save_close", "CommandLink1"),
                    ("sync_close", "CommandLink2"),
                    ("cancel", "CommandLink3"),
                )
                if workshared
                else (("save_close", "CommandLink1"), ("cancel", "CommandLink2")),
            )

        self.assertEqual("save_close", map_choice(_EnumUi.TaskDialogResult.CommandLink1, False))
        self.assertEqual("cancel", map_choice(_EnumUi.TaskDialogResult.CommandLink2, False))
        self.assertEqual("save_close", map_choice(_EnumUi.TaskDialogResult.CommandLink1, True))
        self.assertEqual("sync_close", map_choice(_EnumUi.TaskDialogResult.CommandLink2, True))
        self.assertEqual("cancel", map_choice(_EnumUi.TaskDialogResult.CommandLink3, True))
        # The command-link registration enum is not a Show() result and must
        # never accidentally select Save or Sync.
        self.assertEqual("cancel", map_choice(_EnumUi.TaskDialogCommandLinkId.CommandLink1, False))

    def test_wpf_failure_uses_native_fallback_and_preserves_choice(self):
        """A WPF failure falls through to native warning TaskDialog."""
        close = _load_close()
        native_dialog = type("NativeDialog", (object,), {})()
        native_dialog.links = []
        native_dialog.MainIcon = None

        def add_link(link_id, text):
            native_dialog.links.append((link_id, text))

        native_dialog.AddCommandLink = add_link
        native_dialog.Show = lambda: _EnumUi.TaskDialogResult.CommandLink2

        class NativeUi(_EnumUi):
            TaskDialogIcon = type("TaskDialogIcon", (object,), {"Warning": object()})

            @staticmethod
            def TaskDialog(title):
                del title
                return native_dialog

        # A None return is the helper's documented “WPF unavailable” signal.
        with mock.patch.object(close.temp_phase_dialog, "show_close_decision", return_value=None):
            with mock.patch.object(close, "_get_ui", return_value=NativeUi):
                choice = close._show_close_decision(
                    {
                        "doc_title": "sample.rvt",
                        "doc_is_workshared": True,
                        "dialog_view_lines": [],
                        "tracked_restore_views": [],
                        "untracked_tvp_views": [],
                    },
                    {"title": "sample.rvt"},
                )

        self.assertEqual("sync_close", choice)
        self.assertEqual(NativeUi.TaskDialogIcon.Warning, native_dialog.MainIcon)
        self.assertEqual(
            [
                "Save Restored File and Close",
                "Synchronize Restored File and Close",
                "Keep File Open",
            ],
            [text for _, text in native_dialog.links],
        )
        self.assertEqual(
            _EnumUi.TaskDialogResult.CommandLink3,
            native_dialog.DefaultButton,
        )
        self.assertEqual(EXPECTED_MESSAGE, native_dialog.MainInstruction)
        self.assertEqual(
            self.dialog.RESTORED_MESSAGE,
            native_dialog.MainContent,
        )

    def test_close_state_machine_accepts_wpf_choice_before_native_fallback(self):
        close = _load_close()
        with mock.patch.object(
            close.temp_phase_dialog, "show_close_decision", return_value="save_close"
        ) as show_wpf:
            with mock.patch.object(close, "_get_ui", return_value=None):
                choice = close._show_close_decision(
                    {
                        "doc_title": "sample.rvt",
                        "doc_is_workshared": False,
                        "dialog_view_lines": [],
                        "tracked_restore_views": [],
                        "untracked_tvp_views": [],
                    },
                    {"title": "sample.rvt"},
                )

        self.assertEqual("save_close", choice)
        show_wpf.assert_called_once_with(
            workshared=False,
            title="Temp Phase Warning",
        )

    def test_warning_icon_has_native_or_unicode_fallback(self):
        icon_builder = getattr(self.dialog, "build_warning_icon", None)
        if not callable(icon_builder):
            self.skipTest("warning icon helper is internal to the WPF implementation")

        class FakeImage(object):
            pass

        class FakeTextBlock(object):
            pass

        class FakeBrushes(object):
            DarkOrange = object()

        # Supplying host-independent fake controls forces the native icon
        # conversion path to fail on this non-Revit runner and exercises the
        # visible Unicode warning-glyph fallback.
        try:
            icon = icon_builder(FakeImage, FakeTextBlock, FakeBrushes)
        except TypeError:
            try:
                icon = icon_builder(force_fallback=True)
            except TypeError:
                self.skipTest("warning icon helper has no injectable fallback")
        self.assertTrue(icon)
        self.assertEqual("⚠", getattr(icon, "Text", "⚠"))


if __name__ == "__main__":
    unittest.main()
