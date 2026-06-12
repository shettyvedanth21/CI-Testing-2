## Codex Orchestration Rules

This file is the working discipline for this repo when Codex and OpenCode are both involved.

Codex is the owner of:

- architecture decisions
- scope control
- review judgment
- implementation approval
- validation approval
- deploy-readiness judgment

OpenCode is only an executor.
OpenCode must never become the source of truth for architecture, safety, or release confidence.

## Mandatory Workflow

Every non-trivial engineering task must follow this order unless Codex explicitly narrows it:

1. `REVIEW-ONLY`
2. `PLAN-ONLY`
3. `IMPLEMENT-ONLY`
4. `VALIDATION-ONLY`

OpenCode must not skip ahead.
If Codex asks for review, OpenCode reviews only.
If Codex asks for plan, OpenCode plans only.
If Codex asks for implementation, OpenCode implements only the approved scope.
If Codex asks for validation, OpenCode validates only and does not "fix while validating".

## Scope Discipline

OpenCode must:

- work only in the repo and branch named by Codex
- never switch branches unless Codex explicitly says so
- never create branches unless Codex explicitly says so
- never reset, revert, discard, or checkout files unless Codex explicitly says so
- never broaden scope with "helpful" edits
- never silently refactor unrelated code
- never change architecture on its own
- never touch Docker, env, database state, Redis, S3/MinIO, or runtime state unless the phase explicitly allows it

If the approved scope is insufficient, OpenCode must stop and report back to Codex.

## Validation Standard

Codex does not accept OpenCode summaries blindly.

Before any fix is trusted, Codex should verify:

- actual changed files
- actual diff shape
- test evidence
- runtime evidence when relevant
- blast radius
- whether the scope was respected

No fix is considered permanent only because OpenCode says so.

## Branch Discipline

Current active working branch:

- `Dev-Testing`

Do not open a fresh recovery lane on another branch unless Codex explicitly decides it.

## Current Project Priorities

After the already-committed org-admin and reporting fixes, the remaining permanent-hardening tracks should be handled one by one under Codex control:

1. telemetry/counter hardening and regression protection
2. cost-drift / tariff-ingestion correctness hardening
3. dashboard / report / waste cross-surface parity hardening
4. machine-page latency and state freshness improvements

## Practical Rule

If context gets noisy, refer to:

- [pending.md](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/implementation-docs/pending.md)
- [codex.md](/Users/vedanthshetty/Desktop/GIT-Testing/Shivex-Main/implementation-docs/codex.md)

These two files together are the repo-level memory for how work should proceed.
