"""Test fixtures: isolated test database, HTTP client, authenticated helpers."""
import asyncio
import os

os.environ["MONGODB_DB"] = "pharmacy_ai_os_test"
os.environ["SEED_DEMO"] = "0"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await db.connect()
    await db.client.drop_database("pharmacy_ai_os_test")
    await db.connect()
    yield
    await db.close()


@pytest.fixture
async def client():
    from app.main import app

    # init agents registry + approval callbacks without running lifespan
    from app.agents import domain_agents  # noqa: F401
    from app.core import approvals_callbacks  # noqa: F401

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth_header(roles=("SUPER_ADMIN",), user_id="test-admin", vendor_id=None):
    token = create_access_token(user_id, list(roles), vendor_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    return auth_header()


@pytest.fixture
async def seeded(client, admin_headers):
    """Seed baseline masters (fast, in-process)."""
    r = await client.get("/api/masters/products", headers=admin_headers)
    if len(r.json()) == 0:
        from app.seed.run import _seed_masters, _seed_users

        await _seed_users()
        await _seed_masters()
    return True
