# EasyBIM e-transmit 2.1.18 — detached live-link recovery

## Scope

2.1.18 fixes missing Revit links when a pathless detached host is recovered with
**Load model from file location…**.

The selected RVT remains the authoritative saved host file, but EasyBIM now
keeps the already-open detached Revit document as the dependency-discovery
context instead of converting that row into a plain closed `SAVED_FILE`.

## Why this is necessary

Ordinary file/server Revit links can usually be collected from closed-file
`TransmissionData` because it exposes a directly copyable path.

ACC/cloud Revit links are different. The saved host can expose project/model
identity without exposing one directly copyable RVT. Revit often must load the
link in the current session so its linked Document and exact CollaborationCache
revision become available.

## Detached file-location flow

When the user chooses **Load model from file location…**:

1. The selected RVT is validated as a readable native Revit file.
2. That path becomes the saved host source to transmit.
3. The open detached document is retained only for Revit-link discovery.
4. Unsaved changes in that detached document are not saved or transmitted.
5. Saved `TransmissionData` Revit-link rows remain authoritative for ordinary
   file links, but live ACC/server external-resource rows are merged back when
   they are missing from `TransmissionData`.
6. Loaded Revit links are matched to their live linked Documents.
7. Unloaded Revit links are shown in the existing temporary-reload dialog.
8. ACC/cloud links are preselected because their saved cache commonly requires
   Revit to load the link first.
9. When the user approves **Reload selected and continue**, EasyBIM:
   - snapshots the selected host saved state before any reload;
   - temporarily reloads the selected link in the open detached document;
   - captures the linked Document identity and exact saved local cloud-cache
     revision;
   - restores the link to its original unloaded/local-unloaded state;
   - packages the captured RVT as a linked dependency.
10. No source Save or Sync follows the temporary reload.

The existing warning remains: Revit Reload can affect Undo history and the
working document's modified flag even though the original unloaded state is
restored.

## Server/file-path links

A directly addressable local/network/server RVT link is not preselected for
reload. If its saved path exists, EasyBIM can collect it directly from
`TransmissionData`.

## Safety boundaries

- The user-selected host RVT is not modified.
- The detached working document is not SaveAs'd by the Browse recovery path.
- Unsaved host edits are excluded.
- ACC/cache RVTs are copied read-only into EasyBIM-owned snapshots before
  packaging.
- Original unloaded links are restored after temporary reload.
- The user can uncheck any preselected cloud link or choose
  **Continue without reloading**.
- No filename guessing or detached-host identity mismatch block is reintroduced.
