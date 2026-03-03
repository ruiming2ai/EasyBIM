# AGENTS Guide

## Project Snapshot

- Project name: RL_Tools.extension
- Status: Active pyRevit extension development on `main`
- Primary goal: Maintain and expand RL_Tools pyRevit commands/hooks with safe workflow governance
- Primary stack: Python (pyRevit/Revit API), XAML for command UI

## Current Workflow Summary

- Work inside existing pyRevit folder conventions (`*.panel`, `*.pushbutton`, `*.pulldown`).
- Keep command scripts focused and consistent with existing import/transaction patterns.
- Validate Python syntax for changed scripts before handoff (`python -m py_compile <script.py>`).
- Update command icons/bundles only when behavior or UX changes require it.
- Keep repo-level workflow decisions documented in `AGENTS.md` and mirrored in the repo parent skill.

## Commands

- Python syntax check for one file:
  - `python -m py_compile "<path-to-script.py>"`
- Compile many command scripts:
  - `python -m compileall "RL_Tools.tab"`

## Conventions

- Keep edits narrow and feature-scoped.
- Follow existing command naming and folder structure.
- Prefer shared helpers under `lib/` for repeated logic.
- Keep UI labels and command behavior aligned.

## Decision Log

| Date | Decision | Why | Impact | Status |
|------|----------|-----|--------|--------|
| 2026-03-02 | Standardize repository workflow source on `AGENTS.md` | Align global hub + parent skill governance | Enables reliable drift checks and skill maintenance | Active |

## Session Handoff Log

| Date | What Changed | Files Touched | Checks Run | Next Step |
|------|---------------|---------------|------------|-----------|
| 2026-03-02 | Created baseline AGENTS governance file | `AGENTS.md` | None | Add new decision/handoff rows after merged workflow changes |