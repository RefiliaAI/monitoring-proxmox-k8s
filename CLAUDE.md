# Monitoring Proxmox K8s

## About
monitoring for proxmox and K8s

## Workflow (required for every change)
This project uses **GitHub** (remote: (local only — add a GitHub remote later)). Follow this branch-based flow for
**every** change — never commit directly to `main`:

1. **Create a branch** for the change and push it to GitHub.
2. **Ask the user to verify** the change works. Pause and wait for their explicit
   confirmation — do not merge or release until they approve.
3. **Only after the user confirms**, in this order:
   - Bump the version (semver: patch for fixes, minor for features) and rebuild /
     produce the release artifact, if this project ships one.
   - Merge the branch into `main`.
   - Delete the now-merged branch.
   - Publish a **new GitHub release** with the new version tag (attach the build
     artifact if applicable).

`main` must always reflect verified, working code. Keep this file up to date as the
project's source of truth.

## GitHub authentication
`git push`/`pull` already work with no setup — Petal Hub registered the token with
Git Credential Manager when this project was created. For anything that needs the
**GitHub REST API directly** (creating a release, uploading a release asset, inviting a
collaborator, etc.), read the token from `%APPDATA%\petal-hub\settings.json` →
`githubToken` field. Never print, commit, or otherwise persist the token elsewhere.
