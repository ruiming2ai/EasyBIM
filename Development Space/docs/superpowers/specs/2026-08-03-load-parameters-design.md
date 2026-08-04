# Load Parameters - design

## Problem

EasyBIM's `Parameters` panel could copy and combine parameter *values*, but it
could not create a parameter anywhere. Putting one shared parameter into a
hundred families meant Family Types -> Add, once per family, choosing the group
and Type/Instance by hand every time. Binding it to the project meant Manage ->
Project Parameters, again by hand.

`Load Parameters` does the whole matrix in one pass, and does both targets from
one selection.

## Rules the tool enforces

- **Shared parameters only.** Only a shared parameter carries a GUID, and the
  GUID is what makes the parameter in the family *the same parameter* the
  project knows. A non-shared parameter would have to be recreated by name in
  each family and would silently fail to line up.
- **Families in the project are edited and reloaded.** There is no other way to
  persist a change to a project family.
- **Families from a folder are written back to their `.rfa` and are never
  loaded into the project.** They are a library, not part of this model.
- **Nothing is written before a dry run is shown.** `build_plan` produces the
  complete target x parameter matrix with a status per cell, and the
  confirmation dialog and the executor read the same object - so preview and
  write cannot drift.
- **The window does not close on Load.** Every other multi-step tool here
  closes and reopens because it needs Revit's own pick UI, which a modal dialog
  blocks. Nothing in this tool does, so the selection survives and the second
  load is one click.

## Two ways in, one settings table

The left half is a `DataGrid`, not a list: the checkbox list and the settings
table are the same rows, so they are one control. Its two editable columns -
`Group` and `Type / Instance` - feed **both** load actions, because a family
parameter's group and a project binding's group are the same `GroupTypeId`, and
Type/Instance means the same thing on both sides.

Group choices come from `ParameterUtils.GetAllBuiltInGroups()` with
`LabelUtils.GetLabelForGroup`, enumerated at runtime. A hardcoded list is wrong
in a non-English Revit and drifts every release.

Multi-row editing works two ways, and both follow Sheet Manager's rule that a
spread only happens when the edited row is *part of* the selection
(`len(selected) > 1 and row in selected`), so touching an unselected row stays a
single-row action:

- the bulk bar under the grid (`Set for the N selected rows`), and
- changing a combo inside a selected row, which propagates to the rest.

The target lists are the `clash_detection_setup` pattern verbatim:
`SelectionMode="Extended"` for free shift/ctrl/drag ranges, one checkbox click
spreading across the range, spacebar as the keyboard equivalent, and the label
in its own `TextBlock` - a `CheckBox` carrying the label swallows the row click
and breaks range selection.

Defaults are read from the model so the common case needs no editing at all:
`Group` from `InternalDefinition.GetGroupTypeId()`, `Type / Instance` from
whether the project holds an `InstanceBinding` or a `TypeBinding`. Parameters
added from a `.txt` have neither - a shared parameter file records no group and
no binding - so they default to *Data* / *Type*, Revit's own defaults.

## The temporary shared parameter file

This is the part worth understanding before changing anything.

`FamilyManager.AddParameter` and `BindingMap.Insert` both need an
`ExternalDefinition`, and an `ExternalDefinition` only exists inside a
`DefinitionFile`. A shared parameter read out of a project has none. So a run
builds **one** temporary shared parameter file holding every selected
parameter, whatever its source, and holds it current for the *whole* run - not
just the `AddParameter` calls, because an `ExternalDefinition` is a live handle
onto an open `DefinitionFile` and both `LoadFamily` and the binding insert may
re-read it.

Only the header is written by hand:

```
# This is a Revit shared parameter file.
# Do not edit manually.
*META	VERSION	MINVERSION
META	2	1
```

Every `*GROUP` and `*PARAM` line is written by Revit itself through
`Definitions.Create`. That decision buys three things at once:

1. No hand-mapping of a `ForgeTypeId` onto the legacy `DATATYPE` tokens
   (`LENGTH`, `HVAC_PRESSURE`, `#OTHER#`), a version-sensitive mapping with no
   public helper.
2. Our own bytes stay pure ASCII, so text encoding stays Revit's problem.
3. One code path for project-sourced and file-sourced parameters.

`options.GUID` is set from the source, which is what keeps identity intact.

Rules the implementation obeys, each of which is a real failure otherwise:

- `OpenSharedParameterFile()` returns **`None`**, not an exception, for a
  missing, empty or unparseable file. A `None` aborts before a family is
  touched.
- `SharedParametersFilename` is `""` when the user has never set one; assigning
  `None` from IronPython throws, so `"" ` is the restore default.
- The restore happens in a `finally`, **before** the delete, with the delete
  itself guarded. The setting persists into Revit's `.ini` across sessions, so
  leaving it pointed at a deleted file would follow the user around forever.
  The file lives in pyRevit's user data folder under a recognisable name rather
  than the temp folder, so a leak is soft and diagnosable.
- `Definitions.Create` throws on a duplicate *name* whatever the GUID, and a
  project that has consumed more than one shared parameter file routinely holds
  two. `name_collisions` catches it at plan time, so it is a dialog rather than
  a mid-run crash.
- A `Family Type` spec parameter carries a `DATACATEGORY` that
  `ExternalDefinitionCreationOptions` cannot express. Creating it without the
  category would produce a differently-shaped parameter under the same GUID, so
  those rows are detected at pick time, greyed, and reported.

## Writing into families

Project families run first, inside one assimilated `TransactionGroup`:

```
tg.Start()
  family_doc = doc.EditFamily(family)   # ids, re-resolved every pass
  t = Transaction(family_doc); AddParameter; t.Commit()
  family_doc.LoadFamily(doc, load_options)
  family_doc.Close(False)               # in a finally
tg.Assimilate()
```

`EditFamily` and `LoadFamily` both need the project non-modifiable, i.e. no open
`Transaction`. An open `TransactionGroup` does not make a document modifiable,
so the nesting is legal, and `Assimilate` collapses the batch into one undo
item. `HasStarted()` guards both the rollback and the assimilate.

Families are re-resolved from `ElementId` inside the loop rather than held as
`DB.Family` references, because `LoadFamily` mutates the project between
iterations and a cached reference goes stale.

`IFamilyLoadOptions` is written fresh rather than shared with Families
Transfer, because the two tools want **opposite** answers: Families Transfer
sets `overwriteParameterValues = True`, which is right for a transfer and wrong
here - adding a parameter must leave existing type values alone.

Folder families run **last**, outside any transaction group, because they have
nothing to do with the project document and enclosing them would imply an undo
that does not exist.

### The `.rfa` upgrade hazard

`OpenDocumentFile` silently upgrades an older family in memory and `Save()`
writes it back in the current Revit version. Scanning a 2019 library and
pressing Load would convert the whole library, unopenable on 2019, with no undo.
`BasicFileInfo.Extract` reads the authoring version from the file header
without opening the document, so the confirmation dialog can name how many
files will be upgraded - and that acknowledgement must be ticked before the run
proceeds. An optional output folder mirrors the source tree instead of
overwriting.

## Writing into the project

One ordinary transaction, fully undoable. Two behaviours would be silent data
loss if got wrong, so both are handled explicitly:

- **`ReInsert` replaces a binding wholesale.** Existing categories are read and
  **unioned** with the newly checked ones first. Rebinding without the union
  would quietly unbind every category the user did not happen to tick.
- **Instance and Type are mutually exclusive.** Flipping a bound parameter
  between them discards its values, so the plan reports it and it needs its own
  acknowledgement.

## Cancel means two different things

Project families roll back to nothing; an `.rfa` already saved stays saved,
because a file write has no transaction. The summary therefore keeps two
buckets and the report says which is which, and `clear_applied()` zeroes the
counters on a rollback so the report can never claim work that is gone. Running
the project pass first means a cancel costs nothing in the common case.

## Deliberate exclusions

- **Nested families are not updated.** Adding a parameter to family A does
  nothing to the copy of A nested inside family B. Stated in the confirmation.
- **A folder `.rfa` and a project family can be the same family**, updated by
  two independent paths and left out of step. Detected by name and warned.
- **Non-shared parameters.** See the first rule.
- **Revit's own warning dialogs during reload.** `LoadFamily` runs its own
  internal transaction, so no `IFailuresPreprocessor` can be attached to it;
  the family-document transaction swallows its warnings, and the confirmation
  says project-side dialogs may still appear.

## Files

```
EasyBIM.tab/Parameters.panel/Load Parameters.pushbutton/
    script.py                      launcher, entry guards, Context (Revit callables)
    load_parameters_state.py       pure: rows, selection, plan, report text
    load_parameters_revit.py       every Revit API call
    load_parameters_ui.py          the four WPFWindow classes
    LoadParametersWindow.xaml      parameter grid + Families/Categories tabs
    SharedParameterFileWindow.xaml pick definitions out of a .txt, by group
    ConfirmWindow.xaml             the dry run, with the acknowledgements
    ReportWindow.xaml              what actually happened
    bundle.yaml, icon.png, icon.dark.png
Development Space/tests/test_load_parameters_state.py
Development Space/tests/test_load_parameters_command_names.py
```

`min_revit_version: 2023`, so `GroupTypeId`, `SpecTypeId` and the `ForgeTypeId`
overloads of `AddParameter` and `ParameterBindings.Insert` are all available
unguarded. No `BuiltInParameterGroup` fallback is written - it was removed in
Revit 2024, so it would be a liability rather than insurance.
