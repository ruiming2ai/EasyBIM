# EasyBIM e-transmit 2.1.25 — normal independent hosts and installation provenance

Final host copies are saved as independent models at their package locations.
Workshared hosts retain worksets unless **Disable worksets** is selected, use a
package central, and finish with transmission marking cleared so they open
normally. This finalization also applies to primary hosts when Repath,
Cleanup and Upgrade are OFF; Repath OFF preserves original reference paths and
load intent.

## References are saved into the host

The workflow still acquires the identified saved host/cache edition and copies
dependencies first. Native file references are initially directed to those
packaged files. The disposable Revit worker opens only the owned copy, establishes
the package location, and repairs supported PDFs/images and ACC links when Repath
is enabled. Native relative references are then materialized by opening and
saving the package copy again. Final readback checks the real last-saved reference
data, requested load intent, central location and cleared transmitted flag.

TransmissionData desired paths require a transmitted flag to be consumed by
Revit; a metadata-only desired-path check cannot establish the saved model state.
In this workflow transmission marking is temporary input to processing and is
cleared during the independent save. See
[Autodesk's TransmissionData documentation](https://help.autodesk.com/cloudhelp/2026/ENU/Revit-API-MainReference/files/html/d78d1e9c-1cee-1336-88d5-b605dacd077d.htm).

The **Simple copy and repath** checkbox is removed. Old settings for that choice
are ignored and cannot bypass finalization. Its internal legacy option remains a
source-discovery optimization, not an output mode. Cleanup and Upgrade remain
opt-in; an older host requiring a save in a newer Revit still needs authorized
upgrade processing.

## Failure and recovery

With Repath enabled, missing linked RVTs block normal host finalization rather than letting the worker
fall back to source/cloud references. Failed or cancelled finalization retains
the collected baseline for recovery and reports the run as incomplete. Recovery
files must not be treated as finished independent hosts. Directly collected
linked RVTs retain their acquired bytes; the primary host is the finalized model.

## Identify the running installation

The UI records `ET_INSTALLATION_PROVENANCE` before export, and `manifest.json`
contains `runtime_provenance`: version, installation root, loaded module path,
button script path and local Git HEAD commit when available. Hash lookup reads
bounded local Git metadata, including worktree metadata, without invoking Git or
contacting a remote. ZIP/non-Git installs leave the commit empty.

## Validation limit

`SAVED_REFERENCES_CHECKED` checks persisted paths/load intent and final host state.
It is not a desktop Revit loading test. Desktop acceptance has not been performed:
relocate the complete package, open the finalized host normally, and inspect
Manage Links, unloaded intent, PDF settings, link placement and annotations.
