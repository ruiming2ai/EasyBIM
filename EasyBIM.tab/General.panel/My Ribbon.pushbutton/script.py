# -*- coding: utf-8 -*-
"""Pick buttons from other pyRevit extensions and put them on panels of your own."""
# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

import copy
import io
import json
import os
import sys

from pyrevit import forms, script

SCRIPT_DIR = os.path.dirname(__file__)
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from easybim import my_ribbon  # noqa: E402
import my_ribbon_host as host  # noqa: E402
import my_ribbon_state as state  # noqa: E402
from my_ribbon_ui import (  # noqa: E402
    ButtonSelectionWindow,
    CredentialsWindow,
    DestinationWindow,
    DynamoButtonWindow,
    ImportPreviewWindow,
    MyRibbonWindow,
    SourceSelectionWindow,
)

__title__ = "My Ribbon"
LOGGER = script.get_logger()

STATUS_LOADED = "loaded"
STATUS_NEEDS_RELOAD = "needs pyRevit reload"
STATUS_NOT_INSTALLED = "not installed here"
STATUS_NEEDS_APPLY = "needs Apply"
STATUS_MISSING_TAB = "tab is not on the ribbon"
STATUS_GRAPH_MISSING = "graph file missing (last copy kept)"


# -- helpers ----------------------------------------------------------------------


def _source_dir(source):
    kind = source.get("kind")
    if kind == "dynamo":
        folder = os.path.dirname(state.safe_text(source.get("path")))
        return folder if folder and os.path.isdir(folder) else None
    if kind == "ribbon":
        return None
    if source.get("extra_root"):
        return source["extra_root"] if os.path.isdir(source["extra_root"]) else None
    return host.find_installed_extension_dir(source.get("ext_name"))


def _ribbon_has_tab(summary, tab_names):
    wanted = set(state.normalize_label(t) for t in (tab_names or []))
    return any(state.normalize_label(t.get("title")) in wanted or
               state.normalize_label(t.get("id")) in wanted for t in summary)


def _source_status(working, summary):
    status = {}
    for source in working.get("sources", []):
        kind = source.get("kind")
        if kind == "ribbon":
            status[source["id"]] = STATUS_LOADED if _ribbon_has_tab(summary, source.get("tab_names")) \
                else STATUS_MISSING_TAB
        elif kind == "dynamo":
            graph = host.dynamo_graph_status(source)
            if graph == "no-bundle":
                status[source["id"]] = STATUS_NEEDS_APPLY
            elif graph == "missing":
                status[source["id"]] = STATUS_GRAPH_MISSING
            elif not _ribbon_has_tab(summary, [my_ribbon.LIBRARY_TAB]):
                status[source["id"]] = STATUS_NEEDS_RELOAD
            else:
                status[source["id"]] = STATUS_LOADED
        elif _source_dir(source) is None:
            status[source["id"]] = STATUS_NOT_INSTALLED
        elif source.get("tab_names") and not _ribbon_has_tab(summary, source.get("tab_names")):
            status[source["id"]] = STATUS_NEEDS_RELOAD
        else:
            status[source["id"]] = STATUS_LOADED
    return status


def _open_folder(source):
    folder = _source_dir(source)
    if not folder:
        return False
    return host.show_folder(folder)


def _placement_issues(report):
    issues = {}
    for miss in (report or {}).get("missing", []):
        issues[miss.get("placement")] = miss.get("reason")
    return issues


def _describe_installed_dir(ext_dir, label):
    """Parse one extension folder behind an indeterminate progress bar."""
    with forms.ProgressBar(title="Reading {0}...".format(label), indeterminate=True):
        return host.parse_extension_dir(ext_dir)


def _ext_name_of(ext_dir):
    return state.strip_extension_suffix(os.path.basename(ext_dir.rstrip("\\/")))


def _dynamo_described(source):
    """A one-button 'extension' for the picker and the placement path."""
    name = state.strip_extension_suffix(state.safe_text(source.get("bundle")))
    if name.lower().endswith(".pushbutton"):
        name = name[:-len(".pushbutton")]
    title = source.get("title") or source.get("label") or name
    button = {
        "kind": "button", "name": name, "title": title,
        "tooltip": "Dynamo graph: {0}".format(source.get("path")),
        "icon": source.get("icon") or host.DEFAULT_DYNAMO_ICON,
        "control_id": "CustomCtrl_%CustomCtrl_%{0}%{1}%{2}".format(
            my_ribbon.LIBRARY_TAB, my_ribbon.LIBRARY_DYNAMO_PANEL, name),
        "path": [{"name": my_ribbon.LIBRARY_TAB, "title": my_ribbon.LIBRARY_TAB},
                 {"name": my_ribbon.LIBRARY_DYNAMO_PANEL, "title": my_ribbon.LIBRARY_DYNAMO_PANEL},
                 {"name": name, "title": title}],
        "min_revit": None, "max_revit": None, "in_layout": True, "children": [],
    }
    return {"name": title, "dir": "", "tab_names": [my_ribbon.LIBRARY_TAB],
            "tabs": [{"name": my_ribbon.LIBRARY_TAB, "title": my_ribbon.LIBRARY_TAB,
                      "panels": [{"name": my_ribbon.LIBRARY_DYNAMO_PANEL,
                                  "title": my_ribbon.LIBRARY_DYNAMO_PANEL, "items": [button]}]}],
            "buttons": [button], "has_startup": False, "has_hooks": False}


def _other_ribbon_tabs(summary, installed):
    """Tabs that are neither a pyRevit extension's, nor ours, nor contextual."""
    taken = set()
    for ext in installed or []:
        for name in ext.get("tab_names") or []:
            taken.add(state.normalize_label(name))
    taken.add(state.normalize_label(my_ribbon.LIBRARY_TAB))
    others = []
    for tab in summary:
        key = state.normalize_label(tab.get("title"))
        if key in taken or tab.get("is_ours") or tab.get("is_contextual"):
            continue
        if state.normalize_label(tab.get("id")) in taken:
            continue
        others.append(tab)
    return others


# -- downloading -----------------------------------------------------------------------


def _ask_credentials(message):
    window = CredentialsWindow("CredentialsWindow.xaml", message)
    window.ShowDialog()
    return window.result


def _clone_with_progress(ref, branch, credentials):
    """Clone into the temp folder; returns the temp path or None (cancelled)."""
    temp_root = host.temp_clone_root()
    temp_dir = os.path.join(temp_root, "download")
    host.remove_tree(temp_dir)
    if not os.path.isdir(temp_root):
        os.makedirs(temp_root)
    username, password = credentials if credentials else (None, None)
    title = "Downloading {0} - {{value}} of {{max_value}} objects".format(ref.label)
    with forms.ProgressBar(title=title, cancellable=True) as progress_bar:
        def _progress(received, total):
            progress_bar.update_progress(received, max(total, 1))
            return not progress_bar.cancelled
        host.clone_repository(ref.clone_url, temp_dir, branch=branch, username=username,
                              password=password, progress=_progress)
    return temp_dir


def _find_already_installed(ref):
    """An installed extension whose git remote is this repository, if any."""
    for name in host.installed_extension_names():
        ext_dir = host.find_installed_extension_dir(name)
        if not ext_dir:
            continue
        remote = host.remote_url(ext_dir)
        if not remote:
            continue
        other, _ = state.parse_git_url(remote)
        if other is not None and other.key.split("@")[0] == ref.key.split("@")[0]:
            return name, ext_dir
    return None, None


def _download_git(link, branch):
    """Download a repository and return ``(sources, described_by_source_id)``.

    ``sources`` are source dicts ready for ``state.add_source`` (no ids yet);
    an empty list means nothing was added (cancelled or failed - the user has
    already been told).
    """
    ref, error = state.parse_git_url(link)
    if ref is None:
        forms.alert(error, title=__title__)
        return [], {}
    branch = branch or ref.branch

    existing_name, existing_dir = _find_already_installed(ref)
    if existing_dir:
        try:
            described = _describe_installed_dir(existing_dir, existing_name)
        except Exception as ex:
            forms.alert("{0} is already installed here as {1}, but it could not be read:\n{2}"
                        .format(ref.label, existing_name, ex), title=__title__)
            return [], {}
        source = {"kind": "git", "url": ref.web_url, "branch": branch, "ext_name": existing_name,
                  "label": ref.label, "tab_names": described["tab_names"],
                  "installed_by_my_ribbon": False, "hide_tab": False}
        forms.alert("{0} is already installed here as {1}, so it is used as it is; nothing was "
                    "downloaded.".format(ref.label, existing_name), title=__title__, warn_icon=False)
        return [source], {0: described}

    ok, status, message = host.probe_url(ref.web_url)
    credentials = None
    if not ok:
        if status in (401, 403, 404):
            if not forms.alert(message + "\n\nSign in and try to download anyway?",
                               title=__title__, yes=True, no=True):
                return [], {}
            credentials = _ask_credentials(message)
            if not credentials:
                return [], {}
        else:
            forms.alert(message, title=__title__)
            return [], {}

    temp_dir = None
    for _attempt in range(3):
        try:
            temp_dir = _clone_with_progress(ref, branch, credentials)
            break
        except host.CloneCancelled:
            host.cleanup_temp_root()
            return [], {}
        except host.CloneAuthError as ex:
            credentials = _ask_credentials(
                "The server refused the download ({0}).".format(ex))
            if not credentials:
                host.cleanup_temp_root()
                return [], {}
        except Exception as ex:
            host.cleanup_temp_root()
            forms.alert("The download failed:\n{0}".format(ex), title=__title__)
            return [], {}
    if temp_dir is None:
        host.cleanup_temp_root()
        return [], {}

    plan = host.plan_clone_destination(ref, temp_dir, host.existing_extension_dir_names())
    if plan["kind"] == "none":
        host.cleanup_temp_root()
        forms.alert(plan["message"], title=__title__)
        return [], {}
    if os.path.isdir(plan["final_dir"]):
        if plan["kind"] == "repository":
            # a clone left behind by an earlier removal that could not delete
            # it: replace it, never nest the new download inside it
            host.remove_tree(plan["final_dir"])
        if os.path.isdir(plan["final_dir"]):
            host.cleanup_temp_root()
            forms.alert("A folder already exists where the download would go:\n{0}\n\nRemove or "
                        "rename it and try again.".format(plan["final_dir"]), title=__title__)
            return [], {}
    try:
        host.move_into_place(temp_dir, plan["final_dir"])
    except Exception as ex:
        host.cleanup_temp_root()
        forms.alert("The download could not be moved into place:\n{0}".format(ex), title=__title__)
        return [], {}
    host.cleanup_temp_root()

    for root in plan["register_roots"]:
        ok, message = host.register_extension_root(root)
        if not ok:
            forms.alert("The repository was downloaded to {0}, but pyRevit could not be told to load "
                        "the extensions inside it:\n{1}".format(plan["final_dir"], message),
                        title=__title__)

    sources = []
    described_by_index = {}
    for index, ext_dir in enumerate(plan["ext_dirs"]):
        name = _ext_name_of(ext_dir)
        try:
            described = _describe_installed_dir(ext_dir, name)
        except Exception as ex:
            LOGGER.debug("Parse failed for %s: %s", ext_dir, ex)
            described = {"name": name, "dir": ext_dir, "tab_names": [], "tabs": [], "buttons": [],
                         "has_startup": False, "has_hooks": False}
        extra_root = os.path.dirname(ext_dir) if plan["kind"] == "repository" else None
        sources.append({"kind": "git", "url": ref.web_url, "branch": branch, "ext_name": name,
                        "label": ref.label if len(plan["ext_dirs"]) == 1
                        else "{0} ({1})".format(ref.label, name),
                        "tab_names": described["tab_names"], "installed_by_my_ribbon": True,
                        "hide_tab": True, "extra_root": extra_root})
        described_by_index[index] = described
    return sources, described_by_index


def _install_from_catalogue(row):
    """Install (or reuse) a catalogue entry; returns ``(source, described)``."""
    package = row.get("package")
    installed_dir = None
    try:
        installed_dir = getattr(package, "installed_dir", "") or None
    except Exception:
        installed_dir = None
    freshly_installed = False
    if not installed_dir:
        try:
            with forms.ProgressBar(title="Installing {0}...".format(row.get("name")),
                                   indeterminate=True):
                installed_dir = host.install_catalogue_package(package)
        except Exception as ex:
            forms.alert("{0} could not be installed:\n{1}".format(row.get("name"), ex),
                        title=__title__)
            return None, None
        freshly_installed = True
    if not installed_dir or not os.path.isdir(installed_dir):
        forms.alert("{0} could not be installed. pyRevit's own Extensions window may say why."
                    .format(row.get("name")), title=__title__)
        return None, None
    name = _ext_name_of(installed_dir)
    try:
        described = _describe_installed_dir(installed_dir, name)
    except Exception as ex:
        forms.alert("{0} was installed but could not be read:\n{1}".format(row.get("name"), ex),
                    title=__title__)
        return None, None
    source = {"kind": "catalogue", "name": row.get("name"), "url": row.get("url"),
              "ext_name": name, "label": row.get("name"), "tab_names": described["tab_names"],
              "installed_by_my_ribbon": freshly_installed, "hide_tab": freshly_installed}
    return source, described


# -- picking and placing -------------------------------------------------------------------


def _pick_and_place(working, source, described):
    """Which buttons? -> Where to?  Stages placements into ``working``."""
    already = {}
    label_by_dest = dict((d["id"], u"{0} > {1}".format(d["tab"], d["panel"]))
                         for d in working.get("destinations", []))
    for placement in working.get("placements", []):
        if placement.get("source") == source["id"]:
            already[state.path_key(placement.get("path"))] = label_by_dest.get(
                placement.get("dest"), "?")
    preselected = []
    while True:
        picker = ButtonSelectionWindow("ButtonSelectionWindow.xaml",
                                       source.get("label") or source.get("ext_name"), described,
                                       already_placed=already, host_version=host.host_version(),
                                       preselected_paths=preselected)
        picker.ShowDialog()
        if picker.result != "next":
            return False
        selected = picker.selected
        preselected = [state.path_key(b.get("path")) for b in selected]
        chooser = DestinationWindow("DestinationWindow.xaml", my_ribbon.list_ribbon(), working,
                                    mode="add", count=len(selected))
        chooser.ShowDialog()
        if chooser.result == "back":
            continue
        if not chooser.result:
            return False
        tab, panel, own_tab = chooser.result
        dest = state.add_destination(working, tab, panel, own_tab)
        for button in selected:
            state.add_placement(working, source["id"], dest["id"], button)
        return True


def _forget_pending_delete(pending_deletes, source):
    """A source downloaded again in the same session must not be deleted by the
    Remove that came before it."""
    key = state.normalize_label(source.get("ext_name"))
    keep = [s for s in pending_deletes
            if state.normalize_label(s.get("ext_name")) != key
            and not (s.get("extra_root") and s.get("extra_root") == source.get("extra_root"))]
    del pending_deletes[:]
    pending_deletes.extend(keep)


def _add_source_flow(working, pending_deletes):
    """Where from? -> parse -> Which buttons? -> Where to?

    ``pending_deletes`` is untouched here now that nothing is installed from
    this window; it stays in the signature because Import still downloads.
    """
    del pending_deletes
    installed = None
    while True:
        if installed is None:
            with forms.ProgressBar(title="Reading installed extensions...", indeterminate=True):
                try:
                    installed = host.installed_extensions()
                except Exception as ex:
                    LOGGER.debug("installed_extensions failed: %s", ex)
                    installed = []
        summary = my_ribbon.list_ribbon()
        linked = [s.get("ext_name") for s in working.get("sources", [])] + \
                 [s.get("label") for s in working.get("sources", [])]
        page = SourceSelectionWindow("SourceSelectionWindow.xaml", installed,
                                     ribbon_tabs=_other_ribbon_tabs(summary, installed),
                                     linked_names=linked)
        page.ShowDialog()
        if not page.result:
            return None
        kind = page.result[0]
        if kind == "ribbon":
            tab = page.result[1]
            described = my_ribbon.describe_tab_by_title(tab.get("title"))
            if not described or not described.get("buttons"):
                forms.alert("No buttons could be read from the tab '{0}'.".format(tab.get("title")),
                            title=__title__)
                continue
            entry = state.add_source(working, {
                "kind": "ribbon", "ext_name": tab.get("title"), "label": "{0} (tab)".format(tab.get("title")),
                "tab_names": [tab.get("title")], "installed_by_my_ribbon": False, "hide_tab": False})
            _pick_and_place(working, entry, described)
            return None
        if kind == "dynamo":
            return _add_dynamo_flow(working, page.result[1])
        if kind == "installed":
            ext = page.result[1]
            entry = state.add_source(working, {
                "kind": "installed", "ext_name": ext.get("name"), "label": ext.get("name"),
                "tab_names": ext.get("tab_names"), "installed_by_my_ribbon": False,
                "hide_tab": False})
            _pick_and_place(working, entry, ext)
            return None


def _add_dynamo_flow(working, paths):
    """One window per graph (title, icon), then one Where-to for all of them."""
    added = []
    remaining = list(paths or [])
    existing_bundles = host.existing_dynamo_bundle_names() + \
        [s.get("bundle") for s in working.get("sources", []) if s.get("kind") == "dynamo"]
    while remaining:
        path = remaining.pop(0)
        facts = host.read_dynamo_facts(path)
        if facts.get("problem"):
            forms.alert("{0}\n\n{1}".format(os.path.basename(path), facts["problem"]), title=__title__)
            continue
        already = state.find_source(working, {"kind": "dynamo", "path": path})
        if already is not None:
            forms.alert("{0} is already a source ({1}).".format(os.path.basename(path),
                                                                already.get("title")), title=__title__)
            continue
        default_title = facts.get("name") or os.path.splitext(os.path.basename(path))[0]
        window = DynamoButtonWindow("DynamoButtonWindow.xaml", path, facts, default_title,
                                    remaining=len(remaining))
        window.ShowDialog()
        if window.result is None:
            break
        if window.result == "skip":
            continue
        title = window.result["title"]
        bundle = state.dynamo_bundle_name(title, existing_bundles)
        existing_bundles.append(bundle)
        entry = state.add_source(working, {
            "kind": "dynamo", "path": path, "title": title, "label": title, "bundle": bundle,
            "icon": window.result.get("icon"), "ext_name": my_ribbon.LIBRARY_EXTENSION_NAME,
            "tab_names": [my_ribbon.LIBRARY_TAB], "installed_by_my_ribbon": True, "hide_tab": False})
        added.append(entry)
    if not added:
        return None
    # the library tab is never meant to be seen
    state.set_tabs_hidden(working, [my_ribbon.LIBRARY_TAB], True)
    chooser = DestinationWindow("DestinationWindow.xaml", my_ribbon.list_ribbon(), working,
                                mode="add", count=len(added))
    chooser.ShowDialog()
    if chooser.result and chooser.result != "back":
        tab, panel, own_tab = chooser.result
        dest = state.add_destination(working, tab, panel, own_tab)
        for entry in added:
            button = _dynamo_described(entry)["buttons"][0]
            state.add_placement(working, entry["id"], dest["id"], button)
    return "Added {0} Dynamo graph{1}. Press Apply; the button{1} appear after a pyRevit " \
           "reload.".format(len(added), "" if len(added) == 1 else "s")


def _add_buttons_flow(working, source_id):
    source = state.find_source_by_id(working, source_id)
    if source is None:
        return
    if source.get("kind") == "dynamo":
        _pick_and_place(working, source, _dynamo_described(source))
        return
    if source.get("kind") == "ribbon":
        described = my_ribbon.describe_tab_by_title(source.get("ext_name"))
        if not described:
            forms.alert("The tab '{0}' is not on the ribbon right now.".format(source.get("ext_name")),
                        title=__title__)
            return
        _pick_and_place(working, source, described)
        return
    ext_dir = _source_dir(source)
    if source.get("extra_root"):
        # a repository holding extensions: parse the one this source names
        candidate = os.path.join(source["extra_root"], source.get("ext_name") + ".extension")
        ext_dir = candidate if os.path.isdir(candidate) else None
    if not ext_dir:
        forms.alert("{0} is not installed on this computer, so its buttons cannot be listed. "
                    "Install it (or download it again) first.".format(
                        source.get("label") or source.get("ext_name")), title=__title__)
        return
    try:
        described = _describe_installed_dir(ext_dir, source.get("label") or source.get("ext_name"))
    except Exception as ex:
        forms.alert("The extension could not be read:\n{0}".format(ex), title=__title__)
        return
    if described.get("tab_names"):
        source["tab_names"] = described["tab_names"]
    _pick_and_place(working, source, described)


# -- export / import ---------------------------------------------------------------------------


def _export(working):
    path = forms.save_file(file_ext="json", default_name="my-ribbon.json",
                           title="Export My Ribbon settings")
    if not path:
        return None
    document = state.export_document(working)
    try:
        text = json.dumps(document, indent=2, sort_keys=True)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text if isinstance(text, type(u"")) else text.decode("utf-8"))
    except Exception as ex:
        forms.alert("Could not write {0}:\n{1}".format(path, ex), title=__title__)
        return None
    return "Exported to {0}".format(path)


def _import(working, pending_deletes):
    path = forms.pick_file(file_ext="json", title="Import My Ribbon settings")
    if not path:
        return None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as handle:
            raw = json.loads(handle.read() or "{}")
    except Exception as ex:
        forms.alert("Could not read {0}:\n{1}".format(path, ex), title=__title__)
        return None
    try:
        incoming = my_ribbon.read_registry(raw)
    except my_ribbon.RegistryFormatError as ex:
        forms.alert(state.safe_text(ex), title=__title__)
        return None
    installed = host.installed_extension_names()
    plans = dict((mode, state.plan_import(working, incoming, mode, installed_ext_names=installed))
                 for mode in ("merge", "replace"))
    previews = dict((mode, state.format_import_preview(plan)) for mode, plan in plans.items())
    window = ImportPreviewWindow("ImportPreviewWindow.xaml", path, previews)
    window.ShowDialog()
    if not window.result:
        return None
    plan = plans[window.result]
    result = plan["result"]

    # download what this computer lacks; failures leave the source in place
    # so its buttons show as missing until it is installed
    downloaded = 0
    for source in list(result.get("sources", [])):
        if source.get("kind") == "installed":
            continue
        if _source_dir(source) is not None:
            continue
        if source.get("kind") == "git" and source.get("url"):
            fresh, _ = _download_git(source.get("url"), source.get("branch"))
            if fresh:
                # what a download made *here* says wins over the file, and
                # a stale extra_root from the file must not survive
                for field in ("ext_name", "tab_names", "extra_root", "installed_by_my_ribbon"):
                    source[field] = fresh[0].get(field)
                _forget_pending_delete(pending_deletes, source)
                downloaded += 1
        elif source.get("kind") == "catalogue":
            try:
                rows = host.catalogue_packages(installed_names=installed)
            except Exception:
                rows = []
            match = [r for r in rows if state.normalize_label(r.get("name")) ==
                     state.normalize_label(source.get("name") or source.get("ext_name"))]
            if match:
                fresh, _ = _install_from_catalogue(match[0])
                if fresh:
                    for field in ("ext_name", "tab_names", "installed_by_my_ribbon"):
                        source[field] = fresh[field]
                    _forget_pending_delete(pending_deletes, source)
                    downloaded += 1
    working.clear()
    working.update(result)
    return "Imported {0}: {1} button{2}, {3} source{4} ({5} downloaded). Press Apply to place them.".format(
        os.path.basename(path), len(result.get("placements", [])),
        "" if len(result.get("placements", [])) == 1 else "s",
        len(result.get("sources", [])), "" if len(result.get("sources", [])) == 1 else "s",
        downloaded)


# -- apply -------------------------------------------------------------------------------


def _apply(working, pending_deletes):
    """Save, delete what was removed, apply, and offer a reload when needed.
    Returns ``(saved, notice, report, reloading)``."""
    problems = []
    # Dynamo buttons are bundles My Ribbon writes itself: name them uniquely
    # (this can rename a source's bundle and placements, so it runs before the
    # save), create what is missing, rewrite what changed, refresh stale
    # copies, delete the bundles of removed graphs
    sync = host.sync_dynamo_bundles(working, pending_deletes)
    problems.extend(sync.get("errors", []))
    ok, error = my_ribbon.save_registry(working)
    if not ok:
        forms.alert("The settings could not be saved:\n{0}".format(error), title=__title__)
        return None, None, None, False
    still_used = set(s.get("extra_root") for s in working.get("sources", []) if s.get("extra_root"))
    for source in pending_deletes:
        if source.get("kind") == "dynamo":
            continue  # handled by sync_dynamo_bundles
        if source.get("extra_root") and source["extra_root"] in still_used:
            # another source still lives in that repository clone
            continue
        if source.get("extra_root"):
            # first, while pyRevit's config still lists the path (it drops
            # paths that no longer exist)
            host.unregister_extension_root(source["extra_root"])
        removed_ok, message = host.remove_installed_source(source)
        if not removed_ok and message:
            problems.append(message)
    report = my_ribbon.apply(working)
    saved = copy.deepcopy(working)
    status = _source_status(working, my_ribbon.list_ribbon())
    needs_reload = bool(pending_deletes) or STATUS_NEEDS_RELOAD in status.values() \
        or bool(sync.get("written")) or bool(sync.get("deleted"))
    parts = ["Applied: {0} placed".format(len(report.get("added", [])))]
    if report.get("missing"):
        parts.append("{0} missing".format(len(report["missing"])))
    if report.get("hidden_tabs"):
        parts.append("hidden: {0}".format(", ".join(report["hidden_tabs"])))
    if problems:
        parts.append("; ".join(problems))
    if report.get("errors"):
        parts.append("; ".join(report["errors"]))
    notice = ", ".join(parts) + "."
    if report.get("missing") or problems or report.get("errors"):
        # the window closes after Apply, so anything off is said now
        details = [u"{0}: {1}".format(m.get("title"), m.get("reason")) for m in report.get("missing", [])]
        details += problems + list(report.get("errors", []))
        forms.alert(notice, title=__title__, expanded="\n".join(details), warn_icon=bool(problems))
    reloading = False
    if needs_reload:
        if forms.alert("Some tools need a pyRevit reload before they appear (or disappear). "
                       "Your placements are saved and are applied automatically after the reload.\n\n"
                       "Reload pyRevit now?", title=__title__, yes=True, no=True):
            reloading = True
    return saved, notice, report, reloading


# -- main loop ---------------------------------------------------------------------------


def _run():
    saved, error = my_ribbon.load_registry()
    if error:
        forms.alert("{0}\n\nMy Ribbon starts empty; pressing Apply will overwrite that file."
                    .format(error), title=__title__)
    working = copy.deepcopy(saved)
    pending_deletes = []
    notice = None
    report = None
    while True:
        summary = my_ribbon.list_ribbon()
        window = MyRibbonWindow("MyRibbonWindow.xaml", working, saved,
                                source_status=_source_status(working, summary),
                                placement_issues=_placement_issues(report),
                                ribbon_summary=summary, open_folder=_open_folder,
                                pending_deletes=pending_deletes, notice=notice)
        window.ShowDialog()
        notice = None
        result = window.result
        if result in (None, "close"):
            return
        if result == "add_source":
            notice = _add_source_flow(working, pending_deletes)
            continue
        if isinstance(result, tuple) and result[0] == "add_buttons":
            _add_buttons_flow(working, result[1])
            continue
        if result == "export":
            notice = _export(working)
            continue
        if result == "import":
            notice = _import(working, pending_deletes)
            continue
        if result == "apply":
            new_saved, notice, report, reloading = _apply(working, pending_deletes)
            if new_saved is None:
                # the save failed; the window comes back with everything staged
                continue
            if reloading:
                host.reload_pyrevit()
            # Apply closes the window: the ribbon itself shows the result
            return


try:
    _run()
except Exception as run_error:
    LOGGER.exception("My Ribbon failed.")
    forms.alert("My Ribbon failed:\n{0}".format(run_error), title=__title__)
