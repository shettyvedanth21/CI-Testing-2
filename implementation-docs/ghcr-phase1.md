# GHCR Phase 1 Rollout

This is the first permanent step in moving Shivex from "build on server" deployments to "build in CI, pull on server" deployments.

## What Phase 1 changes

Phase 1 adds a dedicated GitHub Actions workflow that:

- runs on pushes to `main`
- builds the production container images in CI
- publishes them to GitHub Container Registry (GHCR)
- tags them with both:
  - the commit SHA (`sha-...`)
  - `main`

Phase 1 established the GHCR publishing path. The repository now also carries the Phase 2 compose cutover:

- base production compose uses GHCR `image:` references for first-party services
- local compose restores `build:` overrides
- production deploys can move to `docker compose pull` + `docker compose up -d`

## Why this is the permanent fix direction

Today the EC2 server is still:

- building app images locally
- relying on warm Docker cache
- slow after `docker image prune` / `docker builder prune`
- vulnerable to long rebuilds for heavy services

The permanent target model is:

1. CI validates the code.
2. CI builds deployable images.
3. CI pushes them to GHCR.
4. Production servers only pull tagged images.
5. Rollback becomes "change tag and restart", not "rebuild everything".

## Images published in Phase 1

The workflow publishes these images:

- `device-service`
- `energy-service`
- `data-service`
- `rule-engine-service`
- `analytics-api`
- `analytics-worker`
- `data-export-service`
- `reporting-service`
- `waste-analysis-service`
- `copilot-service`
- `auth-service`
- `ui-web`

Notes:

- `analytics-api` and `analytics-worker` come from different targets in the same Dockerfile.
- `data-service`, `rule-engine-service`, `reporting-service`, and `waste-analysis-service` currently publish one base runtime image each; their worker containers can later reuse those same images with command overrides.
- `telemetry-simulator` is intentionally excluded because it is not part of the production runtime path.

## GHCR naming scheme

Images are published using this pattern:

```text
ghcr.io/<owner-lowercase>/<repo-lowercase>-<image-name>:sha-<git-sha>
ghcr.io/<owner-lowercase>/<repo-lowercase>-<image-name>:main
```

Example:

```text
ghcr.io/cittagent/shivex-main-1-device-service:sha-abc1234
```

## What you need to do once

### 1. Decide package visibility

Recommended:

- keep GHCR packages **private**

That is the safer default for Shivex production images.

### 2. Make sure GitHub Actions can publish packages

The workflow already requests:

- `contents: read`
- `packages: write`

No extra repo code change should be needed unless org-level package policies block Actions publishing.

### 3. Prepare a pull token for the server

When we switch production deploys to pull-based images, the EC2 server will need a GitHub token that can pull private GHCR images.

Recommended scope:

- `read:packages`

Later we will use it once on the server with:

```bash
echo "$GHCR_PAT" | docker login ghcr.io -u <github-username> --password-stdin
```

### 4. Watch the first publish run

After this workflow lands, the next successful merge to `main` should publish the image set to GHCR.

You should verify:

- the workflow succeeds
- the packages appear in GHCR
- the tags include `sha-...`

## Production deployment contract after Phase 2

Production should set:

```env
GHCR_OWNER_LOWER=cittagent
GHCR_REPO_LOWER=shivex-main-1
APP_IMAGE_TAG=sha-<commit-sha>
```

Then deploy with:

```bash
docker compose --env-file .env pull
docker compose --env-file .env up -d
```

## Emergency server-build fallback

If GitHub Actions minutes are exhausted or GHCR publishing is temporarily
blocked, the repository now includes a production-safe fallback override:

```text
docker-compose.server-build.yml
```

Use it like this:

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.server-build.yml up -d --build
```

This fallback:

- restores `build:` for first-party app services
- keeps the same production `.env` contract
- does not introduce local-only MySQL, MinIO, or simulator services
- gives production a backup lane when GHCR publishing is unavailable

Use only one deployment mode per release:

- GHCR primary path:
  - `docker compose --env-file .env pull`
  - `docker compose --env-file .env up -d`
- server-build fallback path:
  - `docker compose --env-file .env -f docker-compose.yml -f docker-compose.server-build.yml up -d --build`

Rollback becomes:

1. change `APP_IMAGE_TAG` back to the previous `sha-*`
2. run `docker compose pull`
3. run `docker compose up -d`

## Recommended rollout after Phase 2

1. Merge the GHCR workflow and the compose cutover.
2. Let one `main` build publish images to GHCR.
3. Confirm packages and `sha-*` tags exist.
4. Log the server into `ghcr.io` with a package read token.
5. Switch the server deployment flow from build-based deploys to pull-based deploys.
