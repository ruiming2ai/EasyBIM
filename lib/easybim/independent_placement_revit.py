# -*- coding: utf-8 -*-
"""Independent point-family placement; all Revit access stays on its API thread.

No hosted-family conversion is enabled without the compatibility gate in
Development Space/docs/copy-monitor-compatibility.md. API imports are lazy so
transaction/parameter decisions can be exercised by desktop tests.
"""
import math
import os
import shutil
import tempfile
import uuid
import re
from easybim import independent_placement as math3

CATEGORY_NAMES = (
    "OST_LightingFixtures", "OST_LightingDevices", "OST_ElectricalFixtures",
    "OST_ElectricalEquipment", "OST_MechanicalEquipment", "OST_PlumbingFixtures",
    "OST_DuctTerminal", "OST_Sprinklers", "OST_GenericModel", "OST_DataDevices",
    "OST_CommunicationDevices", "OST_FireAlarmDevices", "OST_SecurityDevices",
    "OST_NurseCallDevices", "OST_TelephoneDevices", "OST_DuctAccessory",
    "OST_PipeAccessory", "OST_DuctFitting", "OST_PipeFitting",
)
PLACEMENT_PARAMETERS = frozenset((
    "FAMILY_LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM", "INSTANCE_ELEVATION_PARAM",
    "INSTANCE_FREE_HOST_OFFSET_PARAM", "INSTANCE_FREE_HOST_PARAM", "INSTANCE_FREE_HOST_ID",
    "INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM", "FAMILY_BASE_LEVEL_PARAM",
    "FAMILY_BASE_LEVEL_OFFSET_PARAM", "FAMILY_TOP_LEVEL_PARAM", "FAMILY_TOP_LEVEL_OFFSET_PARAM",
    "SKETCH_PLANE_PARAM", "HOST_ID_PARAM", "ELEM_TYPE_PARAM", "ELEM_FAMILY_PARAM",
    "ELEM_FAMILY_AND_TYPE_PARAM", "SYMBOL_ID_PARAM", "ELEM_PARTITION_PARAM",
    "PHASE_CREATED", "PHASE_DEMOLISHED", "DESIGN_OPTION_ID", "FAMILY_WORK_PLANE_BASED",
    "FAMILY_HOSTING_BEHAVIOR", "FAMILY_ALWAYS_VERTICAL",
))
try:
    text_type = unicode
except NameError:
    text_type = str


def get_db():
    from pyrevit import DB
    return DB


def text(value):
    return "" if value is None else text_type(value)


def id_value(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def name(element):
    try:
        return text(element.Name)
    except Exception:
        return text(get_db().Element.Name.GetValue(element))


def xyz(value):
    return get_db().XYZ(*value)


def vector(value):
    return [value.X, value.Y, value.Z]


def supported_category(element):
    db = get_db()
    category = getattr(element, "Category", None)
    return category is not None and id_value(category.Id) in set(
        int(getattr(db.BuiltInCategory, key)) for key in CATEGORY_NAMES if hasattr(db.BuiltInCategory, key)
    )


def check_version(doc):
    version = int(doc.Application.VersionNumber)
    if version < 2024 or version > 2027:
        raise ValueError("Independent placement supports Revit 2024-2027.")
    if doc.IsFamilyDocument or doc.IsReadOnly:
        raise ValueError("Open a writable project document.")


def has_physical_host(element):
    db = get_db()
    host = element.Host
    if host is not None and not isinstance(host, db.Level):
        return True
    if element.HostFace is not None:
        return True
    # An unattached sketch plane is still not a certified standalone result.
    for key in ("SKETCH_PLANE_PARAM",):
        parameter = element.get_Parameter(getattr(db.BuiltInParameter, key))
        if parameter is not None and text(parameter.StorageType) == "ElementId":
            if id_value(parameter.AsElementId()) >= 0:
                return True
    return False


def independent_reason(element):
    db = get_db()
    if not isinstance(element, db.FamilyInstance) or not supported_category(element):
        return "Only MEP and Generic Model family instances are supported."
    if not isinstance(element.Location, db.LocationPoint):
        return "Independent curve/adaptive placement is not certified."
    return math3.support_reason(
        text(element.Symbol.Family.FamilyPlacementType), has_physical_host(element),
        element.Symbol.Family.IsInPlace, element.SuperComponent is not None,
    )


def mutation_reason(doc, element):
    db = get_db()
    if element.Pinned:
        return "Element is pinned."
    if id_value(element.GroupId) >= 0:
        return "Element belongs to a group."
    if getattr(element, "DesignOption", None) is not None:
        return "Element belongs to a design option; automatic updates are unavailable."
    if getattr(element, "SuperComponent", None) is not None:
        return "Element is a nested component."
    if doc.IsWorkshared:
        status = db.WorksharingUtils.GetCheckoutStatus(doc, element.Id)
        if status == db.CheckoutStatus.OwnedByOtherUser:
            return "Element is owned by another user."
    return ""


def connected(element):
    model = getattr(element, "MEPModel", None)
    manager = getattr(model, "ConnectorManager", None)
    if manager is None:
        return False
    for connector in manager.Connectors:
        try:
            if connector.IsConnected:
                return True
        except Exception:
            # Logical electrical connectors use system membership below.
            pass
    systems = getattr(model, "GetElectricalSystems", None)
    return bool(systems and list(systems() or []))


def instance_frame(element, transform=None):
    db = get_db()
    if not isinstance(element.Location, db.LocationPoint):
        raise ValueError("No supported point placement on this instance.")
    t = transform or db.Transform.Identity
    x = t.OfVector(element.HandOrientation).Normalize()
    y = t.OfVector(element.FacingOrientation).Normalize()
    z = x.CrossProduct(y).Normalize()
    if z.DotProduct(t.OfVector(element.GetTransform().BasisZ)) < 0:
        z = z.Negate()
    return math3.validate_frame(math3.frame(
        vector(t.OfPoint(element.Location.Point)), vector(x), vector(y), vector(z)))


def reference_record(doc, eid):
    value = id_value(eid)
    if value < 0:
        return dict(kind="sentinel", value=value)
    element = doc.GetElement(eid)
    if element is None:
        return dict(kind="missing", value=value)
    kind = element.GetType().Name
    properties = {}
    if kind == "Material":
        color = element.Color
        properties = dict(color=[color.Red, color.Green, color.Blue],
                          transparency=element.Transparency, shininess=element.Shininess,
                          smoothness=element.Smoothness)
    elif kind == "FamilySymbol":
        properties = dict(family=element.FamilyName, category=id_value(element.Category.Id))
    return dict(kind=kind, name=name(element), properties=properties, uid=element.UniqueId)


def match_reference(source, candidates):
    matches = [v for v in candidates if all(v.get(k) == source.get(k)
                                           for k in ("kind", "name", "properties"))]
    return matches[0] if len(matches) == 1 else None


def parameter_records(element, include_objects=False):
    db = get_db()
    records = []
    for parameter in element.Parameters:
        definition = parameter.Definition
        if definition is None:
            continue
        builtin = ""
        try:
            builtin = text(definition.BuiltInParameter)
            if builtin == "INVALID": builtin = ""
        except AttributeError:
            pass
        storage = text(parameter.StorageType)
        spec = text(definition.GetDataType().TypeId)
        if parameter.IsShared:
            key = "shared:" + text(parameter.GUID).lower()
        elif builtin:
            key = "builtin:" + builtin
        else:
            key = "definition:{}|{}|{}".format(definition.Name, spec, storage)
        value = None
        if parameter.HasValue:
            if storage == "Double": value = parameter.AsDouble()
            elif storage == "Integer": value = parameter.AsInteger()
            elif storage == "String": value = parameter.AsString()
            elif storage == "ElementId": value = reference_record(element.Document, parameter.AsElementId())
        record = dict(key=key, name=text(definition.Name), spec=spec, storage=storage,
                      value=value, writable=not parameter.IsReadOnly,
                      placement=builtin in PLACEMENT_PARAMETERS)
        try:
            record["display"] = parameter.AsValueString() or (
                value.get("name", text(value)) if isinstance(value, dict) else text(value))
        except Exception:
            record["display"] = text(value)
        if include_objects:
            record["_parameter"] = parameter
        records.append(record)
    return records


def resolve_reference(doc, source, cache):
    db = get_db()
    if source["kind"] == "sentinel":
        from System import Int64
        return db.ElementId(Int64(source["value"]))
    existing = doc.GetElement(source.get("uid", "")) if source.get("uid") else None
    if existing is not None:
        candidate = reference_record(doc, existing.Id)
        if match_reference(source, [candidate]):
            return existing.Id
    kind = source["kind"]
    if kind not in ("Material", "FamilySymbol"):
        raise ValueError("Unmappable document reference: " + kind)
    if kind not in cache:
        cache[kind] = [(el, reference_record(doc, el.Id)) for el in
                       db.FilteredElementCollector(doc).OfClass(getattr(db, kind))]
    matched = match_reference(source, [item[1] for item in cache[kind]])
    if matched is None:
        raise ValueError("Missing or ambiguous document reference: " + source.get("name", kind))
    return next(item[0].Id for item in cache[kind] if item[1] is matched)


def transfer_parameters(source, destination, cache=None):
    assignments, issues = math3.parameter_plan(parameter_records(source),
                                             parameter_records(destination, True))
    cache = cache if cache is not None else {}
    for assignment in assignments:
        record = assignment["source"]
        parameter = assignment["destination"]["_parameter"]
        try:
            value = record["value"]
            if record["storage"] == "ElementId":
                value = resolve_reference(destination.Document, value, cache)
            parameter.Set(value)
        except Exception as error:
            issues.append(dict(key=record["key"], name=record["name"], reason=text(error)))
    return issues



def verify_parameter_values(source, destination, strict=False):
    """Read back values after regeneration; a refused numeric driver aborts."""
    assignments, ignored = math3.parameter_plan(parameter_records(source), parameter_records(destination))
    issues = []
    for assignment in assignments:
        original, actual = assignment["source"], assignment["destination"]
        if math3.same_parameter_value(original["value"], actual["value"]):
            continue
        reason = "Value differs after regeneration: " + original["name"]
        if strict or original["storage"] in ("Double", "Integer"):
            raise ValueError(reason)
        issues.append(dict(key=original["key"], name=original["name"], reason=reason))
    return issues


def family_revision(element):
    family = element.Symbol.Family
    return math3.fingerprint(dict(uid=family.UniqueId, version=text(family.VersionGuid),
                                 symbol=element.Symbol.UniqueId, symbol_version=text(element.Symbol.VersionGuid)))


def _reject_load_options(db):
    class RejectConflicts(db.IFamilyLoadOptions):
        def OnFamilyFound(self, familyInUse, overwriteParameterValues):
            return False
        def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
            return False
    return RejectConflicts()


def prepare_symbol(destination_doc, source, cache):
    """Load a source-specific standalone definition, outside a transaction.

    No family is edited in place and no conflict callback overwrites a family.
    A containing TransactionGroup rolls back preparation if placement fails.
    """
    reason = independent_reason(source)
    if reason:
        raise ValueError(reason)
    key = family_revision(source)
    cached = cache.get(key)
    if cached is not None and cached.IsValidObject:
        return cached
    if source.Document.Equals(destination_doc):
        cache[key] = source.Symbol
        return source.Symbol
    db = get_db()
    definition_key = (source.Symbol.Family.UniqueId, text(source.Symbol.Family.VersionGuid))
    prepared_family = cache.get(definition_key)
    if prepared_family is not None and prepared_family.IsValidObject:
        for eid in prepared_family.GetFamilySymbolIds():
            symbol = destination_doc.GetElement(eid)
            if name(symbol) == name(source.Symbol):
                verify_parameter_values(source.Symbol, symbol, strict=True)
                cache[key] = symbol
                return symbol
    family_doc = None
    scratch_doc = None
    folder = tempfile.mkdtemp(prefix="easybim-independent-")
    try:
        try:
            family_doc = source.Document.EditFamily(source.Symbol.Family)
        except Exception:
            # Linked documents may refuse EditFamily. Copy the symbol into a
            # disposable project first; never open/reload or modify the RVT link.
            from System.Collections.Generic import List
            class AbortDuplicates(db.IDuplicateTypeNamesHandler):
                def OnDuplicateTypeNamesFound(self, args):
                    return db.DuplicateTypeAction.Abort
            scratch_doc = destination_doc.Application.NewProjectDocument(db.UnitSystem.Imperial)
            tx = db.Transaction(scratch_doc, "Prepare independent family")
            tx.Start()
            try:
                options = db.CopyPasteOptions()
                options.SetDuplicateTypeNamesHandler(AbortDuplicates())
                copied = db.ElementTransformUtils.CopyElements(
                    source.Document, List[db.ElementId]([source.Symbol.Id]),
                    scratch_doc, db.Transform.Identity, options)
                if tx.Commit() != db.TransactionStatus.Committed:
                    raise ValueError("Source family preparation was rolled back.")
            except Exception:
                if tx.GetStatus() == db.TransactionStatus.Started: tx.RollBack()
                raise
            symbols = [scratch_doc.GetElement(eid) for eid in copied]
            symbols = [item for item in symbols if isinstance(item, db.FamilySymbol)
                       and name(item) == name(source.Symbol)
                       and item.FamilyName == source.Symbol.FamilyName]
            if len(symbols) != 1:
                raise ValueError("Could not isolate the source family type from the link.")
            family_doc = scratch_doc.EditFamily(symbols[0].Family)
        if family_doc is None:
            raise ValueError("Source family could not be opened.")
        nested = [f for f in db.FilteredElementCollector(family_doc).OfClass(db.Family)
                  if f.IsShared]
        if nested:
            raise ValueError("Nested shared-family loading is not certified for independent copies.")
        # Reuse only variants verified against registry baselines by the caller.
        # A same-named family in the model is not proof of original content.
        label = re.sub(r'[<>:"/\\|?*]', '_', name(source.Symbol.Family))[:60]
        isolated_name = "EasyBIM_{}_{}".format(label, uuid.uuid4().hex[:12])
        path = os.path.join(folder, isolated_name + ".rfa")
        family_doc.SaveAs(path)
        family = family_doc.LoadFamily(destination_doc, _reject_load_options(db))
        if family is None:
            raise ValueError("Loading the isolated family was refused.")
        if name(family) != isolated_name:
            raise ValueError("Revit did not preserve the isolated family name.")
        symbols = [destination_doc.GetElement(eid) for eid in family.GetFamilySymbolIds()]
        matches = [s for s in symbols if name(s) == name(source.Symbol)]
        if len(matches) != 1:
            raise ValueError("The original family type could not be resolved uniquely.")
        verify_parameter_values(source.Symbol, matches[0], strict=True)
        cache[key] = matches[0]
        cache[definition_key] = family
        return matches[0]
    finally:
        if family_doc is not None:
            family_doc.Close(False)
        if scratch_doc is not None:
            scratch_doc.Close(False)
        shutil.rmtree(folder, ignore_errors=True)


def _rotation(current, desired):
    # R = desired basis * current basis transpose. R must be a proper rotation.
    a = math3.compose(desired, math3.inverse(current))
    matrix = [[a[k][i] for k in math3.AXES] for i in range(3)]
    cosine = max(-1.0, min(1.0, (sum(matrix[i][i] for i in range(3))-1.0)/2.0))
    angle = math.acos(cosine)
    if angle < 1e-9:
        return [0,0,1], 0.0
    if abs(math.pi-angle) < 1e-6:
        i = max(range(3), key=lambda n: matrix[n][n])
        axis = [0.0,0.0,0.0]
        axis[i] = math.sqrt(max(0.0,(matrix[i][i]+1.0)/2.0))
        if axis[i] < 1e-9:
            raise ValueError("Could not resolve rotation axis.")
        for j in range(3):
            if i != j: axis[j] = (matrix[i][j]+matrix[j][i])/(4.0*axis[i])
    else:
        axis = [matrix[2][1]-matrix[1][2], matrix[0][2]-matrix[2][0],
                matrix[1][0]-matrix[0][1]]
        length = math3.norm(axis)
        axis = [v/length for v in axis]
    return axis, angle


def move_to_frame(doc, element, desired):
    db = get_db()
    math3.validate_frame(desired)
    reason = mutation_reason(doc, element) or independent_reason(element)
    if reason: raise ValueError(reason)
    current = instance_frame(element)
    if math3.placement_matches(desired, current):
        return
    if connected(element):
        raise ValueError("Connected MEP instance: automatic movement requires circuit/connection validation.")
    if math3.determinant(current)*math3.determinant(desired) < 0:
        plane = db.Plane.CreateByNormalAndOrigin(xyz(current["x"]), xyz(current["origin"]))
        from System.Collections.Generic import List
        # MirrorElement creates a copy. False preserves this instance's identity.
        db.ElementTransformUtils.MirrorElements(doc, List[db.ElementId]([element.Id]), plane, False)
        doc.Regenerate()
        current = instance_frame(element)
    axis, angle = _rotation(current, desired)
    if angle > 1e-9:
        origin = xyz(current["origin"])
        line = db.Line.CreateBound(origin, origin.Add(xyz(axis)))
        db.ElementTransformUtils.RotateElement(doc, element.Id, line, angle)
        doc.Regenerate()
    current = instance_frame(element)
    delta = math3.sub(desired["origin"], current["origin"])
    if math3.norm(delta) > 1e-9:
        db.ElementTransformUtils.MoveElement(doc, element.Id, xyz(delta))
    doc.Regenerate()
    verify_instance(element, desired)


def verify_instance(element, desired):
    reason = independent_reason(element)
    if reason: raise ValueError(reason)
    actual = instance_frame(element)
    if not math3.placement_matches(desired, actual):
        raise ValueError("Actual location/orientation differs from the requested placement.")
    return actual


def create_instance(doc, source, symbol, desired):
    db = get_db()
    levels = list(db.FilteredElementCollector(doc).OfClass(db.Level))
    if not levels:
        raise ValueError("A reference level is required, although placement is coordinate-based.")
    level = min(levels, key=lambda item: abs(item.Elevation-desired["origin"][2]))
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()
    element = doc.Create.NewFamilyInstance(xyz(desired["origin"]), symbol, level,
                                          db.Structure.StructuralType.NonStructural)
    issues = transfer_parameters(source, element)
    _, type_issues = math3.parameter_plan(parameter_records(source.Symbol), parameter_records(symbol))
    issues.extend(dict(issue, name="Type: " + issue["name"]) for issue in type_issues)
    doc.Regenerate()
    move_to_frame(doc, element, desired)
    issues.extend(verify_parameter_values(source, element))
    return dict(element=element, source_uid=source.UniqueId, destination_uid=element.UniqueId,
                desired=desired, actual=instance_frame(element),
                parameter_issues=issues, conversion="standalone")


def _failure_options(transaction, db):
    if not hasattr(db, "IFailuresPreprocessor"):
        return
    class RollbackFailures(db.IFailuresPreprocessor):
        def PreprocessFailures(self, accessor):
            # Do not continue after warnings that could alter geometry or
            # remove constraints. Report the failed item, without modal prompts.
            messages = list(accessor.GetFailureMessages())
            if messages:
                return db.FailureProcessingResult.ProceedWithRollBack
            return db.FailureProcessingResult.Continue
    options = transaction.GetFailureHandlingOptions()
    options.SetFailuresPreprocessor(RollbackFailures())
    options.SetClearAfterRollback(True)
    transaction.SetFailureHandlingOptions(options)


def atomic_item(doc, title, prepare, mutate, verify=None):
    """Preparation, instance mutation and metadata form one rollback boundary."""
    db = get_db()
    group = db.TransactionGroup(doc, title)
    transaction = None
    group.Start()
    try:
        prepare()
        transaction = db.Transaction(doc, title + " changes")
        transaction.Start()
        _failure_options(transaction, db)
        value = mutate()
        status = transaction.Commit()
        transaction = None
        if status != db.TransactionStatus.Committed:
            raise ValueError("Revit rolled back the operation because of model failures.")
        if verify is not None:
            verify(value)
        group.Assimilate()
        return dict(ok=True, value=value, error="")
    except Exception as error:
        if transaction is not None:
            transaction.RollBack()
        group.RollBack()
        return dict(ok=False, value=None, error=text(error))
