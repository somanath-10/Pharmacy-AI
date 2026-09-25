"""Pharma AI OS — FastAPI application assembly."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import db
from app.core.errors import register_handlers
from app.core.logging import setup_logging

setup_logging()
log = logging.getLogger("pharmaos.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    from app.core.redis_client import cache

    await cache.connect()
    from app.core.storage import storage

    await storage.init()

    # start transactional-outbox pump
    from app.core.events import bus

    await bus.start_pump()

    # seed master/demo data if enabled
    if settings.SEED_DEMO:
        from app.seed.run import run_seed

        try:
            await run_seed(demo=True)
        except Exception:
            log.exception("seed failed")

    yield
    await bus.stop_pump()
    await db.close()


app = FastAPI(
    title="Pharma / Pharmacy AI Operating System",
    version="1.0.0",
    description="AI-native pharmaceutical enterprise OS: Market-to-Order, P2P, "
                "Plan, Warehouse, QC/QA, Plant/MES, Pharmacy, O2C, Logistics, "
                "Finance, Reverse, Recall, Safety, Compliance — with governed "
                "AI agents and a Human Decision Queue.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_handlers(app)

# ---- security middleware: security headers + global rate limit
from app.core.security import rate_limit as _rate_limit

@app.middleware("http")
async def security_headers_and_rate_limit(request, call_next):
    # Skip rate limit for health/metrics
    if request.url.path in ("/health", "/health/ready", "/metrics"):
        response = await call_next(request)
    else:
        # Global API rate limit (per-IP)
        client_ip = request.client.host if request.client else "unknown"
        allowed = await _rate_limit("api", client_ip, settings.RATE_LIMIT_API_PER_MIN)
        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "60"},
            )
        response = await call_next(request)

    # Security headers
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if settings.ENV in {"production", "staging"}:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return response

# ---- routers
from app.api.routes_analytics import router as analytics_router  # noqa: E402
from app.api.routes_auth import router as auth_router  # noqa: E402
from app.api.routes_auth import users_router  # noqa: E402
from app.api.routes_finance import (  # noqa: E402
    compliance_router,
    finance_router,
    platform_router,
    reverse_router,
    safety_router,
)
from app.api.routes_masters import router as masters_router  # noqa: E402
from app.api.routes_procurement import (  # noqa: E402
    portal_router,
    procurement_router,
    sourcing_router,
    vendors_router,
)
from app.api.routes_quality import (  # noqa: E402
    pharmacy_router,
    production_router,
    qa_router,
    qc_router,
)
from app.api.routes_sales import (  # noqa: E402
    crm_router,
    customer_portal_router,
    sales_router,
)
from app.api.routes_supply import (  # noqa: E402
    inventory_router,
    logistics_router,
    planning_router,
    warehouse_router,
)

# register all agent tools at import time — the agent registry must be
# populated whenever this module loads (uvicorn, ASGITransport tests, probes),
# not only after lifespan starts. Side-effect import.
from app.agents import domain_agents

# register all Human Decision Queue approval callbacks
from app.core import approvals_callbacks

# silence unused import warnings (side-effect imports)
_ = domain_agents
_ = approvals_callbacks

# fail fast if an agent's monitoring map references an unregistered tool
from app.api.routes_finance import assert_monitoring_tools_registered  # noqa: E402

assert_monitoring_tools_registered()

for r in [auth_router, users_router, masters_router, analytics_router,
          crm_router, sales_router, customer_portal_router, planning_router, vendors_router,
          portal_router, sourcing_router, procurement_router, logistics_router,
          warehouse_router, inventory_router, qc_router, qa_router,
          production_router, pharmacy_router, finance_router, reverse_router,
          safety_router, compliance_router, platform_router]:
    app.include_router(r)


# ---- health & metrics
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENV}


@app.get("/health/ready")
async def ready():
    try:
        await db.client.admin.command("ping")
        mongo = "up"
    except Exception as e:
        mongo = f"down: {e}"
    from app.core.redis_client import cache

    # Honest topology reporting: tx claims only when multi-document
    # transactions are actually supported (replica set / sharded).
    topology = db.topology_info()
    return {"status": "ready" if mongo == "up" else "degraded",
            "mongo": mongo, "redis": "up" if cache.is_redis else "fallback",
            "storage": "auto", "ai": "openai" if settings.OPENAI_API_KEY
            else "offline-fallback", "transactions": topology}


@app.get("/metrics")
async def metrics():
    from app.core.database import db as _db

    async def count(coll, q=None):
        try:
            return await _db.db[coll].count_documents(q or {})
        except Exception:
            return -1

    lines = [
        f"pharmaos_open_approvals {await count('approvals', {'status': 'PENDING'})}",
        f"pharmaos_outbox_pending {await count('outbox_events', {'status': 'PENDING'})}",
        f"pharmaos_open_sales_orders {await count('sales_orders', {'status': {'$nin': ['CLOSED', 'CANCELLED']}})}",
        f"pharmaos_open_purchase_orders {await count('purchase_orders', {'status': {'$nin': ['CLOSED', 'CANCELLED', 'REJECTED']}})}",
        f"pharmaos_agent_tool_calls_total {await count('agent_tool_calls')}",
        f"pharmaos_audit_events_total {await count('audit_events')}",
    ]
    return int(0) if False else "\n".join(lines) + "\n"


# ---- response sanitization: serialize ObjectId, strip Mongo _id keys
from bson import ObjectId  # noqa: E402
from fastapi.encoders import ENCODERS_BY_TYPE  # noqa: E402

ENCODERS_BY_TYPE[ObjectId] = str


async def _sanitize(value):
    if isinstance(value, dict):
        return {k: await _sanitize(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [await _sanitize(v) for v in value]
    return value


@app.middleware("http")
async def sanitize_response(request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        try:
            import json as _json

            data = _json.loads(body)
            data = await _sanitize(data)
            body = _json.dumps(data, default=str).encode()
        except Exception:
            pass
        from starlette.responses import Response as _Resp

        headers = {k: v for k, v in response.headers.items()
                   if k.lower() not in ("content-length",)}
        return _Resp(content=body, status_code=response.status_code,
                     headers=headers,
                     media_type="application/json")
    return response


# ---- request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    import time
    import uuid

    from app.core.audit import new_correlation_id

    # Correlation ID: one business thread traced across audit + events (Part 8)
    request.state.correlation_id = request.headers.get(
        "x-correlation-id") or new_correlation_id()
    request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
    # Idempotency-Key header → available to domain services as request.state.idem_key
    idem = request.headers.get("idempotency-key")
    if idem:
        request.state.idem_key = idem
    start = time.time()
    response = await call_next(request)
    dur = int((time.time() - start) * 1000)
    log.info(json_fmt({
        "request_id": request_id, "method": request.method,
        "path": request.url.path, "status": response.status_code,
        "correlation_id": request.state.correlation_id,
        "ms": dur}))
    response.headers["x-request-id"] = request_id
    response.headers["x-correlation-id"] = request.state.correlation_id
    return response


def json_fmt(d: dict) -> str:
    import json

    return json.dumps(d, default=str)
