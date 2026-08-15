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

#: Tabs the engine never hides, whatever the registry says: the pyRevit tab
#: carries Reload, Update, Settings and Extensions - hiding it would leave the
#: user with no way back if My Ribbon itself broke.
PROTECTED_TABS = ("pyrevit",)

SOURCE_KINDS = ("git", "catalogue", "installed")


class RegistryFormatError(Exception):
    """The file was written by a newer My Ribbon than this one."""


# -- registry -------------------------------------------------------------


def empty_registry():
    return {
        "format": FORMAT_VERSION,
        "sources": [],
        "destinations": [],
        "placements": [],
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
    registry["placements"] = [
        p for p in registry["placements"] if p["id"] and p["dest"] and p["path"]]
    return registry


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
    return source


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
    if registry.get("placements"):
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

    for placement in placements:
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

        item, reason = _find_source_item(ribbon, placement)
        if item is None:
            _miss(report, placement, reason)
            continue
        items = _panel_items(panel)
        if _collection_contains(items, item):
            report["added"].append(placement["id"])
            continue
        try:
            _add_item(items, item)
        except Exception as ex:
            _miss(report, placement, "could not add: {0}".format(_safe_text(ex)))
            continue
        new_mirror["items"].append((items, item))
        report["added"].append(placement["id"])
        _log_debug(logger, "Placed {0} in {1} > {2}".format(
            placement.get("title"), dest["tab"], dest["panel"]))

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
        "errors": [],
    }


def _miss(report, placement, reason):
    report["missing"].append({
        "placement": placement.get("id"),
        "title": placement.get("title"),
        "reason": reason,
    })


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
            "panels": panels,
        })
    return summary


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
    # without treating the stack itself as a match.
    for item in _safe_iter(items):
        if _is_stack(item):
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
    wanted_hidden = []
    for source in sources.values():
        if not source.get("hide_tab"):
            continue
        for tab_name in source.get("tab_names", []):
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
    return children


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
