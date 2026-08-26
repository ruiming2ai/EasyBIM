# -*- coding: utf-8 -*-
"""Rebuild loadable families for an older Revit: export downgrade packages
here, rebuild .rfa files from packages in the older release."""

# pylint: disable=import-error,invalid-name,broad-except
import os
import sys
import time

import clr

clr.AddReference("RevitAPIUI")

from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI import TaskDialog

from pyrevit import forms
from pyrevit import revit
from pyrevit import script


SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# The family-selection pages, collectors and link cascade are shared with
# Families Transfer and live in lib/easybim/family_selection_*.
from easybim.compat import safe_text as _safe_text
from easybim.family_selection_revit import get_link_document_options
from easybim.family_selection_revit import get_link_family_options
from easybim.family_selection_revit import get_open_family_documents
from easybim.family_selection_revit import get_selected_family_options_from_selection
from easybim.family_selection_revit import get_source_family_options
from easybim.family_selection_revit import link_notes
from easybim.family_selection_revit import pick_folder
from easybim.family_selection_revit import pick_more_family_options
from easybim.family_selection_revit import prepare_link_documents
from easybim.family_selection_state import get_selected_family_keys
from easybim.family_selection_state import get_selected_source_family_options
from easybim.family_selection_state import merge_source_family_options
from easybim.family_selection_state import merge_transferable_family_options
from easybim.family_selection_state import prune_link_families_to_checked_links
from easybim.family_selection_state import restore_family_selection
from easybim.family_selection_state import split_selected_family_keys
from easybim.family_selection_ui import FAMILY_SELECTION_XAML
from easybim.family_selection_ui import FamilySelectionWindow
from easybim.family_selection_ui import LINK_SELECTION_XAML
from easybim.family_selection_ui import LinkSelectionWindow
from easybim.family_selection_ui import SOURCE_SELECTION_XAML
from easybim.family_selection_ui import SourceSelectionWindow

import families_downgrade_bridge as bridge
import families_downgrade_job as fdj
import families_downgrade_state as state
from families_downgrade_export import export_family_packages
from families_downgrade_rebuild import rebuild_family_packages
from families_downgrade_revit import host_version
from families_downgrade_revit import list_templates
from families_downgrade_ui import DowngradeOptionsWindow
from families_downgrade_ui import ExportOptionsWindow
from families_downgrade_ui import ModeWindow
from families_downgrade_ui import RebuildWindow


__title__ = "Families Downgrade"

LOGGER = script.get_logger()

MODE_XAML = "ModeWindow.xaml"
EXPORT_OPTIONS_XAML = "ExportOptionsWindow.xaml"
DOWNGRADE_OPTIONS_XAML = "DowngradeOptionsWindow.xaml"
REBUILD_XAML = "RebuildWindow.xaml"

RUNNER_SCRIPT = os.path.join(SCRIPT_DIR, "families_downgrade_runner.py")
LIB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "lib"))

STEP_MODE = "mode"
STEP_SOURCE = "source"
STEP_FAMILIES = "families"
STEP_LINK_DOCS = "link_docs"
STEP_LINK_FAMILIES = "link_families"
STEP_OPTIONS = "options"
STEP_REBUILD = "rebuild"

PICK_PROMPT = "Select family instances to include in Families Downgrade"
LINK_REFUSAL_HINT = "Its families cannot be packaged from here."


def _is_cancelled_pick(ex):
    if isinstance(ex, OperationCanceledException):
        return True
    return "cancel" in _safe_text(ex).lower()


def _get_uiapp():
    try:
        return __revit__
    except Exception:
        return None


def _application(uiapp, doc):
    app = getattr(uiapp, "Application", None)
    if app is not None:
        return app
    try:
        return doc.Application
    except Exception:
        return None


def _show_summary(summary):
    TaskDialog.Show(__title__, state.build_summary_text(summary))


def _with_progress(title, work):
    """Run a batch behind a cancellable progress bar.

    A family costs an EditFamily plus one geometry export per attribute
    group; a rebuild costs a template, an import and a FreeForm per body.
    Either is minutes of a frozen Revit unless the user can see it and stop.
    """
    with forms.ProgressBar(title=title + ": {value} of {max_value}",
                           cancellable=True) as progress_bar:

        def _tick(done, total):
            progress_bar.update_progress(done + 1, max(total, 1))
            return not progress_bar.cancelled

        return work(_tick)


def _confirm_replacements(folder, planned_names, noun):
    """One warning for the batch when the folder already holds any of the names.

    Returns True to go ahead. Both halves write with overwrite on, so
    without this a run would replace things silently.
    """
    existing = [name for name in planned_names
                if os.path.exists(os.path.join(folder, name))]
    if not existing:
        return True
    answer = forms.alert(
        state.build_overwrite_text(existing, len(planned_names), noun),
        title=__title__,
        options=["Replace them", "Go back"],
    )
    return answer == "Replace them"


def _with_child_progress(title, paths, total, work):
    """Watch another Revit work. Its progress file drives the bar, so the
    count is the child's real one rather than a spinner, and Cancel asks it
    to stop between families instead of killing a Revit mid-save."""
    with forms.ProgressBar(title=title + ": {value} of {max_value}",
                           cancellable=True) as progress_bar:

        def _tick(_seconds):
            done, child_total = bridge.read_progress(paths)
            maximum = max(child_total or total, 1)
            progress_bar.update_progress(min(done + 1, maximum), maximum)
            return not progress_bar.cancelled

        return work(_tick)


def _target_notes(cli_path, choices):
    notes = []
    if not choices:
        notes.append("No Revit installation was found on this computer.")
    if not cli_path:
        notes.append(
            "The pyRevit command line tool (pyrevit.exe) was not found, so another Revit "
            "cannot be opened from here. Only this Revit's own version can be produced; "
            "for the rest, use 'Write downgrade packages only' and rebuild them there.")
    return notes


def _rebuild_here(app, packages, output_folder):
    """The target is the Revit we are already in: no second Revit needed."""
    options = state.RebuildOptions("", output_folder)
    return _with_progress(
        "Rebuilding families",
        lambda tick: rebuild_family_packages(app, packages, options, progress=tick),
    )


def _rebuild_there(app, paths, packages, options, output_folder, cli_path, installs):
    """Hand the packages to the Revit the user picked. ``(summary, error)``."""
    version = options.target_version
    ok, reason = bridge.preflight(version, cli_path, installs)
    if not ok:
        return None, reason
    data = fdj.build_job(
        paths, output_folder, version,
        script_dir=SCRIPT_DIR, lib_dir=LIB_DIR,
        package_folders=[package.folder for package in packages],
        source_version=host_version(app), created=time.time())
    try:
        fdj.write_job(paths["job_path"], data)
    except Exception as error:
        return None, "The job for Revit {} could not be written: {}".format(version, error)
    # The environment variable has to survive the hop from the CLI into the
    # Revit it starts, which is not ours to guarantee; this is the fallback.
    bridge.write_fallback_job(data)

    timeout = fdj.estimate_timeout_seconds(len(packages))
    return _with_child_progress(
        "Rebuilding in Revit {}".format(version), paths, len(packages),
        lambda tick: bridge.run_downgrade(cli_path, version, RUNNER_SCRIPT, paths,
                                          on_tick=tick, timeout_seconds=timeout),
    )


def _finish_downgrade(paths, output_folder, summary, failure, target_version):
    """Clear up, then report - in that order, so the report can say where the
    packages ended up."""
    if failure:
        summary.add_note(failure)

    keep_run_folder = False
    if failure or summary.failed:
        answer = forms.alert(
            "The downgrade packages are still on disk. Keeping them lets you open Revit {} "
            "yourself and use 'Rebuild families from downgrade packages' on them.".format(
                target_version),
            title=__title__, options=["Keep the packages", "Delete them"])
        if answer == "Keep the packages":
            kept, note = bridge.rescue_packages(paths, output_folder)
            if kept:
                summary.add_note("The downgrade packages were kept at:\n{}".format(kept))
            if note:
                # The move failed, so they are still inside the run folder;
                # removing it now would take away what was just promised.
                summary.add_note(note)
                keep_run_folder = True

    if not keep_run_folder:
        note = bridge.discard_run_folder(paths)
        if note:
            summary.add_note(note)

    state.write_report(output_folder, summary, target_version)
    _show_summary(summary)


def _downgrade_step(doc, app, selected_families, previous, link_note_list):
    """Package the families out of sight and have the chosen Revit write them.

    Returns ``("back"|None, options)``.
    """
    cli_path = bridge.find_pyrevit_cli()
    installs = bridge.installed_revits(cli_path)
    host = host_version(app)
    choices = fdj.target_choices(installs, host)
    options = previous if isinstance(previous, state.DowngradeOptions) else state.DowngradeOptions()
    default = fdj.default_target(choices, host)
    if not options.target_version and default is not None:
        options.target_version = default.version

    window = DowngradeOptionsWindow(
        DOWNGRADE_OPTIONS_XAML, len(selected_families), pick_folder, choices,
        output_folder=options.output_folder, target_version=options.target_version,
        split_types=options.split_types, notes=_target_notes(cli_path, choices))
    window.ShowDialog()
    options = state.DowngradeOptions(window.output_folder, window.target_version,
                                     window.split_types)
    if window.result == "back":
        return "back", options
    if window.result != "downgrade":
        return None, options

    if not os.path.isdir(options.output_folder):
        try:
            os.makedirs(options.output_folder)
        except Exception as error:
            forms.alert("The output folder cannot be created:\n{}".format(error), title=__title__)
            return "back", options

    try:
        paths = bridge.create_run_folder()
    except Exception as error:
        forms.alert("A working folder for the downgrade could not be made:\n{}".format(error),
                    title=__title__)
        return "back", options

    # The packages go to a private folder, never to the user's output folder:
    # they asked for family files, not for the scaffolding.
    export_options = state.ExportOptions(paths["package_folder"], options.split_types)
    export_summary = _with_progress(
        "Preparing families",
        lambda tick: export_family_packages(doc, selected_families, export_options,
                                            progress=tick, app=app),
    )
    for note in link_note_list:
        export_summary.add_note(note)

    packages = state.find_packages(paths["package_folder"])
    if not packages:
        bridge.discard_run_folder(paths)
        export_summary.add_note("No family could be packaged, so nothing was rebuilt.")
        state.write_report(options.output_folder, export_summary, host)
        _show_summary(export_summary)
        return None, options

    planned = state.planned_rfa_filenames(packages)
    if not _confirm_replacements(options.output_folder, planned, "file(s)"):
        bridge.discard_run_folder(paths)
        return "back", options

    if fdj.version_number(options.target_version) == fdj.version_number(host):
        rebuild_summary, failure = _rebuild_here(app, packages, options.output_folder), ""
    else:
        rebuild_summary, failure = _rebuild_there(
            app, paths, packages, options, options.output_folder, cli_path, installs)

    summary = state.combine_downgrade_summaries(
        export_summary, rebuild_summary,
        expected_names=[package.family_name for package in packages]
        if rebuild_summary is not None else None)
    _finish_downgrade(paths, options.output_folder, summary, failure, options.target_version)
    return None, options


def _run_export(uidoc, doc, uiapp, app, previous_options, mode="export"):
    """Pick families, then either package them or downgrade them outright.

    The selection pages are the same either way; only the last step differs,
    so the cascade below is shared rather than copied.

    Returns ``"back"`` to reopen the mode page, ``None`` when finished.
    """
    selected_source_families = get_selected_family_options_from_selection(doc, uidoc)
    selected_project_family_keys = set(get_selected_family_keys(selected_source_families))
    selected_open_family_document_keys = set()
    open_family_documents = []
    families = []
    step = STEP_SOURCE
    options = previous_options or (state.DowngradeOptions() if mode == "downgrade"
                                   else state.ExportOptions())

    project_families_cache = [None]
    link_documents = [None]
    link_family_cache = {}
    selected_link_document_keys = set()
    selected_link_family_keys = set()
    selected_link_families = []

    def _project_families():
        if project_families_cache[0] is None:
            project_families_cache[0] = get_source_family_options(doc, selected_project_family_keys)
        return project_families_cache[0]

    def _link_documents():
        if link_documents[0] is None:
            link_documents[0] = get_link_document_options(doc, selected_link_document_keys)
        return link_documents[0]

    def _probed_links():
        return link_documents[0] or []

    def _read_link_families(window):
        keys = set(window.selected_link_family_keys)
        return keys, get_selected_source_family_options(selected_link_families, keys)

    while True:
        if step == STEP_SOURCE:
            selected_source_families = get_selected_source_family_options(
                selected_source_families, selected_project_family_keys)
            open_family_documents = get_open_family_documents(
                uiapp, doc, selected_open_family_document_keys)
            source_window = SourceSelectionWindow(
                SOURCE_SELECTION_XAML,
                selected_source_families,
                open_family_documents,
                selected_open_family_document_keys,
                link_families=selected_link_families,
                selected_link_family_keys=selected_link_family_keys,
                title=__title__,
                subtitle="Choose the families to package for an older Revit: from this "
                         "project, from opened .rfa files, and from Revit links.",
            )
            source_window.ShowDialog()

            if source_window.result == "load_more":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_family_keys, selected_link_families = _read_link_families(source_window)
                step = STEP_FAMILIES
                continue

            if source_window.result == "load_links":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_family_keys, selected_link_families = _read_link_families(source_window)
                step = STEP_LINK_DOCS
                continue

            if source_window.result == "next":
                selected_project_family_keys = set(source_window.selected_family_keys)
                selected_source_families = get_selected_source_family_options(
                    selected_source_families, selected_project_family_keys)
                selected_open_family_document_keys = set(source_window.selected_document_keys)
                selected_link_family_keys, selected_link_families = _read_link_families(source_window)
                open_family_documents = get_open_family_documents(
                    uiapp, doc, selected_open_family_document_keys)
                families = merge_transferable_family_options(
                    selected_source_families, open_family_documents, selected_link_families)
                if not get_selected_family_keys(families):
                    forms.alert(
                        "Select at least one family, opened .rfa file or link family before continuing.",
                        title=__title__)
                    continue
                step = STEP_OPTIONS
                continue
            return None

        if step == STEP_LINK_DOCS:
            if not _link_documents():
                forms.alert("No Revit links were found in this project.", title=__title__)
                step = STEP_SOURCE
                continue
            link_docs_window = LinkSelectionWindow(
                LINK_SELECTION_XAML, _link_documents(), selected_link_document_keys,
                title=__title__)
            link_docs_window.ShowDialog()
            if link_docs_window.result in ("next", "back"):
                selected_link_document_keys = set(link_docs_window.selected_link_document_keys)
                selected_link_families = prune_link_families_to_checked_links(
                    selected_link_families, selected_link_document_keys)
                selected_link_family_keys = set(get_selected_family_keys(selected_link_families))
                step = STEP_LINK_FAMILIES if link_docs_window.result == "next" else STEP_SOURCE
                continue
            return None

        if step == STEP_LINK_FAMILIES:
            prepare_link_documents(uiapp, doc, _link_documents(), refusal_hint=LINK_REFUSAL_HINT)
            link_families = get_link_family_options(
                _link_documents(), selected_link_family_keys, link_family_cache)
            refusals = link_notes(_link_documents())
            if not link_families:
                forms.alert(
                    "\n\n".join(["No families could be read from the checked Revit links."] + refusals),
                    title=__title__)
                step = STEP_LINK_DOCS
                continue
            links_window = FamilySelectionWindow(
                FAMILY_SELECTION_XAML, link_families, selected_link_family_keys,
                header_text="Load More from Revit Links", allow_model_pick=False,
                require_selection=False, title=__title__)
            links_window.ShowDialog()
            if links_window.result == "add":
                selected_link_family_keys = set(links_window.selected_family_keys)
                selected_link_families = get_selected_source_family_options(
                    link_families, selected_link_family_keys)
                step = STEP_SOURCE
                continue
            if links_window.result == "back":
                step = STEP_LINK_DOCS
                continue
            return None

        if step == STEP_FAMILIES:
            project_families = _project_families()
            restore_family_selection(project_families, selected_project_family_keys)
            open_family_documents = get_open_family_documents(
                uiapp, doc, selected_open_family_document_keys)
            browse_families = merge_transferable_family_options(project_families, open_family_documents)
            if not browse_families:
                forms.alert("No loadable families or selected opened .rfa files were found.",
                            title=__title__)
                return None
            family_window = FamilySelectionWindow(
                FAMILY_SELECTION_XAML, browse_families,
                set(get_selected_family_keys(browse_families)), title=__title__)
            family_window.ShowDialog()

            if family_window.result == "pick":
                pick_project_keys, pick_open_keys, _ = split_selected_family_keys(
                    set(family_window.selected_family_keys))
                selected_project_family_keys = pick_project_keys
                selected_open_family_document_keys = pick_open_keys
                try:
                    picked_families = pick_more_family_options(uidoc, PICK_PROMPT)
                except Exception as ex:
                    if _is_cancelled_pick(ex):
                        continue
                    forms.alert("Select failed: {}".format(ex), title=__title__)
                    return None
                selected_source_families = merge_source_family_options(
                    get_selected_source_family_options(
                        selected_source_families, selected_project_family_keys),
                    picked_families)
                selected_project_family_keys.update(get_selected_family_keys(picked_families))
                continue

            if family_window.result == "add":
                add_project_keys, add_open_keys, _ = split_selected_family_keys(
                    set(family_window.selected_family_keys))
                selected_source_families = merge_source_family_options(
                    get_selected_source_family_options(
                        selected_source_families, selected_project_family_keys),
                    get_selected_source_family_options(project_families, add_project_keys))
                selected_project_family_keys = set(get_selected_family_keys(selected_source_families))
                selected_open_family_document_keys.update(add_open_keys)
                step = STEP_SOURCE
                continue

            if family_window.result == "back":
                selected_source_families = get_selected_source_family_options(
                    selected_source_families, selected_project_family_keys)
                step = STEP_SOURCE
                continue
            return None

        if step == STEP_OPTIONS:
            selected_families = [family for family in families if family.is_selected]
            if mode == "downgrade":
                outcome, options = _downgrade_step(
                    doc, app, selected_families, options, link_notes(_probed_links()))
                if outcome == "back":
                    step = STEP_SOURCE
                    continue
                return None
            options_window = ExportOptionsWindow(
                EXPORT_OPTIONS_XAML, len(selected_families), pick_folder,
                folder=options.folder, split_types=options.split_types)
            options_window.ShowDialog()
            options = state.ExportOptions(options_window.folder, options_window.split_types)
            if options_window.result == "back":
                step = STEP_SOURCE
                continue
            if options_window.result != "export":
                return None
            if not os.path.isdir(options.folder):
                try:
                    os.makedirs(options.folder)
                except Exception as ex:
                    forms.alert("The folder cannot be created:\n{}".format(ex), title=__title__)
                    continue
            planned = state.planned_package_folder_names(selected_families)
            if not _confirm_replacements(options.folder, planned, "package(s)"):
                continue
            summary = _with_progress(
                "Writing downgrade packages",
                lambda tick: export_family_packages(
                    doc, selected_families, options, progress=tick, app=app),
            )
            for note in link_notes(_probed_links()):
                summary.add_note(note)
            state.write_report(options.folder, summary, host_version(app))
            _show_summary(summary)
            return None


def _run_rebuild(app, previous):
    """The rebuild half; returns ``"back"`` for the mode page, ``None`` when done."""
    package_folder, output_folder, packages = previous
    while True:
        window = RebuildWindow(
            REBUILD_XAML, pick_folder, host_version=host_version(app),
            package_folder=package_folder, output_folder=output_folder, packages=packages)
        window.ShowDialog()
        package_folder = window.package_folder
        output_folder = window.output_folder
        packages = window.packages
        if window.result == "back":
            return "back", (package_folder, output_folder, packages)
        if window.result != "rebuild":
            return None, (package_folder, output_folder, packages)
        selected = window.selected_packages
        if not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder)
            except Exception as ex:
                forms.alert("The output folder cannot be created:\n{}".format(ex), title=__title__)
                continue
        planned = state.planned_rfa_filenames(selected)
        if not _confirm_replacements(output_folder, planned, "file(s)"):
            continue
        rebuild_options = state.RebuildOptions(package_folder, output_folder)
        if not list_templates(getattr(app, "FamilyTemplatePath", "")):
            # Revit's own template folder is empty or unset (some installs,
            # some languages): ask once for a family template to build on.
            forms.alert(
                "Revit's family template folder is empty or not set, so there is nothing "
                "to build the families on. Choose a family template (.rft) - Generic Model "
                "is the safe choice - and it will be used for every package in this run.",
                title=__title__)
            template_path = forms.pick_file(file_ext="rft",
                                            title="Choose a family template (.rft)")
            if not template_path:
                continue
            rebuild_options.template_path = _safe_text(template_path)
        summary = _with_progress(
            "Rebuilding families",
            lambda tick: rebuild_family_packages(app, selected, rebuild_options, progress=tick),
        )
        state.write_report(output_folder, summary, host_version(app))
        _show_summary(summary)
        return None, (package_folder, output_folder, packages)


def _run():
    uiapp = _get_uiapp()
    doc = revit.doc
    uidoc = revit.uidoc
    app = _application(uiapp, doc)
    if app is None:
        forms.alert("Revit's application object is not available.", title=__title__)
        return

    export_available = doc is not None and uidoc is not None
    export_hint = ""
    if export_available:
        try:
            if bool(doc.IsFamilyDocument):
                export_available = False
                export_hint = ("Open a project document to export packages: the families are "
                               "picked from a project, from opened .rfa files and from links.")
        except Exception:
            pass
    else:
        export_hint = "Open a project document to export packages."

    export_options = None
    downgrade_options = None
    rebuild_state = ("", "", [])
    while True:
        mode_window = ModeWindow(MODE_XAML, host_version(app), export_available, export_hint)
        mode_window.ShowDialog()
        if mode_window.result == "downgrade":
            outcome = _run_export(uidoc, doc, uiapp, app, downgrade_options, "downgrade")
            if outcome == "back":
                continue
            return
        if mode_window.result == "export":
            outcome = _run_export(uidoc, doc, uiapp, app, export_options)
            if outcome == "back":
                continue
            return
        if mode_window.result == "rebuild":
            outcome, rebuild_state = _run_rebuild(app, rebuild_state)
            if outcome == "back":
                continue
            return
        return


try:
    _run()
except Exception as run_error:
    LOGGER.exception("Families Downgrade failed.")
    forms.alert("Families Downgrade failed:\n{}".format(run_error), title=__title__)
