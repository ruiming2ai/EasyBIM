# AGENTS Guide

## Project Snapshot

- Project name: EasyBIM.extension
- Status: Active pyRevit extension development on `main`
- Primary goal: Maintain and expand EasyBIM pyRevit commands/hooks with safe workflow governance
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
  - `python -m compileall "EasyBIM.tab"`
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

- Naming: create worktree folders as `C:\Users\RML\Documents\GitHub\EasyBIM.extension-<BranchName>`.
- Explicit-name override: if user provides a specific worktree name/path, create that exact folder and create/check out a branch with the exact same name as that worktree folder name.
- One branch per worktree: never keep the same branch checked out in more than one worktree.
- Branch conflict handling:
  - If a requested branch already has a worktree, reuse that worktree instead of creating a duplicate.
  - If that branch must move into primary, remove the secondary worktree and switch primary to that branch.
- Do not auto-generate suffix branch names (for example `*-Worktree-1`) when user requests an explicit worktree name.
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
| 2026-03-04 | Adopt standard promotion pipeline (`branch -> primary worktree branch -> main`) | Ensure predictable delivery from worktrees to production branch | Reduces missed merges and branch drift | Active |
| 2026-03-04 | Standardize shorthand phrase `update feature & main worktree` | Reduce ambiguity and typing for routine promotion requests | Faster communication and fewer git wording mistakes | Active |

## Session Handoff Log

| Date | What Changed | Files Touched | Checks Run | Next Step |
|------|---------------|---------------|------------|-----------|
| 2026-03-02 | Created baseline AGENTS governance file | `AGENTS.md` | None | Add new decision/handoff rows after merged workflow changes |
| 2026-03-04 | Added Git Worktree Manager Style governance and mirrored rule intent for skills | `AGENTS.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | `rg` keyword checks, focused `git diff`, `git status --short` scope check | Continue applying these defaults for all EasyBIM worktree operations |
| 2026-03-04 | Added documented standard git promotion flow and linked `skills.md` process | `AGENTS.md`, `skills.md` | Doc-only update | Follow this process for routine feature promotion |
| 2026-03-04 | Added shorthand workflow command definition and default semantics | `AGENTS.md`, `skills.md` | Doc-only update | Superseded by `sync worktrees` wording |
| 2026-03-04 | Added explicit-name worktree/branch parity rule and removed auto-suffix creation behavior | `AGENTS.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Worktree recreate + doc sync + status verification | Keep explicit worktree names and branch names identical when requested |
| 2026-03-04 | Standardized primary-worktree 3-stage promotion flow via `Temp-Phase-and-View-2` before `main` | `AGENTS.md`, `skills.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Doc update for workflow governance | Superseded by creator-branch sync default |
| 2026-03-13 | Replaced fixed staging-branch shorthand with creator-branch `sync worktrees` semantics | `AGENTS.md`, `skills.md`, `C:\Users\RML\.codex\skills\rml-repo-easybim-extension\SKILL.md` | Targeted doc sync across root repo, mirrored skill, and worktree copies | Keep `main` promotion as a separate explicit step |
