# -*- coding: utf-8 -*-
"""Revit/pyRevit runtime adapter.

View type classification is performed on DB.View objects. pyRevit's existing
document tab colorizer is used only to carry exact, escaped captions to Revit's
unsupported AvalonDock UI layer.
"""

from __future__ import absolute_import

import hashlib
import json

from viewtabcolors import classifier
from viewtabcolors import config
from viewtabcolors import mapping


OWNER_ENVVAR = "VIEWTABCOLOR_OWNER_ACTIVE"
SIGNATURE_ENVVAR = "VIEWTABCOLOR_SIGNATURE"
STATUS_ENVVAR = "VIEWTABCOLOR_STATUS"
PREVIOUS_THEME_ENVVAR = "VIEWTABCOLOR_PREVIOUS_THEME"
PREVIOUS_STATE_ENVVAR = "VIEWTABCOLOR_PREVIOUS_STATE"
OWNED_THEME_ENVVAR = "VIEWTABCOLOR_OWNED_THEME"
FULL_TAB_STYLE_NAME = "Background Fill"


def _api():
    from pyrevit import DB, HOST_APP, UI
    from pyrevit.coreutils import envvars
    from pyrevit.revit import tabs
    from pyrevit.runtime import types
    from pyrevit.userconfig import user_config
    from System.Text.RegularExpressions import Regex

    return DB, UI, HOST_APP, envvars, tabs, types, user_config, Regex


def _safe_get_env(envvars, key, default=None):
    try:
        value = envvars.get_pyrevit_env_var(key)
        return default if value is None else value
    except Exception:
        return default


def _safe_set_env(envvars, key, value):
    try:
        envvars.set_pyrevit_env_var(key, value)
    except Exception:
        pass


def _set_status(envvars, status):
    _safe_set_env(envvars, STATUS_ENVVAR, json.dumps(status, sort_keys=True))


def get_status():
    try:
        unused = _api()
        envvars = unused[3]
        raw = _safe_get_env(envvars, STATUS_ENVVAR, "")
        if raw:
            return json.loads(str(raw))
    except Exception:
        pass
    return {
        "state": "not-run",
        "message": "Tab Color is not enabled in this Revit session.",
    }


def _view_identity(view):
    try:
        return classifier.document_runtime_key(view.Document), str(view.UniqueId)
    except Exception:
        return None, str(getattr(view, "Id", "unknown"))


def _same_document(left, right):
    if left is None or right is None:
        return False
    try:
        return bool(left.Equals(right))
    except Exception:
        return classifier.document_runtime_key(left) == classifier.document_runtime_key(right)


def _same_object(left, right):
    if left is None or right is None:
        return left is right
    try:
        from System import Object

        return bool(Object.ReferenceEquals(left, right))
    except Exception:
        return left is right


def _document_open_views(DB, UI, uiapp, document):
    views = []
    try:
        if bool(document.IsLinked):
            return views
    except Exception:
        pass

    uidocument = None
    owns_uidocument = False
    try:
        active_uidocument = getattr(uiapp, "ActiveUIDocument", None)
        if active_uidocument is not None and _same_document(
            active_uidocument.Document, document
        ):
            uidocument = active_uidocument
        else:
            uidocument = UI.UIDocument(document)
            owns_uidocument = True
        for ui_view in list(uidocument.GetOpenUIViews()):
            view = document.GetElement(ui_view.ViewId)
            if view is not None:
                views.append(view)
    except Exception as ex:
        config.log(
            "Skipped non-visible document '{0}': {1}".format(
                getattr(document, "Title", "<unknown>"), ex
            )
        )
    finally:
        if uidocument is not None and owns_uidocument:
            try:
                uidocument.Dispose()
            except Exception:
                pass
    return views


def _add_host_sheets(DB, document, candidate_views):
    """Add host sheets for activated viewports.

    Autodesk documents that GetOpenUIViews returns the viewport's underlying
    view while a viewport is activated. Adding its owning sheet keeps the sheet
    caption mapped to DrawingSheet instead of incorrectly recoloring the tab as
    a plan/section/etc.
    """
    wanted_ids = set()
    for view in candidate_views:
        if classifier.view_type_name(view) != "DrawingSheet":
            wanted_ids.add(classifier.element_id_value(getattr(view, "Id", None)))
    if not wanted_ids:
        return []

    sheets_by_view_id = dict((view_id, []) for view_id in wanted_ids)
    try:
        viewports = DB.FilteredElementCollector(document).OfClass(DB.Viewport)
        for viewport in viewports:
            view_id = classifier.element_id_value(getattr(viewport, "ViewId", None))
            if view_id not in wanted_ids:
                continue
            sheet_id = getattr(viewport, "SheetId", None)
            if sheet_id is None:
                sheet_id = getattr(viewport, "OwnerViewId", None)
            sheet = document.GetElement(sheet_id) if sheet_id is not None else None
            if sheet is None:
                continue
            candidates = sheets_by_view_id[view_id]
            identity = _view_identity(sheet)
            if all(_view_identity(existing) != identity for existing in candidates):
                candidates.append(sheet)
    except Exception as ex:
        config.log("Could not resolve host sheets: {0}".format(ex))
    # Legends can appear on multiple sheets, and the supported API does not
    # expose which containing tab is active. Infer a host only when unique.
    sheets = []
    for candidates in sheets_by_view_id.values():
        if len(candidates) == 1:
            sheets.append(candidates[0])
    return sheets


def collect_entries(uiapp, profile, extra_views=None):
    DB, UI, unused_host, unused_env, unused_tabs, unused_types, unused_cfg, unused_rx = _api()
    all_views = []
    seen = set()

    try:
        documents = list(uiapp.Application.Documents)
    except Exception:
        documents = []

    extra_by_document = {}
    for extra in extra_views or []:
        if extra is None:
            continue
        try:
            document_key = classifier.document_runtime_key(extra.Document)
            extra_by_document.setdefault(document_key, []).append(extra)
        except Exception:
            pass

    for document in documents:
        document_views = _document_open_views(DB, UI, uiapp, document)
        document_key = classifier.document_runtime_key(document)
        event_views = extra_by_document.get(document_key, [])
        document_views.extend(event_views)

        host_candidates = list(event_views)
        if not host_candidates:
            try:
                active_uidocument = uiapp.ActiveUIDocument
                if active_uidocument is not None and _same_document(
                    active_uidocument.Document, document
                ):
                    host_candidates.append(active_uidocument.ActiveView)
            except Exception:
                pass
        host_sheets = _add_host_sheets(DB, document, host_candidates)

        for view in document_views:
            identity = _view_identity(view)
            if identity in seen:
                continue
            seen.add(identity)
            all_views.append((document, view, "open-ui-view"))
        for sheet in host_sheets:
            identity = _view_identity(sheet)
            if identity in seen:
                continue
            seen.add(identity)
            all_views.append((document, sheet, "activated-viewport-host"))

    # An event view can belong to a document that disappeared from the
    # collection between event delivery and this snapshot. Add it defensively.
    for extra in extra_views or []:
        if extra is None:
            continue
        identity = _view_identity(extra)
        if identity in seen:
            continue
        try:
            document = extra.Document
            seen.add(identity)
            all_views.append((document, extra, "event-view"))
        except Exception:
            pass

    entries = []
    for document, view, source in all_views:
        entry = classifier.make_entry(view, document, profile, source=source)
        if entry:
            entries.append(entry)
    entries.sort(
        key=lambda item: (
            item.get("document_title", ""),
            item.get("view_type", ""),
            item.get("view_name", ""),
        )
    )
    return entries


def _signature(profile, entries, assignments, conflicts):
    payload = {
        "profile": profile,
        "views": [
            [
                entry.get("document_key"),
                entry.get("view_unique_id"),
                entry.get("view_type"),
                entry.get("color"),
                entry.get("captions"),
            ]
            for entry in entries
        ],
        "assignments": sorted(assignments.keys()),
        "conflicts": sorted(conflicts.keys()),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _clear_collection(collection):
    try:
        collection.Clear()
        return
    except Exception:
        pass
    try:
        while collection.Count:
            collection.RemoveAt(collection.Count - 1)
    except Exception:
        pass


def _style_name(style):
    try:
        return str(style.Name)
    except Exception:
        return ""


def _available_tab_styles(types):
    try:
        return list(types.TabColoringTheme.AvailableStyles)
    except Exception:
        return []


def _select_full_tab_style(types):
    """Select pyRevit's solid full-background style without relying on index."""
    styles = _available_tab_styles(types)
    wanted = FULL_TAB_STYLE_NAME.lower()
    for style in styles:
        if _style_name(style).strip().lower() == wanted:
            return style, [_style_name(item) for item in styles]

    # Name matching is preferred, but FillBackground is the actual behavior
    # required and survives a renamed/localized style label.
    for style in styles:
        try:
            if bool(style.FillBackground):
                return style, [_style_name(item) for item in styles]
        except Exception:
            pass
    return None, [_style_name(item) for item in styles]


def _force_full_tab_style(theme, types):
    style, available_names = _select_full_tab_style(types)
    if style is None:
        available = ", ".join(name for name in available_names if name)
        if not available:
            available = "none reported"
        raise RuntimeError(
            "pyRevit's Background Fill tab style is unavailable "
            "(available: {0}).".format(available)
        )
    theme.TabStyle = style
    theme.FamilyTabStyle = style
    return _style_name(style), available_names


def _styled_slot_count(tabs):
    try:
        return len(tabs.get_styled_slots() or [])
    except Exception:
        return None


def _theme_diagnostics(tabs, types):
    details = {
        "active_tab_style": "Unavailable",
        "active_family_tab_style": "Unavailable",
        "available_tab_styles": [],
        "styled_slots": None,
        "active_order_rules": None,
        "active_filters": [],
    }
    details["available_tab_styles"] = [
        _style_name(style) for style in _available_tab_styles(types)
    ]
    try:
        theme = types.DocumentTabEventUtils.TabColoringTheme
        if theme is None:
            return details
        details["active_tab_style"] = _style_name(theme.TabStyle) or "Unnamed"
        details["active_family_tab_style"] = (
            _style_name(theme.FamilyTabStyle) or "Unnamed"
        )
        try:
            details["active_order_rules"] = int(theme.TabOrderRules.Count)
        except Exception:
            pass
        for index in range(int(theme.TabFilterRules.Count)):
            try:
                color, pattern = tabs.get_tab_filterrule(theme, index)
                details["active_filters"].append(
                    {"color": color, "pattern": pattern}
                )
            except Exception:
                pass
    except Exception:
        pass
    details["styled_slots"] = _styled_slot_count(tabs)
    return details


def _snapshot_previous(envvars, document_tab_utils):
    if bool(_safe_get_env(envvars, OWNER_ENVVAR, False)):
        owned_theme = _safe_get_env(envvars, OWNED_THEME_ENVVAR, None)
        active_theme = getattr(document_tab_utils, "TabColoringTheme", None)
        if _same_object(owned_theme, active_theme):
            return
        # pyRevit or another add-in replaced the singleton theme. Treat that
        # new theme as the state to restore instead of keeping a stale object.
        _safe_set_env(envvars, OWNER_ENVVAR, False)
        _safe_set_env(envvars, OWNED_THEME_ENVVAR, None)
        _safe_set_env(envvars, SIGNATURE_ENVVAR, "")
    try:
        _safe_set_env(
            envvars, PREVIOUS_THEME_ENVVAR, document_tab_utils.TabColoringTheme
        )
        _safe_set_env(
            envvars,
            PREVIOUS_STATE_ENVVAR,
            bool(document_tab_utils.IsUpdatingDocumentTabs),
        )
    except Exception:
        pass


def _replace_active_theme(uiapp, theme, document_tab_utils):
    if bool(document_tab_utils.IsUpdatingDocumentTabs):
        document_tab_utils.StopGroupingDocumentTabs()
    document_tab_utils.TabColoringTheme = theme
    document_tab_utils.StartGroupingDocumentTabs(uiapp)


def _rollback_theme(uiapp, theme, running, document_tab_utils):
    try:
        if bool(document_tab_utils.IsUpdatingDocumentTabs):
            document_tab_utils.StopGroupingDocumentTabs()
        document_tab_utils.TabColoringTheme = theme
        if running:
            document_tab_utils.StartGroupingDocumentTabs(uiapp)
    except Exception as ex:
        config.log("Theme rollback failed: {0}".format(ex))


def restore_previous(uiapp=None):
    """Restore the tab theme/state that existed before this feature took over."""
    DB, UI, HOST_APP, envvars, tabs, types, user_config, Regex = _api()
    del DB, UI, Regex
    if uiapp is None:
        uiapp = HOST_APP.uiapp
    if not bool(_safe_get_env(envvars, OWNER_ENVVAR, False)):
        return {
            "state": "disabled",
            "message": "Tab Color is disabled.",
        }

    document_tab_utils = types.DocumentTabEventUtils
    previous_theme = _safe_get_env(envvars, PREVIOUS_THEME_ENVVAR, None)
    previous_state = bool(_safe_get_env(envvars, PREVIOUS_STATE_ENVVAR, False))
    if previous_theme is None:
        previous_theme = tabs.get_tabcoloring_theme(user_config)
        previous_state = bool(getattr(user_config, "colorize_docs", False))

    try:
        if bool(document_tab_utils.IsUpdatingDocumentTabs):
            document_tab_utils.StopGroupingDocumentTabs()
        document_tab_utils.TabColoringTheme = previous_theme
        if previous_state:
            document_tab_utils.StartGroupingDocumentTabs(uiapp)
    except Exception as ex:
        config.log("Could not restore prior tab theme: {0}".format(ex))
        status = {
            "state": "error",
            "message": "The prior pyRevit tab theme could not be restored: {0}".format(
                ex
            ),
        }
        _set_status(envvars, status)
        return status
    else:
        _safe_set_env(envvars, OWNER_ENVVAR, False)
        _safe_set_env(envvars, SIGNATURE_ENVVAR, "")
        _safe_set_env(envvars, PREVIOUS_THEME_ENVVAR, None)
        _safe_set_env(envvars, PREVIOUS_STATE_ENVVAR, False)
        _safe_set_env(envvars, OWNED_THEME_ENVVAR, None)

    status = {
        "state": "disabled",
        "message": "Tab Color is disabled; the prior pyRevit tab theme was restored.",
    }
    _set_status(envvars, status)
    return status


def apply(uiapp=None, extra_views=None, force=False):
    """Reconcile API-derived tab-caption filters with open DB.View objects."""
    try:
        DB, UI, HOST_APP, envvars, tabs, types, user_config, Regex = _api()
        del DB, UI
    except Exception as ex:
        config.log("pyRevit tab API is unavailable: {0}".format(ex))
        return {
            "state": "unsupported",
            "message": "This pyRevit build does not expose its tab colorizer API.",
        }

    if uiapp is None:
        uiapp = HOST_APP.uiapp
    profile = config.load_profile()
    if not profile.get("enabled", False):
        return restore_previous(uiapp)

    if not hasattr(types, "DocumentTabEventUtils"):
        status = {
            "state": "unsupported",
            "message": "DocumentTabEventUtils is unavailable. Update pyRevit.",
        }
        _set_status(envvars, status)
        return status

    try:
        entries = collect_entries(uiapp, profile, extra_views)
        assignments, conflicts = mapping.build_caption_assignments(entries)
        runtime_rules = mapping.build_filter_rules(
            assignments, Regex.Escape, entries=entries
        )
        signature = _signature(profile, entries, assignments, conflicts)

        current_signature = str(_safe_get_env(envvars, SIGNATURE_ENVVAR, ""))
        is_owner = bool(_safe_get_env(envvars, OWNER_ENVVAR, False))
        document_tab_utils = types.DocumentTabEventUtils
        owned_theme = _safe_get_env(envvars, OWNED_THEME_ENVVAR, None)
        active_theme = getattr(document_tab_utils, "TabColoringTheme", None)
        still_owns_active_theme = is_owner and _same_object(
            owned_theme, active_theme
        )
        if (
            not force
            and still_owns_active_theme
            and current_signature == signature
        ):
            return get_status()

        _snapshot_previous(envvars, document_tab_utils)

        active_theme_before = getattr(document_tab_utils, "TabColoringTheme", None)
        active_state_before = bool(document_tab_utils.IsUpdatingDocumentTabs)

        # Start from the user's normal pyRevit theme, but put our literal rules
        # first. The theme exists only in memory; pyRevit's config file is not
        # rewritten. User filter and document-order rules are suspended while
        # this extension owns the colorizer and restored when it is disabled.
        theme = tabs.get_tabcoloring_theme(user_config)
        # Disable document-order colors while this extension owns the
        # singleton colorizer. Otherwise an unmatched tab can be painted by
        # project order and look as though it received a view-type color.
        theme.SortDocTabs = False
        tab_style, available_styles = _force_full_tab_style(theme, types)
        # SortDocTabs controls tab ordering, not whether the inherited order
        # palette can act as a fallback color source. Remove that palette so
        # only the API-derived view-type filters can paint a tab.
        _clear_collection(theme.TabOrderRules)
        _clear_collection(theme.TabFilterRules)
        for rule in runtime_rules:
            tabs.add_tab_filterrule(theme, rule["color"], rule["pattern"])

        try:
            _replace_active_theme(uiapp, theme, document_tab_utils)
        except Exception:
            _rollback_theme(
                uiapp,
                active_theme_before,
                active_state_before,
                document_tab_utils,
            )
            raise
        _safe_set_env(envvars, OWNER_ENVVAR, True)
        _safe_set_env(envvars, OWNED_THEME_ENVVAR, theme)
        _safe_set_env(envvars, SIGNATURE_ENVVAR, signature)

        status = {
            "state": "running",
            "message": "Tab Color is running with full-tab color.",
            "open_views": len(entries),
            "safe_captions": len(assignments),
            "emitted_caption_tokens": sum(
                rule.get("caption_count", 0) for rule in runtime_rules
            ),
            "ambiguous_captions": len(conflicts),
            "runtime_filters": len(runtime_rules),
            "runtime_order_rules": 0,
            "tab_style": tab_style,
            "available_tab_styles": available_styles,
            "matching_mode": "API caption token inside internal tab title",
            "styled_slots": _styled_slot_count(tabs),
        }
        _set_status(envvars, status)
        return status
    except Exception as ex:
        config.log("Apply failed: {0}".format(ex))
        status = {
            "state": "error",
            "message": "Tab Color could not apply: {0}".format(ex),
        }
        _set_status(envvars, status)
        return status


def apply_from_event(uiapp, event_args=None):
    extras = []
    if event_args is not None:
        for property_name in ("CurrentActiveView", "NewActiveView"):
            try:
                value = getattr(event_args, property_name)
                if value is not None and value not in extras:
                    extras.append(value)
            except Exception:
                pass
    return apply(uiapp, extra_views=extras, force=False)


def diagnostics(uiapp=None):
    DB, UI, HOST_APP, envvars, tabs, types, user_config, Regex = _api()
    del DB, UI, envvars, user_config
    if uiapp is None:
        uiapp = HOST_APP.uiapp
    profile = config.load_profile()
    entries = collect_entries(uiapp, profile, [])
    assignments, conflicts = mapping.build_caption_assignments(entries)
    rules = mapping.build_filter_rules(assignments, Regex.Escape, entries=entries)
    return {
        "profile": profile,
        "status": get_status(),
        "entries": entries,
        "assignments": assignments,
        "conflicts": conflicts,
        "runtime_rules": rules,
        "theme": _theme_diagnostics(tabs, types),
    }
