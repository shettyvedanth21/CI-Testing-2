# RDS Migration Prep Checklist

This file is the practical rollout checklist for the current Shivex deployment path:

- keep Docker app deployment for now
- keep Docker Redis for now
- keep current EC2 for now
- move MySQL from Docker to Amazon RDS when ready
- avoid data loss
- keep rollback simple

## 1. What We Are Doing

Current direction:

- app services continue running with Docker Compose
- production server keeps using a real `.env`
- local development keeps using `.env.local`
- MySQL will move from Docker to RDS
- S3 can be wired later if not needed immediately
- Redis stays Docker for now

## 2. What To Do Today

Do these in this exact order.

### Step 1: Create a Git Safety Point

Goal:

- make rollback easy
- create a known stable version before infra changes

Recommended commands:

```bash
git status
git add -A
git commit -m "chore: stable pre-RDS migration checkpoint"
git tag -a pre-rds-migration -m "Stable checkpoint before RDS migration"
git log --oneline --decorate -5
git tag
```

If the repo has files you do not want to commit yet, do not force this step. Clean that first.

### Step 2: Back Up Docker MySQL

Goal:

- guarantee recoverable database state before any migration

Create a backup folder:

```bash
mkdir -p backups/mysql
```

Find the MySQL container:

```bash
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
```

Run dump:

```bash
docker exec -i energy_mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --all-databases --single-transaction --routines --triggers --events' > backups/mysql/pre-rds-all-databases.sql
```

Also compress it:

```bash
gzip -kf backups/mysql/pre-rds-all-databases.sql
ls -lh backups/mysql
```

### Step 3: Verify Docker Persistence

Goal:

- confirm current Docker deployment is not losing state on restart

Check volumes:

```bash
docker volume ls
docker compose ps
docker inspect energy_mysql
docker inspect minio
docker inspect analytics_redis
```

Things to confirm:

- MySQL is backed by a named Docker volume
- MinIO is backed by a named Docker volume if artifacts matter
- Redis persistence is acceptable for current rollout

### Step 4: Save Deployment Inputs

Goal:

- preserve exact current runtime setup

Create snapshot folder:

```bash
mkdir -p backups/deployment-snapshot
```

Copy key files:

```bash
cp .env backups/deployment-snapshot/.env.production.snapshot
cp docker-compose.yml backups/deployment-snapshot/docker-compose.snapshot.yml
git rev-parse HEAD > backups/deployment-snapshot/git-commit.txt
docker compose images > backups/deployment-snapshot/docker-images.txt
docker compose ps > backups/deployment-snapshot/docker-ps.txt
```

### Step 5: Prepare RDS Change List

Goal:

- know exactly what changes tomorrow when manager is ready

These values will change later:

- `MYSQL_HOST`
- `MYSQL_PORT`
- possibly `MYSQL_USER`
- possibly `MYSQL_PASSWORD`
- `DATABASE_URL`

These values do not need to change just for RDS:

- Redis settings if Redis stays Docker
- service ports
- domain URLs
- JWT/auth values

## 3. What To Do Tomorrow When RDS Is Ready

### Step 1: Create RDS

Recommended simple starting posture:

- MySQL 8.x
- Single-AZ
- `db.m6g.large` if that is the practical smallest available option in your console
- `gp3`
- `20 GB`
- public access `No`
- backups enabled
- deletion protection enabled

### Step 2: Capture RDS Connection Details

You will need:

- RDS endpoint
- port
- DB username
- DB password
- database name or schema plan

### Step 3: Test Connection From EC2

Before changing app config, first confirm the server can reach RDS.

Example:

```bash
mysql -h <RDS_HOST> -P 3306 -u <USER> -p
```

### Step 4: Import Dump Into RDS

Example:

```bash
gunzip -c backups/mysql/pre-rds-all-databases.sql.gz | mysql -h <RDS_HOST> -P 3306 -u <USER> -p
```

If you want a smaller export later, dump only the actual app databases instead of `--all-databases`.

### Step 5: Update Production `.env`

Change only the DB-related values to RDS.

Example shape:

```env
MYSQL_HOST=<RDS_HOST>
MYSQL_PORT=3306
MYSQL_USER=<RDS_USER>
MYSQL_PASSWORD=<RDS_PASSWORD>
DATABASE_URL=mysql+aiomysql://<RDS_USER>:<RDS_PASSWORD>@<RDS_HOST>:3306/ai_factoryops
```

### Step 6: Restart Services

```bash
docker compose up -d --build
```

If only DB-backed services need restart, that is also okay.

### Step 7: Run Smoke Tests

Verify:

- login works
- dashboard loads
- create org works
- create plant works
- invite user works
- device onboarding works
- reports page loads
- analytics page loads
- telemetry ingest still works

## 4. Rollback Plan

If something goes wrong after switching to RDS:

1. restore previous `.env`
2. point app back to Docker MySQL
3. restart services
4. verify smoke tests again

Commands:

```bash
cp backups/deployment-snapshot/.env.production.snapshot .env
docker compose up -d --build
```

## 5. Jenkins Recommendation For Now

Do not overcomplicate Jenkins today.

What is enough for now:

- one job that runs backend tests
- one job that runs frontend tests
- optional one smoke test script after deployment

Simple first pipeline scope:

- checkout code
- install dependencies
- run Python tests
- run UI tests
- report pass/fail

That is enough for the current stage.

## 6. What Not To Do Right Now

Do not do these today:

- managed Redis migration
- ECS
- EKS
- autoscaling setup
- load balancer redesign
- major architecture rewrite
- production cutover without backup

## 7. Minimum Safe Outcome For This Week

If you achieve the following, that is a solid outcome:

- `.env` and `.env.local` are separated
- git checkpoint and tag exist
- Docker MySQL backup exists
- deployment snapshot exists
- RDS settings are ready
- RDS import is tested
- production app can switch DB safely

## 8. Quick Summary

Today:

- create git checkpoint
- take DB backup
- verify Docker persistence
- save env and compose snapshot
- prepare RDS migration inputs

Tomorrow:

- create RDS
- test connection
- import dump
- update `.env`
- restart Docker services
- run smoke tests
- keep rollback ready
