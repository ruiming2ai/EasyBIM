# -*- coding: utf-8 -*-
"""Reuse a Revit link's sheets: same layout, aligned by model coordinates."""

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

import linked_sheets_transfer_revit as lst_revit
import linked_sheets_transfer_state as state
import linked_sheets_transfer_ui as ui


__title__ = "Linked Sheets\nTransfer"

LOGGER = script.get_logger()
TITLE = "Linked Sheets Transfer"

MAIN_XAML = "LinkedSheetsTransferWindow.xaml"
CONFIRM_XAML = "ConfirmWindow.xaml"
REPORT_XAML = "ReportWindow.xaml"


class LinkData(object):
    """Everything read out of one link, kept together so a reselect is cheap."""

    def __init__(self, option, sheet_rows, linked_levels, host_levels,
                 level_rows, host_facts):
        self.option = option
        self.sheet_rows = sheet_rows
        self.linked_levels = linked_levels
        self.host_levels = host_levels
        self.level_rows = level_rows
        self.host_facts = host_facts


class Context(object):
    """Everything the window needs from Revit, injected as plain callables.

    Keeps ``linked_sheets_transfer_ui`` free of Revit imports, which is what lets
    the test suite parse and check the window classes off Revit.
    """

    def __init__(self, doc, uidoc):
        self.doc = doc
        self.uidoc = uidoc
        self.link_options = lst_revit.collect_link_options(doc)

    # -- reading ----------------------------------------------------------

    def load_link(self, option):
        sheet_rows = lst_revit.collect_linked_sheets(option.doc)
        linked_levels = lst_revit.collect_linked_levels(option.doc,
                                                        option.transform)
        host_levels = lst_revit.collect_host_levels(self.doc)
        monitored = lst_revit.monitored_level_pairs(self.doc,
                                                    option.instance_key)
        level_rows = state.resolve_level_map(linked_levels, host_levels,
                                             monitored)
        return LinkData(option, sheet_rows, linked_levels, host_levels,
                        level_rows, lst_revit.collect_host_facts(self.doc))

    def refresh_host_facts(self, link_data):
        """A run creates sheets and views, so the collision set has moved on."""
        if link_data is not None:
            link_data.host_facts = lst_revit.collect_host_facts(self.doc)
        return link_data

    # -- running ----------------------------------------------------------

    def run(self, window):
        link_data = window.link_data
        if link_data is None:
            return None

        options = window.options
        plan = state.build_plan(window.sheet_rows, window.level_rows, options,
                                link_data.host_facts,
                                window.link_option.label)
        if not plan.has_work():
            forms.alert(u"Nothing can be created with this selection.",
                        expanded=state.build_confirm_text(plan), title=TITLE)
            return None

        if not self._confirm(plan, options):
            return None

        level_lookup = lst_revit.build_level_lookup(
            window.level_rows, link_data.linked_levels, link_data.host_levels)

        with forms.ProgressBar(
                title="Linked Sheets Transfer: {value} of {max_value}",
                cancellable=True) as progress_bar:

            def _tick(index, total):
                progress_bar.update_progress(index + 1, max(total, 1))
                return not progress_bar.cancelled

            summary = lst_revit.run_copy(self.doc, window.link_option, plan,
                                         level_lookup, progress=_tick)

        self._report(summary)
        return summary

    def _confirm(self, plan, options):
        confirm = ui.ConfirmWindow(
            CONFIRM_XAML,
            state.build_confirm_headline(plan),
            state.build_confirm_text(plan),
            needs_model_change_ack=bool(plan.model_changes),
            needs_double_draw_ack=bool(
                options.get(state.OPT_VIEW_ANNOTATIONS)))
        confirm.ShowDialog()
        return bool(confirm.result)

    def _report(self, summary):
        report = ui.ReportWindow(
            REPORT_XAML,
            state.build_report_headline(summary),
            state.build_report_text(summary),
            can_open_sheet=summary.first_sheet_key is not None)
        report.ShowDialog()
        # Opened only now: Revit refuses to change the active view while a
        # modal dialog is open.
        if report.open_requested and summary.first_sheet_key is not None:
            lst_revit.open_sheet_by_key(self.uidoc, summary.first_sheet_key)


def _run():
    doc = revit.doc
    blocker = lst_revit.document_blocker(doc)
    if blocker:
        forms.alert(blocker, title=TITLE)
        return

    context = Context(doc, revit.uidoc)
    if not context.link_options:
        forms.alert(
            u"This model has no loaded Revit links. Load the link whose "
            u"sheets you want to reuse, then run this again.",
            title=TITLE)
        return

    usable = [option for option in context.link_options if option.is_usable]
    if not usable:
        forms.alert(
            u"None of the loaded links can be reproduced as plan views:\n\n"
            + u"\n".join(u"  {0}".format(option.display)
                         for option in context.link_options),
            title=TITLE)
        return

    window = ui.LinkedSheetsTransferWindow(MAIN_XAML, context)
    window.ShowDialog()


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Linked Sheets Transfer failed.")
    forms.alert("Linked Sheets Transfer failed:\n{0}".format(
        exception_text(run_error)), title=TITLE)
