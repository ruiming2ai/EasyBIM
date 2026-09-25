# e-transmit 2.1.1 host-priority repair

Goal: serialize the current open host, preserve its snapshot independently of dependency errors, and name per-model packages/ZIPs after the model. ACC link acquisition is not expanded.

Evidence: IMG_2576 records HOST_SNAPSHOT_DECLINED and 1/2 hosts, not a Revit SaveAs exception. The old UI confirms Continue before discovery, then asks per-host save permission during collection; a refusal generates a material-only package. No current manifest for ET_20260924_185120 was found in the connected report folder. Do not attribute the other 35 issues to causes not in the screenshot.

Implementation:
1. Add failing tests for model-name job planning (duplicate names/case/ZIP collision), then share that plan with UI preflight and batch archives.
2. Retain a lightweight live document registration. Capture the host before optional live/plugin/link inventory; preserve dependency inventory even if a capture fails. Native SaveAs, not an APS download, creates live host bytes. Permit only explicitly authorized fresh save destinations; no write to original, Sync, Publish, Save/Close of loaded link documents, or automatic restore.
3. Consolidate save consent into the initial dialog. Never start a batch that the user declined to serialize. A caller without authorization still fails closed. Snapshot errors retain their real exception details; freshly saved metadata must match the current DocumentVersion and the document must be clean. A post-save event exception is recoverable only with this proof.
4. Preserve a checksummed HostSnapshot/<model>.rvt. Default preserve-host mode leaves the primary live host bytes unchanged and skips repath/cleanup of that host. Copy materials separately. ACC link acquisition can be explicitly skipped without dropping its existing link element from the host. Opening verification is distinct from snapshot/hash verification.
5. Improve result ordering: host failures before optional plugin coverage. No unrelated button/hook/startup edits. Full portable tests and actual Windows IronPython CI before main merge.

Limits: Native SaveAs can change the working document path; disclose before consent and report after. No undocumented Rename flag, silent source overwrite, cache/published fallback, or untested claim that the live Revit session stays at its prior file location. Revit desktop validation remains required.
