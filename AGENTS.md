# ovbuilder Project Governance (Gathered from openvox-gui)

This document consolidates the key governance rules from ~/Projects/OpenVox/openvox-gui/AGENTS.md, ovox/README.md, scripts/bump-version.sh, CONTRIBUTING.md, SECURITY.md, and related to ensure consistent practices for ovbuilder (the OpenVox VM builder CLI).

**Standing Rules (to be followed EVERY run/session):**

## Version Discipline (STRICT)

- Use Semantic Versioning (SemVer 2.0.0) + pre-releases.
  - Stable: MAJOR.MINOR.PATCH (e.g. 0.2.0)
  - Dev: 0.1.0-dev.N, 0.1.0-beta.1, etc.
- **Every meaningful push increments the pre-release counter**.
- Pair with: update CHANGELOG.md, conventional commit (include "Assisted By: Grok AI"), **annotated tag**, push (branch + tag).
- Single source of truth: root `VERSION` file.
- Use `scripts/bump-version.sh` (or equivalent; adapt for ovbuilder if needed) when version changes.
- ovbuilder is standalone (not lockstep with GUI yet), but follow same pre-release train model for fast iteration.
- User reminder from history: "remember to increment versions on every single push" + "PUSH EVERY TIME".

## Commit Process (Use the /commit skill)

- **Always use the commit skill** (`/commit`) for changes.
- The project-scoped commit skill (if present at .grok/skills/commit/SKILL.md) takes precedence; otherwise global.
- It enforces:
  1. Update CHANGELOG.md with what changed.
  2. Update documentation (README, etc.) if applicable.
  3. Run bump-version.sh if version changed.
  4. Stage everything, commit, push, then deploy (if applicable).
- Conventional commits.
- Always note "Assisted By: Grok AI".
- For ovbuilder: since small standalone, tag+push on commits for dev trains. Use separate release process for stable.
- GitHub Releases are separate/manual, only when ready to ship (not on every commit).
- Never commit secrets.

## Pre-Commit Checklist (ALWAYS, from global + project)

From ~/.grok/AGENTS.md and openvox-gui:

- CHANGELOG
- Docs update
- Version bump (bump script)
- Stage, commit, push, deploy.
- Heredoc safety in shell scripts: prefer `<< 'EOF'` quoted; add NOTE for unquoted.
- Respect all global boundaries (no secrets, etc.).

## Branching

- Follow openvox-gui: default now `main` (staging removed in gui).
- For ovbuilder development: use main or feature branches; for major work use alpha-style if following gui pattern.
- All dev through main for releases.

## Using /release skill

- When dev train ready, use `/release` to promote to clean stable SemVer, update CHANGELOG, tag, push tag.
- Prepare manual GitHub Release only then.

## Other Requirements

- Follow ovox design language for CLI (Typer+Rich, config, etc.).
- For Terraform parts in ovbuilder: follow any Puppet rules if applicable, but primarily Python/CLI.
- Infrastructure: use generic example.com etc (already sanitized).
- Every run: load and follow these rules. Use /commit for any code/docs changes that are meaningful.
- Gather and re-apply these on each major session or when working on ovbuilder/openvox projects.

## Sources Gathered

- /Users/jsheets/Projects/OpenVox/openvox-gui/AGENTS.md (full version discipline, commit, release, branching, heredoc)
- /Users/jsheets/Projects/OpenVox/openvox-gui/ovox/README.md (versioning section, install)
- /Users/jsheets/Projects/OpenVox/openvox-gui/scripts/bump-version.sh (exact propagation logic)
- /Users/jsheets/Projects/OpenVox/openvox-gui/CONTRIBUTING.md (PR process)
- /Users/jsheets/Projects/OpenVox/openvox-gui/SECURITY.md (supported versions)
- /Users/jsheets/Projects/OpenVox/openvox-gui/README.md (headers synced)
- Global ~/.grok/AGENTS.md (pre-commit checklist, skills usage, "PUSH EVERY TIME")
- /Users/jsheets/.grok/skills/commit/SKILL.md (enforcement details)

**Action for every run:** Re-read this AGENTS.md (or the sources). When making changes to ovbuilder code, docs, or VERSION: use /commit skill, bump if needed, update CHANGELOG, push, follow checklist. No exceptions.

This ensures version increments on commit, commit skill usage, and all former requirements.
