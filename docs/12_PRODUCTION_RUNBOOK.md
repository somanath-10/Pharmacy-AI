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
