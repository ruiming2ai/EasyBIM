# -*- coding: utf-8 -*-
"""Classify DB.View objects and derive exact UI-caption candidates."""

from __future__ import absolute_import


try:
    text_type = unicode
except NameError:
    text_type = str


def _text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return u""


def element_id_value(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.Value)
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return _text(element_id)


def view_type_name(view):
    """Return a stable, non-localized ViewType enum member name."""
    try:
        value = _text(view.ViewType)
    except Exception:
        return "Undefined"
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value or "Undefined"


def matching_rule(view_type, profile):
    fallback = None
    for rule in profile.get("rules", []):
        if rule.get("id") == "other":
            fallback = rule
        if view_type in rule.get("view_types", []):
            return rule
    return fallback


def _add_candidate(result, value):
    value = _text(value)
    if not value:
        return
    for candidate in (value, value.strip()):
        if candidate and candidate not in result:
            result.append(candidate)


def view_caption_candidates(view):
    """Return DB-derived strings that Revit may use as the visible tab title.

    View.Name is commonly the header. View.Title is also included because the
    Revit API documents it as the display title plus view-specific modifiers.
    Sheet variants cover common Revit header formats without using them to
    infer the type.
    """
    candidates = []
    try:
        _add_candidate(candidates, view.Name)
    except Exception:
        pass
    try:
        _add_candidate(candidates, view.Title)
    except Exception:
        pass

    if view_type_name(view) == "DrawingSheet":
        number = u""
        name = u""
        try:
            number = _text(view.SheetNumber).strip()
        except Exception:
            pass
        try:
            name = _text(view.Name).strip()
        except Exception:
            pass
        if number:
            _add_candidate(candidates, number)
        if number and name:
            _add_candidate(candidates, u"{0} - {1}".format(number, name))
            _add_candidate(candidates, u"{0}: {1}".format(number, name))
            _add_candidate(candidates, u"Sheet: {0} - {1}".format(number, name))
            _add_candidate(candidates, u"Sheet {0} - {1}".format(number, name))
    return candidates


def host_sheet_caption_candidates(view):
    """Return only sheet-number-qualified candidates for an inferred host.

    The Revit API does not identify which visual sheet tab owns an activated
    viewport. A host sheet inferred from a placed active view is therefore
    deliberately prevented from claiming its unqualified sheet Name, which
    could collide with a real open view. Native sheet tabs normally include
    the sheet number through View.Title or an equivalent formatted caption.
    """
    try:
        number = _text(view.SheetNumber).strip()
    except Exception:
        number = u""
    if not number:
        return []
    try:
        name = _text(view.Name).strip()
    except Exception:
        name = u""
    candidates = []
    try:
        title = _text(view.Title).strip()
        if number in title:
            _add_candidate(candidates, title)
    except Exception:
        pass
    _add_candidate(candidates, number)
    if name:
        _add_candidate(candidates, u"{0} - {1}".format(number, name))
        _add_candidate(candidates, u"{0}: {1}".format(number, name))
        _add_candidate(candidates, u"Sheet: {0} - {1}".format(number, name))
        _add_candidate(candidates, u"Sheet {0} - {1}".format(number, name))
    return candidates


def document_runtime_key(document):
    try:
        path = _text(document.PathName)
    except Exception:
        path = u""
    try:
        title = _text(document.Title)
    except Exception:
        title = u"<untitled>"
    try:
        runtime_hash = str(document.GetHashCode())
    except Exception:
        runtime_hash = "unknown"
    return u"{0}|{1}|{2}".format(path, title, runtime_hash)


def make_entry(view, document, profile, source="open-ui-view"):
    try:
        if bool(view.IsTemplate):
            return None
    except Exception:
        pass

    type_name = view_type_name(view)
    rule = matching_rule(type_name, profile)
    color = None
    category_id = "unmapped"
    category_label = "Unmapped"
    enabled = False
    if rule:
        category_id = rule.get("id", "unmapped")
        category_label = rule.get("label", category_id)
        enabled = bool(rule.get("enabled", False))
        color = rule.get("color") if enabled else None

    try:
        document_title = _text(document.Title)
    except Exception:
        document_title = u"<untitled>"
    try:
        view_name = _text(view.Name)
    except Exception:
        view_name = u""
    try:
        unique_id = _text(view.UniqueId)
    except Exception:
        unique_id = u""

    captions = view_caption_candidates(view)
    if source == "activated-viewport-host":
        captions = host_sheet_caption_candidates(view)

    return {
        "document_key": document_runtime_key(document),
        "document_title": document_title,
        "view_id": element_id_value(getattr(view, "Id", None)),
        "view_unique_id": unique_id,
        "view_name": view_name,
        "view_type": type_name,
        "category_id": category_id,
        "category_label": category_label,
        "category_enabled": enabled,
        "color": color,
        "captions": captions,
        "source": source,
    }
