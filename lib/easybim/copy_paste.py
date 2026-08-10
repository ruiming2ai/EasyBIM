# -*- coding: utf-8 -*-
"""The one place a cross-document ``CopyPasteOptions`` is built.

Revit asks what to do when a copied type's name already exists in the
destination.  Left unanswered it either raises or puts a modal dialog in the
middle of a batch, so every copy needs a handler - and the handler has to be
the same one everywhere, because the two answers mean opposite things:

``UseDestinationTypes``
    A type in this model that happens to share a name with one in the source
    is *this* model's type.  A copy must never redefine it.

``UseOtherTypes``
    Redefines the destination's type from the source, regenerating every
    instance that uses it.  There is no case in this repo where that is what
    the user asked for.

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
