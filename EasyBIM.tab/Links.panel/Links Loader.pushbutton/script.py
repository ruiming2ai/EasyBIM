# -*- coding: utf-8 -*-
"""Export and import Revit link file paths as a JSON file."""

import io
import json
import os
import sys

from pyrevit import forms
from pyrevit import revit
from pyrevit import script

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

import links_loader_state as state
import links_loader_revit as ll_revit
import links_loader_ui as ui

__title__ = "Links Loader"
LOGGER = script.get_logger()


def _do_export(doc, link_records):
    file_path = forms.save_file(
        file_ext="json",
        default_name="{}-links.json".format(
            ll_revit.get_project_name(doc) or "project"
        ),
    )
    if not file_path:
        return

    data = state.build_export_data(
        link_records,
        ll_revit.get_host_version(doc),
        ll_revit.get_project_name(doc),
    )
    try:
        text = state.dump_json(data)
        with io.open(file_path, "w", encoding="utf-8") as fh:
            fh.write(
                text if isinstance(text, type(u"")) else text.decode("utf-8")
            )
    except Exception as ex:
        forms.alert(
            "Could not write file:\n{}".format(ex), title=__title__
        )
        return

    forms.alert(
        "Exported {} link(s) to:\n{}".format(len(link_records), file_path),
        title=__title__,
        warn_icon=False,
    )


def _do_import(doc, current_records, link_elements):
    file_path = forms.pick_file(
        files_filter="Links Loader JSON (*.json)|*.json"
                     "|All files (*.*)|*.*",
        restore_dir=True,
    )
    if not file_path:
        return

    try:
        with io.open(file_path, "r", encoding="utf-8-sig") as fh:
            raw = json.loads(fh.read() or "{}")
    except Exception as ex:
        forms.alert(
            "Could not read file:\n{}".format(ex), title=__title__
        )
        return

    imported, error = state.parse_import_data(raw)
    if error:
        forms.alert(error, title=__title__)
        return

    plan = state.build_import_plan(current_records, imported)

    for item in plan:
        if item.status == "update" and not state.check_file_exists(
            item.new_path
        ):
            item.file_missing = True

    preview = ui.ImportPreviewWindow(
        "ImportPreviewWindow.xaml", plan, file_path
    )
    preview.ShowDialog()
    if preview.result != "apply":
        return

    selected = preview.selected_items
    if not selected:
        return

    lookup = ll_revit.build_link_lookup(current_records, link_elements)
    results = ll_revit.apply_import_plan(doc, selected, lookup)
    summary = state.build_result_summary(results)
    forms.alert(summary, title=__title__, warn_icon=False)


def _run():
    doc = revit.doc
    if doc is None:
        forms.alert(
            "Open a project document first.", title=__title__
        )
        return

    link_records, link_elements = ll_revit.collect_link_records(doc)

    window = ui.LinksLoaderWindow(
        "LinksLoaderWindow.xaml", link_records, bool(link_records)
    )
    window.ShowDialog()

    if window.result == "export":
        _do_export(doc, link_records)
    elif window.result == "import":
        _do_import(doc, link_records, link_elements)


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Links Loader failed.")
    forms.alert(
        "Links Loader failed:\n{}".format(run_error), title=__title__
    )
