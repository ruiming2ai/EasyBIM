# -*- coding: utf-8 -*-
"""WPF windows for the Import Schedules from Excel command."""

# pylint: disable=import-error,invalid-name,broad-except
from pyrevit import forms


TITLE = "Import Schedules from Excel"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class ImportConflictWindow(forms.WPFWindow):
    """Hard-stop report: type-parameter conflicts, nothing importable."""

    def __init__(self, xaml_file_name, conflict_lines):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.conflicts_tb.Text = "\n".join(
            _safe_text(line) for line in conflict_lines or []
        )

    def close_click(self, sender, args):
        del sender, args
        self.Close()


class ImportPreviewWindow(forms.WPFWindow):
    """Dry-run summary shown before anything is written."""

    def __init__(self, xaml_file_name, file_path, preview_lines):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.result = None
        self.file_tb.Text = _safe_text(file_path)
        self.preview_tb.Text = "\n".join(
            _safe_text(line) for line in preview_lines or []
        )

    def import_click(self, sender, args):
        del sender, args
        self.result = "import"
        self.Close()

    def cancel_click(self, sender, args):
        del sender, args
        self.result = "cancel"
        self.Close()
