# 45 — Text Types

Every text type in the model with its usage count, remapped onto the office
standards and retired — the used rogues purge cannot touch.

| Rank | Panel | MEP | Effort | Usefulness | Impact |
|---|---|---|---|---|---|
| 45 of 45 | Annotation (new panel) | no | M | 7/10 | 7/10 |

## Main purpose

Every detail pasted from the office library or another model drags its text
types along, and by CD the project holds fourteen of them — "Arial 2.5mm",
"ARIAL 2.5 Copy 1", '3/32" Arial (2)' — all near-identical, none standard.
Purge only removes the unused ones. The used rogues are the actual problem:
re-typing their notes is a hand job note by note, and the type selector
cannot even say which types are used where, so nobody knows how big the job
is until they start it.

Text Types does one read pass and shows the whole lane: every TextNoteType
with its complete visual fingerprint and a usage count from one iteration of
the model's TextNotes. A standards preset — blessed type names, JSON,
portable by name across projects the way every EasyBIM preset is — marks
which rows are targets; every rogue row gets a Map-to choice, pre-suggested
only on an exact visual match and never forced. Apply re-types the notes
through `Element.ChangeTypeId` and, behind its own acknowledgement, deletes
the emptied source types — one assimilated undo step, counts read back from
the committed model.

It closes the list at 45 because the failure it prevents is inconsistency,
not a wrong number on paper — the set still prints with fourteen text types,
just embarrassingly, which is why it trails the panel's lie-hunters (07 Dim
Overrides, 11 Reference Check). But the field is empty all the same: purge —
native and pyRevit's — only deletes unused types; no free tool counts
text-type usage or remaps the used rogue; Family Types manages family types,
not annotation types; and 38 Family Rename deliberately keeps system types
out of its scope and names this tool as owner of the text lane. It also
completes the Annotation panel as its third founder.

## Basic implementation ideas

- **Bundle & module layout** —
  `EasyBIM.tab/Annotation.panel/Text Types.pushbutton/` (create the panel if
  07 or 11 has not already). Modal window, one command — no ExternalEvent,
  no persistent engine, the same shape as 38 Family Rename. `script.py` thin
  launcher; `bundle.yaml` two-line title "Text\nTypes", narrative tooltip
  naming the out-of-scope surfaces (tag labels, schedule appearance, model
  text), `author: Ruiming Liu`, no `min_revit_version` — this API surface
  predates every supported release. Split: `text_types_state.py`
  (fingerprint equality, suggestion tiers, target/source exclusivity, delete
  eligibility, plan bucketing, preset serialisation — zero Revit imports),
  `text_types_revit.py` (the two collectors into plain dicts, ownership
  probes, the executor with the delete tripwire, post-commit re-read),
  `text_types_ui.py` + `TextTypesWindow.xaml`, `TextTypesConfirm.xaml`,
  `TextTypesReport.xaml`. Presets follow `tag_align_presets.py`: local
  roaming file via `script.get_universal_data_file` (no Revit version in the
  filename), optional shared JSON for the office; if 02 Parameter Check has
  hoisted the generic preset store to `lib/easybim/` by build time, compose
  it — a third consumer settles that hoist. Nothing else hoists: first
  consumer of a text scanner.
- **Revit API route** — Types:
  `FilteredElementCollector(doc).OfClass(TextNoteType)`. The fingerprint is
  every parameter Revit's own Type Properties dialog shows, read by
  BuiltInParameter: `TEXT_FONT`, `TEXT_SIZE`, `TEXT_WIDTH_SCALE`,
  `TEXT_STYLE_BOLD` / `TEXT_STYLE_ITALIC` / `TEXT_STYLE_UNDERLINE`,
  `TEXT_COLOR`, `TEXT_BACKGROUND`, `TEXT_BOX_VISIBILITY`, `LINE_PEN`,
  `LEADER_OFFSET_SHEET`, `TEXT_TAB_SIZE`, and `LEADER_ARROWHEAD` — the last
  is an ElementId, resolved to the arrowhead type's *name* before it crosses
  (identity by name, never the id). Doubles cross as raw internal feet for
  comparison plus `AsValueString()` for display; a parameter a generation
  does not report is simply absent from both sides of every comparison,
  never guessed as a default. Usage: one
  `FilteredElementCollector(doc).OfClass(TextNote)` pass — per note
  `GetTypeId()`, `OwnerViewId`, `GroupId`, element id — no per-view loops.
  Editability: `WorksharingUtils.GetCheckoutStatus` probed per staged note
  at plan time in workshared documents. Write: TransactionGroup "Unify text
  types", assimilated. Per source type, *two* nested Transactions — the free
  notes, then (only when acknowledged) the group-member notes — so a Revit
  refusal inside groups rolls back the group tranche alone, never the
  type's clean notes. Then one nested Transaction per delete:
  `doc.Delete(type_id)` runs only after the type's usage re-reads zero
  inside the group, and the returned-ids tripwire from 10 Families Purge
  backstops it — any returned id beyond the type itself means the delete
  cascaded into something the plan did not know, and that nested
  transaction rolls back with the type re-bucketed "kept — deleting it
  would also delete {n} other elements".
- **The plan/apply cycle** — `build_plan` computes per row: standard or
  source status (exclusive by construction — a Standard row loses its
  Map-to, a mapped row cannot be blessed, so remap chains cannot exist),
  the mapping target, the note ids split mappable / in groups / owned by
  another user / no longer in model (re-verified in a fresh pass at plan
  time), delete eligibility — a type is emptied only when *every* user
  re-types, so group-skipped or owned notes keep their type alive — and
  the totals. The Confirmation window shows one line per source type with
  its fate, the named-exclusion buckets, the sentence that earns trust:
  "Instance formatting — bold runs, leaders, wrapping width — survives the
  swap; the base look becomes the target's." Two acknowledgement ticks:
  groups ("Re-typing a note inside a group changes every instance of that
  group" — enables the group tranches) and delete ("Emptied source types
  are deleted; the type selector loses them" — gates the delete phase;
  unticked means re-type only, all types kept). The Report window reads
  back from the committed model: per-type usage re-counted, deletions
  confirmed by re-collection, counters zeroed for any rolled-back tranche.
- **Edge cases & honest limits** — Named-skip buckets: "in a group (not
  acknowledged)", "owned by {user}", "no longer in model", "unmapped —
  left as is", "type kept — still used by {n} notes", "type kept — last
  text type in the document" (Revit refuses to delete the final one;
  pre-checked so it is a named keep, not a failed transaction), "failed —
  {Revit's message verbatim}, tranche rolled back". Auto-suggestion fires
  only on an exact full-fingerprint match; a near-miss gets a Note —
  "differs from 'Arial 2.5mm' only in background" — and stays unmapped,
  because the tool refuses to guess that an opacity or arrowhead
  difference is unintentional. Count-0 rogues need no mapping and ride the
  same delete acknowledgement, so one run cleans the lane end to end
  instead of leaving a purge chore behind. Out of scope and said so in
  tooltip and report footer: tag label appearance (lives inside the tag
  family), schedule appearance fonts (per-schedule settings, not text
  types), dimension-type text settings, ModelText (`ModelTextType` is a
  different class), and text inside family documents. A preset standard
  missing from the model shows as "named in preset — missing from this
  model"; the tool refuses to create it — authoring a full visual
  definition is a different job from a remap.
- **Risks** — Fingerprint completeness is the whole trust story: two types
  agreeing on font, size, and bold but differing in leader arrowhead or
  background would visibly change drawings if merged on a partial
  fingerprint — so the comparison covers every Type Properties parameter,
  arrowhead by resolved name, and exact-match-only auto-suggestion is a
  pinned rule, not a preference. Size comparison uses a tight
  internal-feet epsilon, never display rounding: two types that *print*
  the same size but store different values are different types, and the
  Note says "same displayed size, different stored size". The sharpest
  hazard is the delete: it is tempting to assume `Document.Delete` on a
  still-used type refuses and fails the transaction — it does not; it
  cascades and takes the notes with it. The design assumes the cascade,
  which is why the emptied re-read happens inside the transaction group
  and the returned-ids tripwire backstops the plan. A deliberate near-miss mapping (a hand-picked
  Map-to) legitimately changes the printed look and can re-wrap notes into
  more lines — that is the merge working, and it is why such rows are
  never auto-suggested. Performance is bounded by design: one pass over
  every TextNote, and the grid itself is types — dozens, not thousands.
- **Tests** — `test_text_types_state.py` pins fingerprint equality
  (missing-parameter symmetry, the feet epsilon, arrowhead-by-name), the
  exact-vs-near-miss tiers with their difference naming,
  standard/source exclusivity, delete eligibility with group-held and
  owned remainders, the last-type keep, plan bucketing, preset round-trip
  including missing-standard rows, and counters zeroed on tranche
  rollback. `test_text_types_command_names.py` pins the new-panel bundle
  metadata, two-line title, XAML↔handler wiring for all three windows,
  96×96 icon pairs, the IronPython AST scan, forbidden-API pins, and zero
  Revit imports in the state module. `test_text_types_revit.py` drives the
  adapter against fakes per generation (with and without `TEXT_TAB_SIZE`),
  the arrowhead id→name resolution, checkout status crossing as plain
  values, the two-tranche transaction shape with the group tranche rolling
  back alone, the delete tripwire re-bucketing a cascading delete, the
  post-commit re-read, and the assertion that nothing but ints and unicode
  crosses back.

## UI description

**Main window** ("Text Types") — resizable modal, root `Grid Margin="14"`,
rows Auto/*/Auto. Header: "Text Types" over the DimGray subtitle "Every text
type, its usage count, and a staged remap onto the standards. Nothing writes
until Apply." A slim **Standards card** on top: preset ComboBox with
Load / Save… / Delete in the `tag_align_presets` idiom (source labels "This
computer" / "Shared folder"), and the count line "3 of 14 types marked as
standards." A preset name the model lacks shows as a flat note row: "'Arial
1.8mm' named in preset — missing from this model." Body card: the type grid
in the Family Types idiom — columns **Standard** (checkbox; ticking blesses
a row in-session, the preset pre-ticks) / **Type** / **Font** / **Size** /
**Width** / **Count** / **Map to** / **Note**. Size and Width display via
`AsValueString`; the row tooltip carries the rest of the fingerprint (bold,
colour, background, border, arrowhead). Map-to is a ComboBox listing only
the standards, blank by default, pre-filled solely on an exact fingerprint
match; Standard rows show "—" there. Remapped rows render red until Apply;
rows the run cannot touch grey out with the reason in a tooltip. The Count
cell's tooltip breaks the number down: "26 notes — 14 in groups, 2 owned by
jsmith." A "Hide unmapped" checkbox filters at rebuild time; the Search box
flips `is_visible` so mappings survive filtering. Footer: status left;
right, **Apply…** (`IsDefault`, disabled with a tooltip until at least one
row is mapped) and **Cancel** (`IsCancel`, asks before dropping staged
mappings).

> "14 text types — 3 standards, 9 mapped, 2 near-misses left unmapped (see
> Note). 312 notes will re-type."

**Confirmation window** — small modal over the Main window. The summary
line, then one line per source type — "'ARIAL 2.5 Copy 1' → 'Arial 2.5mm' —
47 notes; type will be deleted." / "'3/32" Arial (2)' → 'Arial 2.5mm' — 12
of 26 notes; 14 in groups; type kept." — the named exclusions, the
formatting-survival sentence, and the two acknowledgement checkboxes:
"Re-typing a note inside a group changes every instance of that group" and
"Emptied source types are deleted; the type selector loses them." **Apply**
(`IsDefault` — the ticks are optional gates, not blockers: unticked simply
excludes group rows and keeps every type) and **Cancel** (`IsCancel`,
returns to the grid with stages intact).

> "312 notes across 9 types → 3 standards. 7 types will be deleted; 2 kept —
> still used by notes in groups."

**Report window** — read-only WPF table, never stacked message boxes:
sections Re-typed / Skipped (named) / Deleted / Kept (named) / Failed
(Revit's message verbatim), every count read back from the committed model.
**Close** only.

> "312 notes re-typed, 14 skipped (in groups), 7 types deleted, 2 kept —
> counts read back from the model. One undo step."

### User operation flow

1. Ribbon: Annotation → Text Types. The two read passes fill the grid;
   nothing is staged.
2. Load the office preset — its rows tick Standard and lose their Map-to —
   or tick Standard by hand on the rows this project blesses.
3. Exact-fingerprint rogues arrive pre-suggested and red. Read the Note
   column for near-misses ("differs only in background") and hand-pick
   Map-to where the visual change is deliberate.
4. "Hide unmapped" and Search to focus; selections and mappings survive
   both.
5. Press **Apply…**. The Confirmation window lists every type's fate; tick
   the acknowledgements that apply, or leave them — unticked group rows
   become named skips, unticked delete keeps every type.
6. Apply commits one assimilated TransactionGroup: per-type free tranche,
   acknowledged group tranche, then eligible deletes with the tripwire. A
   refused tranche rolls back alone and lands in the report with Revit's
   message; its counters read zero.
7. The Report window opens with counts re-read from the model. A skipped
   item reads: "'ARIAL 2.5 Copy 1' — 14 notes skipped: in a group (not
   acknowledged); type kept — still used by 14 notes."
8. Close. One Ctrl+Z in Revit restores everything — re-typed notes and
   deleted types alike.
9. Cancel path: Cancel in the Confirmation returns to the staged grid,
   model untouched; Cancel in the Main window asks before dropping staged
   work. An unmapped row is "left as is" — skipped, never failed.

## See also

- Existing EasyBIM: **Family Types** — the staged type-grid idiom this
  borrows, managing the family lane this tool deliberately does not;
  **Tag Align** — `tag_align_presets.py`, the preset store this copies;
  **Sheet Manager** — the original staged red grid; **Tags Sweep** and
  **Tag Align** again as the panel's future co-residents.
- Plan siblings: **07 Dim Overrides** and **11 Reference Check** — the
  other Annotation founders (whichever lands first creates the panel);
  **38 Family Rename** — names the loadable lane and explicitly leaves
  text types to this tool; **10 Families Purge** — the unused-half
  neighbour whose `Document.Delete` tripwire this delete phase shares;
  **02 Parameter Check** — the other `tag_align_presets` consumer and the
  trigger for the generic preset-store hoist.
