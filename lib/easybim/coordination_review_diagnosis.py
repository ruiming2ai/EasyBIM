# -*- coding: utf-8 -*-
"""Explain why the Coordination Review report came back empty.

The passive listener cannot prove that links are clear, so an empty capture
used to show a bare ``Detection Error`` that hid four very different
situations: nothing in the model uses Copy/Monitor, the links really have no
changes to review, the listener was not attached when the model opened, or
warnings were captured under a different document identity (which cloud
models make likely, since their path and title can arrive late).

This module turns collected evidence into one verdict.  Pure Python with no
Revit imports, so the unit tests load it standalone.
"""


VERDICT_NOT_APPLICABLE = "not_applicable"
VERDICT_NO_LINKS = "no_links"
VERDICT_LISTENER_OFF = "listener_off"
VERDICT_DOC_MISMATCH = "doc_mismatch"
VERDICT_WARNING_LIST = "warning_list"
VERDICT_CLEAR = "clear"
VERDICT_UNKNOWN = "unknown"

#: Verdicts where the model is fine and the user needs no action.
BENIGN_VERDICTS = (VERDICT_NOT_APPLICABLE, VERDICT_NO_LINKS, VERDICT_CLEAR)


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _as_list(value):
    if not value:
        return []
    try:
        return list(value)
    except Exception:
        return []


def _plural(count, singular, plural=None):
    if _safe_int(count) == 1:
        return singular
    return plural or (singular + "s")


def _mismatch_keys(evidence):
    """Stored document keys that are not this document's own."""
    doc_key = _safe_text(evidence.get("doc_key"))
    aliases = set(_safe_text(alias) for alias in _as_list(evidence.get("doc_aliases")))
    aliases.add(doc_key)
    return [
        key
        for key in (_safe_text(key) for key in _as_list(evidence.get("stored_doc_keys")))
        if key and key not in aliases
    ]


def _listener_covered_open(evidence):
    """False when the listener was demonstrably off while the model opened.

    ``registered_now`` is read at report time, after the listener has done its
    job, so it is not the test.  What matters is whether an attach happened at
    all and whether the last detach happened before the last attach.
    """
    if not _safe_int(evidence.get("register_count")):
        return False
    if evidence.get("registered") or evidence.get("registered_now"):
        return True
    registered_at = evidence.get("registered_at")
    unregistered_at = evidence.get("unregistered_at")
    if registered_at is None:
        return False
    if unregistered_at is None:
        return True
    try:
        return float(unregistered_at) <= float(registered_at)
    except Exception:
        return True


def _saw_traffic(evidence):
    return bool(
        _safe_int(evidence.get("events_seen"))
        or _safe_int(evidence.get("failures_seen"))
    )


def _verdict(code, headline, details, action="", evidence=None):
    return {
        "code": code,
        "headline": headline,
        "details": [line for line in details if line],
        "action": action,
        "benign": code in BENIGN_VERDICTS,
        "evidence": dict(evidence or {}),
    }


def diagnose(evidence):
    """Return the verdict for an empty Coordination Review capture.

    ``evidence`` carries the passive listener's trail (``register_count``,
    ``registered``/``registered_now``, ``registered_at``/``unregistered_at``,
    ``events_seen``, ``failures_seen``, ``matched``, ``recent_texts``,
    ``doc_key``, ``doc_aliases``, ``stored_doc_keys``) plus what the document
    itself says: ``link_count``, ``monitoring_count`` (host elements using
    Copy/Monitor, or None when not scanned) and ``warning_list_matches``
    (Coordination Review entries in Revit's own warning list, or None when
    that list could not be read).
    """
    evidence = dict(evidence or {})
    link_count = evidence.get("link_count")
    monitoring_count = evidence.get("monitoring_count")
    warning_matches = _safe_int(evidence.get("warning_list_matches"), 0)
    mismatched = _mismatch_keys(evidence)

    # 1. Revit's own warning list still holds the warning: trust it over the
    #    listener, which evidently missed the event.
    if warning_matches > 0:
        return _verdict(
            VERDICT_WARNING_LIST,
            "Revit's warning list reports {0} Coordination Review {1}.".format(
                warning_matches, _plural(warning_matches, "warning")
            ),
            [
                "EasyBIM's own listener did not capture the warning when this model "
                "opened, so the list below comes from Revit's warnings instead.",
            ],
            "Open Manage > Warnings to see the same entries.",
            evidence,
        )

    # 2. Nothing to review: no links, or links but no Copy/Monitor at all.
    #    This is the one case that can honestly say the model is fine.
    if link_count is not None and _safe_int(link_count) == 0:
        return _verdict(
            VERDICT_NO_LINKS,
            "This model has no Revit links.",
            ["Coordination Review only applies to linked models."],
            "",
            evidence,
        )

    if monitoring_count is not None and _safe_int(monitoring_count) == 0:
        return _verdict(
            VERDICT_NOT_APPLICABLE,
            "Nothing in this model uses Copy/Monitor.",
            [
                "Coordination Review only reports changes to elements copied or "
                "monitored from a link, and this model monitors none.",
            ],
            "",
            evidence,
        )

    # 3. The listener was not attached while the model opened, so the warning
    #    could not have been captured whether or not it was raised.
    if not _listener_covered_open(evidence):
        return _verdict(
            VERDICT_LISTENER_OFF,
            "EasyBIM's warning listener was not attached when this model opened.",
            [
                "Revit raises the Coordination Review warning once, while the model "
                "loads, so nothing could be captured for this document.",
                "This happens when the model was already open before EasyBIM loaded.",
            ],
            "Reopen the model, or check Manage > Warnings for Coordination Review entries.",
            evidence,
        )

    # 4. Warnings were captured, but filed under another document identity.
    #    Cloud models make this likely: path and title can arrive late.
    if mismatched:
        return _verdict(
            VERDICT_DOC_MISMATCH,
            "Coordination Review warnings were captured under a different document identity.",
            [
                "This document is '{0}'.".format(_safe_text(evidence.get("doc_key")) or "unknown"),
                "Captured under: {0}.".format(", ".join(mismatched[:5])),
                "Cloud models report their path and title late, so the capture and "
                "this report can disagree about which document they describe.",
            ],
            "Send this text to EasyBIM so the identity match can be widened.",
            evidence,
        )

    # 5. The listener was attached and saw Revit's failure traffic, yet no
    #    Coordination Review warning came through: the links have no changes.
    if _saw_traffic(evidence):
        return _verdict(
            VERDICT_CLEAR,
            "No link reported changes needing Coordination Review.",
            [
                "The listener was attached while this model opened and saw {0} "
                "warning {1}, none of them a Coordination Review warning.".format(
                    _safe_int(evidence.get("failures_seen")),
                    _plural(evidence.get("failures_seen"), "message"),
                ),
            ],
            "",
            evidence,
        )

    # 6. Attached but Revit raised nothing at all.  Most often means the links
    #    were already loaded, so no load-time warning was raised for them.
    return _verdict(
        VERDICT_UNKNOWN,
        "No Coordination Review warning was raised for this model.",
        [
            "The listener was attached but Revit raised no warnings at all while "
            "this model opened, which usually means the links loaded without "
            "changes to review.",
        ],
        "Reload a link (Manage Links) to make Revit re-check it, or open "
        "Manage > Warnings.",
        evidence,
    )


def summary_text(verdict):
    """One block of text for the window's empty state."""
    verdict = dict(verdict or {})
    lines = [_safe_text(verdict.get("headline"))]
    details = [_safe_text(line) for line in _as_list(verdict.get("details")) if _safe_text(line)]
    if details:
        lines.append("")
        lines.extend(details)
    action = _safe_text(verdict.get("action"))
    if action:
        lines.append("")
        lines.append(action)
    return "\n".join(line for line in lines if line is not None).strip()
