# -*- coding: utf-8 -*-
"""Revit-free placement mathematics and parameter-transfer decisions.

Frames are orthonormal column bases in Revit internal feet; reflections are
intentional. Never repair an invalid source transform with identity.
"""
import math
import hashlib
import json

POSITION_TOLERANCE = 1.0 / 304.8
ANGLE_TOLERANCE = math.radians(0.1)
AXES = ("x", "y", "z")


def frame(origin=(0, 0, 0), x=(1, 0, 0), y=(0, 1, 0), z=(0, 0, 1)):
    return dict(origin=list(origin), x=list(x), y=list(y), z=list(z))


def add(a, b):
    return [a[i] + b[i] for i in range(3)]


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def norm(a):
    return math.sqrt(dot(a, a))


def determinant(value):
    return dot(value["x"], cross(value["y"], value["z"]))


def validate_frame(value):
    for key in ("origin",) + AXES:
        if len(value[key]) != 3 or any(math.isnan(float(n)) or math.isinf(float(n)) for n in value[key]):
            raise ValueError("Invalid coordinate frame")
    for axis in AXES:
        if abs(norm(value[axis]) - 1.0) > 1e-6:
            raise ValueError("Frame axes must have unit length")
    if any(abs(dot(value[a], value[b])) > 1e-6 for a, b in (("x","y"),("y","z"),("z","x"))):
        raise ValueError("Frame axes must be perpendicular")
    return value


def transform_vector(t, v):
    return [sum(t[a][i] * v[j] for j, a in enumerate(AXES)) for i in range(3)]


def transform_point(t, v):
    return add(t["origin"], transform_vector(t, v))


def compose(a, b):
    validate_frame(a)
    validate_frame(b)
    return frame(transform_point(a, b["origin"]), *[transform_vector(a, b[k]) for k in AXES])


def inverse(t):
    validate_frame(t)
    result = frame((0,0,0), *[[t[a][i] for a in AXES] for i in range(3)])
    result["origin"] = transform_vector(result, [-n for n in t["origin"]])
    return result


def relative(source, destination):
    return compose(inverse(source), destination)


def desired_frame(reference, offset, prototype, align=True):
    validate_frame(reference)
    validate_frame(prototype)
    result = dict((k, list(v)) for k, v in (reference if align else prototype).items())
    result["origin"] = transform_point(reference, offset)
    return result


def placement_matches(expected, actual):
    validate_frame(expected)
    validate_frame(actual)
    if norm(sub(expected["origin"], actual["origin"])) > POSITION_TOLERANCE:
        return False
    return all(dot(expected[k], actual[k]) >= math.cos(ANGLE_TOLERANCE) for k in AXES)


def support_reason(placement, physical_host=False, in_place=False, nested=False):
    if in_place or nested:
        return "In-place and nested/shared family conversion is not certified."
    if placement != "OneLevelBased":
        return ("{} needs an independent family conversion that has not been certified "
                "for Revit 2024-2027. No hosted substitute was placed.").format(placement)
    if physical_host:
        return "The instance still has a physical host or geometry-backed work plane."
    return ""



def same_parameter_value(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        if a.get("kind") and b.get("kind"):
            keys = ("kind", "value") if a.get("kind") == "sentinel" else ("kind", "name", "properties")
            return all(same_parameter_value(a.get(key), b.get(key)) for key in keys)
        return set(a) == set(b) and all(same_parameter_value(a[k],b[k]) for k in a)
    if isinstance(a, (int,float)) and isinstance(b, (int,float)):
        return abs(a-b) <= max(1e-9,1e-10*max(abs(a),abs(b)))
    if isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)):
        return len(a)==len(b) and all(same_parameter_value(x,y) for x,y in zip(a,b))
    return a == b


def parameter_plan(source, destination):
    indexed = {}
    source_counts = {}
    for value in source:
        source_counts[value["key"]] = source_counts.get(value["key"], 0) + 1
    for value in destination:
        indexed.setdefault(value["key"], []).append(value)
    assignments, issues = [], []
    for value in source:
        candidates = indexed.get(value["key"], [])
        reason = ""
        if value.get("placement"):
            reason = "placement value is calculated from coordinates"
        elif len(candidates) > 1 or source_counts[value["key"]] > 1:
            reason = "ambiguous parameter definition"
        elif not candidates:
            reason = "missing destination parameter"
        elif candidates[0].get("placement"):
            reason = "placement value is calculated from coordinates"
        elif value.get("spec") != candidates[0].get("spec") or value.get("storage") != candidates[0].get("storage"):
            reason = "incompatible data type"
        elif not candidates[0].get("writable", False):
            reason = "read-only or calculated destination parameter"
        elif value.get("value") is None:
            reason = "source has no assigned value"
        if reason:
            info = value.get("placement") or value.get("value") is None
            if candidates and "read-only" in reason:
                info = same_parameter_value(value["value"], candidates[0].get("value"))
            issues.append(dict(key=value["key"], name=value.get("name", ""), reason=reason,
                               severity="info" if info else "warning"))
        else:
            assignments.append(dict(source=value, destination=candidates[0]))
    return assignments, issues


def fingerprint(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
