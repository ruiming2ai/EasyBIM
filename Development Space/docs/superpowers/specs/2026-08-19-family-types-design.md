# Family Types — design

*2026-08-19*

## The problem

A Revit type catalogue is a `.txt` file: types on rows, parameters in columns,
and nothing that makes it readable or safely editable. Revit's own Family Types
dialog shows one type at a time, so comparing two types means clicking between
them. The ask: put the whole table on screen, editable, with Excel in and out.

## Shape

One button, `Misc Tools.panel/Family Types.pushbutton`, modal, `min_revit_version: 2021`.

```
script.py            launcher: resolve the family document, run the window, apply
family_types_state.py   pure model - columns, rows, staging, import planning
family_types_revit.py   FamilyManager read/write, the transaction, the reload
family_types_ui.py      the window and the Specify Types dialog
family_types_xlsx.py    the export, and the header/metadata format
FamilyTypesWindow.xaml  ImportTypesDialog.xaml
```

`_state` and `_xlsx` import nothing from pyrevit or .NET, so the desktop suite
drives them directly. Pinned by `test_family_types_command_names`.

## Decisions

### Types are rows, parameters are columns

The type-catalogue shape, and the shape the Excel export mirrors cell for cell.
Revit 2022+ stacks its own dialog the other way, but a transposed export would
stop being a catalogue, and the export is half the point.

### Instance parameters are columns, marked "(default)"

A `FamilyType` holds a value for every family parameter, instance ones
included; that per-type value is the default a new instance is placed with and
it can differ per type. `families_downgrade_export.read_types` already relies on
this — it reads `manager.GetParameters()` (all of them) and calls
`family_type.AsDouble(parameter)` per type. Filtering them out would lose real,
per-type data. The header suffix is what Revit's own dialog uses.

### Values are display strings, not raw internals

`AsValueString` for doubles, so the grid reads the way the same value reads
everywhere else in Revit, and `SetValueString` on the way back so it parses in
the same units. `Set` for integers, strings and element ids.

### Materials are editable; other element references are not

Materials are unique by name inside a family document, so a name in a text cell
resolves to exactly one. A family type or image reference has no such lookup, so
it is shown and locked with the reason in the tooltip. Formula-driven, reporting,
read-only and non-user-modifiable parameters lock the same way.

### A new type is seeded from an existing one

`FamilyManager.NewType` copies the **current type's** values into the type it
creates. A row shown blank in the grid would therefore come out of Revit
carrying whatever the current type held — the grid and the family would disagree
about a type the user just made. So the window seeds a new row from a real type
(which is also what Revit's own New does) and Apply writes every value back. A
parameter left blank keeps what it inherited; there is no way to unset a family
parameter, which is also why a cleared cell is reported as skipped rather than
written as a zero.

### Deletes and renames run before creates

Both free a name. Creating first would collide on a swap — "A" renamed to "B"
while a new "A" appears. `compute_staged_changes` returns the four buckets and
`apply_changes` walks them in that order.

### One transaction, partial failures reported

Everything staged goes into one `Transaction` in the family document. A refused
cell is collected and the rest still commits, the way Sheet Manager reports a
partial apply; only a failure to open the transaction rolls the lot back.

### The reload asks the user

Load Parameters fixes `overwriteParameterValues = False` (2026-08-03) because
its job — add a parameter and put the family back — must not touch the project's
type values. Here changing those values *is* the job, so the answer is not ours
to fix: the shared *Family Already Exists* prompt asks, and the user picks
overwrite or overwrite-with-values.

That prompt and its `IFamilyLoadOptions` moved from Families Transfer into
`lib/easybim/family_load_options.py` for this second consumer — the same move
the 2026-08-15 decision made for `family_selection_*`. Families Transfer keeps
`FamilyTransferLoadOptions` as an alias so its own call sites and tests read
unchanged; the four assertions that pinned the prompt to `script.py` now point
at the lib module.

### A family the user already had open is never closed

`EditFamily` on a family that is already being edited hands back the document
the user is working in, and `Close(False)` would end their session. The launcher
records whether the document was already open and only closes what it opened.

## Excel format

Sheet `FamilyTypes`:

- `A1` = `Family Type`, column A = type names.
- `B1…` = **`<Parameter Name>\n<ElementId>` in one cell**, `text_wrap` so both
  lines show. The id is the round-trip key: it survives a parameter rename in
  Revit, which the header text does not.
- Instance headers `#D9E1F2`, read-only headers `#FF3131` with their cells
  locked and `#D9D9D9`; sheet protected, first row and column frozen.

Hidden sheet `_metadata`: signature / family / document / timestamp / version,
then one row per column describing it (`METADATA_HEADERS`).

Import resolves each column by id, then by name, then by the metadata key by
position, and reports anything left over. Rows match by type name — the
catalogue contract — so an unknown name is a new type and a rename belongs in
the grid, where the two are distinguishable.

## Import is staged, never written

The Specify Types dialog echoes the question a type catalogue asks on load: every
type in the file with its status (New / Changed / Unchanged), ticked or not, plus
an off-by-default "delete types not in the file". What is ticked becomes red
cells in the grid. Apply remains the only thing that touches Revit —
`test_family_types_command_names` asserts the state module never mentions
`Transaction`.

## Tests

- `test_family_types_state.py` (47) — columns, locking, staging, name rules,
  delete rules, search, yes/no parsing, export matrix, import planning and apply.
- `test_family_types_xlsx.py` (15) — filenames, and a real workbook written and
  read back through `easybim.excel_workbook`: header cell shape, the metadata
  sheet, WYSIWYG export, and an edited round trip.
- `test_family_types_command_names.py` (21) — bundle layout, icon size, XAML ↔
  code wiring, and the decisions above.

## Not verified off Revit

This machine has no Revit assemblies, so `FamilyParameter.Id`,
`FamilyType.AsValueString`, `FamilyManager.SetValueString`, `RenameCurrentType`
and `DeleteCurrentType` are reached through guarded probes with documented
fallbacks rather than confirmed statically — the repo's standing approach for
family code spanning 2021-2026.
