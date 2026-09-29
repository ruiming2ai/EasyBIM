# EasyBIM e-transmit 2.1.15 — detached model recovery

## Scope

2.1.15 removes the detached-model dead end that remained in 2.1.14.

When a selected open detached Revit model has no provable saved source path,
eTransmit now asks the user how to proceed instead of requiring them to leave
the tool, save manually, and run it again.

## Recovery dialog

The dialog begins with:

> eTransmit couldn't locate the detached model.
>
> So please make the following decisions to transmit:

Two choices are available.

### Option 1 — Save current detached model and transmit

- The new RVT is saved automatically in the destination already selected in the
  main eTransmit interface. No second destination picker is shown.
- eTransmit asks whether to include the current in-memory changes.
- **Save current changes and transmit** performs an explicit Revit Save As.
- For a workshared detached document, the saved file is created as a new
  workshared central file as required by the Revit Save As API.
- The open Revit document is therefore associated with this newly saved RVT.
- No Sync, Publish, source search, or alternate filename substitution is
  performed.

Revit Save As always writes the current in-memory document state. Therefore,
**Do not include current changes** does not pretend to create a change-free
Save As; it routes to Option 2 so the user can select an existing saved RVT.

### Option 2 — Browse for the existing model

- The user selects the exact existing RVT that the detached model came from.
- The selected file is validated as a native RVT.
- eTransmit verifies that it represents the same model as the open detached
  document before accepting it.
- A matching worksharing central identity is accepted immediately.
- Otherwise eTransmit opens only a task-owned scratch copy, with
  TransmissionData-backed references unloaded, and compares the Project
  Information element UniqueId.
- The user-selected RVT itself is never opened for write, detached, saved, or
  modified.

If the model identity does not match, the file is rejected and the user is
offered **Browse another RVT** or **Cancel**. There is no "Use anyway" bypass.

After either recovery succeeds, the row is converted to the ordinary
`SAVED_FILE` pipeline. This ensures that:

- Option 1 packages the newly saved current state.
- Option 2 packages only the explicitly selected saved state, excluding current
  unsaved edits from the open detached document.
- Repath/Cleanup/Upgrade processing continues to operate only on package/staging
  copies.
- The selected source RVT is never modified.

## Safety boundaries retained

- Automatic source discovery is still attempted first.
- Live ACC workflows are unchanged.
- No filename search, newest-file guessing, or published-version substitution
  was added.
- A wrong browsed model is rejected rather than silently transmitted.
- Normal live-model Save remains in-place only; the new Save As behavior exists
  solely behind the explicit detached-model recovery choice.

## Validation

Portable regression coverage includes:

- explicit Save-current recovery;
- browse-and-verify recovery;
- wrong-RVT rejection followed by browse-again;
- matching worksharing central identity;
- approved recovery-dialog wording and choices;
- the existing collect-only saved-file no-host-open regression.

Real Revit smoke testing is still required for the Save As path and for identity
verification against representative Revit 2024, 2025, and 2026 detached models.
