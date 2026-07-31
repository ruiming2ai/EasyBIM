# EasyBIM Temp Phase controller

This source is the pyRevit 6.5 conversion of the standalone Temp Phase add-in.
It targets Revit 2025 and 2026 separately because Revit API assemblies must not
be mixed in one Revit process.

Normal users do not build or stage anything. EasyBIM ships the supported
controller DLLs in the extension root `bin` folder:

    bin\TempPhaseController.Revit2025.dll
    bin\TempPhaseController.Revit2026.dll

The command and C# hooks select `TempPhaseController.Revit{VersionNumber}` at
runtime, so users only need to update EasyBIM and reload pyRevit. The command
bundle does not declare a mutable `modules:` entry.

Release maintainers can rebuild and verify the packaged DLLs from the EasyBIM
repository:

    .\build\Build-TempPhase.ps1
    .\build\Build-TempPhase.ps1 -VerifyOnly

The release script refuses to package wrong-year assemblies or RevitAPI*.dll
files. Revit API references remain build-only dependencies.

## Blank command recovery

If Revit still executes an older wrapper after update and reload, clear the
generated pyRevit extension assembly/cache for the affected Revit year once, for
example the `pyRevit_2025_*_EasyBIM.dll` files under `%APPDATA%\pyRevit\2025`.
The command writes diagnostics to
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log` before loading the controller,
before and after the phase picker, and after each transaction. A missing or
wrong-year module now produces a Revit TaskDialog with the expected deployment
path instead of a blank pyRevit output window.

Keep the standalone add-in enabled until both Revit 2025 and 2026 pass the
close-recovery acceptance tests.
