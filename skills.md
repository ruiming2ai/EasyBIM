# Skills Workflow

## Shorthand Command

Use this phrase in future requests:

- `update feature & main worktree`

Default interpretation:

1. Commit and push on the active feature worktree branch first.
2. Update/push the same branch in primary worktree when needed and possible.
3. Merge feature branch into `main`.
4. Push `main`.

## Standard Git Promotion Process

Use this process for normal feature delivery in this repository:

1. Confirm current worktree/branch state.
2. Implement and validate feature changes in the feature worktree.
3. Commit changes on feature branch.
4. Push/publish feature branch to `origin`.
5. Sync/push same branch in primary worktree when needed and possible.
6. Merge feature branch into `main`.
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

# Optional same-branch sync on primary worktree
git checkout <branch>
git pull origin <branch>
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
