# -*- coding: utf-8 -*-
"""Add EasyBIM command shortcuts to Revit's native Modify ribbon tab."""

from __future__ import print_function


MODIFY_TAB_ID = "Modify"
MODIFY_PANEL_ID = "EasyBIM_Modify_Shortcuts"
MODIFY_PANEL_TITLE = "EasyBIM"
SOURCE_TAB_TITLE = "EasyBIM"

SHORTCUT_SPECS = (
    ("Slope", ("Slope",)),
    ("3D Rotate Multiple", ("3D Rotate Multiple", "3D Rotate\nMultiple")),
    ("Flip Multiple", ("Flip Multiple", "Flip\nMultiple")),
)


def register_modify_shortcuts(ribbon=None, autodesk_windows=None, logger=None):
    """Add shortcuts for existing EasyBIM commands to the Modify tab.

    The shortcuts reuse the already-created pyRevit ribbon items. This preserves
    the original command handlers and keeps the EasyBIM tab unchanged.
    """
    result = {
        "added": [],
        "already_present": [],
        "missing": [],
        "errors": [],
    }
    logger = logger or _get_logger()

    try:
        ribbon = ribbon or _get_default_ribbon()
    except Exception as ex:
        _record_error(result, logger, "Ribbon unavailable: {}".format(_safe_text(ex)))
        return result

    if ribbon is None:
        _record_error(result, logger, "Ribbon unavailable.")
        return result

    modify_tab = _find_tab(ribbon, MODIFY_TAB_ID)
    if modify_tab is None:
        _record_error(result, logger, "Modify tab not found.")
        return result

    source_tab = _find_tab(ribbon, SOURCE_TAB_TITLE, excluded_tab=modify_tab)
    if source_tab is None:
        _record_error(result, logger, "EasyBIM source tab not found.")
        return result

    try:
        target_panel = _find_or_create_modify_panel(
            modify_tab,
            autodesk_windows=autodesk_windows,
        )
    except Exception as ex:
        _record_error(result, logger, "Modify panel unavailable: {}".format(_safe_text(ex)))
        return result

    if target_panel is None:
        _record_error(result, logger, "Modify panel unavailable.")
        return result

    for display_label, aliases in SHORTCUT_SPECS:
        source_item = _find_source_item(source_tab, aliases, excluded_panel=target_panel)
        if source_item is None:
            result["missing"].append(display_label)
            _log_debug(logger, "Modify shortcut source missing: {}".format(display_label))
            continue

        if _panel_has_item(target_panel, source_item, aliases):
            result["already_present"].append(display_label)
            continue

        try:
            _add_item(_panel_items(target_panel), source_item)
            result["added"].append(display_label)
        except Exception as ex:
            _record_error(
                result,
                logger,
                "Could not add {} shortcut: {}".format(display_label, _safe_text(ex)),
            )

    return result


def _find_or_create_modify_panel(modify_tab, autodesk_windows=None):
    existing_panel = _find_panel(modify_tab, MODIFY_PANEL_ID, MODIFY_PANEL_TITLE)
    if existing_panel is not None:
        return existing_panel

    if autodesk_windows is None:
        autodesk_windows = _get_default_autodesk_windows()
    if autodesk_windows is None:
        return None

    panel_source = autodesk_windows.RibbonPanelSource()
    _try_setattr(panel_source, "Id", MODIFY_PANEL_ID)
    _try_setattr(panel_source, "Title", MODIFY_PANEL_TITLE)
    _try_setattr(panel_source, "AutomationName", MODIFY_PANEL_TITLE)

    panel = autodesk_windows.RibbonPanel()
    _try_setattr(panel, "Source", panel_source)
    _add_item(_tab_panels(modify_tab), panel)
    return panel


def _find_tab(ribbon, target_label, excluded_tab=None):
    target_key = _normalize_label(target_label)
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        if excluded_tab is not None and tab is excluded_tab:
            continue
        for attr_name in ("Id", "Title", "Name", "AutomationName"):
            if _normalize_label(_getattr_safe(tab, attr_name)) == target_key:
                return tab
    return None


def _find_panel(tab, panel_id, panel_title):
    target_id = _safe_text(panel_id)
    target_title_key = _normalize_label(panel_title)
    for panel in _tab_panels(tab):
        source = _panel_source(panel)
        if _safe_text(_getattr_safe(source, "Id")) == target_id:
            return panel
        for attr_name in ("Title", "AutomationName", "Name"):
            if _normalize_label(_getattr_safe(source, attr_name)) == target_title_key:
                return panel
    return None


def _find_source_item(source_tab, aliases, excluded_panel=None):
    for panel in _tab_panels(source_tab):
        if excluded_panel is not None and panel is excluded_panel:
            continue
        item = _find_item_by_aliases(_panel_items(panel), aliases)
        if item is not None:
            return item
    return None


def _find_item_by_aliases(items, aliases):
    aliases = [_normalize_label(alias) for alias in aliases or []]
    for item in _safe_iter(items):
        if _item_matches(item, aliases):
            return item
        child = _find_item_by_aliases(_child_items(item), aliases)
        if child is not None:
            return child
    return None


def _panel_has_item(panel, source_item, aliases):
    source_id = _safe_text(_getattr_safe(source_item, "Id"))
    alias_keys = [_normalize_label(alias) for alias in aliases or []]
    for item in _safe_iter(_panel_items(panel)):
        if item is source_item:
            return True
        if source_id and _safe_text(_getattr_safe(item, "Id")) == source_id:
            return True
        if _item_matches(item, alias_keys):
            return True
    return False


def _item_matches(item, alias_keys):
    if not item:
        return False
    for attr_name in ("Text", "AutomationName", "ItemText", "Title", "Name"):
        if _normalize_label(_getattr_safe(item, attr_name)) in alias_keys:
            return True
    return False


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


def _add_item(collection, item):
    add_method = getattr(collection, "Add", None)
    if callable(add_method):
        add_method(item)
        return
    collection.append(item)


def _normalize_label(value):
    text = _safe_text(value).replace("\\n", " ")
    return " ".join(text.split()).lower()


def _safe_text(value):
    if value is None:
        return ""
    try:
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


def _record_error(result, logger, message):
    result["errors"].append(message)
    _log_debug(logger, message)


def _log_debug(logger, message):
    if logger is None:
        return
    try:
        logger.debug("[Modify Ribbon] {}".format(message))
    except Exception:
        pass


def _get_logger():
    try:
        from pyrevit import script
        return script.get_logger()
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
