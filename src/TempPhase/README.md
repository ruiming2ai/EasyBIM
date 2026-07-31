# EasyBIM Temp Phase controller

This source is the pyRevit 6.5 conversion of the standalone Temp Phase add-in.
It targets Revit 2025 and 2026 separately because Revit API assemblies must not
be mixed in one Revit process.

Build and stage one host-specific module from the repository root. When pyRevit
loads a Windows extension clone, pass that clone explicitly:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025 `
        -ExtensionRoot "C:\Users\RML\Documents\GitHub\EasyBIM.extension"
    .\build\Build-TempPhase.ps1 -RevitVersion 2026 `
        -ExtensionRoot "C:\Users\RML\Documents\GitHub\EasyBIM.extension"

The build produces a versioned controller artifact and stages the selected host
version as TempPhaseController.dll beside the pyRevit command. Only the staged
artifact for the active Revit installation should be present in a live extension.
The preflight refuses to stage when bundle.yaml or script.cs is missing, when
the assembly identity is for the wrong Revit year, or when RevitAPI*.dll files
are beside the controller. The generated DLL is intentionally gitignored, so a
fresh clone must be built and staged before pyRevit can load the command.

The controller is loaded by the Temp Phase command bundle's modules metadata.
The C# hooks in hooks/ delegate into the same static controller instance so
manual-command sessions and close recovery share state.

## Blank command recovery

Close all Revit processes before changing the deployed module. Clear the
generated pyRevit extension assembly/cache for the affected Revit year (for
example, the `pyRevit_2025_*_EasyBIM.dll` files under
`%APPDATA%\pyRevit\2025`), build the matching controller, stage it into the
exact extension root pyRevit has discovered, and restart or reload pyRevit. The
command writes diagnostics to
`%APPDATA%\EasyBIM\Temp Phase\logs\events.log` before loading the controller,
before and after the phase picker, and after each transaction. A missing or
wrong-year module now produces a Revit TaskDialog with the expected deployment
path instead of a blank pyRevit output window.

Use `-NoStage` when only a build is needed. Keep the standalone add-in enabled
until both Revit 2025 and 2026 pass the close-recovery acceptance tests.
