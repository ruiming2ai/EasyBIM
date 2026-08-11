# Families Transfer — three cards, one shape

*2026-08-11*

Follow-on to `2026-08-10-families-transfer-revit-links-design.md`. That round
added the Revit Links source; using the window showed the three cards had been
built at different times and did not match.

## Why the window collapsed when you dragged it

A WPF `*` row takes its minimum from `RowDefinition.MinHeight` **only**. Unlike
an `Auto` row it does not fold its child's desired height into its own minimum,
so it shrinks to zero and the child paints outside its slot. `MinHeight` on the
child `Border` does not help — the child clamps its own render size, but the
Grid still arranges it into the short cell.

Cards 1 and 2 sat in unconstrained `*` rows and ate their own headers. Card 3
escaped only because it was `Auto`, which is also why it was the one card that
never resized.

The rule now: **every `Height="*"` row in `SourceSelectionWindow.xaml` carries a
`MinHeight`**, and inside each card only the list row is `*`. `Window.MinHeight`
is the sum plus the title block, footer and frame, so the floor is unreachable —
the MinHeights are a belt, not a live constraint.
`test_no_source_card_can_be_squashed_to_nothing` fails the build if one is lost.

### Chrome budget (DIPs — recompute if the font changes)

| Piece | Height |
|---|---|
| Card `Border` (1px × 2 + Padding 12 × 2) | 26 |
| Header row (15px header ≈ 20 + 4 margin + 13px count ≈ 17; the 30px buttons fit inside) | 41 |
| Search row (Margin 8+6 + TextBox 28) | 42 |
| List minimum (2 rows × 24 + inner Border 14) | 60 |

Per card 169 → `MinHeight="168"` used for all three (the button rides the header
row, so no card needs a fourth row). Body = 3 × 168 + 2 × 12 = 528. Client =
28 margin + 90 title + 528 + 66 footer = 712; plus non-client ≈ 39 →
`MinHeight="720"`, `Height="920"`, `Width="820"` (the header row now carries
220 + 90 + 90 of buttons alongside the header text).

**Known limit:** 720 does not fit a 1080p screen at 150% Windows scaling
(work area ≈ 693). Inherent to three fixed-chrome cards stacked vertically. If
it bites, move the two card action buttons back onto their own rows and shrink
the title block; the real fix is fewer cards per page.

Two consequences worth stating, because they look cosmetic and are not:

- **Header and count lines use `TextTrimming`, never `TextWrapping`.** A
  wrapping line is a variable-height header, which is the exact thing being
  fixed. Same for the footer status line, whose `Auto` row would otherwise grow
  and push the body off the bottom.
- **Card 3's long parenthetical search label was deleted**, not restyled. It
  wrapped to two or three lines depending on width. The sentence moved to the
  new link page's subtitle, where links are actually chosen.

## One shape

```
┌─ Border ─────────────────────────────────────────────────────────┐
│ Auto   DockPanel:  header + count      [Load More…][All][None]   │
│ Auto   search label + TextBox                                    │
│ *      Border → ScrollViewer → StackPanel   ← the only flex row  │
└──────────────────────────────────────────────────────────────────┘
```

Headers `FontSize="15" FontWeight="Bold" Foreground="#1A1A1A"` (was 11 /
SemiBold / DimGray). One count sentence everywhere:
`format_selection_count_text` → `"X selected, Y unchecked."`, counted over the
**whole** list, never the visible one — that line is the only thing telling the
user a filter is holding rows back.

Empty lists keep the uniform count and put the explanatory sentence in the list
body via `_add_empty_text`. A count line that switches between a number and a
sentence is a variable-width line, which risks wrapping.

All five checkbox lists now go through one `_fill_checkbox_list` helper; only
the label, the empty message and (for links) whether a row is enabled differ.

## Links became a page, not a card

Card 3 listed *links* while its neighbours listed *families*. Links are a step,
not a source — the source is the families they yield.

```
card 3 ──[Load More from Revit Links]──► LinkSelectionWindow ──Next──► family browser
   ▲                                                                       │
   └────────────────── chosen link families, with checkboxes ──────Add──────┘
```

`LinkSelectionWindow` is a new class, not a reuse of `TargetSelectionWindow`,
which has no search, no count, no `_is_ready` guard and no disabled-row
handling, and whose title is pinned by a test with `assertNotIn`.

**Both** Next and Back prune the chosen families against the ticked links: page
1 no longer has the link checkboxes that used to do that, so unticking a link
and pressing Back has to drop its families.

The likeliest defect in this change was `_read_every_selection` forgetting
`_read_selected_link_family_keys()` — unticking a row in card 3 would then
silently do nothing. It has a comment saying so.

## Hide Un-checked

A rebuild-time filter, not a live per-row one: a row that vanishes the instant
you untick it cannot be un-vanished. So the visible set may legitimately hold
unticked rows until the next explicit refresh.

|  | Hide OFF | Hide ON |
|---|---|---|
| **Select All** | no rebuild | **no rebuild** — it only touches rows already on screen, and cannot reveal a hidden row because the hidden rows are exactly the ones it does not touch |
| **Select None** | no rebuild | **rebuild** — every visible row just became unchecked, so the list must go empty |

The rebuild case is cheap: the result is an empty panel, so no CheckBox is
constructed. `_sync_category_expanders()` before it is not optional —
`select_none_click` never rebuilt before, so it never captured expander state,
and every category would spring open.

**The invariant that licenses leaving hidden rows out of the control sync:** a
row hidden by this filter is always an unticked row, so it never holds state a
sync would need to preserve. Rows hidden by the *search* filter do, and are
likewise left alone — for the opposite reason. Both are tested directly.

Not carried across pages (the window is reconstructed on every Back/Next),
consistent with `_expanded_categories`.

## What can be picked in the model

Exactly two classes in the Revit API declare `Symbol`: `FamilyInstance` — so
model families and `AnnotationSymbol` generic annotations already worked — and
`TextElement`, whose `Symbol` returns a `TextElementType`. Tags have neither:
`IndependentTag` derives from `Element`, and its type is a plain `FamilySymbol`
(there is no `IndependentTagType` class). Room, area and space tags are the
same shape via `SpatialElementTag`.

`_family_from_element` now falls back to
`element.Document.GetElement(element.GetTypeId())`, skipping
`InvalidElementId`. The `isinstance(symbol, DB.FamilySymbol)` guard is
load-bearing, not defensive: without it a text note carries a
`TextElementType` into the next step.

`AllowElement` stays fully try/except-wrapped — an exception there is
documented to reject the element **silently**.

### Matchlines

Not families. Verified against `RevitAPI.xml`/`.dll` 2021–2026: across 20,121
types there is no `Matchline` class and no `MatchlineType`; the only hits are
`BuiltInFailures.MatchlineFailures` and four sketch-extent instance parameters.
`BuiltInCategory.OST_Matchline` exists, but a category is not a family, and
`GetTypeId()` on one returns `InvalidElementId`. So a matchline can never
appear in `OfClass(DB.Family)` — its graphics move via Transfer Project
Standards.

The contrast matters for the README: section heads, callout heads, elevation
marks, grid heads, level heads and view reference *tags* **are** loadable
families and are already listed. They just cannot be picked by clicking,
because the clickable element is the system element that references them.

## The naming trap, proved

`test_families_transfer_state.py` matches `script.py` branches by substring over
`ast.dump`, and some break on first match. `ast.walk` is breadth-first, so
branches arrive in the source order of their parent step.

Naming the new local `link_family_window` contains `family_window`, and because
the new step sits *before* `STEP_FAMILIES` in source order it captures
`test_family_add_branch_returns_to_source_without_target_navigation` — which
then still passes while testing a different branch. **A test that stops testing
what it says.** Confirmed by simulation, not inspection.

Locked: `STEP_LINK_DOCS = "link_docs"`, local `link_docs_window`.
`links_window` (page 3) keeps its name for the same reason.
`test_the_link_browser_local_avoids_the_family_window_substring` guards it.

Also closed this round: `_class_control_attributes` only matches `^[A-Z]`
x:Names, so this tool's snake_case `*_tb` / `*_cb` / `*_btn` controls were
invisible to the drift checker and a typo would surface only inside Revit.
`test_every_lowercase_control_has_a_matching_x_name` covers them now.

## Still to verify in Revit

Nothing here is verified in Revit. New this round:

1. Drag the window — only the three lists should shrink.
2. The three headers should read as one hierarchy.
3. Click a tag with **Select in the model**; its family should land in card 1.
4. Card 3 → links → families → Add; the families should come back ticked, and
   unticking one on page 1 should drop it.

Still open from last round: does `link_doc.EditFamily()` succeed or throw?
Probe on a scratch model before trusting the link source.
