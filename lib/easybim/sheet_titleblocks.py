# -*- coding: utf-8 -*-
"""Where a sheet's title block sits, and what moves when it is repositioned.

Two tools need this.  `Linked Sheets Transfer` puts the host title block where
the linked one sits, because sheet coordinates are absolute and its viewports
are aligned to the linked sheet's absolute coordinates - a title block at a
different spot puts every correctly-placed viewport in the wrong place on the
page.  `View Align` has exactly the same problem between two sheets of one
model, so it grew the same option; a second copy of this would let "align" and
"copy and align" drift apart, which is the drift `AGENTS.md` asks to avoid.

Nothing here imports Revit.  ``DB`` and the document arrive as arguments - the
same boundary rule as ``easybim.sheet_revisions`` - so the desktop test suite
drives the whole module with fakes.

Sheet points are *paper* space.  A reference sheet living in a linked or other
open document needs no coordinate conversion, and in particular must not be put
through a ``RevitLinkInstance`` transform: that maps model coordinates, and a
sheet is not part of the model's geometry.
"""

from __future__ import print_function


#: Below this length (feet) a shift is not worth a transaction.
DEFAULT_EPS = 1e-9


def sheet_title_blocks(DB, document, sheet):
    """Title block instances placed on one sheet, in element order."""
    try:
        return list(
            DB.FilteredElementCollector(document, sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements())
    except Exception:
        return []


def first_title_block(DB, document, sheet):
    """``(title_block, extra_count)`` for a sheet.

    ``(None, 0)`` when the sheet carries none.  Where a sheet carries several,
    the first in element order wins and ``extra_count`` says how many were
    ignored, so the caller can report the choice rather than make it silently.
    """
    blocks = sheet_title_blocks(DB, document, sheet)
    if not blocks:
        return None, 0
    return blocks[0], len(blocks) - 1


def location_point(element):
    """``element.Location.Point``, or ``None`` when it has no point."""
    try:
        return element.Location.Point
    except Exception:
        return None


def title_block_shift(source_point, target_point, eps=DEFAULT_EPS):
    """``(dx, dy, dz)`` moving *target* onto *source*.

    ``None`` when either point is missing or the two are already within *eps*
    of each other - there is nothing to write, and an identity move would still
    dirty the document.  Duck-typed on ``.X``/``.Y``/``.Z`` so a Revit ``XYZ``
    and a plain test double are equally acceptable.
    """
    if source_point is None or target_point is None:
        return None

    try:
        dx = float(source_point.X) - float(target_point.X)
        dy = float(source_point.Y) - float(target_point.Y)
        dz = float(source_point.Z) - float(target_point.Z)
    except Exception:
        return None

    eps = float(eps)
    if (dx * dx + dy * dy + dz * dz) < (eps * eps):
        return None
    return (dx, dy, dz)


def plan_sheet_move(sheet_element_ids, aligned_viewport_ids,
                    move_other_content, title_block_id=None):
    """The element ids to shift for one sheet, in a stable order.

    ``sheet_element_ids`` is every sheet-owned element id, viewports included.
    The viewports *this run is aligning* are dropped: they take their position
    from the alignment maths, and their view titles ride along on
    ``Viewport.LabelOffset``.  Everything else keeps its place inside the
    frame, which is the whole point of the sub-option - an unselected viewport
    included.

    With ``move_other_content`` false only the title block moves.
    """
    moving = []
    seen = set()

    if title_block_id is not None:
        moving.append(title_block_id)
        seen.add(title_block_id)

    if not move_other_content:
        return moving

    aligned = set(aligned_viewport_ids or [])
    for element_id in sheet_element_ids or []:
        if element_id in aligned or element_id in seen:
            continue
        seen.add(element_id)
        moving.append(element_id)

    return moving
