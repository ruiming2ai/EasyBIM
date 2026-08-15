# -*- coding: utf-8 -*-
"""The one place a cross-document ``CopyPasteOptions`` is built.

Revit asks what to do when a copied type's name already exists in the
destination.  Unanswered, ``SetDuplicateTypeNamesHandler`` documents the
default as "a modal dialog with options to either copy new types only, or
cancel the operation" - which would stop a batch dead - so every copy needs a
handler.

``DuplicateTypeAction`` has exactly two members, verified against the assembly
metadata for 2021 through 2026:

``UseDestinationTypes``
    Proceed, and use the same-named types already in the destination.  A type
    here that shares a name with one in the source is *this* model's type, and
    the copy leaves it alone.

``Abort``
    Cancel the whole paste.

**There is no overwrite.**  Nothing in the copy/paste API replaces a
destination type or family - the only documented route to that is
``LoadFamily`` with an ``IFamilyLoadOptions``.  (Autodesk did ship
``GroupLoadOptions.ReplaceDuplicatedGroups`` for groups in 2024; there is
deliberately no analogue here.)  So ``UseDestinationTypes`` is answered not
because it beats some redefine option, but because the only alternative is
cancelling.

Keeping the factory here rather than in a command module is what stops a new
call site from quietly forgetting the handler.
"""

# pylint: disable=import-error,invalid-name,broad-except

from pyrevit import DB


class UseDestinationTypes(DB.IDuplicateTypeNamesHandler):
    """Answer Revit's duplicate-type prompt without showing it."""

    def OnDuplicateTypeNamesFound(self, args):
        del args
        return DB.DuplicateTypeAction.UseDestinationTypes


def copy_paste_options():
    options = DB.CopyPasteOptions()
    try:
        options.SetDuplicateTypeNamesHandler(UseDestinationTypes())
    except Exception:
        pass
    return options
