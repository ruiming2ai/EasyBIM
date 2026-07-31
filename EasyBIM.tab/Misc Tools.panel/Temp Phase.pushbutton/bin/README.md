This folder receives the host-specific TempPhaseController.dll during packaging.

Build TempPhaseController.Revit2025.dll or TempPhaseController.Revit2026.dll, then
copy the selected artifact to this folder as TempPhaseController.dll for the matching
Revit installation. Prefer the deployment script so the live bundle and assembly
metadata are preflighted:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025 `
        -ExtensionRoot "C:\path\to\EasyBIM.extension"

Do not place both Revit API versions in one live pyRevit extension. The DLL is
generated and gitignored; it must exist in the exact extension copy discovered by
pyRevit after a fresh checkout.
