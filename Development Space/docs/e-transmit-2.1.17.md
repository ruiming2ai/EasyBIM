# EasyBIM e-transmit 2.1.17 — non-blocking detached file selection and complete link discovery

## Scope

2.1.17 simplifies the manual detached-model recovery introduced in 2.1.15.

When automatic detached-source lookup fails and the user chooses **Load model
from file location…**, that explicit file selection is now treated as
authoritative. eTransmit no longer compares the selected RVT against the open
detached document and no longer blocks the run on a model-identity mismatch.

## Why opening a copy is still useful

A closed RVT exposes ordinary file-based Revit links through
`TransmissionData`, so opening the source model itself is not required for
those references.

However, `TransmissionData` is not a complete inventory of every external
resource. Some Revit/server-managed references are exposed only through APIs
available on an opened Revit document.

For a model explicitly selected through detached recovery, eTransmit therefore:

1. validates that the selected file is a readable native RVT;
2. copies it to EasyBIM-owned staging;
3. reads closed-file `TransmissionData` first;
4. opens only the disposable staging copy for deeper dependency discovery,
   even when Repath, Cleanup and Upgrade are all OFF;
5. detaches that staging copy on open when Revit reports it as workshared;
6. closes the temporary document without saving unless an ordinary package
   processing option later requires a package save.

The user-selected RVT itself is never opened for write, detached, saved,
renamed or modified.

## Manual selection behavior

The retry dialog is now only for an unusable file, such as a missing or invalid
RVT. There is no "selected model does not match" check.

This means the user is responsible for selecting the intended model. eTransmit
does not search for a substitute, pick a newer same-named file, or compare
project identity against the currently open detached document.

## Unchanged behavior

- Automatic detached-source lookup is still attempted first.
- **Save current detached model and transmit** remains available.
- Directly browsed models from the main source list are unchanged unless they
  came from the detached-recovery flow.
- Live ACC behavior is unchanged.
- Repath/Cleanup/Upgrade continue to modify only task-owned package/staging
  copies.
