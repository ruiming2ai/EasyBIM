# -*- coding: utf-8 -*-
"""Plain-Python core for Tag Align.

Everything in this module works on numbers, strings and small value objects so
it can be unit tested without Revit.  ``tag_align_revit`` reads the model and
fills these objects in; ``tag_align_ui`` renders them.

The one idea worth stating up front: a reference tag is stored as *two* offsets
from its host - one in the view's Right/Up axes and one in the host element's
own in-plane frame - both in model feet, together with the scale of the view
they were measured in.  Keeping both means the same reference can be replayed
either "always 2 feet above on the sheet" (view frame) or "always 2 feet
perpendicular to the run" (local frame) without re-reading the model, and
keeping the source scale means the view-scale option can be toggled after the
fact.
"""

import math


# --- scope -----------------------------------------------------------------

SCOPE_SAME_CATEGORY = "same_category"
SCOPE_PAIRED_FAMILY = "paired_family"
SCOPE_EXACT_TYPE = "exact_type"
DEFAULT_SCOPE = SCOPE_SAME_CATEGORY

SCOPE_LABELS = {
    SCOPE_SAME_CATEGORY: "All the families in the same category",
    SCOPE_PAIRED_FAMILY: "Apply to different types, but only paired family",
    SCOPE_EXACT_TYPE: "Exact same family and type match only",
}

# Widest first.  Resolution always prefers the closest reference it can find
# (exact type, then same family, then anything in the category), so a wider
# scope only ever adds a fallback - it never steals a target from a reference
# that matches it more precisely.
SCOPE_ORDER = (SCOPE_SAME_CATEGORY, SCOPE_PAIRED_FAMILY, SCOPE_EXACT_TYPE)

# --- reference picking mode ------------------------------------------------

MODE_SINGLE = "single"
MODE_MULTI = "multi"

# --- anchor kinds ----------------------------------------------------------

ANCHOR_POINT = "point"
ANCHOR_CURVE_MID = "curve_mid"
ANCHOR_BBOX = "bbox"

ANCHOR_LABELS = {
    ANCHOR_POINT: "Location point",
    ANCHOR_CURVE_MID: "Curve midpoint",
    ANCHOR_BBOX: "Bounding box centre",
}

# --- tolerances ------------------------------------------------------------

# Two directions closer than this count as the same orientation.
ORIENTATION_TOL_DEG = 1.0

# 1/64 inch expressed in feet.  Used both for model-space comparisons and, when
# the view-scale option is on, for paper-space comparisons (model feet divided
# by the view scale) - a 1/64 inch difference on the printed sheet.
POSITION_TOL = 1.0 / 768.0

EPS = 1e-9

# --- validation codes ------------------------------------------------------

TAG_TYPE_MIXED = "tag_type_mixed"
CATEGORY_MIXED = "category_mixed"
MULTI_REF_TAG = "multi_ref_tag"
HOST_MISSING = "host_missing"
DUPLICATE = "duplicate"
POSITION_CONFLICT = "position_conflict"
SCOPE_AMBIGUITY = "scope_ambiguity"

CONFLICT_TITLES = {
    POSITION_CONFLICT: "Same element type and orientation, different tag position",
    SCOPE_AMBIGUITY: "Several reference tags claim the same family and orientation",
}

# --- skip reasons ----------------------------------------------------------

SKIP_CATEGORY_MISMATCH = "category_mismatch"
SKIP_NO_RULE_FOR_TYPE = "no_rule_for_type"
SKIP_NO_RULE_FOR_FAMILY = "no_rule_for_family"
SKIP_NO_REFERENCE_FOR_ORIENTATION = "no_reference_for_orientation"
SKIP_AMBIGUOUS_ORIENTATION = "ambiguous_orientation"
SKIP_ALREADY_TAGGED = "already_tagged"
SKIP_NO_TAG = "no_tag"
SKIP_EXTRA_TAGS = "extra_tags"
SKIP_PINNED = "pinned"
SKIP_MULTI_REF_TARGET = "multi_ref_target"
SKIP_LINKED_TAG = "linked_tag"
SKIP_NOT_IN_VIEW = "not_in_view"
SKIP_ALREADY_ALIGNED = "already_aligned"
SKIP_ELEMENT_GONE = "element_gone"
SKIP_NO_ANCHOR = "no_anchor"
SKIP_TAG_TYPE_MISMATCH = "tag_type_mismatch"

SKIP_TEXT = {
    SKIP_CATEGORY_MISMATCH: "Different category from the reference tag's element",
    SKIP_NO_RULE_FOR_TYPE: "No reference tag for this element type",
    SKIP_NO_RULE_FOR_FAMILY: "No reference tag for this family",
    SKIP_NO_REFERENCE_FOR_ORIENTATION: "No reference tag for this orientation",
    SKIP_AMBIGUOUS_ORIENTATION: "Element has no orientation and several references could apply",
    SKIP_ALREADY_TAGGED: "Already tagged (aligning existing tags was turned off)",
    SKIP_NO_TAG: "No tag in this view (use Align & Tag to create one)",
    SKIP_EXTRA_TAGS: "Element carries more than one tag in this view",
    SKIP_PINNED: "Tag is pinned",
    SKIP_MULTI_REF_TARGET: "Tag labels more than one element",
    SKIP_LINKED_TAG: "Tag labels an element in a linked model",
    SKIP_NOT_IN_VIEW: "Not visible in the active view",
    SKIP_ALREADY_ALIGNED: "Already at the reference position",
    SKIP_ELEMENT_GONE: "Element no longer exists",
    SKIP_NO_ANCHOR: "Element has no usable location",
    SKIP_TAG_TYPE_MISMATCH: "Tag type differs from the reference tag type",
}

# --- report caps -----------------------------------------------------------

SKIPPED_DISPLAY_LIMIT = 20
NOTES_DISPLAY_LIMIT = 10


def safe_text(value):
    """Best-effort str() that never raises (CLR objects included)."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        try:
            return value.ToString()
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# angles
# ---------------------------------------------------------------------------


def normalize_angle(angle_deg, span=360.0):
    """Fold an angle into ``[0, span)``."""
    try:
        value = float(angle_deg)
    except Exception:
        return 0.0
    value = math.fmod(value, span)
    if value < 0:
        value += span
    return value


def angular_distance(first_deg, second_deg, fold=False):
    """Shortest angular gap in degrees.

    ``fold`` treats a direction and its 180 degree opposite as identical, which
    is what a line-based element's axis actually is - a wall drawn right to
    left is the same wall.
    """
    span = 180.0 if fold else 360.0
    diff = abs(normalize_angle(first_deg, span) - normalize_angle(second_deg, span))
    return min(diff, span - diff)


def should_fold(first_axis_symmetric, second_axis_symmetric, collapse_flip):
    """180 degree folding only applies when both sides are undirected axes."""
    return bool(collapse_flip) and bool(first_axis_symmetric) and bool(second_axis_symmetric)


def angles_match(first_deg, second_deg, fold=False, tol=ORIENTATION_TOL_DEG):
    return angular_distance(first_deg, second_deg, fold) <= float(tol) + EPS


def format_angle(angle_deg, has_direction=True, axis_symmetric=False):
    """Human label for an in-view direction.

    ``u"..."`` literals keep the degree sign a real character under both
    IronPython 2.7 (where a plain literal would be raw UTF-8 bytes and reach
    WPF as mojibake) and CPython.
    """
    if not has_direction:
        return u"No orientation"

    value = normalize_angle(angle_deg, 180.0 if axis_symmetric else 360.0)
    named = ((0.0, u"Horizontal"), (90.0, u"Vertical"), (180.0, u"Horizontal"),
             (270.0, u"Vertical"))
    for known, label in named:
        if abs(value - known) <= ORIENTATION_TOL_DEG:
            return u"{0} ({1:.0f}°)".format(label, value)
    return u"{0:.1f}°".format(value)


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


class ReferenceRule(object):
    """One measured reference tag.

    ``offset_view`` and ``offset_local`` are ``(u, v)`` pairs in model feet.
    ``offset_view`` runs along the source view's Right/Up axes; ``offset_local``
    runs along the host element's own in-plane frame.  ``view_scale`` is the
    scale of the view they were measured in, which is what lets the offsets be
    re-expressed in paper units on demand.
    """

    def __init__(
        self,
        tag_id=None,
        tag_type_id=None,
        tag_type_label="",
        tag_family_name="",
        host_id=None,
        host_category_id=None,
        host_category_name="",
        host_family_name="",
        host_type_id=None,
        host_type_label="",
        view_id=None,
        view_name="",
        view_scale=1,
        anchor_kind=ANCHOR_POINT,
        has_direction=False,
        angle_deg=0.0,
        axis_symmetric=False,
        offset_view=None,
        offset_local=None,
        offset_normal=0.0,
        tag_orientation=None,
        has_leader=False,
        leader_end_condition=None,
        elbow_view=None,
        elbow_local=None,
        leader_end_view=None,
        leader_end_local=None,
        invalid_reason="",
        notes=None,
    ):
        self.tag_id = tag_id
        self.tag_type_id = tag_type_id
        self.tag_type_label = tag_type_label
        # The tag *family*, not the type.  A target carrying "Door Tag : Large"
        # when the reference is "Door Tag : Small" is the same annotation and
        # gets aligned; a "Fire Rating Tag" on the same door is somebody else's
        # tag and is left alone.
        self.tag_family_name = tag_family_name
        self.host_id = host_id
        self.host_category_id = host_category_id
        self.host_category_name = host_category_name
        self.host_family_name = host_family_name
        self.host_type_id = host_type_id
        self.host_type_label = host_type_label
        self.view_id = view_id
        self.view_name = view_name
        self.view_scale = int(view_scale or 1) or 1
        self.anchor_kind = anchor_kind
        self.has_direction = bool(has_direction)
        self.angle_deg = float(angle_deg or 0.0)
        self.axis_symmetric = bool(axis_symmetric)
        self.offset_view = tuple(offset_view or (0.0, 0.0))
        self.offset_local = tuple(offset_local or (0.0, 0.0))
        # Out-of-plane component of the reference head, kept so a newly created
        # tag lands at the same depth the reference sits at.  Never scaled -
        # depth is not a printed distance.
        self.offset_normal = float(offset_normal or 0.0)
        self.tag_orientation = tag_orientation
        self.has_leader = bool(has_leader)
        self.leader_end_condition = leader_end_condition
        self.elbow_view = tuple(elbow_view) if elbow_view else None
        self.elbow_local = tuple(elbow_local) if elbow_local else None
        self.leader_end_view = tuple(leader_end_view) if leader_end_view else None
        self.leader_end_local = tuple(leader_end_local) if leader_end_local else None
        self.invalid_reason = invalid_reason or ""
        self.notes = list(notes or [])

    @property
    def is_valid(self):
        return not self.invalid_reason

    def type_key(self):
        return safe_text(self.host_type_id)

    def family_key(self):
        return safe_text(self.host_family_name).strip().lower()

    def category_key(self):
        return safe_text(self.host_category_id)

    def scope_key(self, scope):
        """What has to be unique for this reference to be unambiguous."""
        if scope == SCOPE_EXACT_TYPE:
            return self.type_key()
        if scope == SCOPE_PAIRED_FAMILY:
            return self.family_key()
        # Category scope puts every reference in one group, so two references
        # on different families at the same orientation now collide - which is
        # exactly right, because under this scope both claim every element.
        return self.category_key()

    def host_label(self):
        family = safe_text(self.host_family_name).strip()
        type_name = safe_text(self.host_type_label).strip()
        if family and type_name:
            return "{0} : {1}".format(family, type_name)
        return family or type_name or "<unnamed>"

    def orientation_label(self):
        return format_angle(self.angle_deg, self.has_direction, self.axis_symmetric)


class TargetInfo(object):
    """The bits of a target element the matcher needs."""

    def __init__(
        self,
        element_id=None,
        category_id=None,
        family_name="",
        type_id=None,
        type_label="",
        has_direction=False,
        angle_deg=0.0,
        axis_symmetric=False,
        label="",
    ):
        self.element_id = element_id
        self.category_id = category_id
        self.family_name = family_name
        self.type_id = type_id
        self.type_label = type_label
        self.has_direction = bool(has_direction)
        self.angle_deg = float(angle_deg or 0.0)
        self.axis_symmetric = bool(axis_symmetric)
        self.label = label

    def type_key(self):
        return safe_text(self.type_id)

    def family_key(self):
        return safe_text(self.family_name).strip().lower()

    def display_label(self):
        if self.label:
            return self.label
        family = safe_text(self.family_name).strip()
        type_name = safe_text(self.type_label).strip()
        if family and type_name:
            return "{0} : {1}".format(family, type_name)
        return family or type_name or safe_text(self.element_id)


class FatalIssue(object):
    def __init__(self, code, message, details=None):
        self.code = code
        self.message = message
        self.details = list(details or [])


class ConflictGroup(object):
    """A set of references that cannot coexist; the user picks one winner."""

    def __init__(self, code, key_label, members=None):
        self.code = code
        self.key_label = key_label
        self.members = list(members or [])
        self.chosen_index = None

    @property
    def title(self):
        return CONFLICT_TITLES.get(self.code, "Reference conflict")

    def is_resolved(self):
        return self.chosen_index is not None


DROP_ALL = -1


class ValidationResult(object):
    def __init__(self, accepted=None, fatals=None, conflicts=None, notes=None):
        self.accepted = list(accepted or [])
        self.fatals = list(fatals or [])
        self.conflicts = list(conflicts or [])
        self.notes = list(notes or [])

    @property
    def is_blocked(self):
        return bool(self.fatals) or bool(self.conflicts)

    @property
    def is_fatal(self):
        return bool(self.fatals)


class TargetMatch(object):
    def __init__(self, rule=None, use_local_frame=False, skip_reason=""):
        self.rule = rule
        self.use_local_frame = bool(use_local_frame)
        self.skip_reason = skip_reason or ""

    @property
    def matched(self):
        return self.rule is not None and not self.skip_reason


class SkippedItem(object):
    def __init__(self, label, reason, detail="", element_id=None):
        self.label = label
        self.reason = reason
        self.detail = detail
        # Kept so the report's "Select skipped elements" button can put the
        # user straight onto what needs attention.
        self.element_id = element_id

    def reason_text(self):
        return SKIP_TEXT.get(self.reason, self.reason or "Skipped")


class ProcessSummary(object):
    def __init__(self):
        self.considered = 0
        self.aligned = 0
        self.tagged = 0
        self.already_aligned = 0
        self.skipped = []
        self.failed = []
        self.notes = []
        self.cancelled = False

    def skip(self, label, reason, detail="", element_id=None):
        self.skipped.append(SkippedItem(label, reason, detail, element_id))

    def fail(self, label, detail, element_id=None):
        self.failed.append(SkippedItem(label, "failed", detail, element_id))

    def note(self, text):
        text = safe_text(text)
        if text and text not in self.notes:
            self.notes.append(text)

    def skipped_element_ids(self):
        ids = []
        for item in list(self.skipped) + list(self.failed):
            if item.element_id is not None:
                ids.append(item.element_id)
        return ids

    @property
    def changed(self):
        return self.aligned + self.tagged

    def merge(self, other):
        if other is None:
            return self
        self.considered += other.considered
        self.aligned += other.aligned
        self.tagged += other.tagged
        self.already_aligned += other.already_aligned
        self.skipped.extend(other.skipped)
        self.failed.extend(other.failed)
        for note in other.notes:
            self.note(note)
        return self


class TagAlignSession(object):
    """Everything the wizard carries between pages."""

    def __init__(self):
        self.references = []
        self.reference_mode = None
        self.any_orientation = False
        self.scope = DEFAULT_SCOPE
        self.scale_with_view = True
        self.copy_leader = True
        self.change_tag_type = False
        self.include_pinned = False
        self.collapse_flip = True
        self.rotate_fallback = False
        self.confirm_before_process = True
        self.create_tags = False
        self.align_existing = True
        self.batch_ids = []
        self.running = ProcessSummary()

    @property
    def has_references(self):
        return bool(self.references)

    @property
    def uses_local_frame_everywhere(self):
        return self.reference_mode == MODE_SINGLE and bool(self.any_orientation)

    def tag_type_id(self):
        for rule in self.references:
            if rule.tag_type_id is not None:
                return rule.tag_type_id
        return None

    def tag_type_label(self):
        for rule in self.references:
            if rule.tag_type_label:
                return rule.tag_type_label
        return ""

    def host_category_id(self):
        for rule in self.references:
            if rule.host_category_id is not None:
                return rule.host_category_id
        return None

    def host_category_name(self):
        for rule in self.references:
            if rule.host_category_name:
                return rule.host_category_name
        return ""

    def reset_references(self):
        self.references = []
        self.reference_mode = None
        self.any_orientation = False


# ---------------------------------------------------------------------------
# offsets
# ---------------------------------------------------------------------------


def paper_offset(offset, view_scale):
    """Model-feet offset divided by the view scale (printed distance)."""
    scale = float(view_scale or 1) or 1.0
    return (float(offset[0]) / scale, float(offset[1]) / scale)


def offsets_equal(first, second, tol=POSITION_TOL):
    if first is None or second is None:
        return first is None and second is None
    return (
        abs(float(first[0]) - float(second[0])) <= tol + EPS
        and abs(float(first[1]) - float(second[1])) <= tol + EPS
    )


def comparable_offset(rule, scale_with_view, attribute="offset_view"):
    """The offset used when deciding whether two references agree.

    With the view-scale option on, references measured in differently scaled
    views are compared by printed distance; with it off, by model distance.
    """
    offset = getattr(rule, attribute, None) or (0.0, 0.0)
    if scale_with_view:
        return paper_offset(offset, rule.view_scale)
    return (float(offset[0]), float(offset[1]))


def rotate_offset(offset, angle_deg):
    """Rotate an ``(a, b)`` pair by ``angle_deg`` counter-clockwise."""
    radians = math.radians(float(angle_deg or 0.0))
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    a = float(offset[0])
    b = float(offset[1])
    return (a * cos_a - b * sin_a, a * sin_a + b * cos_a)


def scale_offset(offset, source_view_scale, target_view_scale, scale_with_view):
    """Re-express a model-feet offset for a view of a different scale."""
    if not scale_with_view:
        return (float(offset[0]), float(offset[1]))
    source = float(source_view_scale or 1) or 1.0
    target = float(target_view_scale or 1) or 1.0
    factor = target / source
    return (float(offset[0]) * factor, float(offset[1]) * factor)


def compose_target_offset(rule, target, use_local_frame, target_view_scale, scale_with_view):
    """Model-feet ``(u, v)`` along the *target view's* Right/Up axes."""
    if use_local_frame:
        base = rotate_offset(rule.offset_local, target.angle_deg if target.has_direction else 0.0)
    else:
        base = rule.offset_view
    return scale_offset(base, rule.view_scale, target_view_scale, scale_with_view)


def compose_target_secondary(rule, target, use_local_frame, target_view_scale,
                             scale_with_view, view_attr, local_attr):
    """Same as :func:`compose_target_offset` for the leader elbow / end."""
    if use_local_frame:
        source = getattr(rule, local_attr, None)
        if source is None:
            return None
        base = rotate_offset(source, target.angle_deg if target.has_direction else 0.0)
    else:
        source = getattr(rule, view_attr, None)
        if source is None:
            return None
        base = source
    return scale_offset(base, rule.view_scale, target_view_scale, scale_with_view)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def narrower_scope(scope):
    """The next step down from ``scope``, or ``None`` at the narrowest.

    Stepping down one level is the usual way out of a conflict: a set that is
    ambiguous across a whole category is often perfectly well defined per
    family, and only rarely needs the jump all the way to exact type.
    """
    try:
        index = SCOPE_ORDER.index(scope)
    except ValueError:
        return SCOPE_EXACT_TYPE
    if index + 1 < len(SCOPE_ORDER):
        return SCOPE_ORDER[index + 1]
    return None


def cluster_by_orientation(rules, collapse_flip, tol=ORIENTATION_TOL_DEG):
    """Greedy grouping of references that point the same way.

    References are few (a handful, rarely more than a dozen), so a greedy pass
    beats a bucket key - rounding to a grid would split two directions that sit
    either side of a bucket edge but are well inside tolerance of each other.
    """
    clusters = []
    for rule in rules:
        placed = False
        for cluster in clusters:
            head = cluster[0]
            if head.has_direction != rule.has_direction:
                continue
            if not rule.has_direction:
                cluster.append(rule)
                placed = True
                break
            fold = should_fold(head.axis_symmetric, rule.axis_symmetric, collapse_flip)
            if angles_match(head.angle_deg, rule.angle_deg, fold, tol):
                cluster.append(rule)
                placed = True
                break
        if not placed:
            clusters.append([rule])
    return clusters


def validate_references(references, scope=DEFAULT_SCOPE, scale_with_view=True,
                        collapse_flip=True):
    """Check a reference set against the rules the user agreed to.

    Fatal issues discard the whole set; conflicts are resolvable by picking a
    winner per group.  Runs again whenever the scope or the view-scale option
    changes, because both can turn a fine set into an ambiguous one.
    """
    references = list(references or [])
    fatals = []
    notes = []

    usable = []
    for rule in references:
        if rule.invalid_reason:
            notes.append(
                "Dropped {0}: {1}".format(rule.host_label() or "reference", rule.invalid_reason)
            )
            continue
        usable.append(rule)

    if not usable:
        return ValidationResult(accepted=[], fatals=fatals, conflicts=[], notes=notes)

    tag_types = []
    for rule in usable:
        key = safe_text(rule.tag_type_id)
        if key not in [item[0] for item in tag_types]:
            tag_types.append((key, rule.tag_type_label or key))
    if len(tag_types) > 1:
        fatals.append(
            FatalIssue(
                TAG_TYPE_MIXED,
                "Only one tag family type can be used as the reference.",
                ["{0}".format(label) for _, label in tag_types],
            )
        )

    categories = []
    for rule in usable:
        key = safe_text(rule.host_category_id)
        if key not in [item[0] for item in categories]:
            categories.append((key, rule.host_category_name or key))
    if len(categories) > 1:
        fatals.append(
            FatalIssue(
                CATEGORY_MIXED,
                "Reference tags must all label elements of the same category.",
                ["{0}".format(label) for _, label in categories],
            )
        )

    if fatals:
        return ValidationResult(accepted=[], fatals=fatals, conflicts=[], notes=notes)

    conflicts = []
    accepted = []

    groups = {}
    order = []
    for rule in usable:
        key = rule.scope_key(scope)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rule)

    for key in order:
        for cluster in cluster_by_orientation(groups[key], collapse_flip):
            winners = []
            for rule in cluster:
                offset = comparable_offset(rule, scale_with_view)
                duplicate_of = None
                for kept in winners:
                    if offsets_equal(offset, comparable_offset(kept, scale_with_view)):
                        duplicate_of = kept
                        break
                if duplicate_of is None:
                    winners.append(rule)
                else:
                    notes.append(
                        "Merged a duplicate reference on {0} ({1}).".format(
                            rule.host_label(), rule.orientation_label()
                        )
                    )

            if len(winners) == 1:
                accepted.append(winners[0])
                continue

            type_keys = set([rule.type_key() for rule in winners])
            family_keys = set([rule.family_key() for rule in winners])
            code = POSITION_CONFLICT if len(type_keys) == 1 else SCOPE_AMBIGUITY
            head = winners[0]

            if code == POSITION_CONFLICT:
                subject = head.host_label()
            elif len(family_keys) == 1:
                subject = safe_text(head.host_family_name) or "family"
            else:
                # Category scope: the references do not even share a family, so
                # naming one of them would read as if the others belonged to it.
                subject = "{0} (any family)".format(
                    safe_text(head.host_category_name) or "category"
                )

            conflicts.append(
                ConflictGroup(
                    code,
                    "{0} - {1}".format(subject, head.orientation_label()),
                    winners,
                )
            )

    return ValidationResult(accepted=accepted, fatals=fatals, conflicts=conflicts, notes=notes)


def apply_conflict_choices(validation):
    """Fold resolved conflict groups back into the accepted reference list."""
    accepted = list(validation.accepted)
    for group in validation.conflicts:
        if group.chosen_index is None or group.chosen_index == DROP_ALL:
            continue
        try:
            accepted.append(group.members[group.chosen_index])
        except Exception:
            continue
    return accepted


def unresolved_conflicts(validation):
    return [group for group in validation.conflicts if not group.is_resolved()]


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def candidates_for_target(references, target, scope):
    """References whose category and scope allow them to cover ``target``.

    Narrowest match wins regardless of scope: a reference on the target's own
    type beats one on a sibling type, which beats one from another family in
    the category.  Widening the scope therefore only adds fallbacks.
    """
    same_category = [
        rule
        for rule in references
        if safe_text(rule.host_category_id) == safe_text(target.category_id)
    ]
    if not same_category:
        return [], SKIP_CATEGORY_MISMATCH

    exact = [rule for rule in same_category if rule.type_key() == target.type_key()]
    if exact:
        return exact, ""

    if scope == SCOPE_EXACT_TYPE:
        return [], SKIP_NO_RULE_FOR_TYPE

    family = [rule for rule in same_category if rule.family_key() == target.family_key()]
    if family:
        return family, ""

    if scope == SCOPE_PAIRED_FAMILY:
        return [], SKIP_NO_RULE_FOR_FAMILY

    return same_category, ""


def resolve_reference_for_target(references, target, scope=DEFAULT_SCOPE,
                                 any_orientation=False, collapse_flip=True,
                                 rotate_fallback=False):
    """Pick the reference that governs ``target``, or say why none does."""
    candidates, reason = candidates_for_target(references, target, scope)
    if reason:
        return TargetMatch(skip_reason=reason)

    if any_orientation:
        return TargetMatch(rule=candidates[0], use_local_frame=True)

    if not target.has_direction:
        without_direction = [rule for rule in candidates if not rule.has_direction]
        if len(without_direction) == 1:
            return TargetMatch(rule=without_direction[0])
        if len(candidates) == 1:
            return TargetMatch(rule=candidates[0])
        return TargetMatch(skip_reason=SKIP_AMBIGUOUS_ORIENTATION)

    directed = [rule for rule in candidates if rule.has_direction]
    matches = []
    for rule in directed:
        fold = should_fold(rule.axis_symmetric, target.axis_symmetric, collapse_flip)
        if angles_match(rule.angle_deg, target.angle_deg, fold):
            matches.append(rule)
    if matches:
        return TargetMatch(rule=matches[0])

    if not directed:
        # Only references on elements that had no direction of their own.
        if len(candidates) == 1:
            return TargetMatch(rule=candidates[0], use_local_frame=False)
        return TargetMatch(skip_reason=SKIP_NO_REFERENCE_FOR_ORIENTATION)

    if rotate_fallback:
        nearest = None
        nearest_gap = None
        for rule in directed:
            fold = should_fold(rule.axis_symmetric, target.axis_symmetric, collapse_flip)
            gap = angular_distance(rule.angle_deg, target.angle_deg, fold)
            if nearest_gap is None or gap < nearest_gap:
                nearest = rule
                nearest_gap = gap
        return TargetMatch(rule=nearest, use_local_frame=True)

    return TargetMatch(skip_reason=SKIP_NO_REFERENCE_FOR_ORIENTATION)


def resolve_for_session(session, target):
    return resolve_reference_for_target(
        session.references,
        target,
        scope=session.scope,
        any_orientation=session.uses_local_frame_everywhere,
        collapse_flip=session.collapse_flip,
        rotate_fallback=session.rotate_fallback,
    )


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------


def default_length_formatter(value):
    """Feet with two decimals - replaced by Revit's unit formatter at runtime."""
    return "{0:.2f} ft".format(float(value or 0.0))


def describe_offset(offset, format_length=None, view_scale=None):
    format_length = format_length or default_length_formatter
    u = float(offset[0])
    v = float(offset[1])
    horizontal = "Right" if u >= 0 else "Left"
    vertical = "Up" if v >= 0 else "Down"
    text = "{0} {1}, {2} {3}".format(
        horizontal, format_length(abs(u)), vertical, format_length(abs(v))
    )
    if view_scale:
        text += " at 1:{0}".format(int(view_scale))
    return text


def build_reference_row(rule, format_length=None):
    """Display strings for one row of the reference grid."""
    return {
        "tag_type": safe_text(rule.tag_type_label),
        "host": rule.host_label(),
        "category": safe_text(rule.host_category_name),
        "orientation": rule.orientation_label(),
        "offset": describe_offset(rule.offset_view, format_length, rule.view_scale),
        "anchor": ANCHOR_LABELS.get(rule.anchor_kind, rule.anchor_kind),
        "view": safe_text(rule.view_name),
        "notes": "; ".join(rule.notes),
        "rule": rule,
    }


def build_fatal_text(validation):
    lines = []
    for issue in validation.fatals:
        lines.append(issue.message)
        for detail in issue.details:
            lines.append("  - {0}".format(detail))
        lines.append("")
    lines.append("The reference selection was discarded. Please select again.")
    return "\n".join(lines).strip()


def group_skips(summary):
    """``[(reason_text, count, [labels])]`` ordered by count, worst first."""
    buckets = {}
    order = []
    for item in list(summary.skipped or []) + list(summary.failed or []):
        key = item.reason
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)

    rows = []
    for key in order:
        items = buckets[key]
        text = SKIP_TEXT.get(key, key)
        if key == "failed":
            text = "Failed"
        rows.append((text, len(items), [safe_text(item.label) for item in items]))
    rows.sort(key=lambda row: -row[1])
    return rows


def build_preview_text(plan_summary, create_tags):
    """One-line-per-outcome preview shown before a batch write."""
    lines = [
        "About to process {0} element(s).".format(plan_summary.considered),
        "",
        "Align existing tags: {0}".format(plan_summary.aligned),
    ]
    if create_tags:
        lines.append("Create new tags: {0}".format(plan_summary.tagged))
    if plan_summary.already_aligned:
        lines.append("Already in position: {0}".format(plan_summary.already_aligned))

    skips = group_skips(plan_summary)
    if skips:
        lines.append("")
        lines.append("Skipped: {0}".format(sum(row[1] for row in skips)))
        for text, count, _ in skips:
            lines.append("  - {0}: {1}".format(text, count))
    return "\n".join(lines)


def build_report_text(summary, create_tags=False):
    lines = [
        "Elements processed: {0}".format(summary.considered),
        "Tags aligned: {0}".format(summary.aligned),
    ]
    if create_tags:
        lines.append("Tags created: {0}".format(summary.tagged))
    if summary.already_aligned:
        lines.append("Already in position: {0}".format(summary.already_aligned))

    skipped_items = list(summary.skipped or [])
    failed_items = list(summary.failed or [])
    lines.append("Skipped: {0}".format(len(skipped_items)))
    if failed_items:
        lines.append("Failed: {0}".format(len(failed_items)))

    if summary.cancelled:
        lines.append("")
        lines.append("Cancelled before the end - nothing was kept.")

    rows = group_skips(summary)
    if rows:
        lines.append("")
        lines.append("Reasons:")
        shown = 0
        for text, count, labels in rows:
            lines.append("- {0}: {1}".format(text, count))
            for label in labels[:3]:
                lines.append("    {0}".format(label))
                shown += 1
                if shown >= SKIPPED_DISPLAY_LIMIT:
                    break
            if len(labels) > 3:
                lines.append("    plus {0} more".format(len(labels) - 3))
            if shown >= SKIPPED_DISPLAY_LIMIT:
                lines.append("- Output truncated.")
                break

    notes = list(summary.notes or [])
    if notes:
        lines.append("")
        lines.append("Notes:")
        for note in notes[:NOTES_DISPLAY_LIMIT]:
            lines.append("- {0}".format(note))
        if len(notes) > NOTES_DISPLAY_LIMIT:
            lines.append("- Plus {0} more.".format(len(notes) - NOTES_DISPLAY_LIMIT))

    return "\n".join(lines)


def build_running_text(summary, create_tags=False):
    parts = ["{0} aligned".format(summary.aligned)]
    if create_tags:
        parts.append("{0} tagged".format(summary.tagged))
    parts.append("{0} skipped".format(len(summary.skipped) + len(summary.failed)))
    return ", ".join(parts) + " so far."


# ---------------------------------------------------------------------------
# selection filter
# ---------------------------------------------------------------------------


class FilterNode(object):
    """One row of the category -> family -> type filter tree."""

    def __init__(self, key, label, count=0, children=None):
        self.key = key
        self.label = label
        self.count = count
        self.children = list(children or [])
        self.is_checked = True

    def display(self):
        return "{0}  ({1})".format(self.label, self.count)


def build_filter_tree(entries):
    """``entries`` are dicts with category/family/type names and an element id."""
    categories = {}
    order = []
    for entry in entries or []:
        category = safe_text(entry.get("category")) or "<no category>"
        family = safe_text(entry.get("family")) or "<no family>"
        type_name = safe_text(entry.get("type")) or "<no type>"
        if category not in categories:
            categories[category] = {"families": {}, "order": [], "count": 0}
            order.append(category)
        bucket = categories[category]
        bucket["count"] += 1
        if family not in bucket["families"]:
            bucket["families"][family] = {"types": {}, "order": [], "count": 0}
            bucket["order"].append(family)
        family_bucket = bucket["families"][family]
        family_bucket["count"] += 1
        if type_name not in family_bucket["types"]:
            family_bucket["types"][type_name] = 0
            family_bucket["order"].append(type_name)
        family_bucket["types"][type_name] += 1

    tree = []
    for category in sorted(order, key=lambda name: name.lower()):
        bucket = categories[category]
        family_nodes = []
        for family in sorted(bucket["order"], key=lambda name: name.lower()):
            family_bucket = bucket["families"][family]
            type_nodes = [
                FilterNode(
                    (category, family, type_name),
                    type_name,
                    family_bucket["types"][type_name],
                )
                for type_name in sorted(family_bucket["order"], key=lambda name: name.lower())
            ]
            family_nodes.append(
                FilterNode((category, family), family, family_bucket["count"], type_nodes)
            )
        tree.append(FilterNode((category,), category, bucket["count"], family_nodes))
    return tree


def filter_entries(entries, allowed_type_keys):
    """Keep the entries whose (category, family, type) triple is still ticked."""
    allowed = set(allowed_type_keys or [])
    kept = []
    for entry in entries or []:
        key = (
            safe_text(entry.get("category")) or "<no category>",
            safe_text(entry.get("family")) or "<no family>",
            safe_text(entry.get("type")) or "<no type>",
        )
        if key in allowed:
            kept.append(entry)
    return kept


def checked_type_keys(tree):
    keys = set()
    for category_node in tree or []:
        for family_node in category_node.children:
            for type_node in family_node.children:
                if type_node.is_checked:
                    keys.add(type_node.key)
    return keys
