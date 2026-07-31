# EasyBIM Temp Phase controller

This source is the pyRevit 6.5 conversion of the standalone Temp Phase add-in.
It targets Revit 2025 and 2026 separately because Revit API assemblies must not
be mixed in one Revit process.

Build and stage one host-specific module from the repository root:

    .\build\Build-TempPhase.ps1 -RevitVersion 2025
    .\build\Build-TempPhase.ps1 -RevitVersion 2026

The build produces a versioned controller artifact and stages the selected host
version as TempPhaseController.dll beside the pyRevit command. Only the staged
artifact for the active Revit installation should be present in a live extension.

The controller is loaded by the Temp Phase command bundle's modules metadata.
The C# hooks in hooks/ delegate into the same static controller instance so
manual-command sessions and close recovery share state.
