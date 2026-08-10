# Families Transfer — families out of a Revit link

*2026-08-10*

## What changed

Three things, one of them substantial.

1. `Load More from Project` is now **`Load More from Recent Project`**. The page
   it opens keeps its `Transferable Families` title; only the button moved.
2. `Click to Select More in Project` left the source page and now sits on that
   browser as **`Select More in the model`**.
3. A third source card, **`Load More from Revit Links`**, reads families out of
   loaded Revit links without opening the linked `.rvt`.

## Why the link source is shaped the way it is

### The one thing that could not be settled off Revit

`AGENTS.md` (2026-08-05) records that `GetLinkDocument()` hands back a fully
readable document. That is about *reading*. Getting a family *out* is a
different question, and the API docs point the other way:

- `Document.EditFamily` — "may not be called if the document … is in a
  read-only state", and throws "if the family is already being edited", so
  Revit tracks an editing flag on the source document.
- `RevitLinkInstance.GetLinkDocument` — "Operations that … modify the
  document's status in memory … cannot be performed on this document."
- `Document.IsReadOnly` — "not only the model cannot be modified at the
  moment, but even a new transaction may not be started."

Setting that flag is exactly the class of operation `GetLinkDocument` forbids.
So `link_doc.EditFamily(family)` may well throw, and there is no Revit on the
development machine to find out. **The feature is therefore built so that
either answer ships something that works.**

Note what is *not* the problem: `Family.IsEditable` is documented purely in
terms of in-place, not-saveable, and primary-family-in-a-family-document —
links are not mentioned. It is recorded on each row rather than used as a
gate, because gating on it would empty the card, and an empty card reads as
"this link has no families" rather than as a refusal. That is the worst
available outcome, so it is designed out.

### The cascade

Probed **once per link**, when the user presses the button, before the browser
opens. Per-family probing would turn a refusal into one identical failure line
per family in the middle of a batch.

| | Route | When | Overwrites? |
|---|---|---|---|
| 1 | Read from the **open copy** of the same file | The linked `.rvt` is also open in this session | Yes |
| 2 | `link_doc.EditFamily` → `LoadFamily` | The link allows it | Yes |
| 3 | `CopyElements(link_doc → target)` | The link refused | **No** |

Route 1 is free and always correct: `_doc_key()` normalises both a link
document and an open document to `path|<lowercased path>`, so the match is a
dict lookup, and reading from the real document sidesteps the whole read-only
question. Opening the arch model and also linking it is a common enough
workflow that this rung is worth having on its own.

Route 3 is why the card is not a no-op if route 2 turns out to be dead. Its
limit is real and is surfaced rather than hidden: the mandated duplicate-type
handler answers `UseDestinationTypes`, so a family already in the target
**wins**. Copying onto it would report a success that changed nothing. The
target's family names are collected once per target and a clash is skipped
with `already in the target; a copy out of a link cannot overwrite it`.

Export has no route 3: writing an `.rfa` needs a family document, which is the
thing the link refused. It reports that rather than pretending.

### What is deliberately not built

- **`OpenDocumentFile` on the linked file.** It would work, and it breaks the
  feature's only promise. It also silently upgrades an older model to the
  running Revit version with no way back — the reason Load Parameters reads
  headers with `BasicFileInfo` instead. `test_families_transfer_command_names`
  fails the build if the name appears.
- **Staging the copy through the host and rolling it back.** Legal, but a name
  clash in the *host* resolves to the host's family, so the user would get the
  host's `Door_Single.rfa` labelled as the link's, with no error.
- **Nested links.** Not reachable through `GetLinkDocument`; stated in the
  card's own label rather than left to be discovered.

## Keys

`link|<link document key>|<element id>` — e.g.
`link|path|c:\jobs\arch.rvt|12345`. An ElementId means nothing outside its own
document, so without the document key two links holding family 12345 collapse
onto one row. `|` is illegal in a Windows path, so `rsplit("|", 1)` recovers
both halves. Two link instances of one file are one row: they share a single
`Document`, so listing instances would offer the same families twice.

Unchecking a link drops the families chosen from it
(`prune_link_families_to_checked_links`) — a link that is off must not still
transfer.

## The pick round trip

`PickObjects` cannot run while a modal window is up, so `Select More in the
model` hands its selection back, `script.py` closes the window, picks, and
re-enters `STEP_FAMILIES`.

The load-bearing detail: that step used to be entered fresh and left, so it
discarded its own checkbox state on exit. Now that it is *re-entered*,
discarding would wipe whatever the user had ticked before reaching for the
model. The keys are split out, merged with the picks, and carried back in.
Search text and expander state are still lost on the round trip; threading
them through the constructor is a later change if it grates.

## Performance

Applied alongside, all safe:

- Transfer and export run behind a **cancellable** `forms.ProgressBar`.
  Every family costs one `EditFamily` (the most expensive call in the tool)
  plus one `LoadFamily` per target; a few hundred was minutes of frozen Revit
  with no feedback and no way out.
- The project family scan is **collected once** and reused. It used to re-run
  on every entry to `STEP_FAMILIES` — once today, but once *per pick* after
  this change.
- Link families are collected only for **checked** links, and cached per link.
- `resolve_family`'s fallback ran a whole `FilteredElementCollector` pass per
  family option — latent while nothing reached it, quadratic the moment a link
  family lost its handle. Replaced by one `{id: family}` index per document.
- `get_source_family_options` tested eligibility twice per family.
- Select All / Select None no longer rebuild every CheckBox and Expander after
  flipping ticks that are already on screen.

Left alone deliberately: list virtualization and search debounce (both rewrite
the population code across all four windows), and the duplicated
`get_open_family_documents` call, which walks a handful of documents and exists
to refresh selection state.

## Tests

`test_families_transfer_links.py` is the first test in the repo to import a
`*_revit` module. It drives all three cascade rungs against fake Revit objects
— including the refusal path, the name-clash skip, cancellation, and the guard
that stops `Transfer & Close All .rfa` from ever closing a link document. It
follows the fakes pattern of `test_coordination_review_passive.py` and restores
`sys.modules` afterwards so the stubs cannot leak into the rest of the suite.

`test_families_transfer_command_names.py` fills a gap that predates this work:
the tool had no XAML↔handler drift check at all.

## Still to verify in Revit

Everything above is verified off Revit only. In order:

1. **Does `link_doc.EditFamily()` succeed or throw?** This picks route 2 or 3.
2. If it half-succeeds — sets the "already being edited" flag it cannot clear —
   does the link need a reload? **Probe on a scratch model first.**
3. Does `Family.IsEditable` read `True` inside a linked document?
4. End to end: link a model, tick the link, browse, transfer into a second open
   project, and cancel mid-run.
