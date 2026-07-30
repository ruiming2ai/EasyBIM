# Batch Duplicate Host pyRevit Port Design

Date: 2026-05-19
Status: Proposed and approved in brainstorming

## Summary

Port the Revit C# `Batch Duplicate Host` add-in from `C:\Users\RML\Documents\Dynamo - Batch Host` into a new pyRevit command under `EasyBIM.tab/Misc Tools.panel`.

The pyRevit port must preserve the original four-step wizard, public name, icon branding, target-local offset behavior, and summary flow as closely as pyRevit allows.

The port also adds one approved UI behavior change on the Offset step:

- an always-visible `Align Orientation` checkbox
- checked by default on every new run
- checked = C# v`1.0.3` placement mode
- unchecked = C# v`1.0.2` placement mode

## Goals

- Preserve the source add-in's user workflow and logic inside EasyBIM.
- Keep target-local offset semantics based on each target family instance's own orientation.
- Preserve current hosted-family copy behavior without inventing custom rehost logic.
- Make the non-Revit parts of the port testable with plain Python unit tests.

## Non-Goals

- Do not redesign the wizard into a single-window workflow.
- Do not add custom hosted-family rehost logic.
- Do not create `Copy/Monitor` relationships.
- Do not add retagging, batch multi-source selection, or other pyRevit-only feature expansions.
- Do not change repo delivery flow beyond commit and publish to the existing branch target.

## Product Decisions

### Command Location and Branding

- Add a new command at `EasyBIM.tab/Misc Tools.panel/Batch Duplicate Host.pushbutton`.
- Keep the public button title as `Batch Duplicate\nHost`.
- Reuse the source add-in icon branding where practical.

### Wizard Structure

Preserve the source add-in's four-window wizard:

1. `Select Source Element`
2. `Select Categories`
3. `Select Family Types`
4. `Offset`

Behavior rules:

- source step supports `Select` and `Cancel`
- later steps support `Back`, `OK` or `Run`, and `Cancel`
- previous selections and typed values persist when navigating back

### Source Selection

- Source selection is limited to one non-type model or annotation element from the active host project.
- The selection filter must reject element types and null-category elements.
- Allowed source categories are model and annotation categories.

### Target Documents

The target document list must include:

- `Current Project` first
- loaded Revit links after that, sorted by display name

For linked targets:

- use the link document for category, type, and instance discovery
- convert points and vectors back into host-document coordinates before placement

### Target Categories and Types

- Categories are derived only from family instances found in the selected target document.
- Only model categories are listed.
- Family types are grouped by family and sorted case-insensitively.
- Only family types that actually appear on instances in the selected target document are eligible.

### Offset Semantics

The Offset window must use these labels:

- `Hand / Local X`
- `Facing / Local Y`
- `Up / Local Z`

Offset meaning:

- values are interpreted in project length input, but directions come from each target family instance's own local frame
- offset is resolved as `hand * x + facing * y + up * z`
- no fallback to project/global axes is allowed

### Target Local Frame

For each target `FamilyInstance`, compute:

- host-space target point
- host-space local X from `HandOrientation`
- host-space local Y from `FacingOrientation`
- host-space local Z from the cross product of hand and facing, stabilized against the instance transform's Z direction

If the frame cannot be resolved:

- skip that target
- report the skip clearly in the final summary

### Align Orientation Toggle

The Offset window adds one new checkbox:

- label: `Align Orientation`
- always visible
- checked by default on every new run

Behavior:

- checked = C# v`1.0.3` behavior
- unchecked = C# v`1.0.2` behavior

#### Checked Mode

When `Align Orientation` is checked:

- for model elements, attempt source-family-to-target-family frame alignment using a full copy transform
- if the source element is a `FamilyInstance` with usable `HandOrientation` and `FacingOrientation`, align the copied element to the target frame
- if the source model element does not expose a usable orientation frame, still attempt the normal checked-mode path without silently replacing the user's choice
- native Revit warnings/failures are allowed to surface when aligned hosted copy cannot be satisfied

#### Unchecked Mode

When `Align Orientation` is unchecked:

- use translation-only model copy behavior
- placement still uses target-local offset values for the destination point
- only the orientation-alignment part is disabled

### Hosted Source Behavior

Hosted source families are not treated as true `Copy/Monitor`.

The port must preserve the source add-in's native hosted-copy philosophy:

- no explicit host search
- no custom rehost logic
- no monitoring relationship

Expected behavior:

- Revit attempts native copy at the resolved destination transform
- if Revit can preserve a valid hosted result, it places it
- if Revit cannot satisfy the hosted/aligned copy, its normal warning/failure path is allowed

### Annotation Behavior

- View-specific annotations must only copy within the active view.
- If the selected annotation belongs to a different owner view than the active view, skip and report it.
- Non-view-specific annotations still pass through the model-element copy path as in the source add-in's behavior split.

### Summary Dialog

Show a modal completion summary that reports:

- targets processed
- elements created
- skipped count
- skipped item details up to the existing display cap
- notes up to the existing display cap

The summary structure should remain close to the source add-in rather than being redesigned into a pyRevit console report.

## Architecture

### Command Package

The new command package should contain:

- `bundle.yaml`
- `script.py`
- `icon.png`
- `icon.dark.png`
- `SourceSelectionWindow.xaml`
- `CategorySelectionWindow.xaml`
- `FamilyTypeSelectionWindow.xaml`
- `OffsetWindow.xaml`
- focused helper Python modules in the same command folder

### `script.py`

Responsibilities:

- own the wizard step loop
- open each WPF window
- trigger Revit pick selection after the source step
- preserve and pass state across `Back`
- call the placement service on `Run`
- show the final summary dialog

`script.py` should remain thin and not embed the core catalog or placement logic.

### State / DTO Module

Responsibilities:

- hold wizard state across steps
- represent target documents, categories, family groups, types, target instances, skipped placements, and placement summary
- expose summary text generation that can be unit tested without Revit

### Revit Logic Module

Responsibilities:

- source selection filtering
- target document discovery
- category and family type discovery
- target instance point and frame collection
- unit parsing
- model copy behavior for aligned and unaligned modes
- annotation copy behavior

### UI Module

Responsibilities:

- WPF window classes for the four wizard windows
- populate list and tree controls
- restore previous state on back-navigation
- expose clean return values to the controller

## Data Flow

1. User opens `Batch Duplicate Host`.
2. Source window opens and user chooses `Select`.
3. Revit element pick captures one allowed source element.
4. Categories window loads target documents and derived categories.
5. Family Types window loads grouped family types from the selected categories.
6. Offset window collects target-local X/Y/Z values and the `Align Orientation` flag.
7. Target instances are resolved from the selected types.
8. The placement service computes each target destination point from target-local axes and offset values.
9. The placement service runs aligned or unaligned copy mode depending on the checkbox state.
10. The command shows the completion summary.

## State Persistence

The following state must persist when navigating back:

- selected target document
- selected category ids
- selected family type ids
- raw X offset text
- raw Y offset text
- raw Z offset text
- align-orientation checkbox state

## Test Strategy

Add plain-Python tests for the non-Revit layer in `tests/test_batch_duplicate_host_state.py`.

Required coverage:

- summary text count and truncation behavior
- state restore behavior for selected document, categories, types, and align-orientation flag
- deterministic display/sort behavior for documents, categories, and grouped types

Required implementation rule:

- keep the testable logic in plain Python so the test file can import it without Revit or pyRevit runtime dependencies

## Verification

Before claiming completion:

- run the new plain-Python test file
- run `python -m py_compile` on all new Python files in the command package

Manual Revit verification goals:

- current-project targets place correctly
- linked-model targets place correctly in host coordinates
- opposite-facing targets respond with opposite local offsets
- checked `Align Orientation` matches C# v`1.0.3` behavior
- unchecked `Align Orientation` matches C# v`1.0.2` behavior
- hosted-family native Revit warning behavior is preserved
- annotation owner-view restrictions are preserved
- back-navigation restores prior choices

## Delivery

- implement on local branch `Batch-Duplicate-Host`
- publish using the existing remote target `origin/Batch-Host`
- do not merge to `main` unless explicitly requested later

## Assumptions

- `Publish` means commit and push through the existing `Batch-Host` workflow only.
- `Align Orientation` affects model-element copy orientation behavior, not the target-local offset calculation.
- If checked mode is incompatible with a given source element or hosted situation, native Revit warnings are acceptable and should not be suppressed by custom fallback logic.
