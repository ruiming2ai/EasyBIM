# -*- coding: utf-8 -*-
"""Command-only coordination service shared by both buttons. No event hooks."""
from easybim import independent_placement as placement
from easybim import independent_placement_revit as adapter
from easybim import copy_monitor_state as state
from easybim import copy_monitor_storage as storage


def snapshot(element, transform=None, destination=False, type_cache=None):
    params, labels, displays = {}, {}, {}
    cache_key = (element.Document.GetHashCode(), element.Symbol.UniqueId) if type_cache is not None else None
    if type_cache is not None and cache_key in type_cache:
        type_records, revision = type_cache[cache_key]
    else:
        type_records = adapter.parameter_records(element.Symbol)
        revision = adapter.family_revision(element)
        if type_cache is not None:
            type_cache[cache_key] = (type_records, revision)
    for prefix, records in (("", adapter.parameter_records(element)), ("type:", type_records)):
        for record in records:
            if not record["placement"]:
                key = prefix + record["key"]
                labels[key] = prefix + record["name"]
                displays[key] = record.get("display", adapter.text(record["value"]))
                if key in params:
                    params[key] = dict(ambiguous=True)
                else:
                    params[key] = record["value"]
    host = element.Host
    return dict(frame=adapter.instance_frame(element, transform), params=params,
                parameter_labels=labels, parameter_display=displays, element_id=adapter.id_value(element.Id),
                type_revision=revision,
                type_label=adapter.name(element.Symbol), family=element.Symbol.FamilyName,
                host=adapter.text(getattr(host, "UniqueId", "")),
                independence=adapter.independent_reason(element) if destination else "")


def document_uid(doc):
    return doc.ProjectInformation.UniqueId


def link_context(doc, record, cache):
    key = record["link_uid"]
    if key not in cache:
        db = adapter.get_db()
        link = doc.GetElement(key)
        if link is None or not isinstance(link, db.RevitLinkInstance):
            cache[key] = ("link_missing", None, None, None)
        else:
            linked = link.GetLinkDocument()
            if linked is None:
                cache[key] = ("link_unloaded", link, None, None)
            else:
                cache[key] = ("available", link, linked, link.GetTotalTransform())
    status, link, linked, transform = cache[key]
    if linked is not None and document_uid(linked) != record["link_document_uid"]:
        return "link_replaced", link, None, None
    return status, link, linked, transform


def inspect_record(doc, record, link_cache=None, type_cache=None):
    cache = link_cache if link_cache is not None else {}
    status, link, linked, transform = link_context(doc, record, cache)
    destination = doc.GetElement(record["destination_uid"])
    source = linked.GetElement(record["source_uid"]) if linked is not None else None
    db = adapter.get_db()
    if source is not None and isinstance(source, db.RevitLinkInstance):
        status = "nested_link_unavailable"
        source = None
    try:
        src = snapshot(source, transform, type_cache=type_cache) if source is not None else None
        dst = snapshot(destination, destination=True, type_cache=type_cache) if destination is not None else None
        report = state.compare(record, src, dst, status)
    except Exception as error:
        report = dict(record=record, status="scan_error", source=None, destination=None,
                      source_changes=[], destination_changes=[], expected=None,
                      checked=state.timestamp(), error=adapter.text(error))
    report["_source_element"] = source
    report["_destination_element"] = destination
    report["_link"] = link
    return report


def check_changes(doc, progress=None, records=None):
    adapter.check_version(doc)
    records = storage.read_records(doc) if records is None else records
    records = [record for record in records if record.get("active", True)]
    reports, cache, type_cache = [], {}, {}
    cancelled = False
    for index, record in enumerate(records):
        if progress and not progress(index, len(records)):
            cancelled = True
            break
        try:
            reports.append(inspect_record(doc, record, cache, type_cache))
        except Exception as error:
            reports.append(dict(record=record, status="scan_error", source=None, destination=None,
                                source_changes=[], destination_changes=[], expected=None,
                                checked=state.timestamp(), error=adapter.text(error),
                                _source_element=None, _destination_element=None, _link=None))
    return dict(reports=reports, cancelled=cancelled, checked=state.timestamp(),
                total=len(records), completed=len(reports))



def reusable_symbols(doc, records):
    cache = {}
    for record in records:
        if not record.get("active", True) or record["mode"] != "original":
            continue
        key = record["baseline_source"]["type_revision"]
        if key in cache:
            continue
        element = doc.GetElement(record["destination_uid"])
        if element is not None and adapter.family_revision(element) == record["baseline_destination"]["type_revision"]:
            if not adapter.independent_reason(element):
                cache[key] = element.Symbol
    return cache


def copy_requests(doc, requests, progress=None, existing_records=None):
    """Request: source, reference, link, desired, recipe, mode, monitored.

    Each request is atomic including prepared families and registry writes.
    Caller may wrap legacy copies and this batch in a larger TransactionGroup.
    """
    adapter.check_version(doc)
    db = adapter.get_db()
    records = storage.read_records(doc) if existing_records is None else existing_records
    existing = state.index_records(records)
    family_cache, reports = reusable_symbols(doc, records), []
    group = db.TransactionGroup(doc, "EasyBIM independent copies")
    group.Start()
    try:
        for index, request in enumerate(requests):
            if progress and not progress(index, len(requests)):
                reports.append(dict(ok=False, cancelled=True, error="Cancelled before remaining instances.", request=None))
                break
            reference, source = request["reference"], request["source"]
            link = request.get("link")
            monitored = bool(request.get("monitored", True) and link is not None)
            prepared, pending = {}, None
            try:
                if monitored:
                    source_snap = snapshot(reference, link.GetTotalTransform())
                    pending = state.new_record(
                        link.UniqueId, document_uid(reference.Document), reference.UniqueId,
                        "pending", source_snap, dict(source_snap),
                        mode=request["mode"], recipe=request["recipe"],
                        prototype_uid=source.UniqueId if request["mode"] == "duplicate" else "")
                    pending["link_label"] = adapter.name(link)
                    if state.mapping_key(pending) in existing:
                        reports.append(dict(ok=False, error="This source and placement mapping is already monitored.",
                                            request=request))
                        continue
            except Exception as error:
                reports.append(dict(ok=False, error=adapter.text(error), request=request))
                continue
            def prepare():
                prepared["symbol"] = adapter.prepare_symbol(doc, source, family_cache)
            def mutate():
                result = adapter.create_instance(doc, source, prepared["symbol"], request["desired"])
                if monitored:
                    pending["destination_uid"] = result["element"].UniqueId
                    pending["baseline_destination"] = snapshot(result["element"], destination=True)
                    pending["parameter_issues"] = result["parameter_issues"]
                    result["record"] = storage.write_record(doc, pending)
                return result
            def verify(result):
                result["actual"] = adapter.verify_instance(result["element"], request["desired"])
                adapter.verify_parameter_values(source, result["element"])
                result["verification"] = dict(position=True, orientation=True, independent=True,
                                              numeric_parameter_readback=True, post_commit=True)
            result = adapter.atomic_item(doc, "Independent copy", prepare, mutate, verify)
            result["request"] = request
            reports.append(result)
            if result["ok"] and monitored:
                saved = result["value"]["record"]
                existing[state.mapping_key(saved)] = saved
        group.Assimilate()
    except Exception:
        group.RollBack()
        raise
    return reports


def monitor_existing(doc, link, source, destination):
    adapter.check_version(doc)
    if not adapter.supported_category(source) or not adapter.supported_category(destination):
        return dict(ok=False, error="Choose MEP or Generic Model instances in both models.")
    linked = link.GetLinkDocument()
    if linked is None or not source.Document.Equals(linked):
        return dict(ok=False, error="The selected source is not in this loaded link instance.")
    records = storage.read_records(doc)
    if any(r.get("active", True) and r["destination_uid"] == destination.UniqueId for r in records):
        return dict(ok=False, error="This destination already has an active monitoring relationship.")
    src = snapshot(source, link.GetTotalTransform())
    dst = snapshot(destination, destination=True)
    record = state.new_record(link.UniqueId, document_uid(linked), source.UniqueId,
                              destination.UniqueId, src, dst, mode="position")
    record["link_label"] = adapter.name(link)
    if state.mapping_key(record) in state.index_records(records):
        return dict(ok=False, error="This source and placement mapping is already monitored.")
    def mutate():
        return storage.write_record(doc, record)
    return adapter.atomic_item(doc, "Monitor existing element", lambda: None, mutate)


def apply_action(doc, reports, action, progress=None):
    adapter.check_version(doc)
    db = adapter.get_db()
    current = dict((r["id"], r) for r in storage.read_records(doc))
    results, family_cache = [], reusable_symbols(doc, list(current.values()))
    group = db.TransactionGroup(doc, "Copy Monitor review")
    group.Start()
    try:
        for index, report in enumerate(reports):
            if progress and not progress(index, len(reports)):
                results.append(dict(ok=False, error="Cancelled before remaining actions."))
                break
            old = report["record"]
            record = current.get(old["id"])
            if record is None or record["updated"] != old["updated"]:
                results.append(dict(ok=False, error="Relationship changed. Check Changes again."))
                continue
            if action in ("stop", "postpone"):
                fresh = dict(source=None, destination=None, _source_element=None,
                             _destination_element=None, _link=None)
            else:
                try:
                    fresh = inspect_record(doc, record)
                except Exception as error:
                    results.append(dict(ok=False, error=adapter.text(error)))
                    continue
            if action not in ("stop", "postpone") and (
                fresh["source"] is None or fresh["destination"] is None or
                state.changes(report.get("source"), fresh["source"]) or
                state.changes(report.get("destination"), fresh["destination"])
            ):
                results.append(dict(ok=False, error="Source or destination changed. Check Changes again."))
                continue
            source = fresh["_source_element"]
            destination = fresh["_destination_element"]
            prepared = {}
            def prepare():
                if action != "match":
                    return
                reason = adapter.mutation_reason(doc, destination) or adapter.independent_reason(destination)
                if reason:
                    raise ValueError(reason)
                if adapter.connected(destination):
                    raise ValueError("Connected MEP instance requires connection/circuit validation before updating.")
                if record["mode"] == "original":
                    # Isolated source family definitions avoid changing other instances.
                    prepared["symbol"] = adapter.prepare_symbol(doc, source, family_cache)
            def mutate():
                issues = record.get("parameter_issues", [])
                if action == "match":
                    if record["mode"] == "original":
                        destination.Symbol = prepared["symbol"]
                        issues = adapter.transfer_parameters(source, destination)
                    adapter.move_to_frame(doc, destination, fresh["expected"])
                    if record["mode"] == "original":
                        issues.extend(adapter.verify_parameter_values(source, destination))
                src = snapshot(source, fresh["_link"].GetTotalTransform()) if source is not None else None
                dst = snapshot(destination, destination=True) if destination is not None else None
                updated = state.resolve(record, action, src, dst)
                updated["parameter_issues"] = issues
                state.index_records([r for key,r in current.items() if key != updated["id"]] + [updated])
                return storage.write_record(doc, updated)
            def verify(value):
                if action == "match":
                    adapter.verify_instance(destination, fresh["expected"])
                    if record["mode"] == "original":
                        adapter.verify_parameter_values(source, destination)
            result = adapter.atomic_item(doc, "Copy Monitor " + action, prepare, mutate, verify)
            results.append(result)
            if result["ok"]:
                current[record["id"]] = result["value"]
        group.Assimilate()
    except Exception:
        group.RollBack()
        raise
    return results
