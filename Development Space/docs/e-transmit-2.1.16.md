# EasyBIM e-transmit 2.1.16 — clearer detached recovery and progress placement

## Scope

2.1.16 makes two UI refinements after the detached-model recovery work in
2.1.15.

## Detached recovery wording

The recovery action no longer uses the ambiguous phrase "Browse another RVT".

The choices now use file-location language:

- **Option 1 — Save current detached model and transmit**
- **Option 2 — Load model from file location…**

If the selected model does not match the open detached model, the retry action is:

- **Load another model from file location…**
- **Cancel**

The model-identity verification and wrong-model protection from 2.1.15 are
unchanged.

## Progress placement

The Revit-native dockable progress pane remains the preferred surface. It is
registered at startup with `DockPosition.Top`, which lets Revit reserve layout
space for the pane.

The pyRevit progress overlay is still a fallback for sessions where the
dockable pane cannot be used (for example, after updating/reloading the
extension without restarting Revit). The fallback now:

- detects the lower edge of the currently visible Revit ribbon through the
  loaded Revit UI object;
- converts the ribbon's screen coordinate back to WPF device-independent units;
- positions the thin progress strip immediately below the ribbon instead of at
  the top edge of the Revit window;
- retains the existing opacity, phase colors and Cancel behavior;
- falls back to the previous top offset if the ribbon geometry cannot be read.

The registered progress-panel instance is also cached on the persistent pyRevit
forms module so a later EasyBIM module reload can rebind to the already
registered pane instead of unnecessarily switching to the overlay.

## Safety

No model-processing behavior changed in this release.
