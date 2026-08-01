# -*- coding: utf-8 -*-
"""Shared Revit-version compatibility helpers for EasyBIM.

These helpers exist because the Revit API changed shape over the supported
range (2015-2026): ``ElementId.IntegerValue`` and the ``ElementId(Int32)``
constructor were removed in Revit 2026 in favor of ``Value``/``Int64``.
Keeping the fallbacks in one place stops the copies in individual tools from
drifting apart - version-compat code is exactly where a divergent copy
becomes a Revit-release bug.

Import from pushbutton scripts as ``from easybim.compat import ...``; the
extension ``lib`` folder is always on pyRevit's ``sys.path``.

Deliberate exceptions that keep local copies instead of importing this
module: library modules that the test suite loads standalone without the
``easybim`` package (for example ``coordination_review_passive`` and the
``*_state`` companions), and the temp-phase trio, whose helpers are patched
directly by their runtime tests.
"""

from __future__ import print_function


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


def eid_to_int(element_id):
    """Read an ElementId's numeric value across Revit versions.

    ``IntegerValue`` binds on Revit 2015-2025 (removed in 2026);
    ``Value`` binds on Revit 2024+.
    """
    if element_id is None:
        return None
    for attr_name in ("IntegerValue", "Value"):
        try:
            return int(getattr(element_id, attr_name))
        except Exception:
            pass
    try:
        return int(element_id)
    except Exception:
        return None


def int_to_eid(value, element_id_type):
    """Construct an ElementId from a Python int across Revit versions.

    The plain-int constructor is tried first - it is the exact call older
    code always made and binds ``ElementId(Int32)`` on Revit 2015-2025
    unchanged.  Revit 2026 removed that overload, so the plain call raises
    and the explicit ``Int64`` construction (available since Revit 2024)
    takes over.  Returns ``None`` when the value or host cannot produce an
    id.  For hot loops, use :func:`element_id_factory` instead.
    """
    if element_id_type is None or value is None:
        return None
    try:
        value = int(value)
    except Exception:
        return None
    try:
        return element_id_type(value)
    except Exception:
        pass
    try:
        import System

        return element_id_type(System.Int64(value))
    except Exception:
        return None


def element_id_factory(element_id_type):
    """Resolve the working ElementId constructor once; return a callable.

    Per-construction try/except is fine for occasional calls, but inside a
    hot loop on Revit 2026 the plain-int attempt would throw and be caught
    on every iteration.  This probes a single construction and returns a
    callable bound to whichever overload the host supports.
    """
    try:
        element_id_type(1)

        def _construct(value):
            return element_id_type(int(value))

        return _construct
    except Exception:
        pass

    import System

    def _construct_int64(value):
        return element_id_type(System.Int64(int(value)))

    return _construct_int64


def exception_text(exception):
    """Format an exception with its CLR InnerException chain."""
    if exception is None:
        return ""
    text = "{0}: {1}".format(exception.__class__.__name__, safe_text(exception))
    inner = getattr(exception, "InnerException", None)
    if inner is not None:
        text += " | Inner: {0}".format(exception_text(inner))
    return text
