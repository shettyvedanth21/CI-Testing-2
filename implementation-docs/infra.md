# AWS Infra First Steps

## 1. Purpose of This File

This file explains the **exact first AWS resources** you should create for your current stage.

This is designed for:

- first production-style rollout
- low complexity
- cost awareness
- fewer than 10 organizations right now
- room to scale later

This is **not** a maximum-scale architecture document.
This is the **practical first setup**.

---

## 2. What You Need Right Now

For your current stage, create only these first:

1. `RDS MySQL`
2. `S3`
3. `ElastiCache Redis`

Your current EC2 instance can continue running the Dockerized app for now.

That means:

- EC2 runs the services
- RDS stores relational data
- S3 stores files/artifacts
- Redis handles queues/cache/runtime coordination

This is the simplest cost-effective first production-like setup.

---

## 3. What This Setup Is Good For

This setup is reasonable for:

- early real rollout
- lower customer count
- lower to moderate telemetry load
- safer persistence than local Docker volumes
- cleaner long-run path than staying fully local-style

This is **not** the final setup for very large enterprise scale.

Later scaling path:

- keep `RDS`, `S3`, `Redis`
- move app runtime from single EC2 to ECS
- increase worker/API replicas
- tune queue/worker configs

---

## 4. Creation Order

Create in this exact order:

1. `RDS MySQL`
2. `S3 bucket(s)`
3. `ElastiCache Redis`

Why this order:

- database first because most services depend on it
- storage second because reports/exports/datasets need it
- Redis third because it is runtime coordination, not primary source of truth

---

## 5. RDS Checklist

## 5.1 What To Create

Create:

- `Amazon RDS`
- engine: `MySQL`
- version: `MySQL 8 compatible`

Use it for:

- auth
- tenants
- users
- plants
- device metadata
- job metadata
- rule/report metadata
- system relational state

---

## 5.2 Cost-Effective Starting Recommendation

Start small.

Recommended idea:

- `Single-AZ`
- small instance class
- general purpose SSD storage
- automated backups enabled

Keep it modest first.

You do **not** need Multi-AZ on day 1 if cost is a serious concern.
You can upgrade later.

---

## 5.3 Recommended Settings

Use these as your simple first-pass settings:

- Engine: `MySQL`
- Version: latest stable MySQL 8 compatible available in your region
- Deployment: `Single-AZ`
- Template: choose a small/general-purpose production-like option, not free-tier style if this is real rollout
- Storage type: `gp3` if available
- Storage size: start modest, for example `20 GB` or a size that clearly exceeds your current DB usage with headroom
- Public access: `No` if possible
- Backups: `Enabled`
- Backup retention: at least `7 days`
- Deletion protection: `Enabled` if this is real rollout
- Auto minor version upgrade: `Enabled`

---

## 5.4 Network Guidance

If possible:

- keep RDS private
- do not expose it publicly
- allow access only from your EC2 app server security group

If this becomes too confusing right now, still prefer:

- minimal exposure
- tightly restricted security group rules

---

## 5.5 What You Must Save After Creation

After creating RDS, save these:

- `RDS endpoint`
- `port`
- `database name`
- `username`
- `password`
- `region`

You will need all of these for app config.

---

## 5.6 RDS Notes for Your Project

Your platform already relies heavily on MySQL-style relational state, so RDS is the right first managed service.

Do not try to redesign the database now.

Just move the existing relational workload safely.

---

## 6. S3 Checklist

## 6.1 What To Create

Create S3 for:

- reports
- exports
- analytics datasets
- generated artifacts

You can start with:

- one bucket

or

- two to three buckets if you want cleaner separation

For your current stage, one or a few buckets is fine.

---

## 6.2 Cost-Effective Starting Recommendation

Start simple:

- private bucket(s)
- public access blocked
- versioning enabled if affordable/preferred
- no CDN needed now

S3 is usually cost-effective if you avoid unnecessary duplication.

---

## 6.3 Recommended Bucket Strategy

Simple option:

- one main bucket for app artifacts

Cleaner option:

- one bucket for reports/exports
- one bucket for analytics datasets

If you want the least confusion, start with one bucket and logical prefixes/folders.

Example prefixes:

- `reports/`
- `exports/`
- `analytics/`
- `artifacts/`

---

## 6.4 Recommended Settings

- Region: same as EC2/RDS if possible
- Block public access: `Enabled`
- Versioning: `Enabled` if possible
- Encryption: `Enabled`
- Object ownership: bucket owner enforced if available

---

## 6.5 What You Must Save After Creation

Save:

- `bucket name`
- `region`
- `access method`

Preferred access method:

- EC2 IAM role if possible

Fallback:

- access key + secret key

IAM role is better than hardcoded long-term keys.

---

## 6.6 S3 Notes for Your Project

Your platform already uses S3-compatible storage patterns, so S3 is a natural move.

This is a low-risk migration compared to changing storage design.

---

## 7. Redis Checklist

## 7.1 What To Create

Create:

- `Amazon ElastiCache Redis`

Use it for:

- queues
- streams
- cache
- runtime coordination
- worker coordination
- some auth/runtime state

---

## 7.2 Cost-Effective Starting Recommendation

Start small:

- single node
- private access
- auth enabled if possible

You do **not** need a large Redis cluster on day 1.

---

## 7.3 Recommended Settings

- Engine: `Redis`
- Node count: `1` to start
- Public access: `No`
- Network: private access preferred
- Encryption/auth: enable if practical

If auth token setup is available in your chosen Redis mode, use it.

---

## 7.4 What You Must Save After Creation

Save:

- `Redis host`
- `port`
- `password/auth token` if enabled
- `region`

---

## 7.5 Redis Notes for Your Project

Your platform uses Redis as an important coordination/runtime layer, not just as optional cache.

So Redis should be treated as important infrastructure, but you can still start small.

---

## 8. Cost-Aware Recommendations

For your current stage:

- use `Single-AZ RDS`
- use modest storage to start
- use a small Redis node
- use private S3 buckets
- keep the existing EC2 for app runtime for now

This avoids overbuilding too early.

Do **not** spend early on:

- EKS
- complex autoscaling
- large clusters
- multi-region
- advanced networking unless needed immediately

The goal right now is:

- stable
- understandable
- cost-effective
- upgradeable later

---

## 9. What You Can Scale Later Without Throwing This Away

This setup is not wasted later.

Later you can keep:

- `RDS`
- `S3`
- `Redis`

and only change:

- app runtime from EC2 to ECS
- add more worker replicas
- add more API replicas
- separate heavier workloads

So this first setup is a valid stepping stone.

---

## 10. What You Need To Send Me After Creation

After you create these resources, send me:

### RDS

- endpoint
- port
- database name
- username
- password

### S3

- bucket name
- region
- IAM role or key-based access method

### Redis

- host
- port
- password/auth token if enabled

### EC2

- public IP or hostname
- OS
- whether Docker and Docker Compose are already installed

Then I can help you with:

- `.env` mapping
- service configuration changes
- deployment commands
- validation sequence

---

## 11. Very Important Safety Note

Do not store these values casually in random notes/messages:

- DB password
- Redis auth token
- S3 secret access key

Use a safe place and share only what is necessary while configuring.

---

## 12. Final Simple Action Plan

Do this now:

1. Create `RDS MySQL`
2. Create `S3`
3. Create `Redis`
4. Save the connection details
5. Send me the non-sensitive setup details and the necessary connection values safely

Then I will guide you to the next step:

- connecting your current EC2 Docker app to these services

That is the correct next move.
