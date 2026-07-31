# PyRevit_EasyBIM
PyRevit Tools created by Ruiming Liu

## Temp Phase recovery (Revit 2025-2026)

The Temp Phase command is implemented as a pyRevit 6.5 C# command and three
typed hooks. The hooks cancel a close only when temporary view properties need
restoration, defer the transaction to app-idling, prompt the user, and repost
the typed PostableCommand.Close command when requested.

Build and stage the controller for the Revit installation being tested:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025 `
        -ExtensionRoot "C:\Users\RML\Documents\GitHub\EasyBIM.extension"
    .\build\Build-TempPhase.ps1 -RevitVersion 2026 `
        -ExtensionRoot "C:\Users\RML\Documents\GitHub\EasyBIM.extension"

When the live extension is the WSL-backed clone, use `-ArtifactPath` with the
freshly built versioned DLL. This stages into the exact path pyRevit loads:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025 `
        -ExtensionRoot "\\wsl.localhost\Ubuntu\home\rml\repos\EasyBIM" `
        -ArtifactPath "\\wsl.localhost\Ubuntu\home\rml\repos\EasyBIM\src\TempPhase\TempPhase.Revit2025\bin\Release\net8.0-windows\TempPhaseController.Revit2025.dll"

Only the matching TempPhaseController.dll should be present in the live
pyRevit extension. The build preflight verifies the live bundle, controller
assembly identity, and absence of Revit API DLLs beside the module. Because the
controller DLL is generated and gitignored, the exact extension copy discovered
by pyRevit must be supplied when it differs from this checkout.

If the button opens a blank pyRevit window, close Revit, clear that year's
generated pyRevit extension assembly/cache, rebuild and stage the matching
module, then reload pyRevit. The command logs its Revit version, loaded
extension/controller paths, loader/type-load failures, picker events, and
transaction completion at `%APPDATA%\EasyBIM\Temp Phase\logs\events.log` and
shows a Revit TaskDialog for missing or wrong-year modules. The original
standalone add-in remains the fallback until runtime parity is verified in both
Revit versions.
