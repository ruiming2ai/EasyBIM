# -*- coding: utf-8 -*-
"""Versioned, Revit-free monitoring records and conflict decisions."""
import copy
import datetime
import json
import uuid
from easybim import independent_placement as placement

SCHEMA_VERSION = 1


def timestamp():
    return datetime.datetime.utcnow().isoformat() + "Z"


def new_record(link_uid, link_document_uid, source_uid, destination_uid,
               source, destination, mode="original", recipe=None, prototype_uid=""):
    return dict(
        schema_version=SCHEMA_VERSION, id=str(uuid.uuid4()), active=True,
        link_uid=link_uid, link_document_uid=link_document_uid,
        source_uid=source_uid, destination_uid=destination_uid, mode=mode,
        prototype_uid=prototype_uid,
        recipe=recipe or dict(relative=placement.frame()),
        baseline_source=copy.deepcopy(source), baseline_destination=copy.deepcopy(destination),
        accepted=None, postponed=False, updated=timestamp(), parameter_issues=[],
    )


def expected_frame(record, source):
    recipe = record["recipe"]
    if "relative" in recipe:
        return placement.compose(source["frame"], recipe["relative"])
    return placement.desired_frame(source["frame"], recipe["offset"],
                                   recipe["orientation"], recipe["align"])


def values_equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a-b) <= max(1e-9, 1e-10*max(abs(a),abs(b)))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(values_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_equal(x,y) for x,y in zip(a,b))
    return a == b


def changes(before, after):
    if before is None or after is None:
        return ["availability"]
    found = []
    if not placement.placement_matches(before["frame"], after["frame"]):
        found.append("location/orientation")
    for key in ("type_revision", "host", "independence"):
        if before.get(key) != after.get(key):
            found.append(key)
    old, new = before.get("params", {}), after.get("params", {})
    for key in sorted(set(old) | set(new)):
        if not values_equal(old.get(key), new.get(key)):
            found.append(key)
    return found


def compare(record, source, destination, availability="available"):
    result = dict(record=record, status="unchanged", source=source, destination=destination,
                  source_changes=[], destination_changes=[], expected=None, checked=timestamp())
    if not record.get("active", True):
        result["status"] = "stopped"
        return result
    if availability != "available":
        result["status"] = availability
        return result
    if destination is None:
        result["status"] = "destination_missing"
        return result
    if source is None:
        result["status"] = "source_missing"
        return result
    result["expected"] = expected_frame(record, source)
    result["source_changes"] = changes(record["baseline_source"], source)
    result["destination_changes"] = changes(record["baseline_destination"], destination)
    accepted = record.get("accepted")
    if accepted and not changes(accepted["source"], source) and not changes(accepted["destination"], destination):
        result["status"] = "accepted"
        return result
    if result["source_changes"] and result["destination_changes"]:
        result["status"] = "conflict"
    elif result["source_changes"]:
        result["status"] = "source_changed"
    elif result["destination_changes"]:
        result["status"] = "local_changed"
    elif not placement.placement_matches(result["expected"], destination["frame"]):
        result["status"] = "placement_difference"
    if destination.get("independence"):
        result["status"] = "conversion_required"
    elif result["status"] == "unchanged" and record.get("parameter_issues"):
        result["status"] = "parameter_exceptions"
    if record.get("postponed") and result["status"] not in ("unchanged", "accepted"):
        result["status"] = "postponed"
    return result


def resolve(record, action, source, destination):
    result = copy.deepcopy(record)
    if action == "stop":
        result["active"] = False
    elif action == "postpone":
        result["postponed"] = True
    else:
        if source is None or destination is None:
            raise ValueError("This action requires an available source and destination.")
        result["postponed"] = False
        if action == "accept":
            result["accepted"] = dict(source=copy.deepcopy(source), destination=copy.deepcopy(destination))
        elif action in ("relative", "match"):
            if action == "relative":
                result["recipe"] = dict(relative=placement.relative(source["frame"], destination["frame"]))
            result["baseline_source"] = copy.deepcopy(source)
            result["baseline_destination"] = copy.deepcopy(destination)
            result["accepted"] = None
        else:
            raise ValueError("Unknown review action: " + action)
    result["updated"] = timestamp()
    return result


def mapping_key(record):
    return placement.fingerprint([record["link_uid"], record["source_uid"], record["mode"],
                                  record.get("prototype_uid", ""), record["recipe"]])


def index_records(records):
    indexed, ids = {}, set()
    for record in records:
        validate_record(record)
        if record["id"] in ids:
            raise ValueError("Duplicate relationship identity; repair the registry before updating.")
        ids.add(record["id"])
        if record.get("active", True):
            key = mapping_key(record)
            if key in indexed:
                raise ValueError("Duplicate monitoring mapping; review existing relationships.")
            indexed[key] = record
    return indexed


def validate_record(record):
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported Copy Monitor record version.")
    for key in ("id","link_uid","link_document_uid","source_uid","destination_uid"):
        if not isinstance(record.get(key), (str, type(u""))) or not record[key]:
            raise ValueError("Invalid relationship " + key)
    if record.get("mode") not in ("original","duplicate","position"):
        raise ValueError("Invalid monitoring mode.")
    recipe = record["recipe"]
    if "relative" in recipe:
        placement.validate_frame(recipe["relative"])
    else:
        placement.validate_frame(recipe["orientation"])
        placement.validate_frame(placement.frame(recipe["offset"]))
    for key in ("baseline_source","baseline_destination"):
        placement.validate_frame(record[key]["frame"])
    return record


def encode(record):
    payload = dict((key,value) for key,value in record.items() if not key.startswith("_"))
    validate_record(payload)
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)


def decode(payload):
    try:
        record = json.loads(payload)
        return validate_record(record)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid Copy Monitor relationship: " + str(error))
