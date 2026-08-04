# -*- coding: utf-8 -*-
"""Add shared parameters to families, or bind them to project categories."""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import os
import sys

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from easybim.compat import exception_text

import load_parameters_revit as lp_revit
import load_parameters_state as state
import load_parameters_ui as ui


__title__ = "Load\nParameters"

LOGGER = script.get_logger()
TITLE = "Load Parameters"

MAIN_XAML = "LoadParametersWindow.xaml"
FILE_XAML = "SharedParameterFileWindow.xaml"
CONFIRM_XAML = "ConfirmWindow.xaml"
REPORT_XAML = "ReportWindow.xaml"

DIALOG_NOTE = (
    "Revit may still raise its own warning dialogs while families are "
    "reloaded; they cannot be suppressed from outside the reload."
)


class Context(object):
    """Everything the window needs from Revit, injected as plain callables.

    Keeps ``load_parameters_ui`` free of Revit imports, which is what lets the
    test suite parse and check the window classes off Revit.
    """

    def __init__(self, doc, app):
        self.doc = doc
        self.app = app
        self.group_choices = _group_choices()
        self._default_group = lp_revit.default_group_id()
        self.parameter_rows = self._project_parameters()
        self.category_rows = lp_revit.collect_categories(doc)
        self.last_folder = u""

    # -- sources ----------------------------------------------------------

    def _project_parameters(self):
        rows = lp_revit.collect_project_parameters(self.doc)
        known = set(choice.group_id for choice in self.group_choices)
        for row in rows:
            if row.group_id not in known:
                row.group_id = self._default_group
        return rows

    def collect_project_families(self):
        return lp_revit.collect_project_families(self.doc, self.app)

    def pick_output_folder(self):
        return lp_revit.pick_folder("Where should the updated .rfa files go?")

    def pick_folder_families(self, owner):
        del owner
        folder = lp_revit.pick_folder("Select a folder of family files.")
        if not folder:
            return [], None
        recursive = forms.alert(
            "Include family files in subfolders?",
            title=TITLE, yes=True, no=True)
        self.last_folder = folder
        rows = lp_revit.scan_family_folder(self.app, folder,
                                           recursive=bool(recursive))
        return rows, folder

    def pick_from_shared_file(self, owner):
        del owner
        path = forms.pick_file(files_filter="Shared parameter file (*.txt)|"
                                            "*.txt|All files (*.*)|*.*",
                               restore_dir=True)
        if not path:
            return []

        groups, error = lp_revit.read_shared_parameter_file(self.app, path)
        if error:
            forms.alert(error, title=TITLE)
            return []
        if not groups:
            forms.alert("That file holds no parameter definitions.",
                        title=TITLE)
            return []

        window = ui.SharedParameterFileWindow(FILE_XAML, path, groups)
        window.ShowDialog()
        picked = window.result or []
        if not picked:
            return []

        return self._reject_guid_conflicts(picked)

    def _reject_guid_conflicts(self, rows):
        """Refuse a pick that would duplicate a project parameter's name.

        Two shared parameters with one name and different GUIDs make schedules
        and filters pick whichever they like, and the result is miserable to
        diagnose - so it is blocked rather than warned about.
        """
        conflicts = state.guid_conflicts(
            rows, lp_revit.project_guids_by_name(self.doc))
        if not conflicts:
            return rows

        blocked = set(row.key for row, _ in conflicts)
        lines = []
        for row, existing in conflicts:
            lines.append(u"{0}\n    picked {1}\n    in model {2}".format(
                row.name, row.guid, existing))
        forms.alert(
            "{0} parameter(s) were not added: this model already has a "
            "different shared parameter with the same name.".format(
                len(conflicts)),
            expanded=u"\n".join(lines), title=TITLE)
        return [row for row in rows if row.key not in blocked]

    # -- running ----------------------------------------------------------

    def run(self, window, action):
        if action == state.ACTION_FAMILIES:
            return self._run_families(window)
        return self._run_project(window)

    def _usable_parameters(self, window):
        """Checked rows minus everything the plan refuses to write."""
        checked = state.checked_rows(window.parameter_rows)
        blocked = set()
        for row in checked:
            if not row.supported:
                blocked.add(row.key)
        for group in state.name_collisions(checked):
            for row in group:
                blocked.add(row.key)
        return [row for row in checked if row.key not in blocked]

    def _confirm(self, plan, mode, headline):
        confirm = ui.ConfirmWindow(
            CONFIRM_XAML,
            headline,
            state.build_confirm_text(plan, mode),
            needs_upgrade_ack=bool(plan.upgrade_files),
            needs_binding_ack=bool(plan.binding_flips),
            note=DIALOG_NOTE if plan.action == state.ACTION_FAMILIES else u"",
        )
        confirm.ShowDialog()
        return bool(confirm.result)

    def _report(self, summary, action):
        report = ui.ReportWindow(
            REPORT_XAML,
            state.build_headline(summary, action),
            state.build_report_text(summary, action))
        report.ShowDialog()

    def _run_families(self, window):
        families = [row for row in window.family_rows if row.is_checked]
        if not families:
            forms.alert("Check at least one family first.", title=TITLE)
            return None

        # Claim the family elements before anything is planned, so ownership
        # shows up as a named skip rather than a modal dialog per family.
        lp_revit.checkout_families(self.doc, families)

        params = self._usable_parameters(window)
        project_names = [row.name for row in families
                         if row.source == state.SOURCE_PROJECT]
        plan = state.build_family_plan(
            window.parameter_rows, window.family_rows, window.mode,
            project_family_names=project_names)

        if not plan.has_work():
            forms.alert("There is nothing to write with this selection.",
                        expanded=state.build_confirm_text(plan, window.mode),
                        title=TITLE)
            return None
        if not params:
            forms.alert("None of the checked parameters can be written.",
                        expanded=state.build_confirm_text(plan, window.mode),
                        title=TITLE)
            return None
        if not self._confirm(plan, window.mode,
                             "Write {0} parameter change(s) into {1} "
                             "family(ies)?".format(len(plan.work_cells),
                                                   len(plan.targets_with_work))):
            return None

        summary = state.RunSummary()
        with lp_revit.TempSharedParameterFile(self.app, params) as temp:
            if temp.error:
                forms.alert(temp.error, title=TITLE)
                return None
            self._write_families(window, families, params, temp, summary)

        self._report(summary, state.ACTION_FAMILIES)
        return summary

    def _write_families(self, window, families, params, temp, summary):
        project_rows = [row for row in families
                        if row.source == state.SOURCE_PROJECT and row.editable]
        folder_rows = [row for row in families
                       if row.source == state.SOURCE_FOLDER and row.editable]
        total = max(len(project_rows) + len(folder_rows), 1)
        offset = [0]

        with forms.ProgressBar(title="Load Parameters: {value} of {max_value}",
                               cancellable=True) as progress_bar:

            def _tick(index, _sub_total):
                progress_bar.update_progress(offset[0] + index + 1, total)
                return not progress_bar.cancelled

            lp_revit.load_into_project_families(
                self.doc, families, params, temp.definitions, window.mode,
                summary, progress=_tick)

            if summary.cancelled:
                return

            offset[0] = len(project_rows)
            lp_revit.load_into_folder_families(
                self.app, families, params, temp.definitions, window.mode,
                summary, output_folder=window.output_folder or None,
                progress=_tick)

    def _run_project(self, window):
        categories = [row for row in window.category_rows if row.is_checked]
        if not categories:
            forms.alert("Check at least one category first.", title=TITLE)
            return None

        params = self._usable_parameters(window)
        plan = state.build_project_plan(
            window.parameter_rows, window.category_rows,
            bound_lookup=lp_revit.binding_lookup(self.doc))

        if not plan.has_work() or not params:
            forms.alert("There is nothing to bind with this selection.",
                        expanded=state.build_confirm_text(plan, window.mode),
                        title=TITLE)
            return None
        if not self._confirm(plan, window.mode,
                             "Bind {0} parameter(s) to {1} "
                             "category(ies)?".format(len(params),
                                                     len(categories))):
            return None

        summary = state.RunSummary()
        with lp_revit.TempSharedParameterFile(self.app, params) as temp:
            if temp.error:
                forms.alert(temp.error, title=TITLE)
                return None
            with forms.ProgressBar(
                    title="Load Parameters: {value} of {max_value}",
                    cancellable=True) as progress_bar:

                def _tick(index, total):
                    progress_bar.update_progress(index + 1, max(total, 1))
                    return not progress_bar.cancelled

                lp_revit.load_into_project(
                    self.doc, self.app, params, window.category_rows,
                    temp.definitions, summary, progress=_tick)

        self._report(summary, state.ACTION_PROJECT)
        return summary


def _group_choices():
    choices = [state.GroupChoice(group_id, label)
               for group_id, label in lp_revit.group_choices()]
    if not choices:
        choices = [state.GroupChoice(lp_revit.default_group_id(), u"Data")]
    return choices


def _run():
    doc = revit.doc
    blocker = lp_revit.document_blocker(doc)
    if blocker:
        forms.alert(blocker, title=TITLE)
        return

    context = Context(doc, doc.Application)
    if not context.parameter_rows:
        forms.alert(
            "This model has no shared parameters yet. Use "
            "'Add from Shared Parameter File...' in the window to load some.",
            title=TITLE, warn_icon=False)

    window = ui.LoadParametersWindow(MAIN_XAML, context)
    window.ShowDialog()


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Load Parameters failed.")
    forms.alert("Load Parameters failed:\n{0}".format(
        exception_text(run_error)), title=TITLE)
