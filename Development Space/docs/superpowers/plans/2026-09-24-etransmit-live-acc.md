# e-transmit 2.1 Implementation Plan

**Goal:** Implement the user-approved active-document, authenticated cloud, per-model ZIP, and plugin-source redesign without substituting unverified model versions.
**Architecture:** Keep the existing file/package engine and Revit copy backend. Add a per-run source registry, a read-only APS client, and a session backend that inventories open documents before acquiring host bytes. Real Revit operations stay on the command thread; batch jobs isolate their output and errors.
**Tech Stack:** pyRevit IronPython 2.7, Python standard library/.NET networking, WPF, Revit 2023+ API, APS OAuth v2 PKCE/data management/RCM linked files, unittest on Linux/Windows and actual Windows IronPython.
**Spec:** User-approved `EasyBIM_eTransmit_209_review.md` and the approval to implement, including optional Autodesk sign-in and runtime save/snapshot confirmation.

## Global constraints
- Keep original basenames and folder mirrors. No basename/latest/WIP substitution for cloud resources.
- Never save/synchronize/publish originals silently. Never SaveAs or Close a linked document.
- Live inventory is distinct from serialization of unsaved state; unavailable host snapshots must not block collection of already-discovered materials or be called complete.
- Credentials and signed URLs are session-only and never written to package logs or repository fixtures.
- Core file copies retain checksum verification. Imported images stay embedded. Existing link elements are not recreated.
- Do not modify any other EasyBIM buttons, panel layouts, hooks or startup scripts.

## Review focus
- Two documents with the same element IDs and file basenames cannot collide in a combined package.
- ACC API links omitted due to permissions remain missing in the expected-inventory comparison.
- Current open cloud revision may differ from the selected published revision; require a revision comparison or an explicitly labeled published-mode choice.
- A refused/failed live snapshot must still deliver the dependency inventory and must not delete a working document's new saved file.
- OAuth callback/redirects, API pagination and downloads cannot leak bearer credentials or escape package roots.

## Tasks and evidence gates
1. Safety regressions: aliases keyed by document/source; embedded images excluded on every inventory path; deferred verification remains DEFERRED. Run the existing suite, write the failing tests, change production code, run all tests.
2. Batch runner: `run_batch(models, root, backend_factory, options, extras, cancelled, pulse)` with failure isolation and `zip_per_model` forcing separate packages. One atomic, CRC-verified ZIP per result with both Sources and _Refs. Test independent outputs, cancellation, failed job followed by successful job, archive publication failure, duplicate basenames.
3. APS networking/authentication: `ApiClient.get_json`, `download`, OAuth PKCE loopback flow, memory-only token refresh. Read-only Data Management browser and exact published-version RCM requests; paginated linkedFiles, safe filenames, host identity validation and expected-link diagnostics. Use official response contracts, injected transports in tests, and RFC PKCE vectors; never call user cloud services from CI.
4. Session source registry: retain Document handles outside JSON, snapshot live inventories once, read loaded links without saving/reopening them, capture named resource and cloud identities. Saved-file, live-state and explicitly selected published-version modes remain separate. Snapshot original only after per-document runtime confirmation into a persistent recovery folder, never silently revert/save again. Engine accepts only registry-owned virtual source IDs, queues pre-inventoried references even if host acquisition fails, uses live inventory when available, and preserves file-based compatibility.
5. Plugin spreadsheet discovery: bounded readable vendor-associated Extensible Storage and external-source field inspection; provenance-only workbook records, read-denied/unsupported coverage diagnostics, no workbook execution or private storage modification. Test nested readable fields, JSON/XML path containers, duplicate references, false-positive text rejection, unreadable schemas and cancellation.
6. WPF integration: selected live document defaults to current state, new published-ACC picker and sign-in (client ID only), explicit snapshot confirmation, individual ZIP checkbox and plugin-source checkbox. The batch runner consumes mixed selections. Add tests for event wiring, source modes, no forced saved path for live inventory, and memory-only credentials.
7. Full regression/compatibility runs, package documentation and whole-diff self-review. Push isolated branch, run GitHub Windows/Linux/IronPython CI, verify final files and merge to main without force. Actual licensed-Revit and company ACC tests are explicitly not represented by CI.

## Rulings
- User explicitly approved the review/design and said implement, so proceed inline without another approval gate.
- No authenticated APS app registration or licensed Revit runtime is available here. Implement usable optional sign-in/configuration and public API contracts; installation instructions must disclose one-time APS native-app registration/provisioning, rather than embedding credentials or claiming live ACC validation.
- Do not implement parallel Revit threads. A batch means several selected jobs with safe sequential Revit API calls. Per-job ZIP is included; multiprocess Revit automation is outside this approved change.
