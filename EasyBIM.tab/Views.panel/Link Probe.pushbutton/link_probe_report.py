# -*- coding: utf-8 -*-
"""Pure formatting and classification for Link Probe.

No Revit, pyRevit, System or clr imports live here, so the whole diagnostic
can be reasoned about and unit-tested off Revit.  The Revit layer collects
plain values - strings, ints, booleans, sets of category names - and hands
them to :func:`build_report`.

Two pieces of judgement live in this module and are the reason it exists
separately:

``classify_binding``
    The "Frozen" diagnosis.  The Revit API cannot read the Model Categories
    tab of a link's display settings, so frozen state is *inferred*: if the
    link is Custom and all nine readable Basics properties still say "by
    linked view", then nothing on the Basics tab put it into Custom, and the
    only other door into Custom is a category or workset tab.

``geometry_verdict``
    Whether ``Options.View`` makes link geometry respect the link's display
    settings.  The whole probe exists to answer this, and the answer hinges
    on an asymmetry that is easy to get wrong: a truncated walk can prove a
    category is *present*, never that it is *absent*.
"""

from __future__ import print_function


# ---------------------------------------------------------------------------
# Revit API vocabulary
# ---------------------------------------------------------------------------

# LinkVisibility member names, spelled exactly as Revit spells them.  It is
# ByLinkView - not ByLinkedView - and a typo here binds to nothing at runtime
# while looking perfectly reasonable in review.
VIS_BY_HOST = u"ByHostView"
VIS_BY_LINK = u"ByLinkView"
VIS_CUSTOM = u"Custom"

# The nine RevitLinkGraphicsSettings members that Revit 2025 added and that
# make the Frozen inference possible.  Exactly nine: the inference is defined
# as "all of these read ByLinkView", so a tenth property in a future Revit
# must break a test rather than quietly weaken the diagnosis.
BASICS_TYPE_FIELDS = (
    u"view_range",
    u"color_fill",
    u"object_styles",
    u"nested_links",
    u"view_filters",
    u"discipline_type",
    u"phase_type",
    u"phase_filter_type",
    u"detail_level_type",
)

BASICS_LABELS = {
    u"view_range": u"View range",
    u"color_fill": u"Color fill",
    u"object_styles": u"Object styles",
    u"nested_links": u"Nested links",
    u"view_filters": u"View filters",
    u"discipline_type": u"Discipline",
    u"phase_type": u"Phase",
    u"phase_filter_type": u"Phase filter",
    u"detail_level_type": u"Detail level",
}


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

CLASS_TRACKING = u"Tracking"
CLASS_FROZEN = u"Frozen (inferred)"
CLASS_AMBIGUOUS = u"Ambiguous"
CLASS_BROKEN = u"Broken"
CLASS_UNMANAGED = u"Unmanaged"
CLASS_UNREADABLE = u"Unreadable"

CLASS_NOTES = {
    CLASS_TRACKING:
        u"By Linked View - follows the architect, cannot go stale.",
    CLASS_FROZEN:
        u"Custom, but every readable Basics property still inherits, so the "
        u"customization is on a category or workset tab. Those tabs hold a "
        u"frozen copy the API cannot read.",
    CLASS_AMBIGUOUS:
        u"Custom with at least one Basics property overridden. There may or "
        u"may not also be a frozen category tab - unreadable either way.",
    CLASS_BROKEN:
        u"Points at a linked view that no longer resolves. Revit falls back "
        u"to By Host View without saying so.",
    CLASS_UNMANAGED:
        u"By Host View - the link draws with this view's own V/G.",
    CLASS_UNREADABLE:
        u"Could not be read; see the note.",
}


def classify_binding(visibility,
                     basics=None,
                     link_loaded=True,
                     linked_view_resolved=False,
                     had_linked_view_id=False,
                     basics_api=True,
                     read_error=u""):
    """Diagnose one (view, link) binding from readable state alone.

    ``basics`` maps :data:`BASICS_TYPE_FIELDS` members to LinkVisibility
    member names, or to ``None`` where the property could not be read.

    Order matters more than it looks:

    * Unreadable wins outright.  An unloaded architectural link must not
      turn every view in the project into a false ``Broken``.
    * ``Broken`` beats ``Frozen``: a binding pointing at nothing is a
      different, worse problem than one pointing at something stale.
    * Without the 2025 properties the Frozen inference has no evidence, so
      Custom degrades to ``Ambiguous``.  Never claim Frozen on a host that
      cannot see the nine.
    """
    if not link_loaded:
        return CLASS_UNREADABLE, u"The link is not loaded."
    if read_error:
        return CLASS_UNREADABLE, read_error

    basics = basics or {}

    if visibility == VIS_BY_HOST:
        # A dangling LinkedViewId leaves its fossil behind when Revit
        # silently reverts the display.  That fossil is the fingerprint of a
        # binding that used to work - quite different from a view nobody
        # ever bound.
        if had_linked_view_id and not linked_view_resolved:
            return CLASS_BROKEN, u"By Host View, but a dead linked-view id remains."
        return CLASS_UNMANAGED, CLASS_NOTES[CLASS_UNMANAGED]

    if visibility in (VIS_BY_LINK, VIS_CUSTOM) and not linked_view_resolved:
        return CLASS_BROKEN, u"The stored linked view id does not resolve."

    if visibility == VIS_BY_LINK:
        return CLASS_TRACKING, CLASS_NOTES[CLASS_TRACKING]

    if visibility == VIS_CUSTOM:
        if not basics_api:
            return (CLASS_AMBIGUOUS,
                    u"Revit 2024 exposes no Basics properties, so Frozen "
                    u"cannot be distinguished from Ambiguous here.")
        overridden = overridden_basics(basics)
        if overridden:
            return (CLASS_AMBIGUOUS,
                    u"Overridden on the Basics tab: " + u", ".join(overridden))
        return CLASS_FROZEN, CLASS_NOTES[CLASS_FROZEN]

    return CLASS_UNREADABLE, u"Unrecognised visibility type: " + _text(visibility)


def overridden_basics(basics):
    """Labels of the Basics properties that are not inheriting.

    A property that could not be read is ``None`` and is deliberately not
    reported as overridden - unknown is not the same as changed, and calling
    it changed would turn every unreadable property into a false Ambiguous.
    """
    labels = []
    for field in BASICS_TYPE_FIELDS:
        value = (basics or {}).get(field)
        if value is None:
            continue
        if value != VIS_BY_LINK:
            labels.append(u"{0}={1}".format(BASICS_LABELS[field], value))
    return labels


# ---------------------------------------------------------------------------
# the geometry experiment
# ---------------------------------------------------------------------------

VERDICT_WORKS = u"WORKS"
VERDICT_IGNORES_SETTINGS = u"IGNORES_SETTINGS"
VERDICT_VIEW_IGNORED = u"VIEW_IGNORED"
VERDICT_UNUSABLE = u"UNUSABLE"
VERDICT_INCONCLUSIVE = u"INCONCLUSIVE"

VERDICT_MEANINGS = {
    VERDICT_WORKS: (
        u"Options.View DOES respect the link's display settings. A tool can "
        u"read which categories a link is actually drawing, so the frozen "
        u"category table becomes observable. This changes the design."),
    VERDICT_IGNORES_SETTINGS: (
        u"Link geometry ignores the link's display settings: the hidden "
        u"category came back anyway. The frozen table stays unreadable."),
    VERDICT_VIEW_IGNORED: (
        u"Setting Options.View changed nothing at all - the same categories "
        u"came back with and without it. The frozen table stays unreadable."),
    VERDICT_UNUSABLE: (
        u"Geometry extraction did not return usable data. See the error."),
    VERDICT_INCONCLUSIVE: (
        u"The run could not decide. See the note; usually the walk was "
        u"truncated, or the chosen category never appeared at all."),
}


def geometry_verdict(control_categories,
                     test_categories,
                     target_category,
                     control_truncated=False,
                     test_truncated=False,
                     error=u""):
    """Read the experiment.  Returns ``(verdict, note)``.

    ``control_categories`` is what came back with no view applied;
    ``test_categories`` is what came back with ``Options.View`` set to a view
    where ``target_category`` is hidden inside the link.

    The asymmetry that governs everything here: a truncated walk stops early,
    so it can prove a category is **present** but never that it is
    **absent**.  Every verdict that rests on absence - WORKS and
    VIEW_IGNORED - is therefore downgraded to INCONCLUSIVE when the walk that
    would have to have found it was cut short.  Reporting a truncated absence
    as WORKS would be the single most expensive mistake this probe could
    make, because the whole product would then be built on it.
    """
    if error:
        return VERDICT_UNUSABLE, error

    control = set(control_categories or [])
    test = set(test_categories or [])
    target = _text(target_category)

    if not control and not test:
        return VERDICT_UNUSABLE, u"No geometry came back from either pass."

    if target not in control:
        return (VERDICT_INCONCLUSIVE,
                u"'{0}' never appeared even without a view applied{1}. Pick a "
                u"category the link actually contains, or raise the cap."
                .format(target,
                        u" (control walk truncated)" if control_truncated else u""))

    in_test = target in test

    if in_test:
        # A presence claim survives truncation: we found it, so it is there.
        return (VERDICT_IGNORES_SETTINGS,
                u"'{0}' came back in both passes, even though it is hidden in "
                u"the link's display settings.".format(target))

    if test_truncated:
        return (VERDICT_INCONCLUSIVE,
                u"'{0}' was absent from the view pass, but that walk was "
                u"truncated - it may simply not have been reached. Raise the "
                u"cap and run again.".format(target))

    if control == test:
        return (VERDICT_VIEW_IGNORED,
                u"Both passes returned exactly the same categories.")

    return (VERDICT_WORKS,
            u"'{0}' came back without a view and disappeared with the view "
            u"applied.".format(target))


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

RULE = u"=" * 74
THIN = u"-" * 74


def _text(value):
    if value is None:
        return u""
    try:
        return value if isinstance(value, type(u"")) else unicode(value)  # noqa: F821
    except NameError:  # pragma: no cover - Python 3 test host
        return value if isinstance(value, str) else str(value)
    except Exception:
        return u""


def _yes_no(value):
    if value is None:
        return u"unknown"
    return u"yes" if value else u"no"


LABEL_WIDTH = 28


def _kv(lines, label, value, indent=2):
    # Always at least two spaces, even when the label overruns the column.
    # A fixed-width format silently welds "Template controls RVT Links" to
    # its value, and this report exists to be read.
    label = _text(label)
    pad = max(LABEL_WIDTH - len(label), 2)
    lines.append(u"{0}{1}{2}{3}".format(
        u" " * indent, label, u" " * pad, _text(value)))


def _section(lines, title):
    lines.append(u"")
    lines.append(RULE)
    lines.append(title)
    lines.append(RULE)


def build_report(payload):
    """Render the whole diagnostic as plain copy-pasteable text."""
    payload = payload or {}
    lines = []

    lines.append(RULE)
    lines.append(u"EASYBIM LINK PROBE")
    lines.append(RULE)
    lines.append(u"Read-only. No transaction was opened and nothing was "
                 u"changed in this model.")

    _section(lines, u"1. HOST")
    host = payload.get("host") or {}
    _kv(lines, u"Revit version", host.get("version"))
    _kv(lines, u"Build", host.get("build"))
    _kv(lines, u"Document", host.get("title"))
    _kv(lines, u"Workshared", _yes_no(host.get("workshared")))
    _kv(lines, u"Link overrides API (2024+)", _yes_no(host.get("links_api")))
    _kv(lines, u"Basics properties (2025+)", _yes_no(host.get("basics_api")))
    if not host.get("basics_api"):
        lines.append(u"  NOTE  Without the 2025 properties the Frozen "
                     u"diagnosis cannot be made; every Custom binding below "
                     u"reads as Ambiguous.")

    _section(lines, u"2. ACTIVE VIEW")
    view = payload.get("view") or {}
    _kv(lines, u"Name", view.get("name"))
    _kv(lines, u"Type", view.get("type"))
    _kv(lines, u"Is a view template", _yes_no(view.get("is_template")))
    _kv(lines, u"Is dependent", _yes_no(view.get("is_dependent")))
    if view.get("primary_name"):
        _kv(lines, u"Primary view", view.get("primary_name"))
        lines.append(u"  NOTE  V/G on a dependent view comes from its "
                     u"primary; writes to it would be inert.")
    _kv(lines, u"View template", view.get("template_name") or u"(none)")
    _kv(lines, u"Template controls RVT Links",
        _yes_no(view.get("template_controls_links")))
    if view.get("template_controls_links"):
        lines.append(u"  NOTE  SetLinkOverrides on this view would succeed "
                     u"and change nothing - the template owns the setting.")

    _section(lines, u"3. LINKS IN THIS MODEL")
    links = payload.get("links") or []
    if not links:
        lines.append(u"  No Revit links found.")
    for link in links:
        lines.append(u"  {0}".format(_text(link.get("label"))))
        _kv(lines, u"Loaded", _yes_no(link.get("loaded")), indent=6)
        _kv(lines, u"Instance id", link.get("instance_key"), indent=6)
        _kv(lines, u"Type id", link.get("type_key"), indent=6)
        if link.get("error"):
            _kv(lines, u"Error", link.get("error"), indent=6)

    _section(lines, u"4. LINK DISPLAY SETTINGS ON THE ACTIVE VIEW")
    lines.append(u"Frozen is INFERRED, never measured - the API cannot read "
                 u"the category tabs.")
    lines.append(u"Please check each verdict against the Revit dialog and say "
                 u"where it is wrong.")
    for binding in payload.get("bindings") or []:
        lines.append(u"")
        lines.append(u"  {0}".format(_text(binding.get("link_label"))))
        _kv(lines, u"Visibility type", binding.get("visibility"), indent=6)
        _kv(lines, u"Linked view", binding.get("linked_view_name") or u"(none)",
            indent=6)
        _kv(lines, u"Linked view id resolves",
            _yes_no(binding.get("linked_view_resolved")), indent=6)
        _kv(lines, u"VERDICT", binding.get("classification"), indent=6)
        note = binding.get("note")
        if note:
            lines.append(u"      why: {0}".format(_text(note)))
        basics = binding.get("basics") or {}
        lines.append(u"      Basics properties:")
        for field in BASICS_TYPE_FIELDS:
            value = basics.get(field)
            lines.append(u"        {0:<16}{1}".format(
                BASICS_LABELS[field],
                _text(value) if value is not None else u"(unreadable)"))
        extra = binding.get("extra") or {}
        for key in sorted(extra.keys()):
            lines.append(u"        {0:<16}{1}".format(key, _text(extra[key])))
        if binding.get("type_slot_differs") is not None:
            _kv(lines, u"Type-id slot differs",
                _yes_no(binding.get("type_slot_differs")), indent=6)

    _section(lines, u"5. THE GEOMETRY EXPERIMENT")
    geom = payload.get("geometry") or {}
    if not geom:
        lines.append(u"  Not run.")
    else:
        _kv(lines, u"Link tested", geom.get("link_label"))
        _kv(lines, u"Category you hid", geom.get("target_category"))
        _kv(lines, u"Objects walked (no view)", geom.get("control_count"))
        _kv(lines, u"Truncated", _yes_no(geom.get("control_truncated")))
        _kv(lines, u"Objects walked (with view)", geom.get("test_count"))
        _kv(lines, u"Truncated", _yes_no(geom.get("test_truncated")))
        _kv(lines, u"Categories (no view)", len(geom.get("control") or []))
        _kv(lines, u"Categories (with view)", len(geom.get("test") or []))
        lines.append(u"")
        lines.append(u"  VERDICT: {0}".format(_text(geom.get("verdict"))))
        lines.append(u"  {0}".format(_text(
            VERDICT_MEANINGS.get(geom.get("verdict"), u""))))
        if geom.get("note"):
            lines.append(u"  note: {0}".format(_text(geom.get("note"))))

        only_control = sorted(set(geom.get("control") or [])
                              - set(geom.get("test") or []))
        only_test = sorted(set(geom.get("test") or [])
                           - set(geom.get("control") or []))
        lines.append(u"")
        lines.append(u"  Present without a view, absent with one ({0}):"
                     .format(len(only_control)))
        for name in only_control[:60]:
            lines.append(u"      {0}".format(_text(name)))
        if len(only_control) > 60:
            lines.append(u"      ... and {0} more".format(len(only_control) - 60))
        if only_test:
            lines.append(u"  Present only with the view applied ({0}):"
                         .format(len(only_test)))
            for name in only_test[:60]:
                lines.append(u"      {0}".format(_text(name)))

    _section(lines, u"6. THE LINKED VIEW, READ DIRECTLY")
    lines.append(u"This is the drift baseline: proof that the architect's own "
                 u"view is fully readable.")
    scan = payload.get("linked_view_scan") or {}
    if not scan:
        lines.append(u"  Not run.")
    else:
        _kv(lines, u"Linked view", scan.get("view_name"))
        _kv(lines, u"Categories readable", scan.get("category_count"))
        _kv(lines, u"Categories hidden there", len(scan.get("hidden") or []))
        _kv(lines, u"Categories overridden", len(scan.get("overridden") or []))
        _kv(lines, u"View filters", len(scan.get("filters") or []))
        _kv(lines, u"View range readable", _yes_no(scan.get("view_range_ok")))
        if scan.get("error"):
            _kv(lines, u"Error", scan.get("error"))
        hidden = sorted(scan.get("hidden") or [])
        lines.append(u"  Hidden in the architect's view:")
        for name in hidden[:60]:
            lines.append(u"      {0}".format(_text(name)))
        if len(hidden) > 60:
            lines.append(u"      ... and {0} more".format(len(hidden) - 60))

    _section(lines, u"7. TIMINGS")
    for label, seconds in payload.get("timings") or []:
        lines.append(u"  {0:<40}{1:.2f}s".format(_text(label), seconds))

    errors = payload.get("errors") or []
    if errors:
        _section(lines, u"8. ERRORS")
        for item in errors:
            lines.append(u"  {0}".format(_text(item)))

    lines.append(u"")
    lines.append(THIN)
    lines.append(u"Copy everything above and paste it back.")
    lines.append(THIN)
    return u"\n".join(lines)
