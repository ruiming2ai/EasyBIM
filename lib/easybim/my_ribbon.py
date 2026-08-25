# -*- coding: utf-8 -*-
"""My Ribbon: put buttons from other pyRevit extensions on panels of your own.

The mechanism is the one ``modify_ribbon`` already uses in production to place
EasyBIM buttons on Revit's Modify tab: the *live* ``Autodesk.Windows``
ribbon item is added to another panel's item collection.  It is the same
object, so its command handler, enabled state, icon and tooltip stay exactly
what the owning extension makes them.  Nothing is copied to disk and no
pyRevit reload is needed to re-arrange.

Two things live here:

* the **registry** - one JSON file (``script.get_universal_data_file``) that
  says which extensions the user linked and where each picked button goes.
  It is the single source of truth; the ribbon is rebuilt from it.
* the **apply engine** - reads the registry, removes whatever it added last
  time (the added objects are mirrored in a pyRevit envvar, so a reload
  cannot orphan them), creates the user's tabs and panels, shares the items in
  order and hides the source tabs the user asked to hide.

Apply runs on the first Idling tick of every session (``startup.py`` queues
it) because that is the first moment every extension is on the ribbon -
``startup.py`` itself runs while later extensions are still loading - and it
runs again from the My Ribbon window whenever the user presses Apply.

The module is duck-typed against the ribbon (``ribbon.Tabs``,
``tab.Panels``, ``panel.Source.Items``) so the tests drive it with fakes.
"""

from __future__ import print_function

import io
import json
import os


try:
    from pyrevit import script
except Exception:
    script = None


FORMAT_VERSION = 1
FILE_ID = "EasyBIM_MyRibbon"

#: What the last apply added, so the next apply can take it away first.
APPLIED_ENVVAR = "EASYBIM_MYRIBBON_APPLIED"
#: Set by ``startup.py``; consumed on the first Idling tick.
PENDING_ENVVAR = "EASYBIM_MYRIBBON_PENDING"

ID_PREFIX = "EasyBIM_MyRibbon_"

#: Tabs the engine never hides, whatever the registry says: EasyBIM carries
#: the My Ribbon button itself (the only way back to un-hide anything), and
#: Modify is Revit's contextual editing tab.  The pyRevit tab *can* be hidden
#: (the Show/Hide window warns); EasyBIM staying visible is the way back.
PROTECTED_TABS = ("easybim", "modify")

SOURCE_KINDS = ("git", "catalogue", "installed", "ribbon", "dynamo")

#: Placement rows that are layout, not buttons: a vertical separator and the
#: slide-out fold (everything after it drops into the panel's fold-out).
#: They carry no source and no path; the engine draws an object of its own.
#: Kept equal to ``my_ribbon_state.MARKER_KINDS`` (pinned by a test).
MARKER_KINDS = ("separator", "slideout")

#: My Ribbon's own extension: holds the bundles it generates itself (today:
#: Dynamo graphs).  pyRevit loads it like any extension; its tab is hidden.
LIBRARY_EXTENSION_NAME = "EasyBIM_MyRibbon"
LIBRARY_TAB = "My Ribbon Library"
LIBRARY_DYNAMO_PANEL = "Dynamo"


class RegistryFormatError(Exception):
    """The file was written by a newer My Ribbon than this one."""


# -- registry -------------------------------------------------------------


def empty_registry():
    return {
        "format": FORMAT_VERSION,
        "sources": [],
        "destinations": [],
        "placements": [],
        "hidden_tabs": [],
    }


def read_registry(raw):
    """Validate a parsed JSON document and return a normalised registry.

    Lenient about missing lists (an empty file is an empty registry) and
    strict about the format number: a document from a newer My Ribbon is
    refused rather than half-read, the same rule Tag Align presets follow.
    """
    if not isinstance(raw, dict):
        return empty_registry()
    version = raw.get("format", None)
    if version is None:
        version = FORMAT_VERSION if not raw else None
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise RegistryFormatError("This settings file has no readable format number.")
    if version > FORMAT_VERSION:
        raise RegistryFormatError(
            "This settings file was written by a newer My Ribbon (format {0}); "
            "this version reads up to format {1}. Update EasyBIM first.".format(
                version, FORMAT_VERSION))

    registry = empty_registry()
    registry["sources"] = [_clean_source(s) for s in _list_of_dicts(raw.get("sources"))]
    registry["destinations"] = [
        _clean_destination(d) for d in _list_of_dicts(raw.get("destinations"))]
    registry["placements"] = [
        _clean_placement(p) for p in _list_of_dicts(raw.get("placements"))]
    registry["sources"] = [s for s in registry["sources"] if s["id"]]
    registry["destinations"] = [d for d in registry["destinations"] if d["id"]]
    # a layout marker (separator / slide-out) has no button path by design
    registry["placements"] = [
        p for p in registry["placements"]
        if p["id"] and p["dest"] and (p["path"] or p["kind"] in MARKER_KINDS)]
    # hidden_tabs is the one truth for tab visibility; files from before it
    # existed only carried the per-source "hide its own tab" flag, so union.
    hidden = []
    raw_hidden = raw.get("hidden_tabs")
    for name in (raw_hidden if isinstance(raw_hidden, list) else []):
        _add_unique_name(hidden, _text(name))
    for source in registry["sources"]:
        if source.get("hide_tab"):
            for name in source.get("tab_names", []):
                _add_unique_name(hidden, name)
    registry["hidden_tabs"] = hidden
    return registry


def _add_unique_name(names, name):
    if not name:
        return
    key = _normalize_label(name)
    if any(_normalize_label(existing) == key for existing in names):
        return
    names.append(name)


def _list_of_dicts(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value):
    if value is None:
        return ""
    try:
        return value if isinstance(value, type(u"")) else str(value)
    except Exception:
        return ""


def _clean_source(raw):
    kind = _text(raw.get("kind")).lower()
    if kind not in SOURCE_KINDS:
        kind = "git"
    source = {
        "id": _text(raw.get("id")),
        "kind": kind,
        "url": _text(raw.get("url")) or None,
        "branch": _text(raw.get("branch")) or None,
        "ext_name": _text(raw.get("ext_name")),
        "label": _text(raw.get("label")) or _text(raw.get("ext_name")),
        "tab_names": [_text(t) for t in (raw.get("tab_names") or []) if _text(t)],
        "installed_by_my_ribbon": bool(raw.get("installed_by_my_ribbon", False)),
        "hide_tab": bool(raw.get("hide_tab", False)),
        "extra_root": _text(raw.get("extra_root")) or None,
    }
    if kind == "catalogue":
        # the catalogue entry name is how the source is found again on import
        source["name"] = _text(raw.get("name")) or source["ext_name"]
    if kind == "dynamo":
        source["path"] = _text(raw.get("path"))
        source["title"] = _text(raw.get("title")) or source["label"]
        # a bundle is one plain "<Name>.pushbutton" folder name; anything else
        # (a path, a drive letter, dots) is dropped so the sync names it afresh
        bundle = _text(raw.get("bundle"))
        source["bundle"] = bundle if is_bundle_folder_name(bundle) else ""
        source["icon"] = _text(raw.get("icon")) or None
        # the bundle is nothing but what My Ribbon wrote, whoever's file the
        # registry came from
        source["installed_by_my_ribbon"] = True
    return source


def is_bundle_folder_name(name):
    """One plain ``<Name>.pushbutton`` folder name: no separators, no drive
    letter, not dots-only.  Shared rule with the host (pinned by a test)."""
    name = _text(name)
    if not name or name != name.strip() or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    return name.lower().endswith(".pushbutton") and len(name) > len(".pushbutton")


def _clean_destination(raw):
    return {
        "id": _text(raw.get("id")),
        "tab": _text(raw.get("tab")),
        "panel": _text(raw.get("panel")),
        "own_tab": bool(raw.get("own_tab", False)),
    }


def _clean_placement(raw):
    path = []
    for level in raw.get("path") or []:
        if isinstance(level, dict):
            name = _text(level.get("name"))
            title = _text(level.get("title")) or name
        else:
            name = _text(level)
            title = name
        if name or title:
            path.append({"name": name or title, "title": title})
    try:
        order = int(raw.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return {
        "id": _text(raw.get("id")),
        "source": _text(raw.get("source")),
        "dest": _text(raw.get("dest")),
        "order": order,
        "kind": _text(raw.get("kind")) or "button",
        "title": _text(raw.get("title")) or (path[-1]["title"] if path else ""),
        "control_id": _text(raw.get("control_id")),
        "path": path,
        # placements sharing a stack id render as one row of small buttons
        "stack": _text(raw.get("stack")),
    }


def registry_path():
    """Roaming, Revit-version-independent, like Tag Align's presets file."""
    if script is None:
        return None
    try:
        return script.get_universal_data_file(FILE_ID, "json")
    except Exception:
        return None


def load_registry(path=None):
    """Return ``(registry, error_text)``; never raises.

    A missing file is an empty registry with no error.  An unreadable or
    newer-format file is an empty registry *with* the reason, so the caller
    can tell the user instead of silently wiping their setup.
    """
    path = path or registry_path()
    if not path or not os.path.isfile(path):
        return empty_registry(), ""
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            raw = json.loads(handle.read() or "{}")
    except Exception as ex:
        return empty_registry(), "Could not read {0}: {1}".format(path, ex)
    try:
        return read_registry(raw), ""
    except RegistryFormatError as ex:
        return empty_registry(), _text(ex)


def save_registry(registry, path=None):
    """Return ``(ok, error_text)``.  Writes beside the file and swaps."""
    path = path or registry_path()
    if not path:
        return False, "No settings file path is available."
    folder = os.path.dirname(path)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
    except Exception as ex:
        return False, "Could not create {0}: {1}".format(folder, ex)

    document = dict(registry)
    document["format"] = FORMAT_VERSION
    text = json.dumps(document, indent=2, sort_keys=True)
    temporary = path + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text if isinstance(text, type(u"")) else text.decode("utf-8"))
        if os.path.isfile(path):
            os.remove(path)
        os.rename(temporary, path)
    except Exception as ex:
        try:
            if os.path.isfile(temporary):
                os.remove(temporary)
        except Exception:
            pass
        return False, "Could not write {0}: {1}".format(path, ex)
    return True, ""


def registry_has_work(registry):
    """True when applying would touch the ribbon at all."""
    if not isinstance(registry, dict):
        return False
    if registry.get("placements") or registry.get("hidden_tabs"):
        return True
    return any(s.get("hide_tab") for s in registry.get("sources", []))


# -- startup queue ----------------------------------------------------------


def _get_envvar(name, default=None):
    if script is None:
        return default
    try:
        value = script.get_envvar(name)
    except Exception:
        return default
    return default if value is None else value


def _set_envvar(name, value):
    if script is None:
        return False
    try:
        script.set_envvar(name, value)
        return True
    except Exception:
        return False


def queue_startup_apply():
    """Called by ``startup.py``: apply on the first Idling tick, not now.

    ``startup.py`` runs while later extensions are still loading, so their
    tabs are not on the ribbon yet.  Every pyRevit reload runs ``startup.py``
    again, which is exactly when the placements must be re-applied.
    """
    return _set_envvar(PENDING_ENVVAR, True)


def has_pending_startup_apply():
    return bool(_get_envvar(PENDING_ENVVAR, False))


def run_pending_startup_apply(**kwargs):
    """Consume the pending flag and apply the saved registry once."""
    _set_envvar(PENDING_ENVVAR, None)
    return apply_saved(**kwargs)


# -- apply engine -----------------------------------------------------------


def apply_saved(path=None, ribbon=None, autodesk_windows=None, logger=None):
    """Load the registry from disk and apply it.  Never raises."""
    registry, error = load_registry(path)
    if error:
        report = _empty_report()
        report["errors"].append(error)
        return report
    if not registry_has_work(registry) and not _load_mirror_has_entries():
        # Nothing to place and nothing placed before: do not even touch the
        # ribbon.  (An empty registry with leftovers from a previous apply
        # still goes through apply(), which takes them back.)
        return _empty_report()
    return apply(registry, ribbon=ribbon, autodesk_windows=autodesk_windows, logger=logger)


def _load_mirror_has_entries():
    mirror = _load_mirror()
    return any(mirror.get(key) for key in ("items", "panels", "tabs", "hidden"))


def apply(registry, ribbon=None, autodesk_windows=None, logger=None):
    """Rebuild the user's placements on the live ribbon from ``registry``.

    Returns a report dict: ``added`` (placement ids), ``missing`` (list of
    ``{"placement", "reason"}``), ``created_tabs``, ``created_panels``,
    ``hidden_tabs``, ``shown_tabs``, ``errors``.  Never raises.
    """
    report = _empty_report()
    logger = logger or _get_logger()

    try:
        ribbon = ribbon or _get_default_ribbon()
    except Exception as ex:
        report["errors"].append("Ribbon unavailable: {0}".format(_safe_text(ex)))
        return report
    if ribbon is None:
        report["errors"].append("Ribbon unavailable.")
        return report
    autodesk_windows = autodesk_windows or _get_default_autodesk_windows()

    mirror = _load_mirror()
    _undo_previous(mirror, report, logger)
    new_mirror = _empty_mirror()

    destinations = dict((d["id"], d) for d in registry.get("destinations", []))
    sources = dict((s["id"], s) for s in registry.get("sources", []))
    placements = sorted(
        registry.get("placements", []),
        key=lambda p: (p.get("dest", ""), p.get("order", 0), p.get("id", "")))
    placed_items = {}

    index = 0
    while index < len(placements):
        placement = placements[index]
        index += 1
        dest = destinations.get(placement.get("dest"))
        if dest is None:
            _miss(report, placement, "its destination no longer exists")
            continue
        try:
            panel = _ensure_destination(
                ribbon, autodesk_windows, dest, new_mirror, report, logger)
        except Exception as ex:
            _miss(report, placement, "destination could not be prepared: {0}".format(
                _safe_text(ex)))
            continue
        if panel is None:
            _miss(report, placement, "tab '{0}' is not on the ribbon".format(dest["tab"]))
            continue

        if placement.get("kind") in MARKER_KINDS:
            _place_marker(autodesk_windows, panel, placement, new_mirror, report, logger)
            continue

        stack_id = placement.get("stack")
        if stack_id:
            # placements are sorted by (dest, order): a stack's members are
            # the run of rows right here that share its id
            run = [placement]
            while index < len(placements) \
                    and placements[index].get("dest") == placement.get("dest") \
                    and placements[index].get("stack") == stack_id:
                run.append(placements[index])
                index += 1
            if len(run) >= 2:
                _place_stack(ribbon, autodesk_windows, panel, run, new_mirror,
                             report, placed_items, logger)
                continue
            # a stack of one is just a button: place it flat, shared

        item, reason = _find_source_item(ribbon, placement)
        if item is None:
            _miss(report, placement, reason)
            continue
        items = _panel_items(panel)
        if _collection_contains(items, item):
            report["added"].append(placement["id"])
            placed_items[placement["id"]] = item
            continue
        try:
            _add_item(items, item)
        except Exception as ex:
            _miss(report, placement, "could not add: {0}".format(_safe_text(ex)))
            continue
        new_mirror["items"].append((items, item))
        report["added"].append(placement["id"])
        placed_items[placement["id"]] = item
        _log_debug(logger, "Placed {0} in {1} > {2}".format(
            placement.get("title"), dest["tab"], dest["panel"]))

    _apply_dynamo_icons(ribbon, placements, placed_items, sources, report, logger)
    _apply_tab_visibility(ribbon, registry, sources, destinations, new_mirror, mirror, report, logger)
    _drop_unused_containers(ribbon, mirror, new_mirror, report, logger)
    _store_mirror(new_mirror)
    return report


def _empty_report():
    return {
        "added": [],
        "missing": [],
        "created_tabs": [],
        "created_panels": [],
        "hidden_tabs": [],
        "shown_tabs": [],
        "dynamo_icons": [],
        "errors": [],
    }


def _miss(report, placement, reason):
    report["missing"].append({
        "placement": placement.get("id"),
        "title": placement.get("title"),
        "reason": reason,
    })


# -- stacked rows and layout markers -----------------------------------------


def _place_stack(ribbon, autodesk_windows, panel, run, mirror, report,
                 placed_items, logger):
    """2-3 placements as one row of small buttons.

    The row (``RibbonRowPanel``) is our own object; each button in it is a
    small **clone** that shares the original's command handler.  The original
    is never touched - button size is a property of the item itself, so
    shrinking the shared object would shrink it on its home tab too.
    """
    row = _create_stack_row(autodesk_windows, run[0])
    if row is None:
        for placement in run:
            _miss(report, placement, "Autodesk.Windows unavailable, cannot build the stack")
        return
    items = _panel_items(panel)
    try:
        _add_item(items, row)
    except Exception as ex:
        for placement in run:
            _miss(report, placement, "could not add: {0}".format(_safe_text(ex)))
        return
    entry = (items, row)
    mirror["items"].append(entry)
    row_items = _getattr_safe(row, "Items")
    added = 0
    for placement in run:
        item, reason = _find_source_item(ribbon, placement)
        if item is None:
            _miss(report, placement, reason)
            continue
        clone = _make_stack_clone(autodesk_windows, item, placement)
        if clone is None:
            _miss(report, placement, "could not build the small copy")
            continue
        try:
            _add_item(row_items, clone)
        except Exception as ex:
            _miss(report, placement, "could not add: {0}".format(_safe_text(ex)))
            continue
        added += 1
        report["added"].append(placement["id"])
        placed_items[placement["id"]] = clone
    if not added:
        # every member missing: an empty row would paint as a sliver
        _remove_item(items, row)
        mirror["items"].remove(entry)
        return
    _log_debug(logger, "Stacked {0} small button(s).".format(added))


def _create_stack_row(autodesk_windows, first_placement):
    row_type = _getattr_safe(autodesk_windows, "RibbonRowPanel")
    if row_type is None:
        return None
    try:
        row = row_type()
    except Exception:
        return None
    _try_setattr(row, "Id", ID_PREFIX + "row_" + slug(first_placement.get("stack")))
    return row


def _make_stack_clone(autodesk_windows, item, placement):
    """A small copy of ``item``: same command handler (so the click runs the
    same command and the enabled state follows it), text/icon/tooltip copied
    at apply time, ``Size`` Standard so the stack renders it small."""
    button_type = _getattr_safe(autodesk_windows, "RibbonButton")
    if button_type is None:
        return None
    try:
        clone = button_type()
    except Exception:
        return None
    _try_setattr(clone, "Id", ID_PREFIX + "p_" + slug(placement.get("id")))
    for attr_name in ("Text", "Image", "LargeImage", "ToolTip", "Description",
                      "KeyTip", "IsEnabled", "IsToolTipEnabled"):
        value = _getattr_safe(item, attr_name)
        if value is not None:
            _try_setattr(clone, attr_name, value)
    if not _safe_text(_getattr_safe(clone, "Text")):
        _try_setattr(clone, "Text", placement.get("title"))
    handler = _getattr_safe(item, "CommandHandler")
    if handler is not None:
        _try_setattr(clone, "CommandHandler", handler)
    parameter = _getattr_safe(item, "CommandParameter")
    if parameter is not None:
        _try_setattr(clone, "CommandParameter", parameter)
    standard = _getattr_safe(_getattr_safe(autodesk_windows, "RibbonItemSize"), "Standard")
    if standard is not None:
        _try_setattr(clone, "Size", standard)
    _try_setattr(clone, "ShowText", True)
    _try_setattr(clone, "ShowImage", True)
    return clone


def _place_marker(autodesk_windows, panel, placement, mirror, report, logger):
    """A separator or the slide-out fold: an object of our own, so there is
    no source to find - create, add, mirror."""
    marker = _create_layout_marker(autodesk_windows, placement)
    if marker is None:
        _miss(report, placement, "Autodesk.Windows unavailable, cannot draw it")
        return
    items = _panel_items(panel)
    try:
        _add_item(items, marker)
    except Exception as ex:
        _miss(report, placement, "could not add: {0}".format(_safe_text(ex)))
        return
    mirror["items"].append((items, marker))
    report["added"].append(placement["id"])
    _log_debug(logger, "Placed a {0}.".format(placement.get("kind")))


def _create_layout_marker(autodesk_windows, placement):
    if placement.get("kind") == "separator":
        type_name, id_word = "RibbonSeparator", "sep"
    else:
        type_name, id_word = "RibbonPanelBreak", "fold"
    marker_type = _getattr_safe(autodesk_windows, type_name)
    if marker_type is None:
        return None
    try:
        marker = marker_type()
    except Exception:
        return None
    _try_setattr(marker, "Id", ID_PREFIX + id_word + "_" + slug(placement.get("id")))
    return marker


# -- ribbon summary (for the destination picker) ------------------------------


def list_ribbon(ribbon=None):
    """Tabs and panels currently on the ribbon, as plain dicts:
    ``[{"title", "id", "is_ours", "panels": [{"title", "id", "is_ours"}]}]``.
    Never raises; an unavailable ribbon gives an empty list."""
    try:
        ribbon = ribbon or _get_default_ribbon()
    except Exception:
        return []
    summary = []
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        panels = []
        for panel in _tab_panels(tab):
            source = _panel_source(panel)
            title = _panel_title(panel)
            if not title:
                continue
            panels.append({
                "title": title,
                "id": _safe_text(_getattr_safe(source, "Id")),
                "is_ours": _is_ours(source),
            })
        title = _tab_title(tab)
        if not title:
            continue
        summary.append({
            "title": title,
            "id": _safe_text(_getattr_safe(tab, "Id")),
            "is_ours": _is_ours(tab),
            "is_visible": _getattr_safe(tab, "IsVisible", True) is not False,
            "is_contextual": bool(_getattr_safe(tab, "IsContextualTab", False)),
            "panels": panels,
        })
    return summary


# -- a live tab as a source (native Revit tabs, other add-ins) ----------------

#: AdWindows item types by what My Ribbon can do with them.  Anything not
#: listed is refused (galleries, combos, text boxes, sliders, labels...).
_RIBBON_BUTTON_TYPES = ("RibbonButton", "RibbonToggleButton", "RibbonCheckBox",
                        "RibbonRadioButton", "RibbonCommandItem")
_RIBBON_GROUP_TYPES = ("RibbonSplitButton", "RibbonMenuButton", "RibbonListButton",
                       "RibbonChecklistButton", "RibbonRadioButtonGroup")
_RIBBON_SKIP_TYPES = ("RibbonSeparator", "RibbonRowBreak", "RibbonPanelBreak")


def describe_ribbon_tab(tab):
    """Read a live ribbon tab into the same plain dicts ``describe_extension``
    gives for a parsed pyRevit extension, so the picker can show native Revit
    and add-in buttons.  Icons come back as ``icon_source`` (an ImageSource),
    not a file path.  Never raises; odd items are skipped."""
    tab_level = {"name": _safe_text(_getattr_safe(tab, "Id")) or _tab_title(tab),
                 "title": _tab_title(tab)}
    panels = []
    buttons = []
    for panel in _tab_panels(tab):
        source = _panel_source(panel)
        title = _panel_title(panel)
        if not title and not _safe_text(_getattr_safe(source, "Id")):
            continue
        panel_level = {"name": _safe_text(_getattr_safe(source, "Id")) or title,
                       "title": title or _safe_text(_getattr_safe(source, "Id"))}
        items = []
        _collect_live_items(_panel_items(panel), [tab_level, panel_level], items, buttons)
        if items:
            panels.append({"name": panel_level["name"], "title": panel_level["title"],
                           "items": items})
    return {
        "name": tab_level["title"],
        "dir": "",
        "tab_names": [tab_level["title"]],
        "tabs": [{"name": tab_level["name"], "title": tab_level["title"], "panels": panels}],
        "buttons": buttons,
        "has_startup": False,
        "has_hooks": False,
        "live": True,
    }


def describe_tab_by_title(title, ribbon=None):
    """``describe_ribbon_tab`` for the live tab called ``title`` (None when
    it is not on the ribbon)."""
    try:
        ribbon = ribbon or _get_default_ribbon()
    except Exception:
        return None
    if ribbon is None:
        return None
    tab = _find_tab(ribbon, [title])
    return describe_ribbon_tab(tab) if tab is not None else None


def _collect_live_items(items, path, out, flat):
    for item in _safe_iter(items):
        if _is_ours(item):
            # our stacked rows, clones and markers are copies of things that
            # already have a home - never offer them as sources
            continue
        chain = _type_chain(item)
        type_name = chain[0] if chain else ""
        if any(name in _RIBBON_SKIP_TYPES for name in chain):
            continue
        if _is_stack(item):
            # a row panel is a stack: its buttons sit flat on the panel
            _collect_live_items(_child_items(item), path, out, flat)
            continue
        item_id = _safe_text(_getattr_safe(item, "Id"))
        title = _item_title(item)
        if not item_id and not title:
            continue
        # a Revit/add-in subclass of RibbonButton is still a button: the
        # base types decide, nearest first
        kind = None
        for name in chain:
            if name in _RIBBON_GROUP_TYPES:
                kind = "pulldown"
                break
            if name in _RIBBON_BUTTON_TYPES:
                kind = "button"
                break
        if kind is None:
            kind = "ribbon-" + (type_name.lower() or "item")
        level = {"name": item_id or title, "title": title or item_id}
        entry = {
            "kind": kind,
            "name": level["name"],
            "title": level["title"],
            "tooltip": _item_tooltip_text(item),
            "icon": None,
            "icon_source": _getattr_safe(item, "Image") or _getattr_safe(item, "LargeImage"),
            "control_id": item_id,
            "path": path + [level],
            "min_revit": None,
            "max_revit": None,
            "in_layout": True,
            "children": [],
            "type_name": type_name,
        }
        out.append(entry)
        flat.append(entry)
        if kind == "pulldown":
            _collect_live_items(_child_items(item), path + [level], entry["children"], flat)


def _type_name(obj):
    chain = _type_chain(obj)
    return chain[0] if chain else ""


def _type_chain(obj):
    """The object's type name followed by its base type names (CLR), or the
    Python class and its bases for fakes."""
    names = []
    get_type = _getattr_safe(obj, "GetType")
    if callable(get_type):
        try:
            current = get_type()
            while current is not None and len(names) < 12:
                names.append(_safe_text(_getattr_safe(current, "Name")))
                current = _getattr_safe(current, "BaseType")
            return [n for n in names if n]
        except Exception:
            names = []
    try:
        return [klass.__name__ for klass in type(obj).__mro__ if klass is not object]
    except Exception:
        return [obj.__class__.__name__]


def _item_title(item):
    for attr_name in ("Text", "AutomationName", "Name"):
        text = _safe_text(_getattr_safe(item, attr_name))
        if text.strip():
            return text
    return _id_tail(_getattr_safe(item, "Id"))


def _item_tooltip_text(item):
    tooltip = _getattr_safe(item, "ToolTip")
    if tooltip is None:
        return ""
    if isinstance(tooltip, type(u"")) or isinstance(tooltip, str):
        return " ".join(_safe_text(tooltip).split())
    parts = []
    for attr_name in ("Title", "Content", "ExpandedContent"):
        value = _getattr_safe(tooltip, attr_name)
        if value is not None and (isinstance(value, type(u"")) or isinstance(value, str)):
            text = " ".join(_safe_text(value).split())
            if text and text not in parts:
                parts.append(text)
    return " - ".join(parts)


# -- the native Dynamo look for Dynamo buttons ----------------------------------


def find_native_dynamo_button(ribbon, exclude=None):
    """Revit's own Dynamo button (Manage > Visual Programming > Dynamo), or
    None when Dynamo is not installed.  Dynamo's own "Visual Programming"
    panel wins; a plain title match elsewhere is the fallback.  Items on My
    Ribbon's own panels, on the library tab, and anything in ``exclude`` (the
    items just placed) never match, so a graph titled "Dynamo" cannot pass
    for Revit's button."""
    exclude = list(exclude or [])
    fallback = None
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        if _is_ours(tab) or _normalize_label(_tab_title(tab)) == _normalize_label(LIBRARY_TAB):
            continue
        for panel in _tab_panels(tab):
            if _is_ours(_panel_source(panel)):
                continue
            on_dynamo_panel = _normalize_label(_panel_title(panel)) == "visual programming"
            for item in _safe_iter(_panel_items(panel)):
                if any(item is skip for skip in exclude):
                    continue
                if _normalize_label(_item_title(item)) != "dynamo" \
                        and _normalize_label(_id_tail(_getattr_safe(item, "Id"))) != "dynamo":
                    continue
                if on_dynamo_panel:
                    return item
                if fallback is None:
                    fallback = item
    return fallback


def _apply_dynamo_icons(ribbon, placements, placed_items, sources, report, logger):
    """Give placed Dynamo graphs Revit's own Dynamo images unless the user
    chose an icon.  Same object on both panels, so the library copy changes
    too - which is fine, that tab is hidden."""
    wanted = []
    for placement in placements:
        source = sources.get(placement.get("source")) or {}
        if source.get("kind") != "dynamo" or source.get("icon"):
            continue
        item = placed_items.get(placement.get("id"))
        if item is not None:
            wanted.append((placement, item))
    if not wanted:
        return
    native = find_native_dynamo_button(ribbon, exclude=list(placed_items.values()))
    if native is None:
        return
    large = _getattr_safe(native, "LargeImage")
    small = _getattr_safe(native, "Image")
    for placement, item in wanted:
        done = False
        if large is not None:
            done = _try_setattr(item, "LargeImage", large) or done
        if small is not None:
            done = _try_setattr(item, "Image", small) or done
        if done:
            report["dynamo_icons"].append(placement.get("id"))
    _log_debug(logger, "Dynamo icons applied to {0} item(s).".format(len(report["dynamo_icons"])))


# -- mirror of what we added ------------------------------------------------


def _empty_mirror():
    # items: (items_collection, item) pairs; panels/tabs: objects we created;
    # hidden: tabs we set invisible.
    return {"items": [], "panels": [], "tabs": [], "hidden": []}


def _load_mirror():
    stored = _get_envvar(APPLIED_ENVVAR, None)
    if not isinstance(stored, dict):
        return _empty_mirror()
    mirror = _empty_mirror()
    for key in mirror:
        value = stored.get(key)
        if isinstance(value, list):
            mirror[key] = list(value)
    return mirror


def _store_mirror(mirror):
    _set_envvar(APPLIED_ENVVAR, mirror)


def _undo_previous(mirror, report, logger):
    """Take back last apply's additions.  Everything is best-effort: a stale
    reference from before a pyRevit reload is simply skipped."""
    for entry in mirror.get("items", []):
        try:
            items, item = entry
            _remove_item(items, item)
        except Exception:
            pass
    for tab in mirror.get("hidden", []):
        try:
            _try_setattr(tab, "IsVisible", True)
        except Exception:
            pass
    _log_debug(logger, "Cleared {0} previously placed items.".format(
        len(mirror.get("items", []))))
    # panels and tabs we created stay until _drop_unused_containers decides.


def _drop_unused_containers(ribbon, old_mirror, new_mirror, report, logger):
    """Remove panels/tabs we created earlier that the new registry no longer
    fills.  A container that is still in use is carried over to the new
    mirror so the next apply can judge it again."""
    for panel_entry in old_mirror.get("panels", []):
        try:
            tab, panel = panel_entry
        except Exception:
            continue
        if any(panel is kept[1] for kept in new_mirror["panels"]):
            continue
        if _safe_len(_panel_items(panel)) == 0:
            try:
                _remove_item(_tab_panels(tab), panel)
                _log_debug(logger, "Removed empty panel {0}".format(_panel_title(panel)))
            except Exception:
                new_mirror["panels"].append((tab, panel))
        else:
            new_mirror["panels"].append((tab, panel))
    for tab in old_mirror.get("tabs", []):
        if any(tab is kept for kept in new_mirror["tabs"]):
            continue
        if _safe_len(_tab_panels(tab)) == 0:
            try:
                _remove_item(getattr(ribbon, "Tabs", None), tab)
                _log_debug(logger, "Removed empty tab {0}".format(_tab_title(tab)))
            except Exception:
                new_mirror["tabs"].append(tab)
        else:
            new_mirror["tabs"].append(tab)


# -- destinations -----------------------------------------------------------


def _ensure_destination(ribbon, autodesk_windows, dest, mirror, report, logger):
    """Return the destination panel, creating tab and/or panel when allowed."""
    tab = _find_tab(ribbon, [dest["tab"], _our_id(dest["tab"])])
    if tab is None:
        if not dest.get("own_tab"):
            return None
        if autodesk_windows is None:
            raise RuntimeError("Autodesk.Windows unavailable, cannot create tab")
        tab = _create_tab(autodesk_windows, ribbon, dest["tab"])
        mirror["tabs"].append(tab)
        report["created_tabs"].append(dest["tab"])
        _log_debug(logger, "Created tab {0}".format(dest["tab"]))
    elif dest.get("own_tab") and _is_ours(tab):
        # Carried over from a previous apply: keep tracking it.
        if not any(tab is kept for kept in mirror["tabs"]):
            mirror["tabs"].append(tab)

    panel = _find_panel(tab, [dest["panel"], _our_id(dest["panel"])])
    if panel is None:
        if autodesk_windows is None:
            raise RuntimeError("Autodesk.Windows unavailable, cannot create panel")
        panel = _create_panel(autodesk_windows, tab, dest["panel"])
        mirror["panels"].append((tab, panel))
        report["created_panels"].append("{0} > {1}".format(dest["tab"], dest["panel"]))
        _log_debug(logger, "Created panel {0} > {1}".format(dest["tab"], dest["panel"]))
    elif _is_ours(_panel_source(panel)):
        if not any(panel is kept[1] for kept in mirror["panels"]):
            mirror["panels"].append((tab, panel))
    return panel


def _create_tab(autodesk_windows, ribbon, title):
    tab = autodesk_windows.RibbonTab()
    _try_setattr(tab, "Id", _our_id(title))
    _try_setattr(tab, "Title", title)
    _try_setattr(tab, "Name", title)
    _try_setattr(tab, "AutomationName", title)
    _try_setattr(tab, "IsVisible", True)
    _try_setattr(tab, "IsEnabled", True)
    _add_item(getattr(ribbon, "Tabs"), tab)
    return tab


def _create_panel(autodesk_windows, tab, title):
    panel_source = autodesk_windows.RibbonPanelSource()
    _try_setattr(panel_source, "Id", _our_id(title))
    _try_setattr(panel_source, "Title", title)
    _try_setattr(panel_source, "AutomationName", title)
    _try_setattr(panel_source, "Name", title)
    panel = autodesk_windows.RibbonPanel()
    _try_setattr(panel, "Source", panel_source)
    _add_item(_tab_panels(tab), panel)
    return panel


def _our_id(title):
    return ID_PREFIX + slug(title)


def _is_ours(obj):
    return _safe_text(_getattr_safe(obj, "Id")).startswith(ID_PREFIX)


def slug(text):
    """A safe AdWindows Id fragment: letters, digits and underscores only."""
    out = []
    for char in _safe_text(text):
        if char.isalnum() and ord(char) < 128:
            out.append(char)
        else:
            out.append("_")
    collapsed = "_".join(part for part in "".join(out).split("_") if part)
    return collapsed or "Untitled"


# -- source items -----------------------------------------------------------


def _find_source_item(ribbon, placement):
    """Locate the live item a placement points at.

    Order: exact ``Id`` anywhere on the ribbon; then the structural walk
    tab > panel > (container >) item where every level matches by folder
    name *or* title (pyRevit names tabs and buttons after their folders but
    panels and pulldowns after their titles).  Returns ``(item, reason)``.
    """
    path = placement.get("path") or []
    if len(path) < 3:
        return None, "the saved button path is incomplete"

    control_id = placement.get("control_id")
    if control_id:
        hit = _find_item_by_id(ribbon, control_id, tab_level=path[0])
        if hit is not None:
            return hit, ""

    tab = _find_tab(ribbon, _level_aliases(path[0]))
    if tab is None:
        return None, "tab '{0}' is not on the ribbon (extension not loaded, disabled, or " \
                     "needs a pyRevit reload)".format(path[0].get("title"))
    panel = _find_panel(tab, _level_aliases(path[1]))
    if panel is None:
        return None, "panel '{0}' is not on tab '{1}'".format(
            path[1].get("title"), path[0].get("title"))
    items = _panel_items(panel)
    item = None
    for level in path[2:]:
        item = _find_item_by_aliases(items, _level_aliases(level))
        if item is None:
            return None, "button '{0}' is not in {1} > {2}".format(
                level.get("title"), path[0].get("title"), path[1].get("title"))
        items = _child_items(item)
    return item, ""


def _find_item_by_id(ribbon, control_id, tab_level=None):
    target = _safe_text(control_id)
    if not target:
        return None
    tab_keys = _level_aliases(tab_level) if tab_level else None
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        if tab_keys and not _tab_matches(tab, tab_keys):
            continue
        for panel in _tab_panels(tab):
            hit = _find_by_id_recursive(_panel_items(panel), target)
            if hit is not None:
                return hit
    return None


def _find_by_id_recursive(items, target):
    for item in _safe_iter(items):
        if _safe_text(_getattr_safe(item, "Id")) == target:
            return item
        hit = _find_by_id_recursive(_child_items(item), target)
        if hit is not None:
            return hit
    return None


def _level_aliases(level):
    if isinstance(level, dict):
        return [level.get("name"), level.get("title")]
    return [level]


def _find_tab(ribbon, aliases):
    keys = _keys(aliases)
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        if _tab_matches(tab, keys):
            return tab
    return None


def _tab_matches(tab, keys):
    for attr_name in ("Id", "Title", "Name", "AutomationName"):
        if _normalize_label(_getattr_safe(tab, attr_name)) in keys:
            return True
    return False


def _find_panel(tab, aliases):
    keys = _keys(aliases)
    for panel in _tab_panels(tab):
        source = _panel_source(panel)
        panel_id = _safe_text(_getattr_safe(source, "Id"))
        if _normalize_label(panel_id) in keys or _normalize_label(_id_tail(panel_id)) in keys:
            return panel
        for attr_name in ("Title", "AutomationName", "Name"):
            if _normalize_label(_getattr_safe(source, attr_name)) in keys:
                return panel
    return None


def _find_item_by_aliases(items, aliases):
    keys = _keys(aliases)
    for item in _safe_iter(items):
        if _item_matches(item, keys):
            return item
    # Stacks show their children flat on the panel: look one level down
    # without treating the stack itself as a match.  Our own rows hold
    # clones, never a source - skip them so a clone cannot pass for its
    # original.
    for item in _safe_iter(items):
        if _is_stack(item) and not _is_ours(item):
            hit = _find_item_by_aliases(_child_items(item), aliases)
            if hit is not None:
                return hit
    return None


def _item_matches(item, keys):
    if not item:
        return False
    if _normalize_label(_id_tail(_getattr_safe(item, "Id"))) in keys:
        return True
    for attr_name in ("Text", "AutomationName", "ItemText", "Title", "Name"):
        if _normalize_label(_getattr_safe(item, attr_name)) in keys:
            return True
    return False


def _is_stack(item):
    """AdWindows renders a pyRevit stack as a ``RibbonRowPanel``."""
    type_name = ""
    get_type = _getattr_safe(item, "GetType")
    if callable(get_type):
        try:
            type_name = _safe_text(_getattr_safe(get_type(), "Name"))
        except Exception:
            type_name = ""
    if not type_name:
        type_name = item.__class__.__name__
    return "RowPanel" in type_name or "Stack" in type_name


def _id_tail(item_id):
    text = _safe_text(item_id)
    if "%" in text:
        return text.rsplit("%", 1)[-1]
    return text


def _keys(aliases):
    return set(_normalize_label(alias) for alias in (aliases or []) if _safe_text(alias))


# -- tab visibility ---------------------------------------------------------


def _apply_tab_visibility(ribbon, registry, sources, destinations, new_mirror, old_mirror,
                          report, logger):
    protected = set(PROTECTED_TABS)
    for dest in destinations.values():
        protected.add(_normalize_label(dest.get("tab")))
    wanted_names = list(registry.get("hidden_tabs") or [])
    for source in sources.values():
        if source.get("hide_tab"):
            for tab_name in source.get("tab_names", []):
                _add_unique_name(wanted_names, tab_name)
    wanted_hidden = []
    for tab_name in wanted_names:
        key = _normalize_label(tab_name)
        if key in protected:
            continue
        tab = _find_tab(ribbon, [tab_name])
        if tab is not None and not any(tab is t for t in wanted_hidden):
            wanted_hidden.append(tab)
    for tab in wanted_hidden:
        if _try_setattr(tab, "IsVisible", False):
            new_mirror["hidden"].append(tab)
            report["hidden_tabs"].append(_tab_title(tab))
    for tab in old_mirror.get("hidden", []):
        if not any(tab is t for t in wanted_hidden):
            report["shown_tabs"].append(_tab_title(tab))


# -- ribbon plumbing (duck-typed) -----------------------------------------


def _tab_panels(tab):
    panels = getattr(tab, "Panels", None)
    return [] if panels is None else panels


def _panel_source(panel):
    return getattr(panel, "Source", None)


def _panel_items(panel):
    source = _panel_source(panel)
    if source is None:
        return []
    items = getattr(source, "Items", None)
    return [] if items is None else items


def _panel_title(panel):
    return _safe_text(_getattr_safe(_panel_source(panel), "Title"))


def _tab_title(tab):
    return _safe_text(_getattr_safe(tab, "Title")) or _safe_text(_getattr_safe(tab, "Id"))


def _child_items(item):
    children = []
    for attr_name in ("Items", "SubItems", "Children"):
        value = _getattr_safe(item, attr_name)
        if value:
            children.extend(list(_safe_iter(value)))
    get_items = _getattr_safe(item, "GetItems")
    if callable(get_items):
        try:
            children.extend(list(_safe_iter(get_items())))
        except Exception:
            pass
    # a type that exposes both Items and GetItems() would list each child twice
    unique = []
    for child in children:
        if not any(child is seen for seen in unique):
            unique.append(child)
    return unique


def _collection_contains(collection, item):
    for existing in _safe_iter(collection):
        if existing is item:
            return True
    return False


def _add_item(collection, item):
    add_method = getattr(collection, "Add", None)
    if callable(add_method):
        add_method(item)
        return
    collection.append(item)


def _remove_item(collection, item):
    if collection is None:
        return
    remove_method = getattr(collection, "Remove", None)
    if callable(remove_method):
        remove_method(item)
        return
    if item in collection:
        collection.remove(item)


def _safe_len(collection):
    try:
        return len(list(_safe_iter(collection)))
    except Exception:
        return 0


def _normalize_label(value):
    text = _safe_text(value).replace("\\n", " ").replace("\n", " ")
    return " ".join(text.split()).lower()


def _safe_text(value):
    if value is None:
        return ""
    try:
        if isinstance(value, type(u"")):
            return value
        return str(value)
    except Exception:
        return ""


def _safe_iter(value):
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _getattr_safe(obj, attr_name, default=None):
    if obj is None:
        return default
    try:
        return getattr(obj, attr_name)
    except Exception:
        return default


def _try_setattr(obj, attr_name, value):
    try:
        setattr(obj, attr_name, value)
        return True
    except Exception:
        return False


def _log_debug(logger, message):
    if logger is None:
        return
    try:
        logger.debug("[My Ribbon] {0}".format(message))
    except Exception:
        pass


def _get_logger():
    try:
        return script.get_logger() if script is not None else None
    except Exception:
        return None


def _get_default_autodesk_windows():
    try:
        import clr
        clr.AddReference("AdWindows")
        import Autodesk.Windows
        import Autodesk
        return Autodesk.Windows
    except Exception:
        return None


def _get_default_ribbon():
    autodesk_windows = _get_default_autodesk_windows()
    if autodesk_windows is None:
        return None
    try:
        return autodesk_windows.ComponentManager.Ribbon
    except Exception:
        return None
