# -*- coding: utf-8 -*-
"""The room-to-region relationship, kept inside the model on each region.

Extensible Storage is the only mechanism that reaches a team on an ACC cloud
model: the entity travels with the element, so a Sync to Central publishes it
to everyone who opens the model, and no Revit API writes files into ACC Docs.

The entity rides on the **filled region itself** rather than a separate
DataStorage element, which is what ``copy_monitor_storage`` does per
relationship.  A region's link to its room is a property of that region, so
storing it there means the record is created, copied and destroyed with the
thing it describes: deleting the region takes the record with it and nothing
is left dangling, and on a two-thousand room job it costs no extra elements.
Copying a region copies its record too, which reads as a lie about which view
it belongs to - and that is exactly how the tool detects a copied region.

Reads never open a transaction.  **Writes join the caller's transaction**, so
a region and its record land in the same undo step; a write outside one is a
programming error and says so rather than opening its own.
"""

from __future__ import print_function

try:
    from pyrevit import DB
except Exception:  # off-Revit (unit tests)
    DB = None

import space_boundary_state as state


#: EasyBIM's own id for this record.  Distinct from the checkers' store
#: (b7e41d92-...) and Copy Monitor's (370c7234-...); a test pins that.
SCHEMA_GUID = "a1f38c47-52d9-4b6e-8c31-7e04d95a2f68"
SCHEMA_NAME = "EasyBIMSpaceBoundaryV1"
SCHEMA_VENDOR = "EASYBIM"
FIELD = "record"


def _db(db):
    return db if db is not None else DB


def _storage(db):
    return getattr(db, "ExtensibleStorage", None) if db is not None else None


def _string_type():
    import System

    return System.String


def schema(create=False, db=None):
    """``(schema, reason)``.  Looked up before it is built, always.

    A schema is registered per Revit session, so a second build under the
    same GUID throws for the rest of that session.  If another add-in owns
    this GUID with a different shape, the record is reported unavailable
    rather than taking the command down.
    """
    db = _db(db)
    storage = _storage(db)
    if storage is None:
        return None, u"This Revit build has no Extensible Storage."
    try:
        import System

        guid = System.Guid(SCHEMA_GUID)
    except Exception as ex:
        return None, state.safe_text(ex) or u"unknown error"

    try:
        found = storage.Schema.Lookup(guid)
    except Exception:
        found = None
    if found is not None:
        try:
            if found.GetField(FIELD) is None:
                return None, (u"Another add-in has registered a different schema under "
                              u"EasyBIM's id; the in-model record is unavailable.")
        except Exception as ex:
            return None, state.safe_text(ex) or u"unknown error"
        return found, u""
    if not create:
        return None, u""

    try:
        import clr

        builder = storage.SchemaBuilder(guid)
        builder.SetSchemaName(SCHEMA_NAME)
        builder.SetVendorId(SCHEMA_VENDOR)
        builder.SetReadAccessLevel(storage.AccessLevel.Public)
        builder.SetWriteAccessLevel(storage.AccessLevel.Public)
        builder.AddSimpleField(FIELD, clr.GetClrType(_string_type()))
        return builder.Finish(), u""
    except Exception as ex:
        return None, state.safe_text(ex) or u"unknown error"


def availability(doc, db=None):
    """``(available, reason)`` - whether this document can carry the record."""
    if doc is None:
        return False, u"No document is open."
    if bool(getattr(doc, "IsFamilyDocument", False)):
        return False, u"Open a project rather than a family."
    found, reason = schema(create=False, db=db)
    if found is None and reason:
        return False, reason
    return True, u""


def read_record(element, db=None):
    """The record on one element, or ``{}``.  Never raises, never writes."""
    found, _reason = schema(create=False, db=db)
    if found is None or element is None:
        return {}
    try:
        entity = element.GetEntity(found)
        if entity is None or not entity.IsValid():
            return {}
        return state.decode(entity.Get[_string_type()](FIELD))
    except Exception:
        return {}


def read_all(doc, db=None):
    """Every region in this model that carries a record.

    The collector is filtered by schema, which is what keeps ten thousand
    regions to one indexed query instead of ten thousand entity reads.
    """
    db = _db(db)
    found, _reason = schema(create=False, db=db)
    storage = _storage(db)
    if found is None or storage is None or doc is None:
        return []
    region_class = getattr(db, "FilledRegion", None)
    try:
        collector = db.FilteredElementCollector(doc)
        if region_class is not None:
            collector = collector.OfClass(region_class)
        collector = collector.WherePasses(storage.ExtensibleStorageFilter(found.GUID))
        elements = list(collector.ToElements())
    except Exception:
        return []

    rows = []
    for element in elements:
        record = read_record(element, db=db)
        if not record:
            continue
        rows.append({"element": element, "record": record})
    return rows


def write_record(doc, element, record, db=None):
    """Put the record on the element, inside the caller's transaction.

    Deliberately opens no transaction of its own: the region and its record
    have to commit together, or an undo would leave one without the other.
    """
    db = _db(db)
    if element is None:
        return False, u"There is no region to write the record onto."
    if not bool(getattr(doc, "IsModifiable", False)):
        return False, u"A transaction is required to write the relationship."
    found, reason = schema(create=True, db=db)
    if found is None:
        return False, reason or u"The in-model record is unavailable."
    storage = _storage(db)
    try:
        entity = storage.Entity(found)
        entity.Set[_string_type()](FIELD, state.encode(record))
        element.SetEntity(entity)
    except Exception as ex:
        return False, state.safe_text(ex) or u"unknown error"
    return True, u""


def checkout_reason(doc, element_id, db=None):
    """``u""`` when it is ours to write, else why it is not.

    Never checks anything out on the user's behalf: a borrowed element is a
    named skip, and the report says who has it.
    """
    db = _db(db)
    if doc is None or element_id is None:
        return u""
    if not bool(getattr(doc, "IsWorkshared", False)):
        return u""
    utils = getattr(db, "WorksharingUtils", None)
    if utils is None:
        return u""
    try:
        status = utils.GetCheckoutStatus(doc, element_id)
    except Exception:
        return u""
    if state.safe_text(status).split(u".")[-1] != u"OwnedByOtherUser":
        return u""
    owner = u"Another user"
    try:
        owner = state.safe_text(utils.GetWorksharingTooltipInfo(doc, element_id).Owner) or owner
    except Exception:
        pass
    return state.sentence_for("owned_by_other", owner)
