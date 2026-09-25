# e-transmit 2.1.2 — approved saved-cache export

User ruling: unsaved edits need not be included. Do not Save, SaveAs, Sync,
Publish, Reload, Close or relocate any original/open Document. Use an exact local
saved cache for the selected host and loaded links; preserve model-named jobs.
GitHub publication to main is explicitly authorized.

1. Reconcile the unpublished 2.1.1 host-preservation and model-folder changes with
   main 20b9da5. Preserve unrelated tabs/tools/hooks and all project data locally.
2. Add read-only cache root discovery (current Revit release; Revit.ini override),
   exact cloud project/model GUID lookup, exclusion of CentralCache/PacCache and
   reparse points, stable copy/hashes and metadata/version validation on copies.
3. Use saved-state acquisition by default for actual open Documents. Loaded links
   require the loaded edition (GUID and save count). Modified primary hosts may
   use one uniquely identified saved state, with explicit unsaved-state exclusion.
   Never select by latest timestamp, display filename or published version.
4. Keep a raw _HostState baseline. Inspect/detach/repath only disposable/package
   copies, with separate copied/validated/normalized/verified status. Use saved
   inventory for a modified host, not its unsaved reference edits. Retain a host
   when optional plugin or linked-file processing fails.
5. Remove SaveAs prompts and skip-cloud-cache defaults from the standard UI. Keep
   existing explicit APS published mode separate. Model folder/ZIP names persist.
6. Regression-first tests for discovery, candidate rejection, read-only behavior,
   cancellation, changing files, duplicate caches, version mismatches, metadata
   failure, unsaved primary state, dependent links, and batch names. Run portable
   suite, Windows/IronPython CI, review final diff and merge without force.

Acceptance limitation: CI cannot open licensed Revit or a real CollaborationCache.
A version/readability failure must be reported and never turned into success.
We will not clear caches, synthesize RVT internals or use newer published models.

Local evidence ledger:
- 244-test unpublished 2.1.1 baseline passed before cache changes.
- Cache discovery/copy tests were observed failing before the new module existed.
- Session/UI regressions were observed failing on the prior SaveAs route.
- Whole-branch review found three additional boundaries: cloud identity was lost
  during saved-path conversion; an unexpected working Document could be returned
  by OpenDocumentFile; saved-cache reports still used legacy SaveAs language.
  Added failing tests, corrected each and reran the suite.
- 284 portable tests pass locally (two platform-specific skips). Windows and
  real IronPython jobs remain required before merge; they are not Revit tests.
- The full original-file cache guard stays enabled for every generic copy entry
  point. Only the dedicated identity/version-bound cache acquisition reads caches.
