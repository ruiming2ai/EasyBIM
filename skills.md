# Skills Workflow

## Shorthand Command

Use this phrase in future requests:

- `update feature & main worktree`

Default interpretation:

1. Commit and push on the active feature worktree branch first.
2. In primary worktree, merge/push the active feature branch into creator branch `Temp-Phase-and-View-2`.
3. Merge `Temp-Phase-and-View-2` into `main`.
4. Push `main`.

## Standard Git Promotion Process

Use this process for normal feature delivery in this repository:

1. Confirm current worktree/branch state.
2. Implement and validate feature changes in the feature worktree.
3. Commit changes on feature branch.
4. Push/publish feature branch to `origin`.
5. In primary worktree, checkout/pull `Temp-Phase-and-View-2`.
6. Merge `origin/<feature-branch>` into `Temp-Phase-and-View-2`.
7. Push `Temp-Phase-and-View-2` to `origin`.
8. Merge `Temp-Phase-and-View-2` into `main`.
9. Push `main` to `origin`.
10. Record workflow updates in `AGENTS.md` and `skills.md` when process changes.

## Command Sequence (Typical)

```powershell
git worktree list
git rev-parse --abbrev-ref HEAD
git status --short

git add <files>
git commit -m "<feature summary>"
git push origin <branch>

# Primary worktree staging branch sync
git checkout Temp-Phase-and-View-2
git pull origin Temp-Phase-and-View-2
git merge --no-ff origin/<feature-branch>
git push origin Temp-Phase-and-View-2

git checkout main
git pull origin main
git merge --no-ff Temp-Phase-and-View-2
git push origin main
```

## Guardrails

- Run syntax checks for changed Python scripts before commit.
- Keep commits scoped to the requested feature and related workflow docs.
- Avoid destructive git commands (`reset --hard`, checkout file reverts) unless explicitly requested.
