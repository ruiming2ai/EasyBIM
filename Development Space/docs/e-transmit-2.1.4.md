# e-transmit 2.1.4 — one Links folder

Each selected host has an independent model-named package with its original-named
RVT at the root, reports, and a single `Links` folder. A dependency used repeatedly
is delivered once within that package. Separate host packages remain independent,
so a common dependency is included in each host package that needs it.

## File Structure Organization

The new dropdown is saved with the existing collection preferences:

| Selection | Package paths |
|---|---|
| **By category** (default) | `Links/Revit`, `Links/CAD`, `Links/IFC`, `Links/PDF`, `Links/Images`, `Links/Point Cloud`, etc. |
| Retain original folder structure | Original source hierarchy beneath `Links`; no second source mirror. |
| All files together | Files directly in `Links`, without category folders. |

Other category folders are `Navisworks`, `DWF`, `Keynotes`, `Analysis`,
`Spreadsheets`, and `Other`. Decals use `Images`. Empty categories are not created.
The programmatic/settings option `file_structure` accepts `categories`, `original`,
and `flat`; missing or invalid values use `categories`. Batch exports always use
independent host packages, including when old settings requested combined output.

Different files sharing a case-insensitive destination name each get an additional
source-parent folder with a stable identity suffix. Their filenames are unchanged.
Destination assignment is independent of discovery order. Equal file contents
alone do not merge unrelated sources. Verified aliases for the same source/edition
share one delivered target and retain their original reference-element diagnostics.

Required file sets remain together in every mode. An RCP remains beside its
original-named Support folder; explicitly added folders and analysis-report trees
retain their internal paths. Overlapping added directories and directly referenced
members are collected once. These preserved support trees are exceptions to flattening.

## Temporary preparation and final delivery

Acquisition and dependency inspection first use a task-owned local temporary area.
The selected folder layout is then prepared there, and exported models are processed
with package-relative references. Original acquired bytes remain available for
rollback outside the deliverable. Working documents and source caches are unchanged.

Completed files are copied to the final destination with checksum verification.
No generated `Sources`, `_Refs`, or `_HostState` folder is delivered or archived.
There is no duplicate short-path PDF/image alias and no filename shortening.

A long final path does not by itself prevent filesystem collection. Revit may still
reject the chosen hierarchy, even in temporary preparation, or reject the final
location. The files remain available and the report records failed/deferred model
verification, with advice to move the complete package to a shorter location where
appropriate. Temporary verification does not certify the final path. This release
does not claim to remove Revit's path-length restrictions.

Temporary preparation is deleted after successful delivery/reporting. Failed
delivery retains its recovery directory; failed rollback retains its original copy.
The report identifies these locations. Cancellation retains completed delivered
files and reports any undelivered recovery files.

ZIPs are prepared and CRC-checked at a short temporary path before verified delivery.
When both per-model and whole-batch ZIP options are selected, the whole-batch ZIP
contains package folders and reports, excluding the generated individual ZIPs.

## Reports and compatibility

Existing file/reference tables retain their source and final `target`/`relative`
fields. File rows add `original_relative`, `layout_identity`, `source_aliases` when
present, `collision_separated`, `preparation_verification`, and `delivery_status`.
`model_verification` always describes the final delivered location. Reference rows
record preparation verification separately from final `verification`. The legacy
manifest `aliases` list remains empty for reader compatibility.

`Load Unloaded Files`, saved-cache acquisition, upgrade/cleanup choices, source
integrity checks, and missing-link diagnostics remain available. Repath OFF collects
and organizes files without claiming repaired references. CAD-internal dependencies,
external point-cloud scans, and private plugin references remain reported limitations.

## Validation

See `e-transmit-validation.txt` for executed Linux, Windows and IronPython results.
Regressions cover three layouts, preference migration, identity-based deduplication,
case-insensitive collisions, nested/overlapping support trees, temporary/final checks,
long-path reports and ZIP delivery, cancellation, rollback and failed delivery.

No licensed Revit installation or company ACC cache is available on this machine.
Morrison/Anthropology exports, real Revit 2024 reopening and relocation, WPF rendering,
link placement and annotations remain unperformed desktop checks. Follow
`e-transmit-desktop-checklist.md`; automated file/API-shaped checks are not proof of
portable Revit packages.
