"""Pharma AI OS — FastAPI application assembly."""
import logging
import sys
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

    # register all agent tools (imports register decorated tools)
    from app.agents import domain_agents  # noqa: F401

    # register all Human Decision Queue approval callbacks
    from app.core import approvals_callbacks  # noqa: F401

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
from app.api.routes_sales import crm_router, sales_router  # noqa: E402
from app.api.routes_supply import (  # noqa: E402
    inventory_router,
    logistics_router,
    planning_router,
    warehouse_router,
)

for r in [auth_router, users_router, masters_router, analytics_router,
          crm_router, sales_router, planning_router, vendors_router,
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

    return {"status": "ready" if mongo == "up" else "degraded",
            "mongo": mongo, "redis": "up" if cache.is_redis else "fallback",
            "storage": "auto", "ai": "openai" if settings.OPENAI_API_KEY
            else "offline-fallback"}


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

        return _Resp(content=body, status_code=response.status_code,
                     headers=dict(response.headers),
                     media_type="application/json")
    return response


# ---- request logging middleware
@app.middleware("http")
async def log_requests(request, call_next):
    import time
    import uuid

    request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
    start = time.time()
    response = await call_next(request)
    dur = int((time.time() - start) * 1000)
    log.info(json_fmt({
        "request_id": request_id, "method": request.method,
        "path": request.url.path, "status": response.status_code,
        "ms": dur}))
    response.headers["x-request-id"] = request_id
    return response


def json_fmt(d: dict) -> str:
    import json

    return json.dumps(d, default=str)
