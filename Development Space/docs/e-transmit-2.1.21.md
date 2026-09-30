# EasyBIM e-transmit 2.1.21 — report-driven linked-cache recovery

## What the supplied report actually showed

The 2.1.20 report had Repath enabled, but no repath operation ran. Four Revit
links were requested; three were acquired. One loaded architectural dependency
had two valid cache candidates in the same account/project scope:

- DIRECT: NumberOfSaves 4869.
- LinkedModels: NumberOfSaves 4954, matching the loaded link's counter.
- Both cache VersionGUIDs differed from the loaded document's VersionGUID.

The old cache selector rejected the different-edition pair. The missing RVT then
triggered HOST_PRESERVED_LINKS_UNAVAILABLE, deliberately skipping the entire
host's repath/finalization, including its successfully copied CAD and PDF files.
Marking the host transmitted still ran; that is not a link-path repair.

The CAD/PDF rows in this report already had loaded=True. The earlier fix for
missing load-state flags was not the cause of this reported failure.

## Narrow acquisition change

A new saved-edition fallback is available only for a linked-source entry with
valid loaded DocumentVersion metadata, when all of the following hold:

1. No exact loaded revision match was found.
2. Exactly two validated candidates remain: one DIRECT and one LINKED_MODELS.
3. Both belong to the same nonempty account/project/root scope and Revit format.
4. The LinkedModels candidate's save count equals the loaded link's save count.
5. The DIRECT candidate's save count is strictly older.

In that case the tool acquires the saved LinkedModels RVT and records
LINKED_MODELS_LOADED_SAVE_COUNT as the selection reason. It retains both actual
and loaded version identities and SAVED_CACHE_DIFFERS_FROM_LOADED. A matching
counter is NOT an exact revision match; selection_note explicitly records this.

There is no filename search, newest-timestamp selection, cloud download, source
save, or cache write. Existing exact-match and same-edition selection rules are
unchanged. Primary hosts cannot use this new linked-source fallback.

Different accounts/roots, unknown layouts, conflicting formats, duplicate roles,
a newer DIRECT edition, or indistinguishable counters remain ambiguous. Missing
or genuinely ambiguous dependencies still prevent unsafe host opening. This
release does not remove that safety gate or silently deliver partial repairs.

## Validation and its boundary

Twenty new regression tests cover actual native-payload copying, cache
selection, source immutability, refusal cases, and the real Registry,
SessionBackend and packaging engine. An anonymized mixed-reference reproduction
runs as both an ACC-live and a detached host, with four Revit links, two CAD links
and a PDF. Before the change it reproduces three acquired links and blocked
finalization; afterward all four are acquired and finalization receives every
packaged target with its intended loaded/unloaded state.

BasicFileInfo and final Revit writes in these tests are substituted boundaries,
not execution inside Revit. Existing API-shaped CAD/PDF/Revit tests also pass.
The supplied new report contains an ACC-live host; the detached path is covered
by the shared-code regression, not a second supplied native run.

No Revit API repath implementation or Python runtime was changed in this release.
Native acceptance still requires running e-transmit and opening the final
package in Revit, checking both paths and loaded states. A cache-related warning
may remain because the selected saved edition is not an exact loaded revision.
