# Production Runbook (Frozen)

## Environments & configuration

All configuration via environment variables (see `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| MONGODB_URI | Mongo connection string | mongodb://localhost:27017 |
| MONGODB_DB | Database name | pharmacy_ai_os |
| REDIS_URL | Redis (optional; blank disables) | "" |
| JWT_SECRET | Token signing secret | change-me (REQUIRED in prod) |
| ACCESS_TOKEN_EXPIRE_MINUTES | Access TTL | 720 |
| OPENAI_API_KEY | AI gateway (blank → offline fallback) | "" |
| OPENAI_MODEL | Model name | gpt-4o-mini |
| S3_ENDPOINT/S3_BUCKET/AWS keys | Object storage (blank → GridFS/local) | "" |
| USE_TXNS | Require replica-set transactions | true (auto-degrades) |
| SEED_DEMO | Seed demo data on boot | false |
| RATE_LIMIT_PER_MIN | Simple API rate limit | 600 |

## Deploy

```bash
cp .env.example .env      # set JWT_SECRET, MONGODB_URI (replica set for txns)
docker compose up -d --build
docker compose exec api python -m app.seed.run   # master data (SEED_DEMO=1 adds demo)
```

Services: `mongo` (replica set single-node), `redis`, `minio`, `api` (uvicorn workers),
`frontend` (nginx serving built React + /api proxy). Health: `/health`, `/health/ready`.

## Operations

- **Logs**: JSON to stdout; aggregate via your collector. Every log line has request_id.
- **Metrics**: `/metrics` (Prometheus text) — request counts/latency, outbox backlog, agent runs.
- **Outbox pump**: background task every 2s; backlog alert if > 1000. Manual replay:
  `python -m app.core.outbox_replay --since 24h`.
- **Backups**: `mongodump --uri=$MONGODB_URI --db=$MONGODB_DB` nightly; test restores monthly.
  Object storage bucket versioning ON; document metadata is in Mongo.
- **Upgrades**: blue-green behind proxy; Mongo minor upgrades rolling; never edit regulated
  collections manually — use supersede endpoints.

## Incident quick answers

- Agents misbehaving → disable per-agent flag in `agent_registry` / restart api; queue items remain.
- Mongo tx errors (standalone) → USE_TXNS=false degrades gracefully (single-doc atomicity).
- OpenAI outage → offline deterministic fallback engaged automatically (flagged in responses).
- Queue flooding → raise policy limits via policy_rules; categories page shows source domains.

## Production phase additions (this release)

### Backup & recovery — scripts/
- `scripts/backup-mongo.sh [dir]` — nightly `mongodump --gzip --archive` of the
  whole database (GridFS included) + tar of `uploads/`; 14-day retention
  (`RETAIN_DAYS` overridable). Cron: `0 2 * * *`.
- `scripts/restore-mongo.sh <archive>` — guarded restore (`--drop`), asks for
  typed confirmation. After restore: `docker compose restart api` (rebuilds
  indexes). Disaster-recovery order: provision host → restore latest archive →
  restore `uploads/` tar → start stack → verify `/health/ready` + spot-check
  audit trail. Rollback: keep the pre-upgrade archive; restore + redeploy the
  previous image tag.

### CI/CD — .github/workflows/ci.yml
Backend (ruff lint, compile check, pytest against real Mongo/Redis services),
frontend build, docker image builds for api + web, dependency audits
(pip-audit / npm audit) and a naive secret scan. Deploy gates on all green.

### Environments
- `docker-compose.yml` — development (mongo, redis, api, web).
- `docker-compose.prod.yml` — overlay: production env, no exposed mongo/redis
  ports, no auto-seed, resource limits. Usage:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
- Staging: same overlay with `SEED_DEMO=1` once and staging secrets in `.env`.
- Secrets only via `.env` / orchestrator secrets — never in images or repo.

### Agent governance
- `POST /api/agents/{agent_id}/status` — kill-switch (`DISABLED`/`PAUSED`/
  `ACTIVE`), role-gated (SUPER_ADMIN/COMPLIANCE/MANAGEMENT), audited.
- AI token/cost metering: every OpenAI call is recorded per model/day in
  `ai_usage`; `GET /api/ai/usage` shows real spend.
- `GET /api/finance/payments/anomalies` — duplicate payments, amount spikes,
  repeated failures (deterministic).

### Executive dashboard
`GET /api/analytics/executive` + `/executive` page: all domains, AI governance
(automation rate, agent fleet), and deterministic business risks.
Advanced analytics: `/api/analytics/advanced/quality-trends`, `/plant-oee`,
`/inventory-intelligence`.

### Unique-index reconciliation (upgraded databases)
On boot the API reconciles partial-unique indexes (dispenses per Rx, one
customer invoice per SO, one supplier invoice per PO+number). Databases created
before these indexes existed are repaired automatically (stale non-unique index
dropped and recreated as unique) — duplicate protection is real, not assumed.
