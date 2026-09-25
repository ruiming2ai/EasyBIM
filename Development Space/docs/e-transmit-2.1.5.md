# e-transmit 2.1.5 — paired ACC cache selection

The uploaded Morrison 2.1.4 report stopped before host acquisition and link
inspection. Its direct cache and LinkedModels files had the same saved revision
GUID (337646cd-1395-4c60-a8ee-f3974048aa53) and save count (250), but different
SHA-256 checksums. The loaded modified document had another revision GUID.
The old selector treated every byte difference as an unresolved conflict.

## Selection and safeguards

Exact loaded revision matches still take precedence. Among eligible candidates,
the selector recognizes the same root/account/project/model's direct file and
LinkedModels copy. If their complete DocumentVersion and Revit format agree and
each role contains only one byte variant, the direct copy wins deterministically.
This rule applies equally to hosts and links. It does not assert byte equivalence
or explain why the two files differ, and does not prove either opens in Revit.

Different editions without an exact match, different roots/accounts with differing
bytes, unknown layouts and conflicting copies within one role remain ambiguous.
No timestamps, filenames alone or published downloads choose a replacement.
Native-file, source-stability, identity, version and copy-checksum checks remain.
Repeated acquisition reuses the retained snapshot only after candidate selection
and validation; tampered retained snapshots fail. The engine still rejects
unexplained conflicting bytes for the same acquired identity.

Dependency inspection uses the acquired saved edition when it differs from the
loaded document. Unsaved edits remain excluded. Working models and source caches
are never saved, synchronized, reloaded, repaired or cleared.

## Output

REPORT.txt, START_HERE.txt and DIAGNOSTICS.txt include host candidate roles,
revisions, checksums, selection/rejection evidence and selection rationale.
The manifest adds link_discovery_status, cache role/scope, selection reason and
per-candidate selection evidence. Failed acquisition before inspection explicitly
reports NOT_PERFORMED; numeric link counts remain counts of discovered references.
No settings migration or UI changes are required.

The three layouts and single Links tree remain as in 2.1.4. Snapshot reuse does
not merge unrelated files or weaken the engine's source-identity checks. Temporary
processing and final-location Revit verification remain separate.

## Validation

See e-transmit-validation.txt for executed platform suites. Regression fixtures
reproduce the uploaded metadata and differing cache bytes, not the actual RVTs.
Revit 2024 Morrison/Anthropology exports, relocation, link states, placement and
annotations require the workstation checks in e-transmit-desktop-checklist.md.
