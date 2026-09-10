# -*- coding: utf-8 -*-
"""One DataStorage element per relationship; no metadata on copied instances.

Reads never create a schema or start a transaction. Writes participate in the
caller's transaction, so Undo/Redo and rollback include the relationship.
"""
from easybim import copy_monitor_state as state

SCHEMA_GUID = "370c7234-a843-4f4f-917d-94b882be2401"
FIELD = "relationship"


def schema(create=False):
    import clr
    from System import Guid, String
    from pyrevit import DB
    storage = DB.ExtensibleStorage
    found = storage.Schema.Lookup(Guid(SCHEMA_GUID))
    if found is not None:
        field = found.GetField(FIELD)
        if field is None or field.ValueType != clr.GetClrType(String):
            raise ValueError("Copy Monitor storage schema does not match this extension.")
        return found
    if not create:
        return None
    builder = storage.SchemaBuilder(Guid(SCHEMA_GUID))
    builder.SetSchemaName("EasyBIMCopyMonitorV1")
    builder.SetReadAccessLevel(storage.AccessLevel.Public)
    builder.SetWriteAccessLevel(storage.AccessLevel.Public)
    builder.AddSimpleField(FIELD, clr.GetClrType(String))
    return builder.Finish()


def read_records(doc):
    from System import String
    from pyrevit import DB
    found = schema()
    if found is None:
        return []
    records = []
    # Schema filter avoids touching unrelated DataStorage belonging to other tools.
    collector = DB.FilteredElementCollector(doc).OfClass(DB.ExtensibleStorage.DataStorage)
    collector = collector.WherePasses(DB.ExtensibleStorage.ExtensibleStorageFilter(found.GUID))
    for element in collector:
        entity = element.GetEntity(found)
        if not entity.IsValid():
            raise ValueError("Invalid Copy Monitor storage entity: " + element.UniqueId)
        record = state.decode(entity.Get[String](FIELD))
        record["_storage_uid"] = element.UniqueId
        records.append(record)
    state.index_records(records)
    return records


def write_record(doc, record):
    from System import String
    from pyrevit import DB
    if not doc.IsModifiable:
        raise ValueError("A transaction is required to write a monitoring relationship.")
    encoded = state.encode(record)
    found = schema(True)
    uid = record.get("_storage_uid")
    element = doc.GetElement(uid) if uid else None
    if uid and element is None:
        raise ValueError("The monitoring record changed or was removed. Check Changes again.")
    if element is not None and doc.IsWorkshared:
        if DB.WorksharingUtils.GetCheckoutStatus(doc, element.Id) == DB.CheckoutStatus.OwnedByOtherUser:
            raise ValueError("Another user owns this monitoring relationship.")
    if element is None:
        element = DB.ExtensibleStorage.DataStorage.Create(doc)
        element.Name = "EasyBIM Copy Monitor " + record["id"]
    entity = DB.ExtensibleStorage.Entity(found)
    entity.Set[String](FIELD, encoded)
    element.SetEntity(entity)
    result = dict(record)
    result["_storage_uid"] = element.UniqueId
    return result
