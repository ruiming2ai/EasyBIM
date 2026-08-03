# -*- coding: utf-8 -*-
"""Show Clash Detection Mode's state on its ribbon button.

The panel can be closed and the alert dismissed, so without this there is no
way to tell a running session from a stopped one without clicking. A dot on
the button answers that at a glance: green while running, amber while paused,
nothing when off.

The dot is *drawn over whatever image the button currently has* rather than
swapped for a pre-baked icon. That keeps it correct in both the light and dark
Revit themes without shipping four more PNGs, and it survives pyRevit choosing
the icon variant at load time.

The ribbon-walking helpers are deliberately a local copy of the ones in
``view_template_ribbon`` - the same trade-off ``compat.py`` documents, so the
desktop test suite can load either module standalone.
"""

from __future__ import print_function


TARGET_TAB_ALIASES = ("EasyBIM",)
#: Must match the pushbutton's ``bundle.yaml`` title, newline included -
#: renaming the button without updating this silently loses the badge.
TARGET_ITEM_ALIASES = (
    "Clash Detection Mode",
    "Clash Detection\nMode",
)

STATE_ON = "on"
STATE_PAUSED = "paused"

#: (red, green, blue) of the dot per state.
STATE_COLORS = {
    STATE_ON: (0x2E, 0xA0, 0x43),
    STATE_PAUSED: (0xE8, 0x9C, 0x0E),
}

_ORIGINAL_IMAGES = {}
_CURRENT_STATE = [None]


def set_state(state, ribbon=None, logger=None):
    """Badge the button for ``state``; ``None`` restores the original icon."""
    logger = logger or _get_logger()
    if state == _CURRENT_STATE[0]:
        return True

    item = _find_target_item(ribbon)
    if item is None:
        _log_debug(logger, "Clash Detection button not found on the ribbon.")
        return False

    _remember_original(item)

    if state is None:
        applied = _restore(item)
    else:
        applied = _apply_badge(item, STATE_COLORS.get(state, STATE_COLORS[STATE_ON]))

    if applied:
        _CURRENT_STATE[0] = state
    return applied


def current_state():
    return _CURRENT_STATE[0]


def _find_target_item(ribbon=None):
    try:
        ribbon = ribbon or _get_default_ribbon()
    except Exception:
        return None
    if ribbon is None:
        return None
    tab = _find_tab(ribbon, TARGET_TAB_ALIASES)
    if tab is None:
        return None
    return _find_item_by_aliases(_tab_items(tab), TARGET_ITEM_ALIASES)


def _remember_original(item):
    if _ORIGINAL_IMAGES:
        return
    for attr_name in ("Image", "LargeImage"):
        _ORIGINAL_IMAGES[attr_name] = _getattr_safe(item, attr_name)


def _restore(item):
    restored = False
    for attr_name, image in _ORIGINAL_IMAGES.items():
        if image is not None and _try_setattr(item, attr_name, image):
            restored = True
    return restored


def _apply_badge(item, rgb):
    applied = False
    for attr_name in ("Image", "LargeImage"):
        source = _ORIGINAL_IMAGES.get(attr_name)
        if source is None:
            continue
        badged = _draw_badge(source, rgb)
        if badged is not None and _try_setattr(item, attr_name, badged):
            applied = True
    return applied


def _draw_badge(image_source, rgb):
    """Render ``image_source`` with a status dot in its top-right corner."""
    try:
        from System.Windows import Point
        from System.Windows import Rect
        from System.Windows.Media import Brushes
        from System.Windows.Media import Color
        from System.Windows.Media import DrawingVisual
        from System.Windows.Media import PixelFormats
        from System.Windows.Media import SolidColorBrush
        from System.Windows.Media.Imaging import RenderTargetBitmap

        width = int(getattr(image_source, "Width", 0) or 32)
        height = int(getattr(image_source, "Height", 0) or 32)
        if width <= 0 or height <= 0:
            return None

        visual = DrawingVisual()
        context = visual.RenderOpen()
        context.DrawImage(image_source, Rect(0, 0, width, height))

        radius = max(3.0, width * 0.22)
        center = Point(width - radius - 1.0, radius + 1.0)
        # White disc first so the dot reads against a busy icon; the coloured
        # dot sits inside it.  No Pen needed, which keeps this version-safe.
        context.DrawEllipse(Brushes.White, None, center, radius + 1.2, radius + 1.2)
        context.DrawEllipse(
            SolidColorBrush(Color.FromRgb(rgb[0], rgb[1], rgb[2])),
            None,
            center,
            radius,
            radius,
        )
        context.Close()

        target = RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32)
        target.Render(visual)
        target.Freeze()
        return target
    except Exception:
        return None


# -- ribbon walking -------------------------------------------------------


def _find_tab(ribbon, aliases):
    alias_keys = [_normalize_label(alias) for alias in aliases or []]
    for tab in _safe_iter(_getattr_safe(ribbon, "Tabs", [])):
        for attr_name in ("Id", "Title", "Name", "AutomationName"):
            if _normalize_label(_getattr_safe(tab, attr_name)) in alias_keys:
                return tab
    return None


def _tab_items(tab):
    items = []
    for panel in _safe_iter(_getattr_safe(tab, "Panels", [])):
        source = _getattr_safe(panel, "Source")
        items.extend(list(_safe_iter(_getattr_safe(source, "Items", []))))
    return items


def _find_item_by_aliases(items, aliases):
    alias_keys = [_normalize_label(alias) for alias in aliases or []]
    return _find_item(items, alias_keys)


def _find_item(items, alias_keys):
    for item in _safe_iter(items):
        if _item_matches(item, alias_keys):
            return item
        child = _find_item(_child_items(item), alias_keys)
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


def _log_debug(logger, message):
    if logger is None:
        return
    try:
        logger.debug("[Clash Detection Ribbon] {0}".format(message))
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

        return Autodesk.Windows.ComponentManager.Ribbon
    except Exception:
        return None
