# -*- coding: utf-8 -*-
"""Revit's "Family Already Exists" question, and the load options behind it.

Shared by every EasyBIM tool that calls ``Document.LoadFamily`` and wants the
user to decide what happens to a family that is already in the target.  It
lives here rather than in one command because Families Transfer and Family
Types need exactly the same widget and exactly the same answer-remembering,
and a second copy is the drift the Conventions section warns about (the same
reasoning that moved ``family_selection_*`` into ``lib`` for its second
consumer).

Two pieces:

``build_overwrite_prompt()``
    Our own version of Revit's dialog - command links plus a verification
    checkbox - or ``None`` when the host cannot show it.  It is ours rather
    than ``UIDocument.GetRevitUIFamilyLoadOptions()`` because Revit scopes its
    own "do this for all loading families" tick to a single ``LoadFamily``
    call, and these tools load one family per call, so the tick would never
    cover a batch.

``FamilyLoadOptions``
    The ``IFamilyLoadOptions`` implementation that asks it and remembers the
    answer across the batch.

Revit/.NET imports are guarded so the desktop test suite can import the module
off Revit; the prompt builder simply returns ``None`` there.
"""

# pylint: disable=import-error,invalid-name,broad-except
from __future__ import print_function

try:
    from Autodesk.Revit import DB
except Exception:  # off-Revit (unit tests)
    DB = None

try:
    from Autodesk.Revit import UI
    from Autodesk.Revit.UI import TaskDialog
except Exception:  # off-Revit (unit tests)
    UI = None
    TaskDialog = None


OVERWRITE = "overwrite"
OVERWRITE_WITH_VALUES = "overwrite_values"
DECLINE = "decline"

OVERWRITE_LINK = "Overwrite the existing version"
OVERWRITE_VALUES_LINK = "Overwrite the existing version and its parameter values"
APPLY_TO_ALL_TEXT = "Do this for all loading families"


def _safe_text(value):
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


_OptionsBase = DB.IFamilyLoadOptions \
    if DB is not None and hasattr(DB, "IFamilyLoadOptions") else object


class FamilyLoadOptions(_OptionsBase):
    """Answers Revit's "this family already exists" question.

    Revit raises this only when the family is both already in the target and
    actually *different*, so a byte-identical family loads with no prompt at
    all - the same as loading it by hand.

    ``ask`` is a callable taking the family name and returning
    ``(answer, apply_to_all)``. Left as ``None`` the answer is a silent
    overwrite including parameter values: that is what a UI-less session gets,
    and what a Revit too old to show the prompt falls back to.
    """

    def __init__(self, ask=None):
        self._ask = ask
        #: Set once the user ticks "do this for all"; suppresses later prompts.
        self._remembered = None
        self.family_name = ""
        self.declined = False
        #: How many times the user was actually asked - the apply-to-all is
        #: only meaningful if this stays at 1 for a batch.
        self.prompts = 0

    def begin(self, family_name):
        """Name the family about to load, and clear the last decline.

        ``OnFamilyFound`` is handed only ``familyInUse`` and an out-parameter;
        there is no family name anywhere in the callback, so the caller has to
        leave one here for the prompt to use.
        """
        self.family_name = _safe_text(family_name)
        self.declined = False

    def _decide(self, family_name):
        if self._remembered is not None:
            return self._remembered
        if self._ask is None:
            return OVERWRITE_WITH_VALUES

        self.prompts += 1
        try:
            answer, apply_to_all = self._ask(family_name)
        except Exception:
            # A prompt that cannot be shown must not abandon the family.
            return OVERWRITE_WITH_VALUES

        if answer not in (OVERWRITE, OVERWRITE_WITH_VALUES, DECLINE):
            answer = OVERWRITE_WITH_VALUES
        # A decline is never remembered: Cancel means "not this one", so the
        # next family that clashes asks again.
        if apply_to_all and answer != DECLINE:
            self._remembered = answer
        return answer

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        del familyInUse
        answer = self._decide(self.family_name)
        if answer == DECLINE:
            self.declined = True
            return False
        try:
            overwriteParameterValues.Value = (answer == OVERWRITE_WITH_VALUES)
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        del familyInUse
        # Nested and shared families can name themselves, and they run through
        # the same remembered answer so one tick covers them too.
        name = _safe_text(getattr(sharedFamily, "Name", "")) or self.family_name
        answer = self._decide(name)
        if answer == DECLINE:
            self.declined = True
            return False
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = (answer == OVERWRITE_WITH_VALUES)
        except Exception:
            pass
        return True


def build_overwrite_prompt():
    """Our own "Family Already Exists" question, or None if we cannot ask it.

    Built from the same widget Revit uses - command links plus a verification
    checkbox - so it looks like the dialog an interactive load shows. The
    difference that matters is that the checkbox is ours: Revit's own version
    of it does not survive a load driven one family at a time, and honouring
    it across the batch is the whole point.

    Returning None puts the caller back on its silent behaviour: overwrite and
    report, without asking. That is what a Revit with no verification checkbox
    gets, rather than a prompt whose tick would do nothing.
    """
    if UI is None or TaskDialog is None:
        return None

    command_ids = getattr(UI, "TaskDialogCommandLinkId", None)
    link_one = getattr(command_ids, "CommandLink1", None)
    link_two = getattr(command_ids, "CommandLink2", None)
    if link_one is None or link_two is None:
        return None

    # VerificationText and WasVerificationChecked have both been in the API
    # since 2011, so this guard should never fire; it is here so an exotic
    # host degrades to the original behaviour instead of throwing.
    try:
        probe = TaskDialog("EasyBIM")
        probe.VerificationText = APPLY_TO_ALL_TEXT
    except Exception:
        return None

    def _ask(family_name):
        dialog = TaskDialog("Family Already Exists")
        # Without this Revit prepends "External Command Name - " to the title.
        dialog.TitleAutoPrefix = False
        dialog.MainInstruction = (
            "You are trying to load the family {}, which already exists in "
            "this project.  What do you want to do?".format(family_name)
        )
        # Cancel is a common button; its result is TaskDialogResult.Cancel,
        # which is also what ESC, Alt+F4 and the X return - one branch covers
        # all four. Note the two enums disagree on values for the same names,
        # so they must never be cast to each other.
        dialog.CommonButtons = UI.TaskDialogCommonButtons.Cancel
        dialog.DefaultButton = UI.TaskDialogResult.Cancel
        # VerificationText, never ExtraCheckBoxText: setting both throws.
        dialog.VerificationText = APPLY_TO_ALL_TEXT
        dialog.AddCommandLink(link_one, OVERWRITE_LINK)
        dialog.AddCommandLink(link_two, OVERWRITE_VALUES_LINK)

        result = dialog.Show()

        # A method, not a property, and it throws unless the dialog has a
        # verification checkbox and has already been shown - both true here.
        try:
            apply_to_all = bool(dialog.WasVerificationChecked())
        except Exception:
            apply_to_all = False

        if result == UI.TaskDialogResult.CommandLink1:
            return OVERWRITE, apply_to_all
        if result == UI.TaskDialogResult.CommandLink2:
            return OVERWRITE_WITH_VALUES, apply_to_all
        # Cancel means "not this one". The tick is deliberately ignored here,
        # so the next family that clashes asks again.
        return DECLINE, False

    return _ask
