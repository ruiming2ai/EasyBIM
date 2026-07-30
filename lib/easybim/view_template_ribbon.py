# -*- coding: utf-8 -*-
"""Mirror Revit's native View Template icon onto the EasyBIM command."""

SOURCE_TAB_ALIASES = ("View",)
SOURCE_ITEM_ALIASES = (
    "View Templates",
    "View Template",
    "Manage View Templates",
    "Manage View Templates...",
)
TARGET_TAB_ALIASES = ("EasyBIM",)
TARGET_ITEM_ALIASES = (
    "Batch Transfer View Template Settings",
    "Batch Transfer View\nTemplate Settings",
)


def apply_native_view_template_icon(ribbon=None, logger=None):
    """Copy native View Template ribbon images to the EasyBIM command if found."""
    result = {
        "updated": False,
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
        _record_missing(result, logger, "ribbon")
        return result

    source_tab = _find_tab(ribbon, SOURCE_TAB_ALIASES)
    if source_tab is None:
        _record_missing(result, logger, "source")
        return result

    target_tab = _find_tab(ribbon, TARGET_TAB_ALIASES)
    if target_tab is None:
        _record_missing(result, logger, "target")
        return result

    source_item = _find_item_by_aliases(_tab_items(source_tab), SOURCE_ITEM_ALIASES)
    if source_item is None:
        _record_missing(result, logger, "source")
        return result

    target_item = _find_item_by_aliases(_tab_items(target_tab), TARGET_ITEM_ALIASES)
    if target_item is None:
        _record_missing(result, logger, "target")
        return result

    copied = False
    for attr_name in ("Image", "LargeImage"):
        image = _getattr_safe(source_item, attr_name)
        if image is None:
            continue
        if _try_setattr(target_item, attr_name, image):
            copied = True

    if copied:
        result["updated"] = True
    else:
        _record_missing(result, logger, "source image")
    return result


def _find_tab(ribbon, aliases):
    alias_keys = [_normalize_label(alias) for alias in aliases or []]
    for tab in _safe_iter(getattr(ribbon, "Tabs", [])):
        for attr_name in ("Id", "Title", "Name", "AutomationName"):
            if _normalize_label(_getattr_safe(tab, attr_name)) in alias_keys:
                return tab
    return None


def _tab_items(tab):
    items = []
    for panel in _safe_iter(getattr(tab, "Panels", [])):
        source = _getattr_safe(panel, "Source")
        panel_items = _getattr_safe(source, "Items", [])
        items.extend(list(_safe_iter(panel_items)))
    return items


def _find_item_by_aliases(items, aliases):
    alias_keys = [_normalize_label(alias) for alias in aliases or []]
    for item in _safe_iter(items):
        if _item_matches(item, alias_keys):
            return item
        child = _find_item_by_aliases(_child_items(item), alias_keys)
        if child is not None:
            return child
    return None


def _item_matches(item, alias_keys):
    if item is None:
        return False
    for attr_name in ("Text", "AutomationName", "ItemText", "Title", "Name"):
        if _normalize_label(_getattr_safe(item, attr_name)) in alias_keys:
            return True
    return False


def _child_items(item):
    children = []
    for attr_name in ("Items", "SubItems", "Children", "PulldownItems"):
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


def _normalize_label(value):
    text = _safe_text(value).replace("\\n", " ").replace("\n", " ")
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


def _record_missing(result, logger, label):
    result["missing"].append(label)
    _log_debug(logger, "Native View Template icon missing: {}".format(label))


def _record_error(result, logger, message):
    result["errors"].append(message)
    _log_debug(logger, message)


def _log_debug(logger, message):
    if logger is None:
        return
    try:
        logger.debug("[View Template Ribbon] {}".format(message))
    except Exception:
        pass


def _get_logger():
    try:
        from pyrevit import script
        return script.get_logger()
    except Exception:
        return None


def _get_default_ribbon():
    try:
        import clr
        clr.AddReference("AdWindows")
        import Autodesk.Windows
        import Autodesk
        return Autodesk.Windows.ComponentManager.Ribbon
    except Exception:
        return None
