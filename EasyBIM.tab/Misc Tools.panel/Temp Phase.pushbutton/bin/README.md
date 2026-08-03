This command-level bin folder is intentionally not used for Temp Phase runtime
deployment. The Temp Phase button and close-recovery hooks are Python-only; no
controller DLL or pyRevit module preload is required. Any ignored DLL left in
this folder is a stale local build artifact and is not part of the EasyBIM
package.

Normal users only need to update EasyBIM and reload pyRevit. The former
standalone C# fallback has been removed from the repository; the Python
command and hooks are the only Temp Phase implementation.
