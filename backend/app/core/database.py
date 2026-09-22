"""MongoDB connection, index management, GridFS bucket, transaction helper."""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.core.config import settings

log = logging.getLogger("pharmaos.db")


class Database:
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None
    gridfs = None

    @property
    def name(self) -> str:
        return settings.MONGODB_DB

    async def connect(self):
        self.client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            appname=settings.APP_NAME,
        )
        await self.client.admin.command("ping")
        self.db = self.client[settings.MONGODB_DB]
        try:
            import gridfs  # local import: only needed for sync GridFS buckets via motor.pymongo
            from motor.motor_asyncio import AsyncIOMotorGridFSBucket

            self.gridfs = AsyncIOMotorGridFSBucket(self.db)
        except Exception:  # pragma: no cover
            self.gridfs = None
        await self.ensure_indexes()
        log.info("mongo connected db=%s", settings.MONGODB_DB)

    async def close(self):
        if self.client:
            self.client.close()

    def supports_transactions(self) -> bool:
        try:
            hello = self.client.admin.command("hello")
            return bool(hello.get("setName")) and settings.USE_TXNS
        except Exception:
            return False

    @contextmanager
    def session(self):
        """Yield (session, client) for optional transactions."""
        if self.supports_transactions():
            with self.client.start_session() as s:
                with s.start_transaction():
                    yield s
        else:
            yield None

    async def ensure_indexes(self):
        from pymongo import IndexModel

        db = self.db
        idx = {
            "users": [IndexModel([("email", ASCENDING)])],
            "warehouses": [IndexModel([("code", ASCENDING)])],
            "products": [IndexModel([("sku", ASCENDING)]),
                         IndexModel([("name", ASCENDING)])],
            "customers": [IndexModel([("code", ASCENDING)])],
            "vendors": [IndexModel([("code", ASCENDING)]),
                        IndexModel([("status", ASCENDING)])],
            "vendor_documents": [IndexModel([("vendor_id", ASCENDING)]),
                                 IndexModel([("expiry_date", ASCENDING)])],
            "inventory_movements": [
                IndexModel([("movement_id", ASCENDING)]),
                IndexModel([("product_id", ASCENDING)]),
                IndexModel([("batch_id", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
            ],
            "inventory_balances": [
                IndexModel([("product_id", ASCENDING),
                            ("warehouse_id", ASCENDING),
                            ("batch_id", ASCENDING)]),
            ],
            "batches": [IndexModel([("batch_id", ASCENDING)]),
                        IndexModel([("product_id", ASCENDING)])],
            "reservations": [IndexModel([("reference_type", ASCENDING),
                                         ("reference_id", ASCENDING),
                                         ("status", ASCENDING)])],
            "purchase_requisitions": [IndexModel([("pr_id", ASCENDING)])],
            "purchase_orders": [
                IndexModel([("po_id", ASCENDING)]),
                IndexModel([("vendor_id", ASCENDING)]),
                IndexModel([("status", ASCENDING)]),
            ],
            "asns": [IndexModel([("asn_id", ASCENDING)]),
                     IndexModel([("po_id", ASCENDING)])],
            "grns": [IndexModel([("grn_id", ASCENDING)]),
                     IndexModel([("po_id", ASCENDING)])],
            "sourcing_events": [IndexModel([("event_id", ASCENDING)])],
            "bids": [IndexModel([("event_id", ASCENDING)])],
            "contracts": [IndexModel([("vendor_id", ASCENDING)])],
            "qc_samples": [IndexModel([("sample_id", ASCENDING)]),
                           IndexModel([("status", ASCENDING)]),
                           IndexModel([("ref_type", ASCENDING),
                                       ("ref_id", ASCENDING)])],
            "specifications": [IndexModel([("product_id", ASCENDING),
                                           ("status", ASCENDING)])],
            "deviations": [IndexModel([("deviation_id", ASCENDING)])],
            "capas": [IndexModel([("capa_id", ASCENDING)])],
            "production_orders": [IndexModel([("order_id", ASCENDING)]),
                                  IndexModel([("status", ASCENDING)])],
            "batch_records": [IndexModel([("order_id", ASCENDING)])],
            "equipment": [IndexModel([("code", ASCENDING)])],
            "leads": [IndexModel([("status", ASCENDING)])],
            "opportunities": [IndexModel([("status", ASCENDING)])],
            "inquiries": [IndexModel([("status", ASCENDING)])],
            "quotations": [IndexModel([("quote_id", ASCENDING)])],
            "sales_orders": [IndexModel([("order_id", ASCENDING)]),
                             IndexModel([("status", ASCENDING)]),
                             IndexModel([("customer_id", ASCENDING)])],
            "prescriptions": [IndexModel([("status", ASCENDING)]),
                              IndexModel([("sales_order_id", ASCENDING)])],
            "dispenses": [IndexModel([("rx_id", ASCENDING)])],
            "shipments": [IndexModel([("shipment_id", ASCENDING)]),
                          IndexModel([("sales_order_id", ASCENDING)]),
                          IndexModel([("status", ASCENDING)])],
            "supplier_invoices": [IndexModel([("invoice_id", ASCENDING)]),
                                  IndexModel([("po_id", ASCENDING)]),
                                  IndexModel([("status", ASCENDING)])],
            "customer_invoices": [IndexModel([("invoice_id", ASCENDING)]),
                                  IndexModel([("sales_order_id", ASCENDING)])],
            "payments": [IndexModel([("payment_id", ASCENDING)])],
            "gl_entries": [IndexModel([("account", ASCENDING)]),
                            IndexModel([("created_at", DESCENDING)])],
            "return_requests": [IndexModel([("return_id", ASCENDING)]),
                                IndexModel([("status", ASCENDING)])],
            "recalls": [IndexModel([("recall_id", ASCENDING)])],
            "recall_tasks": [IndexModel([("recall_id", ASCENDING),
                                         ("status", ASCENDING)])],
            "safety_cases": [IndexModel([("case_id", ASCENDING)])],
            "licences": [IndexModel([("expiry_date", ASCENDING)])],
            "documents": [IndexModel([("document_id", ASCENDING)]),
                          IndexModel([("entity_type", ASCENDING)])],
            "notifications": [IndexModel([("status", ASCENDING)]),
                              IndexModel([("created_at", DESCENDING)])],
            "audit_events": [
                IndexModel([("entity_type", ASCENDING),
                            ("entity_id", ASCENDING)]),
                IndexModel([("timestamp", DESCENDING)]),
                IndexModel([("entity_key", ASCENDING),
                            ("actor.id", ASCENDING)]),
            ],
            "workflows": [IndexModel([("entity_type", ASCENDING),
                                      ("entity_id", ASCENDING)])],
            "approvals": [IndexModel([("status", ASCENDING)]),
                           IndexModel([("category", ASCENDING)]),
                           IndexModel([("entity_type", ASCENDING),
                                       ("entity_id", ASCENDING)])],
            "outbox_events": [IndexModel([("status", ASCENDING),
                                          ("created_at", ASCENDING)])],
            "idempotency_keys": [IndexModel([("key", ASCENDING)],
                                            unique=True)],
            "counters": [IndexModel([("name", ASCENDING)], unique=True)],
            "agent_runs": [IndexModel([("agent", ASCENDING)])],
            "agent_tool_calls": [IndexModel([("agent", ASCENDING)]),
                                  IndexModel([("created_at", DESCENDING)])],
            "events": [IndexModel([("created_at", DESCENDING)])],
            "planning_proposals": [IndexModel([("status", ASCENDING)]),
                                   IndexModel([("product_id", ASCENDING)])],
            "pick_tasks": [IndexModel([("sales_order_id", ASCENDING),
                                       ("status", ASCENDING)])],
            "quality_holds": [IndexModel([("status", ASCENDING)])],
            "controlled_registers": [IndexModel([("rx_id", ASCENDING)])],
        }
        for coll, keys in idx.items():
            try:
                await db[coll].create_indexes(keys)
            except Exception as e:  # pragma: no cover
                log.warning("index create failed %s: %s", coll, e)
        # unique human-id indexes (sparse allows docs without the field)
        uniq = {
            "purchase_orders": "po_id",
            "purchase_requisitions": "pr_id",
            "grns": "grn_id",
            "inventory_movements": "movement_id",
            "supplier_invoices": "invoice_id",
            "customer_invoices": "invoice_id",
            "payments": "payment_id",
            "sales_orders": "order_id",
            "quotations": "quote_id",
            "shipments": "shipment_id",
            "asns": "asn_id",
            "qc_samples": "sample_id",
            "production_orders": "order_id",
            "batches": "batch_id",
            "return_requests": "return_id",
            "recalls": "recall_id",
            "safety_cases": "case_id",
        }
        for coll, field in uniq.items():
            try:
                await db[coll].create_index(
                    [(field, ASCENDING)], unique=True, sparse=True)
            except Exception as e:  # pragma: no cover
                log.warning("unique index failed %s: %s", coll, e)


db = Database()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()
