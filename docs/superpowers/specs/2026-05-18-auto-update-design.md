# EasyBIM Auto Update Design

Date: 2026-05-18
Status: Active native pyRevit Update wrapper

## Summary

EasyBIM Auto Update is a convenience wrapper around the native pyRevit `Update` tool.

On each new Revit session, EasyBIM should:

- run once during extension startup
- call pyRevit's native update entry point
- rely on pyRevit for update checks, update output, and reload behavior
- show a concise popup only when native pyRevit Update installs repository changes
- avoid any EasyBIM-owned update engine

The `Auto Update` button under `EasyBIM.tab/Misc Tools.panel` should execute the same native pyRevit update entry point on demand and follow the same popup rule.

## Product Decision

EasyBIM does not implement its own extension updater.

The implementation calls `pyrevit.versionmgr.updater.update_pyrevit()`, matching the native pyRevit `Update.smartbutton` script. EasyBIM keeps only a small session guard so startup auto update does not loop after pyRevit reloads.

EasyBIM may observe pyRevit-managed repo metadata before and after native Update to decide whether to show a popup. It must not perform its own update operation.

## Components

### `startup.py`

- runs when pyRevit loads `EasyBIM.extension`
- checks the session guard
- marks the session before invoking update
- calls `auto_update.run_startup_auto_update()`

### `lib/easybim/auto_update.py`

- owns the once-per-session guard helpers
- exposes `run_startup_auto_update()`
- exposes `run_manual_auto_update()`
- delegates both flows to pyRevit's native updater
- snapshots pyRevit-managed repo heads before and after native Update
- temporarily captures pyRevit reload requests so it can show a popup before handing control back to the native reload

### `Auto Update.pushbutton`

- calls `run_manual_auto_update()`
- does not add custom status dialogs or custom update handling
- shows the same changed-repo popup as startup only when native Update installs changes

## Data Flow

Startup:

1. Revit starts.
2. pyRevit loads EasyBIM.
3. `startup.py` checks whether startup update already ran in this Revit session.
4. If not, it marks the session and calls `run_startup_auto_update()`.
5. EasyBIM calls pyRevit's native update entry point.
6. If native Update requests a reload, EasyBIM compares pre/post repo heads.
7. If repo heads changed, EasyBIM shows the changed-repo popup.
8. EasyBIM calls pyRevit's original reload function.
9. If pyRevit reloads, `startup.py` runs again and exits because the guard is set.

Manual:

1. User clicks EasyBIM `Auto Update`.
2. The button calls `run_manual_auto_update()`.
3. EasyBIM calls pyRevit's native update entry point.
4. If native Update requests a reload, EasyBIM compares pre/post repo heads.
5. If repo heads changed, EasyBIM shows the changed-repo popup.
6. EasyBIM calls pyRevit's original reload function.

Popup body:

```text
pyRevit Update installed changes:

- EasyBIM
- <other changed repo names>

pyRevit is reloading.
```

No popup is shown when no repo hash changed.

## Testing Strategy

- Unit-test the startup guard behavior.
- Unit-test that startup and manual auto update call the native pyRevit update entry point once.
- Unit-test no popup when repo heads match.
- Unit-test one popup listing changed repos when one or more repo heads change.
- Unit-test native reload still runs after the popup.
- Unit-test native reload is restored if native Update raises.
- Unit-test snapshot failure falls back to native Update behavior without a popup.
- Syntax-check `startup.py`, the EasyBIM Auto Update button script, and `lib/easybim/auto_update.py`.

## References

- pyRevit native Update smartbutton source: `extensions/pyRevitCore.extension/pyRevit.tab/pyRevit.panel/Update.smartbutton/script.py`
- pyRevit updater reference: <https://docs.pyrevitlabs.io/reference/pyrevit/versionmgr/updater/>
