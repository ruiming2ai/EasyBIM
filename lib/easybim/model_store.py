# -*- coding: utf-8 -*-
"""A small JSON record kept inside the ``.rvt`` itself - how the checkers
remember which findings you set aside.

Extensible Storage is the only mechanism that reaches a team on an ACC
cloud model: the entity travels with the model, so a Sync to Central
publishes it to everyone who opens it, and no Revit API writes files into
ACC Docs.  ``tag_align_revit`` proved the shape for Tag Align's "This
model" presets; this is the general version the two checkers share, and
repointing Tag Align at it is the deferred hoist (its own three-destination
preset tests come with it, so that is a change of its own).

**This module is the one place in the checkers that writes to the model.**
Their scans open no Transaction and are pinned that way; ``write`` opens
exactly one, touching exactly one hidden ``DataStorage`` element that holds
the record.  Nothing else in the document is read or changed by it.

Reads never raise; writes return ``(ok, reason)`` so the window can say why
rather than pretending.  ``db`` is injectable so the desktop tests drive it
with fakes shaped like the API.
"""

from __future__ import print_function

import json

try:
    from pyrevit import DB
except Exception:  # off-Revit (unit tests)
    DB = None

try:
    from easybim.compat import safe_text
    from easybim.json_text import dumps as _json_dumps
except Exception:
    # Loaded standalone by a desktop test, without the package.  Inside Revit
    # the package is always there, and json_text is not optional: the
    # standard encoder cannot write a finding whose name carries an accent.
    _json_dumps = json.dumps

    def safe_text(value):
        if value is None:
            return ""
        try:
            return str(value)
        except Exception:
            try:
                return value.ToString()
            except Exception:
                return ""


#: EasyBIM's own id for the checkers' record.  Registered once per Revit
#: session; a second build under the same GUID throws, which is why the
#: lookup below always comes first.
SCHEMA_GUID = "b7e41d92-3c58-4a6f-9d20-8e5c1f4a7b36"
SCHEMA_NAME = "EasyBIMChecks"
SCHEMA_VENDOR = "EASYBIM"
SCHEMA_FIELD = "payload"
TRANSACTION_NAME = "EasyBIM ignore list"

PAYLOAD_VERSION = 1


def _db(db):
    return db if db is not None else DB


def _storage_module(db):
    return getattr(db, "ExtensibleStorage", None) if db is not None else None


def _string_type():
    import System

    return System.String


def _exception_text(exception):
    try:
        text = safe_text(exception)
    except Exception:
        text = u""
    return text or u"unknown error"


def get_schema(db=None):
    """``(schema, reason)``.  Reuses a registered schema; never builds twice.

    If something else already owns this GUID with a different shape, the
    record is reported unavailable rather than taking the command down.
    """
    db = _db(db)
    storage = _storage_module(db)
    if storage is None:
        return None, u"This Revit build has no Extensible Storage."

    try:
        import System

        guid = System.Guid(SCHEMA_GUID)
    except Exception as ex:
        return None, _exception_text(ex)

    try:
        existing = storage.Schema.Lookup(guid)
    except Exception:
        existing = None

    if existing is not None:
        try:
            if existing.GetField(SCHEMA_FIELD) is None:
                return None, (u"Another add-in has registered a different schema under "
                              u"EasyBIM's id; the in-model record is unavailable.")
        except Exception as ex:
            return None, _exception_text(ex)
        return existing, u""

    try:
        import clr

        builder = storage.SchemaBuilder(guid)
        builder.SetSchemaName(SCHEMA_NAME)
        builder.SetVendorId(SCHEMA_VENDOR)
        builder.SetReadAccessLevel(storage.AccessLevel.Public)
        builder.SetWriteAccessLevel(storage.AccessLevel.Public)
        builder.AddSimpleField(SCHEMA_FIELD, clr.GetClrType(_string_type()))
        return builder.Finish(), u""
    except Exception as ex:
        return None, _exception_text(ex)


def availability(doc, db=None):
    """``(available, reason)`` - whether this document can carry the record."""
    if doc is None:
        return False, u"No document is open."
    if bool(getattr(doc, "IsFamilyDocument", False)):
        return False, u"Open a project rather than a family."
    if bool(getattr(doc, "IsLinked", False)):
        return False, u"This is a linked model; open it directly to store the record."
    schema, reason = get_schema(db)
    if schema is None:
        return False, reason
    return True, u""


def _find_storage(doc, schema, db):
    storage = _storage_module(db)
    if storage is None:
        return None
    try:
        collector = db.FilteredElementCollector(doc).OfClass(storage.DataStorage)
    except Exception:
        return None
    for element in collector:
        try:
            entity = element.GetEntity(schema)
            if entity is not None and entity.IsValid():
                return element
        except Exception:
            continue
    return None


def _read_text(doc, schema, db):
    element = _find_storage(doc, schema, db)
    if element is None:
        return u""
    try:
        entity = element.GetEntity(schema)
        return safe_text(entity.Get[_string_type()](SCHEMA_FIELD))
    except Exception:
        return u""


def normalize(raw):
    """Whatever is in the model -> ``{tool key: [finding keys]}``.

    A payload written by a newer EasyBIM is read as far as it is understood;
    the worst outcome of a strange record must be an empty ignore list, never
    a broken command.
    """
    payload = {"version": PAYLOAD_VERSION, "tools": {}}
    if not isinstance(raw, dict):
        return payload
    tools = raw.get("tools")
    if isinstance(tools, dict):
        for tool_key, keys in tools.items():
            if not isinstance(keys, (list, tuple)):
                continue
            names = []
            seen = set()
            for entry in keys:
                text = safe_text(entry).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                names.append(text)
            payload["tools"][safe_text(tool_key)] = sorted(names)
    return payload


def read_payload(doc, db=None):
    """The whole record, normalised.  Never raises."""
    db = _db(db)
    schema, _reason = get_schema(db)
    if schema is None or doc is None:
        return normalize(None)
    text = _read_text(doc, schema, db)
    if not text:
        return normalize(None)
    try:
        return normalize(json.loads(text))
    except Exception:
        return normalize(None)


def read(doc, tool_key, db=None):
    """The set of finding keys this tool has set aside in this model."""
    payload = read_payload(doc, db=db)
    return set(payload["tools"].get(safe_text(tool_key)) or [])


def write(doc, tool_key, keys, db=None):
    """Replace this tool's list in the model.  ``(ok, reason)``; never raises.

    One Transaction, one ``DataStorage`` element.  Other tools' lists in the
    same record are read first and written back untouched, so two checkers
    cannot clobber each other.
    """
    db = _db(db)
    available, reason = availability(doc, db=db)
    if not available:
        return False, reason
    schema, reason = get_schema(db)
    if schema is None:
        return False, reason

    payload = read_payload(doc, db=db)
    payload["tools"][safe_text(tool_key)] = sorted(
        set(safe_text(key).strip() for key in keys or [] if safe_text(key).strip()))
    text = _json_dumps(payload, indent=1, sort_keys=True)

    storage = _storage_module(db)
    try:
        transaction = db.Transaction(doc, TRANSACTION_NAME)
    except Exception as ex:
        return False, _exception_text(ex)

    try:
        transaction.Start()
    except Exception as ex:
        return False, u"Revit would not start a transaction: {0}".format(_exception_text(ex))

    try:
        element = _find_storage(doc, schema, db)
        if element is None:
            element = storage.DataStorage.Create(doc)
        entity = storage.Entity(schema)
        entity.Set[_string_type()](SCHEMA_FIELD, text)
        element.SetEntity(entity)
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return False, _exception_text(ex)
    return True, u""


def set_ignored(doc, tool_key, finding_key, ignored, db=None):
    """Add or remove one finding.  ``(ok, keys, reason)``.

    The keys handed back are what the model now holds, so a caller that
    fails never shows a row as ignored when nothing was stored.
    """
    db = _db(db)
    finding_key = safe_text(finding_key).strip()
    current = read(doc, tool_key, db=db)
    if not finding_key:
        return False, current, u"That finding has no stable key to store."
    wanted = set(current)
    if ignored:
        wanted.add(finding_key)
    else:
        wanted.discard(finding_key)
    if wanted == current:
        return True, current, u""
    ok, reason = write(doc, tool_key, wanted, db=db)
    return (ok, wanted, u"") if ok else (False, current, reason)
