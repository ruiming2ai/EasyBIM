# -*- coding: utf-8 -*-
"""Moving the things a sheet owns, as one block, in paper space.

Two tools need this.  `View Align` shifts a sheet's frame and content so the
frame matches a reference sheet's; `Sheet Align` shifts a whole sheet's
contents onto the page zero or onto a reference frame.  The awkward parts are
identical in both - which elements the sheet actually owns, and putting pins
back - and this session found two real bugs in the first copy of them, so a
second copy would mean finding each bug twice.

Nothing here imports Revit.  ``DB`` and ``eid_to_int`` arrive as arguments, the
same boundary rule as ``easybim.sheet_revisions``, so the desktop suite drives
the whole module with fakes.
"""

from __future__ import print_function

from easybim import sheet_titleblocks


#: Shift so the frame lands on the sheet's own (0, 0).
MODE_SHEET_ORIGIN = "sheet_origin"
#: Shift so the frame lands where the reference sheet's frame sits.
MODE_TITLE_BLOCK_ORIGIN = "title_block_origin"


def builtin_category_int(builtin_category):
    try:
        return int(builtin_category)
    except Exception:
        return None


def element_category(element, eid_to_int):
    """``(category_id_int, category_name)``; either half can be None/blank."""
    category = getattr(element, "Category", None)
    if category is None:
        return None, ""
    try:
        return eid_to_int(category.Id), category.Name
    except Exception:
        return None, ""


def owned_elements(DB, doc, sheet, eid_to_int, title_block_category_int,
                   keep_title_block_id=None):
    """``{int id: element}`` for the things the sheet itself owns.

    ``FilteredElementCollector(doc, sheet.Id)`` is scoped by *visibility*, not
    by ownership: it also returns the model elements seen through the placed
    viewports.  This repo names that same unfiltered call
    ``_visible_element_ids`` in Families Downgrade, and Grid Offset depends on
    the behaviour to reach project-wide ``DB.Grid`` datums through a view.

    Moving one of those would translate real model geometry by a paper-space
    vector, so the ownership test is not optional.  Sheet Manager's
    ``copy_sheet_detailing`` and Linked Sheets Transfer's
    ``sheet_detailing_ids`` guard the same collector the same way.
    """
    found = {}
    try:
        elements = (
            DB.FilteredElementCollector(doc, sheet.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return found

    sheet_id_int = eid_to_int(sheet.Id)
    by_id = {}
    candidates = []

    for element in elements:
        element_id = eid_to_int(getattr(element, "Id", None))
        if element_id in (None, -1):
            continue

        is_viewport = isinstance(element, DB.Viewport)

        owner_id = None
        if not is_viewport:
            try:
                owner_id = eid_to_int(element.OwnerViewId)
            except Exception:
                owner_id = None

        try:
            is_revision_schedule = bool(
                getattr(element, "IsTitleblockRevisionSchedule", False))
        except Exception:
            is_revision_schedule = False

        category_id, _ = element_category(element, eid_to_int)
        is_title_block = (title_block_category_int is not None
                          and category_id == title_block_category_int)

        by_id[element_id] = element
        candidates.append((element_id, owner_id, is_viewport,
                           is_revision_schedule, is_title_block))

    for element_id in sheet_titleblocks.sheet_owned_ids(
            candidates, sheet_id_int,
            keep_title_block_id=keep_title_block_id):
        found[element_id] = by_id[element_id]
    return found


def unpin_if_pinned(element):
    """Unpin so the element can move. True when it was pinned."""
    try:
        if bool(getattr(element, "Pinned", False)):
            element.Pinned = False
            return True
    except Exception:
        pass
    return False


def move_element(DB, element, shift):
    """Move one sheet-owned element by a ``(dx, dy, dz)`` sheet-space shift.

    Issued one element at a time on purpose.  The Revit API has no
    ``CanMoveElement`` to pre-test with - the ``Can*`` pair is
    ``CanMirrorElement``/``CanMirrorElements`` - so a grouped, workset-locked
    or otherwise immovable element can only be discovered by trying.  One at a
    time makes that cost a single reported note instead of the whole run.

    A viewport is repositioned through ``SetBoxCenter``, the idiom the tools
    already use for moving one, rather than through ``ElementTransformUtils``.
    """
    if not element or not getattr(element, "IsValidObject", True):
        return False, "Element is no longer valid."

    try:
        if isinstance(element, DB.Viewport):
            center = element.GetBoxCenter()
            element.SetBoxCenter(DB.XYZ(center.X + shift[0],
                                        center.Y + shift[1],
                                        center.Z + shift[2]))
        else:
            DB.ElementTransformUtils.MoveElement(
                element.Document, element.Id,
                DB.XYZ(shift[0], shift[1], shift[2]))
        return True, ""
    except Exception as ex:
        return False, "Failed moving element: {}".format(ex)


def restore_pins(pinned):
    """Re-pin what was unpinned in order to move it.

    ``pinned`` is ``[(element, label)]``.  Returns
    ``(restored_count, [(label, reason)])`` - the caller records the failures
    its own way.

    This is housekeeping after the real work, so a pin that will not go back is
    reported and the run continues: staying silent would leave an element
    unlocked with nobody told, and raising would discard work that has already
    succeeded.
    """
    restored = 0
    failures = []
    for element, label in pinned:
        try:
            element.Pinned = True
            restored += 1
        except Exception as ex:
            failures.append((label, "pin could not be restored: {}".format(ex)))
    return restored, failures


def target_point_for(mode, title_block_point, reference_point=None):
    """Where this sheet's title block should end up, as ``(x, y, z)``.

    ``MODE_SHEET_ORIGIN`` normalises: the frame goes to the sheet's own (0, 0),
    and no reference sheet is involved.  Comparing a reference's sheet origin
    to a target's could never produce a shift, because the origin is the same
    point on every sheet - only a landmark that differs per sheet, the frame,
    can.

    ``MODE_TITLE_BLOCK_ORIGIN`` matches: the frame goes where the reference
    sheet's frame sits.  Z is carried through from the sheet's own frame; a
    sheet is flat and Revit keeps its own value there.
    """
    if title_block_point is None:
        return None

    try:
        z = float(title_block_point.Z)
    except Exception:
        try:
            z = float(title_block_point[2])
        except Exception:
            return None

    if mode == MODE_SHEET_ORIGIN:
        return (0.0, 0.0, z)

    if mode != MODE_TITLE_BLOCK_ORIGIN:
        return None

    if reference_point is None:
        return None
    try:
        return (float(reference_point.X), float(reference_point.Y), z)
    except Exception:
        pass
    try:
        return (float(reference_point[0]), float(reference_point[1]), z)
    except Exception:
        return None
