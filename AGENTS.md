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
- Use shorthand command phrase `update feature & main worktree` for the standard promotion workflow.
- Default meaning of that phrase:
  - Commit + push on active feature worktree branch first (example: `Dev` in `RL_Tools.extension-Dev`).
  - Sync/push same branch in primary worktree when needed and possible.
  - Merge that branch into `main` and push `main`.

## Commands

- Python syntax check for one file:
  - `python -m py_compile "<path-to-script.py>"`
- Compile many command scripts:
  - `python -m compileall "RL_Tools.tab"`
- Primary worktree and branch checks:
  - `git worktree list`
  - `git rev-parse --abbrev-ref HEAD`
- Standard promotion commands:
  - `git add <files>`
  - `git commit -m "<message>"`
  - `git push origin <branch>`
  - `git checkout <branch>` (in primary worktree if branch handoff is required)
  - `git pull origin <branch>`
  - `git push origin <branch>`
  - `git checkout main`
  - `git merge --no-ff <branch>`
  - `git push origin main`

## Conventions

- Keep edits narrow and feature-scoped.
- Follow existing command naming and folder structure.
- Prefer shared helpers under `lib/` for repeated logic.
- Keep UI labels and command behavior aligned.

## Git Worktree Manager Style

- Naming: create worktree folders as `C:\Users\RML\Documents\GitHub\RL_Tools.extension-<BranchName>`.
- One branch per worktree: never keep the same branch checked out in more than one worktree.
- Branch conflict handling:
  - If a requested branch already has a worktree, reuse that worktree instead of creating a duplicate.
  - If that branch must move into primary, remove the secondary worktree and switch primary to that branch.
- Primary branch movement:
  - No fixed fallback branch is enforced.
  - Primary branch switching is situational and user-directed.
- Bulk-create defaults:
  - Target all local non-`main` branches by default.
  - Skip branches already represented by same-branch worktrees.
  - If target path exists but is not a registered worktree, classify it as `path conflict`, skip it, and report it.
- Deletion defaults:
  - Prefer `git worktree remove <path>` first.
  - Use `git worktree remove --force <path>` only when complete deletion is explicitly requested and local edits may exist.
  - If folder deletion fails because it is locked/in use, report it and provide a retry command after handles are closed.
  - Run `git worktree prune` after removals.
- Orphan handling:
  - If a folder exists on disk but is absent from `git worktree list`, treat it as an orphan worktree folder.
  - Remove orphan folders only when explicitly requested.

## Decision Log

| Date | Decision | Why | Impact | Status |
|------|----------|-----|--------|--------|
| 2026-03-02 | Standardize repository workflow source on `AGENTS.md` | Align global hub + parent skill governance | Enables reliable drift checks and skill maintenance | Active |
| 2026-03-04 | Standardize Git worktree manager style rules | Preserve consistent branch/worktree behavior across sessions | Reduces duplicate checkouts, path conflict churn, and deletion errors | Active |
| 2026-03-04 | Adopt standard promotion pipeline (`branch -> primary worktree branch -> main`) | Ensure predictable delivery from worktrees to production branch | Reduces missed merges and branch drift | Active |
| 2026-03-04 | Standardize shorthand phrase `update feature & main worktree` | Reduce ambiguity and typing for routine promotion requests | Faster communication and fewer git wording mistakes | Active |

## Session Handoff Log

| Date | What Changed | Files Touched | Checks Run | Next Step |
|------|---------------|---------------|------------|-----------|
| 2026-03-02 | Created baseline AGENTS governance file | `AGENTS.md` | None | Add new decision/handoff rows after merged workflow changes |
| 2026-03-04 | Added Git Worktree Manager Style governance and mirrored rule intent for skills | `AGENTS.md`, `C:\Users\RML\.codex\skills\rml-repo-rl-tools-extension\SKILL.md` | `rg` keyword checks, focused `git diff`, `git status --short` scope check | Continue applying these defaults for all RL_Tools worktree operations |
| 2026-03-04 | Added documented standard git promotion flow and linked `skills.md` process | `AGENTS.md`, `skills.md` | Doc-only update | Follow this process for routine feature promotion |
| 2026-03-04 | Added shorthand workflow command definition and default semantics | `AGENTS.md`, `skills.md` | Doc-only update | Use `update feature & main worktree` for typical worktree promotion |
