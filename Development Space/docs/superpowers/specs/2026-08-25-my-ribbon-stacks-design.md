# My Ribbon — stacks of small buttons, separators, the slide-out

*2026-08-25*

Follow-on to `2026-08-16-my-ribbon-tabs-uninstall-native-dynamo-design.md`.
Rounds 1–2 place every button full-size on a flat panel. The user asked for
pyRevit's layout vocabulary on panels of their own — the **stacked small
buttons** a `.stack` folder gives, plus separators and the slide-out — with
the choices confirmed while planning: stacks **and** separators **and** the
slide-out this round (user-made drop-down groups deferred), and a stacked
button is a **small clone**, never the shared object resized.

## Why a stacked button must be a clone

Button size is a property of the ribbon item itself (`RibbonItemSize` on the
item; Revit's `AddStackedItems` makes Standard-size items — the row does not
shrink what sits in it). The shared-object mechanism therefore cannot render
a placed button small without also shrinking it on its home tab — invisible
for a hidden tab, ugly for an installed extension's, unacceptable for
Annotate ▸ Tag by Category. So a **flat placement stays the shared live
object** (nothing changes), and a **stacked placement becomes a clone**: a
new `RibbonButton`, `Size = Standard`, `ShowText = True`, our `Id`
(`EasyBIM_MyRibbon_p_<placement>`), `Text`/`Image`/`LargeImage`/`ToolTip`
copied at apply time — and the original's **`CommandHandler` shared**, so the
click runs the same command and the enabled state follows the same
`CanExecute`. Copies refresh every session (the apply rebuilds everything);
drift is bounded to one session. This is the "v1.5 cloned button" the
2026-08-15 spec deferred, arriving with size as the reason; rename/re-icon
can now ride the same builder later.

The clone builder is the **only** code that may say `"Size"` or
`"CommandHandler"` — pinned by a contract test, so no future edit can
mutate a shared original.

## Registry (format stays 1)

- A placement gains `"stack": "<id>"` (`k1`, `k2`…). Members of one stack
  are contiguous in per-destination order; the block sits where its first
  member is. 2–3 members (`STACK_MIN`/`STACK_MAX`), plain buttons only
  (`STACKABLE_KINDS` — a drop-down needs its full-height arrow).
- A **separator** and the **slide-out fold** are placements with
  `kind: "separator"` / `"slideout"`, empty source and path — so ordering,
  Up/Down, Remove, `count_changes`, export and Apply's staging all work on
  them unchanged. `read_registry`'s keep-filter admits the marker kinds
  without a path; at most one `slideout` per destination.
- `normalize_layout` (now inside `renumber`, so every remove/move/import
  self-heals) restores contiguity, dissolves stacks of one, sheds a fourth
  member, un-stacks markers, and keeps only the first fold.
- **Older EasyBIM reading this file:** `stack` is dropped by its
  `_clean_placement` and marker rows fail its path filter — the buttons come
  back flat, in order, with no noise. Nothing to migrate forward either: a
  round-1/2 file simply has no layout.
- Export deep-copies, so the layout travels. Import: **Replace** carries it
  verbatim (stack ids re-minted so they never collide); **Merge** keeps an
  existing panel's layout — incoming stacks still group their own buttons
  (fresh ids), but a file's separators/folds land only on panels the import
  itself creates.

## The engine

`apply` walks placements sorted by (dest, order) as before; a run of rows
sharing a stack id becomes one owned `RibbonRowPanel` (`Id`
`EasyBIM_MyRibbon_row_<stack>`), mirrored like any added item and taken back
whole next session — clones die with their row, so idempotence needs no new
machinery. A marker becomes an owned `RibbonSeparator` or `RibbonPanelBreak`
(AdWindows renders everything after a panel break in the slide-out — the
one mechanism claim below that only Revit can prove). Degenerates: a run of
one places the shared object flat; a member that fails to resolve is
reported missing and the row is built from the rest; a row with no members
is removed again. Two guards keep our copies out of the source pool: the
structural walk no longer looks through **our** rows (a clone must never
pass for its original), and `describe_ribbon_tab` skips ours entirely (the
"Other tabs" picker cannot offer a clone back).

## The window

The tree shows a stack as a parent row — *Stacked (2 small buttons)* — with
its members as children; markers render as `———  separator` and `—▼—
slide-out` rows. Four buttons join the action row (now a WrapPanel, the
Sources side's reasoning): **Stack with next** (this row + the one below,
refusals as plain sentences: at most three, drop-downs never), **Unstack**
(dissolves the whole stack, buttons stay), **Add separator**, **Add
slide-out** (greyed once the panel has one, tooltip says so). Up/Down move a
stack as a block; a member moves inside its stack and steps out at the edge;
Move to… carries a whole stack (still stacked) or one row (leaving its
stack). Remove on a stack node is disabled — Unstack is the verb. The
buttons count no longer counts markers.

## Reviewed before commit

Mutation-checked: dropping the `CommandHandler` copy, an off-by-one in the
stack cap, and sharing the original into the row instead of the clone each
fail tests (1/1/3). The adversarial read fixed while building: marker rows
must not route through `add_placement` (its empty-path dedupe would collapse
every separator into one — pinned by a contract test); `read_registry`'s
path filter would have silently dropped markers; a lone "stacked" row after
edits places shared-flat rather than as a one-button row.

## Still to verify in Revit

1. A 2-stack and a 3-stack render as small rows; a clone's click runs the
   original command; the clone greys exactly when the original does (the
   shared-`CommandHandler` `CanExecute` claim — highest risk).
2. Clone icons at stacked size (Image copied; LargeImage along) look right,
   for a pyRevit button, a native button and a Dynamo graph (Revit's Dynamo
   images land on the clone).
3. `RibbonPanelBreak` really folds the rows after it into the panel's
   slide-out, and the separator draws.
4. Re-apply, pyRevit reload, and Remove/Unstack leave no orphan rows, and
   the originals sit unchanged on their home tabs throughout.
5. Export here, import on another machine: same rows, stacks and fold after
   one reload; the same file on a round-2 EasyBIM shows the buttons flat.
