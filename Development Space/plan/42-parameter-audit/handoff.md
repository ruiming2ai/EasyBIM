# 42 — Parameter Audit

Finds the same-name, different-GUID shared parameters already poisoning the
model — and rebuilds the offending families onto the winning GUID with one
undo step.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 42 of 45 | Parameters | no | L | 7/10 | 9/10 |

## Main purpose

Two shared parameters with the same display name and different GUIDs make
schedules and filters pick whichever they like. A schedule column reads
blank for half the families, a filter matches the wrong elements, and the
failure is miserable to diagnose because everything is *named* correctly.
Load Parameters refuses to create this clash at the door — its
`guid_conflicts` check blocks the load outright — but it says nothing about
the clashes already inside, and they arrive constantly: a manufacturer
family downloaded with its own "Voltage", a consultant model merged years
ago, a .txt that was regenerated instead of extended. The fix by hand is to
rebuild the parameter in every offending family, carrying its values and
formulas across, and it is tedious enough that in practice it never
happens.

Parameter Audit is the census and the cure. The census needs no family
editing at all: bindings, `SharedParameterElement`s, and the shared
parameters every loaded family's symbols expose are all readable from the
project side, so one pass groups the model's shared parameters into
name-clash camps, each camp listing its families and which schedules and
filters resolve to which GUID. The cure is the L in the effort column: for
a chosen winning GUID, each offending family is opened, its wrong parameter
captured — per-type values and every formula that references it — removed,
rebuilt from the winning `ExternalDefinition`, restored, and loaded back.
Project families only in v1, so the entire run is one Ctrl+Z; a saved file
on disk has no undo, and this tool refuses to make two different promises
in one run.

Nothing in EasyBIM or the free ecosystem occupies this ground. Load
Parameters prevents new clashes; 02 Parameter Check names an ambiguous
parameter name as a finding it refuses to resolve — and its finding text
points here as the cure. Native Revit offers no view of GUIDs at all. The
rank is honest about frequency — most models need this once, not weekly —
but the 9 impact reflects what that one run repairs: years of schedule rot,
fixed in an afternoon, provably, with the post-commit report re-reading
GUIDs from the model to show convergence.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Parameters.panel/Parameter Audit.pushbutton/` beside Load
  Parameters, with `script.py` (`__persistentengine__ = True` — the audit
  window is modeless; Scan, Show, and the fix batch ride the bridge),
  `bundle.yaml` (two-line title "Parameter\nAudit", narrative tooltip,
  `author: Ruiming Liu`), 96×96 icons, `ParameterAuditWindow.xaml`,
  `FixWizardWindow.xaml`, `FixConfirmWindow.xaml`, `FixReportWindow.xaml`,
  `parameter_audit_state.py` (the .txt parser, camp grouping, formula
  dependency sort, capture/restore planning — pure Python),
  `parameter_audit_revit.py` (census pass, family rebuild, read-back),
  `parameter_audit_ui.py`, `parameter_audit_xlsx.py` (findings export,
  xlsxwriter guarded). The findings tree runs on `circuit_schedule_state`'s
  generic engine; the reload prompt is `lib/easybim/family_load_options`.
  The office-.txt parser is written here in state code (the
  shared-parameter-file tidy idea was cut below the line; nothing existing
  parses the file off-Revit) and is the declared hoist if a second consumer
  appears. Load Parameters' `SharedParametersFilename` save-and-restore
  idiom for `OpenSharedParameterFile` is reused, not re-invented — lifting
  that helper into `lib/easybim/` is this tool's one hoist obligation,
  since both bundles then consume it.
- **Revit API route** — census: `doc.ParameterBindings` iterated for
  project bindings; `FilteredElementCollector` `OfClass(
  SharedParameterElement)` for `.Name`/`.GuidValue`/`.Id`; per loaded
  `Family`, its symbols' `Parameters` with `IsShared` checked first and
  `Parameter.GUID` still guarded (it throws on odd parameters — the same
  guard Load Parameters carries). Schedule references:
  `OfClass(ViewSchedule)` → `Definition.GetField(i).ParameterId` matched
  against `SharedParameterElement.Id`. Filter references:
  `OfClass(ParameterFilterElement)` → `GetElementFilter()` unwrapped behind
  a capability probe, older `GetRules()` as the fallback probe; a filter
  whose rules cannot be read lands in "references unreadable", never
  guessed. Repair, per family: `doc.EditFamily(family)` — which requires
  no open `Transaction` in the project document; an open
  `TransactionGroup` is fine, and that distinction is the transaction
  shape — then inside the family document, under its own quiet
  transactions: capture the losing `FamilyParameter`'s per-type values by
  `StorageType` across `FamilyManager.Types` and every `FamilyParameter.
  Formula` that names it; clear those formulas (`SetFormula(p, None)`);
  `RemoveParameter` (which throws if a referencing formula remains —
  hence the order); `AddParameter` with the winning `ExternalDefinition`
  obtained through the save/restore `OpenSharedParameterFile` idiom,
  preserving group and instance/type placement; restore values per type
  via `CurrentType` and `Set`; re-set formulas in dependency order; then
  `family_doc.LoadFamily(doc, options)` through the shared overwrite
  prompt and `Close(False)`. Remove-before-add is forced by Revit itself:
  the winning definition carries the *same name* as the loser, and a
  family cannot hold both.
- **The plan/apply cycle** — `build_plan` for a chosen camp computes, per
  ticked family: the losing parameter, every type value to carry (shown
  as read), every formula to clear and restore with its dependency order,
  and the schedules and filters expected to converge. The Fix
  confirmation window shows that complete dry run and states the subtle
  promise in words — "schedules bound to the losing GUID keep their
  columns only because the winner lands under the same name" — behind the
  acknowledgement checkbox "Rebuild N families onto GUID …-a4f2. One undo
  step." Apply runs the batch inside one assimilated `TransactionGroup`;
  each family is a nested group, and a family whose restore fails — a
  formula that will not re-set, a load refused — rolls back its own group
  and lands under Failed, never half-converted, with counters zeroed on
  rollback. The report window re-reads GUIDs, schedule field ids, and
  filter references from the committed model: Converged / Skipped /
  Failed, proven, not assumed.
- **Edge cases & honest limits** — named buckets in the census: *"instance
  parameters read from a sample placed instance"* (symbols expose type
  parameters; a family with no placed instances gets "instance parameters
  not discoverable without opening the family" — the repair pass sees the
  full truth anyway and reports any late-found camp member); *"GUID
  unreadable (n)"* for throwing parameters; *"filter references
  unreadable"*. The tool never auto-picks a winner — the office .txt's
  GUID is pre-selected when a .txt is given and its row says why; with no
  .txt the choice is the user's alone and the "absent from office file"
  finding kind greys out. Same-GUID/different-names camps and orphaned
  `SharedParameterElement`s bound to nothing are census findings only —
  v1 repairs the name-clash kind and plainly says the others are listed,
  not fixed. Project *bindings* on the losing GUID are reported but not
  rebound in v1; the confirmation names them as remaining work. In-place
  families and families that refuse `EditFamily` are skips with reasons,
  never failures.
- **Risks** — formula capture and restore is the hard core: restore must
  run in topological order over formula references, a cycle fails that
  family closed, and name-based formula parsing must not false-match
  ("Width" inside "Widths") — token-boundary matching, pinned hard in
  desktop tests. `EditFamily` on a hundred families is minutes, not
  seconds: cancellable `forms.ProgressBar`, per-family ticks, and the
  plan states the family count up front. The `SharedParametersFilename`
  swap mutates application state — the restore must sit in a finally, as
  Load Parameters already does. Values whose storage type differs between
  loser and winner definitions (a text loser, a number winner) cannot be
  carried — that family is skipped at plan time with the reason, not
  discovered at apply time.
- **Tests** — `test_parameter_audit_state.py` pins the .txt parser, camp
  grouping, the topological formula sort with cycle → fail-closed, the
  token-boundary formula matcher, and the per-storage-type capture matrix.
  `test_parameter_audit_command_names.py` pins bundle metadata,
  XAML↔handler wiring for all four windows, icon sizes, and the
  IronPython AST scan. `test_parameter_audit_revit.py` drives the adapter
  over fakes shaped like each API generation — `Parameter.GUID` throwing,
  `GetElementFilter` absent, `RemoveParameter` throwing on a live formula,
  a refused `LoadFamily` — asserting each failure rolls back its nested
  group, lands in a named bucket, and only plain data crosses back.

## UI description

**Audit window** — resizable modeless, root `Grid Margin="14"`, rows
Auto/*/Auto. Header: "Parameter Audit" SemiBold ~30px over the DimGray
subtitle "Shared parameters that disagree about who they are." A small
top row holds the optional office .txt picker ("Office file: EasyBIM
Shared.txt" with Browse). Body: the findings tree grouped by kind —
**Same name, different GUIDs (4)** / **Same GUID, different names (1)** /
**Absent from office file (12)** / **Bound to nothing (3)** — a name-clash
node opening into its GUID camps, each camp row carrying the truncated
GUID, its family list, and a second DimGray line like "referenced by 3
schedules, 1 filter · project binding". **Show** on a family row selects
its placed instances via the bridge. Live Search; expander state survives
Refresh. Footer: status left, buttons **Refresh** (`IsDefault`),
**Fix…** (enabled only with a name-clash camp selected; tooltip explains
otherwise), **Export**, **Close** (`IsCancel`).

> "214 shared parameters read — 4 name clashes across 19 families, 3 orphans. Nothing changed yet."

**Fix wizard window** — small modal owned by the audit window. Step one:
the winning GUID as radio rows per camp member (the office .txt's row
pre-selected and marked "office file"), each row listing what already sits
on that GUID. Step two: the family checkbox card — count line "7 of 9
families selected.", Select All / Select None, families that cannot be
rebuilt greyed with the reason in a tooltip ("storage type differs —
value cannot be carried").

**Fix confirmation window** — the complete dry run as a read-only tree:
per family, the values to carry and the formulas to clear and restore in
order, plus the schedules-keep-their-columns sentence, over the
acknowledgement checkbox "Rebuild 7 families onto GUID …-a4f2. One undo
step." **Apply** stays disabled until it is ticked.

**Fix report window** — read-only table, Converged / Skipped / Failed,
every count re-read from the committed model, each Failed row naming the
step that rolled it back.

> "7 families rebuilt, 0 failed — all 3 schedules now resolve GUID …-a4f2. Verified against the model."

> "6 rebuilt, 1 failed: 'ACME-VAV' — formula 'FlowText' would not re-set; its group rolled back."

### User operation flow

1. Ribbon: Parameters → Parameter Audit. The Audit window opens and the
   census runs; camps fill the tree, worst kinds first.
2. Optionally point Browse at the office .txt — the absent-from-file kind
   ungreys and .txt GUIDs get their "office file" tag.
3. Select the "Voltage" clash, press **Fix…**; pick the winning GUID,
   untick any family to leave alone.
4. Read the confirmation — every value, every formula, the schedule
   sentence — tick the acknowledgement, press **Apply**. **Cancel** at
   either step returns to the audit window with nothing written.
5. The cancellable ProgressBar ticks per family; cancelling mid-batch
   keeps finished families and reports the rest as "skipped — cancelled".
6. A skipped item looks like: "ACME-VAV — storage type differs — value
   cannot be carried" (planned skip) or "skipped — cancelled"; a failed
   one names its step and rolled back alone.
7. The report proves convergence from the model; close it, and the audit
   tree Refreshes — the repaired camp is gone.
8. One Ctrl+Z in Revit undoes the entire run if it was all a mistake.

## See also

- Existing: **Load Parameters** (blocks new clashes at the door; donates
  the `guid_conflicts` idea, the GUID-read guard, and the
  `OpenSharedParameterFile` save/restore idiom), **Parameter Copy** /
  **Parameter Combine** (the day-to-day parameter movers this census
  protects).
- Siblings: **02 Parameter Check** (its "ambiguous name" finding names
  this tool as the cure), **17 Where Used** (the general who-references-it
  lookup; this tool's schedule/filter cross-reference is the parameter
  slice of it), **26 Family Audit** (the other census over loaded
  families).
