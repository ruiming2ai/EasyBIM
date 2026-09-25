# e-transmit 2.1.6 — unmodified cloud host saved-edition fallback

Greek Theater run ET_20260925_140947 used 2.1.5 and failed before host copying
or dependency inspection. The report identified one native Revit 2024 cache
candidate for the host's cloud project/model GUIDs. Its saved revision was
94a34fa7-0fad-4319-b92a-fa10582453bc, while the loaded document reported
60d1db2f-e83f-47cf-bd85-bebe4ed04391. Both save counts were 87. IsModified was false.

2.1.5 allowed a saved edition different from the loaded revision only for links
or modified primary hosts. That restriction was inconsistent with saved-cache
export: an unmodified cloud host could fail despite an unambiguous saved file.

## Correction

Cloud hosts with known loaded revisions now use the same exact-match-first,
unambiguous-saved-edition fallback regardless of IsModified. Equal save counts
alone never establish a revision match. A mismatch is explicitly labeled
SAVED_CACHE_DIFFERS_FROM_LOADED and generates a warning showing both revisions.
This also permits a unique saved edition with a different save count; the
exporter does not claim it is the live document's revision or the latest ACC state.

The existing snapshot scanner inspects the acquired saved RVT rather than reusing
inventory from the different live revision. The 2.1.5 same-edition paired-cache
rule, genuine ambiguity rejection, byte-integrity and source-stability checks
remain. Local non-cloud file acquisition rules are unchanged. Working documents
and caches are not saved, synchronized, reloaded or modified.

## Regression coverage

The new fixture reproduces Greek Theater's exact revision GUIDs/save count and
unmodified state. Additional checks cover exact revision precedence, ambiguity,
and the real Registry -> SessionBackend -> engine path collecting an unloaded
ACC link from the saved host inventory into Links/Revit. API operations are
mocked; actual company RVTs are not available on the development machine.

See e-transmit-validation.txt for executed platform results. Real Revit 2024
exports, local repathing, load states, placement, annotations and reopening after
relocation still require workstation acceptance, including Greek Theater,
Morrison and Anthropology. Successful filesystem copying does not prove a
portable Revit package.
