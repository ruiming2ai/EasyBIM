# 02 — Parameter Check

The zero-amp report generalised: which elements are missing their data, for any parameter, any category, every milestone.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 02 of 45 | Parameters | no | M | 9/10 | 8/10 |

## Main purpose

Every deliverable milestone ends with the same question: which elements are
missing their data? Today that means building a throwaway schedule per
category, eyeballing the blanks, deleting the schedule, and doing it all again
next milestone. The zero-amp report in Update Circuit Rating answers this
perfectly — for one parameter, on circuits. Nothing answers it for twelve
parameters across six categories, which is what a real data-drop checklist
looks like.

Parameter Check makes the checklist a saved artifact. A *check set* is a list
of rules — category + parameter (by name) + requirement, where a requirement
is *non-empty*, *non-zero*, or *one-of* an allowed-value list typed in or
pasted from Excel. Check sets save as named JSON presets carrying only
category and parameter names, portable to any project per the house rule that
an ElementId means nothing in the next document. Run it and the result is the
zero-amp pattern at full width: a read-only failure table — category, family,
type, id, parameter, what it holds, what the rule wanted — always read from
the live model at report time, so re-running after fixes shows what is left to
do, and an empty report says so.

The tool writes nothing, ever. Fixing belongs to the Excel import and to
Parameter Copy, and the report says which to reach for. That restraint is also
the differentiation: native Revit needs one schedule per category and cannot
express "one of these values"; Autodesk's free Model Checker lives outside the
model, wants XML authoring, and cannot zoom to a failing element from a result
row; the Excel tool moves values and Parameter Copy writes them, but neither
judges them. Rank 02 because the check-fix-Refresh loop touches every
discipline on every project, and because the machinery — presets, collectors,
the report window — is all ground the repository has walked before.

## Basic implementation ideas

- **Bundle & module layout** — `EasyBIM.tab/Parameters.panel/Parameter Check.pushbutton/`
  with `script.py` (`__persistentengine__ = True`; the window is modeless so
  the fix-and-Refresh loop works), `bundle.yaml` (two-line title
  "Parameter\nCheck", narrative tooltip, `author: Ruiming Liu`),
  `ParameterCheckWindow.xaml`, `PresetNameWindow.xaml`,
  `parameter_check_state.py` (rules, blank semantics, preset serialisation —
  zero Revit imports), `parameter_check_revit.py` (collectors into dicts),
  `parameter_check_ui.py`, `parameter_check_xlsx.py` (allowed-values paste-in
  and failure export). Presets follow `tag_align_presets.py` exactly: local
  file via `script.get_universal_data_file`, optional shared JSON file for the
  office — copy the pattern now, hoist a generic preset store to
  `lib/easybim/` only when this second consumer proves the shapes match.
  Reuse `excel_workbook.read_workbook_sheets` for pasted allowed-value lists
  and `ExternalEventBridge` for Run/Refresh/Show.
- **Revit API route** — one `FilteredElementCollector` per category:
  `OfCategory(bic).WhereElementIsNotElementType()` for instance rules, a
  separate `WhereElementIsElementType()` pass where a rule says Type.
  Categories are offered by name from the model's category list and resolved
  back through a name→BuiltInCategory map built at scan time — never stored as
  ids. Scope narrows the collector: whole model, active view
  (`FilteredElementCollector(doc, view.Id)`), or current selection
  (`uidoc.Selection.GetElementIds()`). Parameter lookup is by definition name
  with the instance-wins/type-fills-a-gap semantics Update Circuit Rating
  established: read the instance parameter; if absent, read it off the type.
  Blank tests are storage-type aware, decided in the revit layer where
  `StorageType` is visible and shipped across as a plain verdict string:
  `HasValue` false, empty/whitespace `AsString` for strings, `AsDouble() == 0`
  for numerics, `AsInteger() == 0` for integers (with yes/no parameters
  exempted from *non-zero* — an unchecked checkbox is a value), invalid
  ElementId for element references. One-of comparisons run against
  `AsValueString()` display text, stated in the UI ("compared as displayed"),
  so what the user typed matches what the user sees. No writes, no
  Transaction — pinned by the command-names test.
- **The scan/report cycle** — read-only: scan → judge → report.
  `parameter_check_revit.scan(doc, scope, rules)` makes one bounded pass per
  distinct category in the check set and returns element dicts (id, category,
  family, type, level, and per-rule raw readings — ints and unicode only).
  `parameter_check_state.evaluate` turns that into per-rule results: failures,
  passes counted, and two named non-failure verdicts — *not applicable* (the
  project has no parameter of that name on that category) and *ambiguous
  name* (two project parameters share the display name on that category; the
  tool refuses to pick one, and the finding text names Parameter Audit as the
  cure). The report is re-read live on every Refresh; an empty run reports
  "Nothing failing. 1,240 elements pass all 12 rules." rather than a blank
  pane.
- **Edge cases & honest limits** — per-category scan cap (50,000 elements)
  and per-rule display cap (2,000 rows) with the truncation stated in the
  expander header — "showing 2,000 of 3,412; export carries all of them."
  Elements with no type (in-place oddities) fill the type column with "—",
  never crash the fill-a-gap read. A rule on a category with zero elements in
  scope reports "0 elements in scope", distinct from passing. The tool does
  not validate formulas, units, or cross-parameter logic ("if X then Y") —
  requirement stays the three verbs, and the tooltip says a rules language
  beyond that is out of scope. It never resolves duplicate parameter names by
  GUID behind the user's back: ambiguity is a finding, not a coin flip.
- **Risks** — the three blanks (a 0 length, an empty string, an unset
  ElementId) are three different tests; getting one wrong silently inverts a
  rule, so the storage-type matrix is the most heavily pinned state logic.
  Big models: the per-category caps and the one-pass-per-category rule keep
  the scan bounded, but a check set with ten categories is ten collector
  passes — the status line must tick per category so a long scan reads as
  progress, not a hang. Name-based lookup inherits the duplicate-name mess by
  design; surfacing it as a finding is the honest way out, and the preset
  format must survive a parameter name that no longer exists (that is what
  *not applicable* is for). Display-text one-of comparison means a unit
  reformat can break an allowed list — the "compared as displayed" label and
  the raw value column in the failure row keep that debuggable.
- **Tests** — `test_parameter_check_state.py` pins the blank-verdict matrix
  per storage type, one-of matching (case, whitespace, the yes/no exemption),
  preset round-trip carrying names only, and cap/truncation arithmetic.
  `test_parameter_check_command_names.py` pins bundle metadata, both XAMLs'
  handler wiring, icon sizes, the IronPython AST scan, and the no-Transaction
  pin. `test_parameter_check_revit.py` drives the adapter over fakes for both
  API generations: instance-wins/type-fills, duplicate definition names,
  missing categories, selection scope. `test_parameter_check_xlsx.py` pins
  the allowed-values paste-in parse and the failure-export rows.

## UI description

**Main window** — resizable modeless window, root `Grid Margin="14"`, header
"Parameter Check" over a DimGray subtitle naming the document and the loaded
preset ("Preset: Issue for Construction"). Body is two cards side by side.
Left, the **Check set card**: a rules grid — Category ComboBox (names from
the model), Parameter ComboBox (names present on that category, filled at
open), Requirement ComboBox (non-empty / non-zero / one of…), and an
allowed-values cell that opens into a small text area with a "Paste from
Excel…" button — plus Add Rule / Remove, and **Save Preset** / **Load
Preset** buttons in the card header. Right, the **Scope card**: three radio
choices — Whole model / Active view / Current selection — with the selection
count shown and the radio greyed (never hidden) when nothing is selected,
tooltip carrying the reason. Footer: status left; **Run** (`IsDefault`),
**Export** (disabled until a run), **Close** (`IsCancel`), 110×35.

After Run the body swaps to the **results view**: one expander per rule,
header carrying the verdict count — "Door — Fire Rating — non-empty (23
failing)" — state preserved across Refresh. Rows: category, family, type, id
(linkified key), what it holds, what the rule wanted, and a **Show** button
(select + zoom via ExternalEvent). Not-applicable and ambiguous-name rules
render as grey single-line expanders with the reason inline. Status lines:

> "1,240 elements checked — 87 failing, 2 rules not applicable in this model."

> "Scanning Mechanical Equipment… category 4 of 6."

> "Nothing failing. 1,240 elements pass all 12 rules."

**Preset name window** — a small modal over the Main window: one TextBox, a
hint line ("Presets carry category and parameter names only — portable to any
project."), OK/Cancel. Overwriting an existing name asks with a TaskDialog
command-link prompt, the native mimicry rule.

### User operation flow

1. Ribbon: Parameters → Parameter Check. The Main window opens; last-used
   preset is pre-loaded if one exists.
2. Build or adjust the check set — add a rule per parameter that must be
   filled for this milestone; paste an allowed-value list from the office
   standards sheet. **Save Preset**, name it in the Preset name window.
3. Pick scope (Whole model), press **Run**. The status line ticks per
   category; the results view fills, one expander per rule.
4. A skipped item looks like: a grey expander reading "AWP Zone — not
   applicable in this model — no parameter of that name on Walls", or
   "Comments — ambiguous: two parameters share this name (see Parameter
   Audit)". Neither counts as failing.
5. Click **Show** on a failing row; Revit zooms to it. Fix the data — by
   hand, through Parameter Copy, or round-trip the export through the Excel
   tool.
6. Press **Refresh**: the model is re-read, fixed rows disappear, expander
   state survives. The loop is check, fix, Refresh until the footer reads
   zero failing.
7. **Export** writes the remaining failures (ids as a visible key, names
   authoritative) for whoever owns the data.
8. **Close** or Esc at any point — the cancel path writes nothing because no
   path writes anything.

## See also

- Existing: **Update Circuit Rating** (the zero-amp report this generalises),
  **Excel** (the fix path for bulk data), **Parameter Copy** (the fix path for
  derived data), **Family Types** (type-level data editing).
- Siblings: **42 Parameter Audit** (the cure the ambiguous-name finding points
  at — definition-level hygiene vs this tool's value-level checking),
  **01 Circuit Check** (the same check-fix-Refresh loop, electrical-specific),
  **26 Family Audit** (library-side quality gates before families ever reach a
  project).
