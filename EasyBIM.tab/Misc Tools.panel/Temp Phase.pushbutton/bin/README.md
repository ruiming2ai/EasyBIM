This command-level bin folder is intentionally not used for Temp Phase runtime
deployment. The Temp Phase button and close-recovery hooks are Python-only; no
controller DLL or pyRevit module preload is required. Any ignored DLL left in
this folder is a stale local build artifact and is not part of the EasyBIM
package.

Normal users only need to update EasyBIM and reload pyRevit. The standalone C#
implementation remains under `src/TempPhase` as a rollback fallback.
