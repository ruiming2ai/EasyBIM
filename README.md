# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase recovery (Revit 2025-2026)

Temp Phase is a Python-only pyRevit workflow. The button applies the selected
phase and records the original view state in the EasyBIM session. The Python
`doc-closing`, `app-idling`, and `doc-closed` hooks cancel a supported close,
restore tracked phases and any other discoverable Temporary View Properties,
ask whether to continue, and repost Revit's normal close command when safe.

Normal users only need to update EasyBIM from GitHub and reload pyRevit. No
DLL staging, build step, or cache clearing is required. The standalone C# add-in
and its source remain installed as a fallback until Revit 2025 and 2026 runtime
parity is proven.

Diagnostics are written to
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log`. Look for
`DocClosingHookEntry`, `DocClosingCancelSucceeded`, `TempPhaseRestoreCommitted`,
and `TempPhaseCloseReposted` when verifying the workflow. The C# controller
projects under `src/TempPhase` are developer-only rollback assets and are not
loaded by the normal pyRevit command or hooks.
