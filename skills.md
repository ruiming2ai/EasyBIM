# Skills Workflow

## Standard Git Promotion Process

Use this process for normal feature delivery in this repository:

1. Confirm current worktree/branch state.
2. Implement and validate feature changes.
3. Commit changes on the working branch.
4. Push branch to `origin`.
5. Ensure the same branch in the primary worktree is updated (pull/fast-forward if required).
6. Merge the branch into `main` from the primary worktree.
7. Push `main` to `origin`.
8. Record workflow updates in `AGENTS.md` and `skills.md` when process changes.

## Command Sequence (Typical)

```powershell
git worktree list
git rev-parse --abbrev-ref HEAD
git status --short

git add <files>
git commit -m "<feature summary>"
git push origin <branch>

git checkout main
git pull origin main
git merge --no-ff <branch>
git push origin main
```

## Guardrails

- Run syntax checks for changed Python scripts before commit.
- Keep commits scoped to the requested feature and related workflow docs.
- Avoid destructive git commands (`reset --hard`, checkout file reverts) unless explicitly requested.
