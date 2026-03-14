# Skills Workflow

## Shorthand Command

Use this phrase in future requests:

- `sync worktrees`

Default interpretation:

1. Commit and push on the active feature worktree branch first.
2. Identify the creator branch associated with that feature worktree. `Temp-Phase-and-View-2` is only an example creator branch, not a fixed target.
3. In primary worktree, sync the creator branch with the feature worktree branch.
4. Stop after the creator branch is updated unless a separate merge to `main` is explicitly requested.

## Standard Git Promotion Process

Use this process for normal feature delivery in this repository:

1. Confirm current worktree/branch state.
2. Implement and validate feature changes in the feature worktree.
3. Commit changes on the active feature worktree branch.
4. Push/publish the feature worktree branch to `origin`.
5. In primary worktree, checkout/pull `<creator-branch>`.
6. Merge `origin/<feature-worktree-branch>` into `<creator-branch>`.
7. Push `<creator-branch>` to `origin`.
8. Merge `<creator-branch>` into `main` only when explicitly requested.
9. Record workflow updates in `AGENTS.md` and `skills.md` when process changes.

## Command Sequence (Typical)

```powershell
git worktree list
git rev-parse --abbrev-ref HEAD
git status --short

git add <files>
git commit -m "<feature summary>"
git push origin <feature-worktree-branch>

# Primary worktree creator-branch sync
git checkout <creator-branch>
git pull origin <creator-branch>
git merge --no-ff origin/<feature-worktree-branch>
git push origin <creator-branch>

# Optional later promotion to main (explicit request only)
git checkout main
git pull origin main
git merge --no-ff <creator-branch>
git push origin main
```

## Guardrails

- Use the actual creator branch that spawned the feature worktree. Do not assume a fixed branch name.
- If `<creator-branch>` and `<feature-worktree-branch>` are the same in your setup, substitute the same branch name for both placeholders.
- Run syntax checks for changed Python scripts before commit.
- Keep commits scoped to the requested feature and related workflow docs.
- Avoid destructive git commands (`reset --hard`, checkout file reverts) unless explicitly requested.
