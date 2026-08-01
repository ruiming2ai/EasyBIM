# -*- coding: utf-8 -*-
"""Compact Temp Phase close-warning dialog.

The close hook runs from Revit's Idling context, so it can safely display a
modal WPF window while the document remains open.  WPF is loaded lazily: the
normal pyRevit runtime does not need PresentationFramework just to import the
close state machine.  If WPF is unavailable, ``temp_phase_close`` falls back
to Revit's native TaskDialog.
"""

from __future__ import print_function


import os


TITLE = "Temp Phase Warning"
WARNING_MESSAGE = (
    "Please Sync or Save the model again to remove all the Temporary Phases and Views Settings before closing!!"
)
RESTORED_MESSAGE = "Temporary phase/view state has been restored."

# Compatibility aliases make the presentation contract easy to test and keep
# the wording available to other EasyBIM UI code without duplicating it.
MESSAGE = WARNING_MESSAGE
COMPACT_WARNING_MESSAGE = WARNING_MESSAGE

RED_HEX = "#C00000"

# A private sentinel distinguishes a WPF host failure from ``cancel``.  None
# is a real user choice (the safe Keep File Open action), so it cannot also
# mean that a WPF host failed to load.
_WPF_UNAVAILABLE = object()


try:
    from pyrevit import script as _pyrevit_script

    _LOGGER = _pyrevit_script.get_logger()
except Exception:
    _LOGGER = None


def _log(message):
    """Write UI-path diagnostics without making logging a runtime dependency."""
    if _LOGGER is None:
        return
    try:
        _LOGGER.info("[EasyBIM Temp Phase] %s", message)
    except Exception:
        try:
            _LOGGER.info(message)
        except Exception:
            pass


def build_warning_runs():
    """Return the exact rich-text spans used by the warning body.

    Each item is a small, host-independent dictionary so tests can verify the
    wording and emphasis without loading WPF on a non-Windows runner.
    """
    return [
        {"text": "Please ", "bold": False, "color": None},
        {"text": "Sync", "bold": True, "color": RED_HEX},
        {"text": " or ", "bold": False, "color": None},
        {"text": "Save", "bold": True, "color": RED_HEX},
        {"text": " the model again to remove all the ", "bold": False, "color": None},
        {"text": "Temporary Phases", "bold": True, "color": None},
        {"text": " and Views Settings before closing!!", "bold": False, "color": None},
    ]


def build_warning_segments():
    """Alias for callers that prefer the term ``segments``."""
    return build_warning_runs()


def action_labels(workshared):
    """Return semantic action/label pairs in their visual order."""
    actions = [
        ("save_close", "Save and Close"),
    ]
    if bool(workshared):
        actions.append(("sync_close", "Synchronize and Close"))
    actions.append(("cancel", "Keep File Open"))
    return actions


def show_close_decision(workshared=False, title=TITLE):
    """Show the close decision through pyRevit's XAML/WPFWindow host first.

    ``forms.WPFWindow`` is pyRevit's supported route for loading a XAML
    window.  The direct WPF implementation below is retained as a fallback
    for older/isolated hosts, and ``temp_phase_close`` still owns the native
    Revit TaskDialog fallback when both WPF paths are unavailable.
    """
    result = _show_close_decision_wpfwindow(workshared, title)
    if result is not _WPF_UNAVAILABLE:
        return result
    return _show_close_decision_direct(workshared, title)


def _show_close_decision_wpfwindow(workshared=False, title=TITLE):
    """Show the compact warning using pyRevit's ``forms.WPFWindow``."""
    try:
        from pyrevit import forms
    except Exception:
        return _WPF_UNAVAILABLE

    xaml_path = os.path.join(os.path.dirname(__file__), "ui", "temp_phase_warning.xaml")
    result = {"value": "cancel"}

    class _TempPhaseWarningWindow(forms.WPFWindow):
        def __init__(self):
            forms.WPFWindow.__init__(self, xaml_path)
            self.Topmost = True
            self.Title = title or TITLE
            self._configure_warning_icon()
            self._configure_warning_text()
            self.restored_heading.Text = RESTORED_MESSAGE
            if bool(workshared):
                self.sync_btn.Visibility = self._visible
            else:
                self.sync_btn.Visibility = self._collapsed
            self.save_btn.Click += self._make_choice_handler("save_close")
            self.sync_btn.Click += self._make_choice_handler("sync_close")
            self.keep_open_btn.Click += self._make_choice_handler("cancel")
            # Keep File Open is deliberately the safe default.  It also owns
            # the Escape and title-bar-close paths in the XAML window.
            self.keep_open_btn.IsDefault = True
            self.keep_open_btn.IsCancel = True

        def _configure_warning_icon(self):
            """Load the native Windows warning icon into the XAML Image.

            The XAML already contains a visible warning glyph.  Hide it only
            after native icon conversion succeeds, so an unusual Revit host
            can never leave the warning row blank.
            """
            try:
                import clr

                clr.AddReference("PresentationCore")
                clr.AddReference("WindowsBase")
                clr.AddReference("System.Drawing")
                from System.Drawing import SystemIcons
                from System.Windows import Int32Rect, Visibility
                from System.Windows.Interop import Imaging
                from System.Windows.Media.Imaging import BitmapSizeOptions

                self.warning_icon_image.Source = Imaging.CreateBitmapSourceFromHIcon(
                    SystemIcons.Warning.Handle,
                    Int32Rect.Empty,
                    BitmapSizeOptions.FromEmptyOptions(),
                )
                self.warning_icon_image.Visibility = Visibility.Visible
                self.warning_icon_fallback.Visibility = Visibility.Collapsed
                self._visible = Visibility.Visible
                self._collapsed = Visibility.Collapsed
            except Exception:
                try:
                    from System.Windows import Visibility

                    self.warning_icon_image.Visibility = Visibility.Collapsed
                    self.warning_icon_fallback.Visibility = Visibility.Visible
                    self._visible = Visibility.Visible
                    self._collapsed = Visibility.Collapsed
                except Exception:
                    # The XAML defaults to the Unicode warning glyph.  Use
                    # integers only as a last resort for unusual test doubles;
                    # real WPF hosts always provide Visibility.
                    self._visible = 0
                    self._collapsed = 1

        def _configure_warning_text(self):
            from System.Windows.Documents import Run
            from System.Windows.Media import Brushes, FontWeights

            try:
                self.warning_message.Inlines.Clear()
            except Exception:
                pass
            self.warning_message.Foreground = getattr(Brushes, "RoyalBlue", Brushes.Blue)
            for segment in build_warning_runs():
                run = Run(segment["text"])
                if segment.get("bold"):
                    run.FontWeight = FontWeights.Bold
                if segment.get("color") == RED_HEX:
                    red_brush = getattr(Brushes, "Red", None)
                    if red_brush is None:
                        red_brush = getattr(Brushes, "DarkRed", None)
                    if red_brush is not None:
                        run.Foreground = red_brush
                self.warning_message.Inlines.Add(run)

        def _make_choice_handler(self, choice):
            def _handler(sender, args):
                del sender, args
                result["value"] = choice
                self.Close()

            return _handler

    try:
        window = _TempPhaseWarningWindow()
        window.ShowDialog()
    except Exception as ex:
        _log("TempPhaseWarningWpfWindowUnavailable {0}".format(ex))
        return _WPF_UNAVAILABLE
    _log("TempPhaseWarningWpfWindowShown xaml={0}".format(xaml_path))
    return result.get("value", "cancel")


def _show_close_decision_direct(workshared=False, title=TITLE):
    """Show the rich WPF dialog.

    Returns ``save_close``, ``sync_close`` or ``cancel`` when WPF is shown.
    Returns ``None`` when WPF cannot be loaded so the caller can use its
    native Revit TaskDialog fallback.
    """
    try:
        import clr

        clr.AddReference("PresentationFramework")
        clr.AddReference("PresentationCore")
        clr.AddReference("WindowsBase")
        clr.AddReference("System.Drawing")

        from System.Windows import (
            HorizontalAlignment,
            ResizeMode,
            SizeToContent,
            Thickness,
            VerticalAlignment,
            Window,
            WindowStartupLocation,
        )
        from System.Windows.Controls import Button, Image, Orientation, StackPanel, TextBlock
        from System.Windows.Documents import Run
        from System.Windows.Media import Brushes, FontWeights

        window = Window()
        window.Title = title or TITLE
        window.SizeToContent = SizeToContent.WidthAndHeight
        window.WindowStartupLocation = WindowStartupLocation.CenterScreen
        window.ResizeMode = ResizeMode.NoResize
        window.ShowInTaskbar = False
        window.Topmost = True
        window.MinWidth = 560
        window.MaxWidth = 760

        root = StackPanel()
        root.Margin = Thickness(18)

        message_row = StackPanel()
        message_row.Orientation = Orientation.Horizontal
        warning_icon = build_warning_icon(Image, TextBlock, Brushes)
        if warning_icon is None:
            # Keep WPF available even if neither the native icon nor the
            # Unicode fallback could be created by an unusual host.
            warning_icon = TextBlock()
            warning_icon.Text = u"\u26a0"
            warning_icon.FontSize = 34
            warning_brush = getattr(Brushes, "DarkOrange", None)
            if warning_brush is None:
                warning_brush = getattr(Brushes, "Orange", None)
            if warning_brush is not None:
                warning_icon.Foreground = warning_brush
        message_row.Children.Add(warning_icon)

        message = TextBlock()
        message.TextWrapping = True
        # Keep the dialog compact even when WPF calculates its preferred
        # width from the message's unwrapped text.
        message.MaxWidth = 650
        message.VerticalAlignment = VerticalAlignment.Center
        message.Foreground = getattr(Brushes, "RoyalBlue", Brushes.Blue)
        for segment in build_warning_runs():
            run = Run(segment["text"])
            if segment.get("bold"):
                run.FontWeight = FontWeights.Bold
            color = segment.get("color")
            if color == RED_HEX:
                # Use the standard bright red brush.  The semantic run data
                # keeps the exact ``RED_HEX`` token for host-independent tests
                # and future theme-specific brush creation.
                red_brush = getattr(Brushes, "Red", None)
                if red_brush is None:
                    red_brush = getattr(Brushes, "DarkRed", None)
                if red_brush is not None:
                    run.Foreground = red_brush
            message.Inlines.Add(run)
        message_panel = StackPanel()
        message_panel.Margin = Thickness(14, 0, 0, 0)
        message_panel.Children.Add(message)
        message_row.Children.Add(message_panel)
        root.Children.Add(message_row)

        restored = TextBlock()
        restored.Text = RESTORED_MESSAGE
        restored.Margin = Thickness(50, 10, 0, 0)
        restored.Foreground = getattr(Brushes, "RoyalBlue", Brushes.Blue)
        restored.FontSize = 12
        root.Children.Add(restored)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 20, 0, 0)

        choice_holder = {"value": "cancel"}

        def choose(choice):
            def _handler(sender, args):
                del sender, args
                choice_holder["value"] = choice
                try:
                    window.DialogResult = True
                except Exception:
                    window.Close()

            return _handler

        action_pairs = action_labels(workshared)
        for index, (choice, label) in enumerate(action_pairs):
            button = Button()
            button.Content = label
            button.MinWidth = 118 if choice == "cancel" else 190
            button.Height = 32
            if index > 0:
                button.Margin = Thickness(8, 0, 0, 0)
            button.Click += choose(choice)
            # Keep File Open is the safe default and is also the Escape action.
            if choice == "cancel":
                button.IsDefault = True
                button.IsCancel = True
            buttons.Children.Add(button)

        root.Children.Add(buttons)
        window.Content = root
        window.ShowDialog()
        return choice_holder["value"]
    except Exception as ex:
        _log("TempPhaseWarningWpfDirectUnavailable {0}".format(ex))
        return None


def build_warning_icon(image_type=None, textblock_type=None, brushes=None):
    """Build a native warning icon, falling back to a visible warning glyph."""
    try:
        if image_type is None or textblock_type is None or brushes is None:
            import clr

            clr.AddReference("PresentationFramework")
            clr.AddReference("PresentationCore")
            clr.AddReference("System.Drawing")
            from System.Windows.Controls import Image, TextBlock
            from System.Windows.Media import Brushes

            image_type = Image
            textblock_type = TextBlock
            brushes = Brushes
        from System.Drawing import SystemIcons
        from System.Windows import Int32Rect, Thickness
        from System.Windows.Interop import Imaging
        from System.Windows.Media.Imaging import BitmapSizeOptions

        image = image_type()
        image.Source = Imaging.CreateBitmapSourceFromHIcon(
            SystemIcons.Warning.Handle,
            Int32Rect.Empty,
            BitmapSizeOptions.FromEmptyOptions(),
        )
        image.Width = 36
        image.Height = 36
        image.Margin = Thickness(0, 0, 0, 0)
        return image
    except Exception:
        if textblock_type is None or brushes is None:
            return None
        warning = textblock_type()
        warning.Text = u"\u26a0"
        warning.FontSize = 34
        try:
            from System.Windows import TextAlignment
            from System.Windows.Media import FontWeights

            warning.FontWeight = FontWeights.Bold
            warning.TextAlignment = TextAlignment.Center
        except Exception:
            pass
        warning_brush = getattr(brushes, "DarkOrange", None)
        if warning_brush is None:
            warning_brush = getattr(brushes, "Orange", None)
        if warning_brush is not None:
            warning.Foreground = warning_brush
        warning.Width = 40
        return warning


def _task_dialog_result_to_choice(result, UI, workshared=False):
    """Map native TaskDialogResult values to the shared semantic actions."""
    result_enum = getattr(UI, "TaskDialogResult", None)
    result_map = action_labels(workshared)
    for index, (choice, unused_label) in enumerate(result_map):
        del unused_label
        expected = getattr(result_enum, "CommandLink{0}".format(index + 1), None)
        if expected is not None:
            try:
                if result == expected:
                    return choice
            except Exception:
                pass
    result_text = str(result).lower() if result is not None else ""
    if "commandlink1" in result_text:
        return result_map[0][0]
    if "commandlink2" in result_text and len(result_map) > 1:
        return result_map[1][0]
    if "commandlink3" in result_text and len(result_map) > 2:
        return result_map[2][0]
    return "cancel"
