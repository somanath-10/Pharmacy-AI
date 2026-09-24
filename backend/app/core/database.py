"""MongoDB connection, index management, GridFS bucket, transaction helper.

Transaction honesty: `transaction()` yields a real Motor session only when the
topology supports multi-document transactions (replica set / sharded). On a
standalone dev server it yields None and the caller MUST degrade gracefully —
never claim guarantees that do not exist. `tx_support` reflects reality.

Session propagation: inside `async with db.transaction() as s:` the active
ClientSession is published via a ContextVar; DAL helpers (e.g. inventory
`record_movement`) pick it up automatically so every nested write joins the
transaction without threading a `session=` argument through every caller.
On error exit the pymongo session context manager aborts the transaction.
"""
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, read_concern, write_concern

from app.core.config import settings

log = logging.getLogger("pharmaos.db")

# Active ClientSession for the current async task (None outside a transaction).
_current_session: ContextVar[Optional[object]] = ContextVar(
    "mongo_session", default=None)


def current_session():
    """The Motor ClientSession for the current task, or None (non-transactional).

    DAL helpers pass this into every write so operations join an open
    transaction when one exists. It is ALWAYS safe to pass the result of this
    function as `session=`: None is valid for non-transactional writes.
    """
    return _current_session.get()


class Database:
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None
    gridfs = None
    # accurate topology flag, refreshed on connect
    tx_support: bool = False

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
        # Detect replica-set capability once; refresh lazily on demand.
        self.tx_support = await self._detect_tx_support()
        try:
            from motor.motor_asyncio import AsyncIOMotorGridFSBucket

            self.gridfs = AsyncIOMotorGridFSBucket(self.db)
        except Exception:  # pragma: no cover
            self.gridfs = None
        await self.ensure_indexes()
        log.info("mongo connected db=%s", settings.MONGODB_DB)

    async def close(self):
        if self.client:
            self.client.close()

    async def _detect_tx_support(self) -> bool:
        """True only for replica-set/sharded topologies with USE_TXNS enabled."""
        if not settings.USE_TXNS:
            return False
        try:
            hello = await self.client.admin.command("hello")
            return bool(hello.get("setName"))
        except Exception:
            return False

    async def supports_transactions(self) -> bool:
        if self.tx_support:
            return True
        self.tx_support = await self._detect_tx_support()
        return self.tx_support

    @asynccontextmanager
    async def transaction(self):
        """Async context manager for a multi-document transaction.

        Yields a Motor ClientSession when transactions are available, else None.
        The session is published on the ContextVar, so every DAL helper that
        uses `current_session()` automatically joins the transaction.
        Commits on clean exit; aborts on exception (pymongo semantics of the
        start_transaction context manager). Uses majority write concern so the
        commit itself is durable.

        On a standalone topology this yields None and executes without any
        transactional guarantee — callers must remain individually atomic
        (single-document conditional updates, ledger-first ordering).
        """
        if not await self.supports_transactions():
            yield None
            return
        async with await self.client.start_session() as s:
            token = _current_session.set(s)
            try:
                async with s.start_transaction(
                    read_concern=read_concern.ReadConcern("snapshot"),
                    write_concern=write_concern.WriteConcern("majority"),
                ):
                    yield s
            finally:
                _current_session.reset(token)

    def session(self):
        """Backwards-compatible alias for existing call sites.

        DEPRECATED: returns the *current task's* session (valid to pass as
        session=), never starts a new one. Prefer `db.transaction()`.
        """
        return _current_session.get()

    def topology_info(self) -> dict:
        """Honest topology report — used by readiness/verification endpoints."""
        return {
            "transactions_supported": bool(self.tx_support),
            "replica_set": bool(self.tx_support),
            "standalone": not self.tx_support,
            "use_txns_setting": bool(settings.USE_TXNS),
        }

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
                # unique one row per FULL stock dimension — organization, site,
                # warehouse, location/bin, product, batch, stock status, UOM —
                # matching the service-layer _balance_key exactly. Non-unique
                # here let concurrent first-movements create duplicate rows and
                # could not distinguish two bins holding the same batch.
                IndexModel([("organization_id", ASCENDING),
                            ("site_id", ASCENDING),
                            ("warehouse_id", ASCENDING),
                            ("location_id", ASCENDING),
                            ("product_id", ASCENDING),
                            ("batch_id", ASCENDING),
                            ("stock_status", ASCENDING),
                            ("uom", ASCENDING)],
                           unique=True, name="uniq_balance_dim"),
                IndexModel([("product_id", ASCENDING),
                            ("stock_status", ASCENDING)]),
            ],
            "batches": [IndexModel([("batch_id", ASCENDING)]),
                        IndexModel([("product_id", ASCENDING)])],
            "reservations": [IndexModel([("reference_type", ASCENDING),
                                         ("reference_id", ASCENDING),
                                         ("product_id", ASCENDING)],
                                        unique=True, name="uniq_reservation_ref_product"),
                            IndexModel([("status", ASCENDING)])],
            "dispenses": [IndexModel([("dispense_id", ASCENDING)]),
                          IndexModel([("rx_id", ASCENDING)])],
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
            "production_plans": [IndexModel([("plan_id", ASCENDING)]),
                                 IndexModel([("status", ASCENDING)])],
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
                          IndexModel([("entity_type", ASCENDING)]),
                          IndexModel([("content_hash", ASCENDING)])],
            "notifications": [IndexModel([("status", ASCENDING)]),
                              IndexModel([("created_at", DESCENDING)])],
            "audit_events": [
                IndexModel([("entity_type", ASCENDING),
                            ("entity_id", ASCENDING)]),
                IndexModel([("timestamp", DESCENDING)]),
                IndexModel([("entity_key", ASCENDING),
                            ("actor.id", ASCENDING)]),
                IndexModel([("entity_key", ASCENDING),
                            ("timestamp", DESCENDING)]),
                IndexModel([("correlation_id", ASCENDING)]),
                IndexModel([("actor.id", ASCENDING),
                            ("action", ASCENDING)])],
            "workflows": [IndexModel([("entity_type", ASCENDING),
                                      ("entity_id", ASCENDING)],
                                     unique=True)],
            "approvals": [IndexModel([("status", ASCENDING)]),
                           IndexModel([("category", ASCENDING)]),
                           IndexModel([("entity_type", ASCENDING),
                                       ("entity_id", ASCENDING)])],
            "outbox_events": [IndexModel([("status", ASCENDING),
                                          ("created_at", ASCENDING)])],
            "idempotency_keys": [IndexModel([("key", ASCENDING)],
                                            unique=True)],
            "counters": [IndexModel([("name", ASCENDING)], unique=True)],
            "agent_runs": [IndexModel([("agent", ASCENDING)]),
                                  IndexModel([("created_at", DESCENDING)])],
            "agent_tool_calls": [IndexModel([("agent", ASCENDING)]),
                                  IndexModel([("created_at", DESCENDING)]),
                                  IndexModel([("status", ASCENDING)])],
            "ai_usage": [IndexModel([("model", ASCENDING), ("day", ASCENDING)],
                                     unique=True)],
            "events": [IndexModel([("created_at", DESCENDING)]),
                       IndexModel([("name", ASCENDING)])],
            "planning_proposals": [IndexModel([("status", ASCENDING)]),
                                   IndexModel([("product_id", ASCENDING)])],
            "pick_tasks": [IndexModel([("sales_order_id", ASCENDING),
                                       ("status", ASCENDING)])],
            "quality_holds": [IndexModel([("status", ASCENDING)])],
            "controlled_registers": [IndexModel([("rx_id", ASCENDING)])],
            "campaigns": [IndexModel([("status", ASCENDING)])],
            "stock_plans": [IndexModel([("status", ASCENDING)])],
            "vendor_qualifications": [
                IndexModel([("vendor_id", ASCENDING),
                            ("product_id", ASCENDING),
                            ("site_id", ASCENDING)]),
                IndexModel([("status", ASCENDING)]),
            ],
            "service_requests": [IndexModel([("status", ASCENDING)])],
            # ---- Part 1-8 new collections
            "refresh_tokens": [
                IndexModel([("token_hash", ASCENDING)], unique=True),
                IndexModel([("user_id", ASCENDING), ("revoked_at", ASCENDING)]),
                IndexModel([("expires_at", ASCENDING)],
                           expireAfterSeconds=0),  # TTL cleanup
            ],
            "password_resets": [IndexModel([("token_hash", ASCENDING)]),
                                IndexModel([("expires_at", ASCENDING)],
                                           expireAfterSeconds=0)],
            "vendor_bank_verifications": [
                IndexModel([("vendor_id", ASCENDING),
                            ("requested_by", ASCENDING)])],
            "po_amendments": [IndexModel([("po_id", ASCENDING)]),
                              IndexModel([("request_id", ASCENDING)],
                                         unique=True, sparse=True)],
            "po_versions": [IndexModel([("po_id", ASCENDING),
                                        ("version", ASCENDING)],
                                       unique=True)],
            "vendor_disputes": [IndexModel([("vendor_id", ASCENDING),
                                            ("status", ASCENDING)])],
            "vendor_questionnaires": [
                IndexModel([("vendor_id", ASCENDING),
                            ("status", ASCENDING)]),
                IndexModel([("questionnaire_id", ASCENDING)], unique=True)],
            "vendor_requalifications": [
                IndexModel([("vendor_id", ASCENDING)])],
            "qa_issues": [IndexModel([("vendor_id", ASCENDING),
                                      ("status", ASCENDING)])],
            "demand_history": [IndexModel([("product_id", ASCENDING),
                                           ("period", ASCENDING)],
                                          unique=True)],
            "budgets": [IndexModel([("department", ASCENDING)], unique=True)],
            # ---- WMS / quality phase new collections
            "warehouse_locations": [
                IndexModel([("warehouse_id", ASCENDING),
                            ("location_id", ASCENDING)], unique=True),
                IndexModel([("zone", ASCENDING)])],
            "gate_entries": [IndexModel([("asn_id", ASCENDING)]),
                             IndexModel([("status", ASCENDING)])],
            "replenishment_tasks": [
                IndexModel([("status", ASCENDING)]),
                IndexModel([("warehouse_id", ASCENDING),
                            ("product_id", ASCENDING)])],
            "transfer_orders": [
                IndexModel([("transfer_id", ASCENDING)], unique=True),
                IndexModel([("status", ASCENDING)])],
            "cycle_counts": [
                IndexModel([("count_id", ASCENDING)], unique=True),
                IndexModel([("warehouse_id", ASCENDING),
                            ("product_id", ASCENDING)]),
                IndexModel([("status", ASCENDING)])],
            "change_requests": [
                IndexModel([("change_id", ASCENDING)], unique=True),
                IndexModel([("status", ASCENDING)]),
                IndexModel([("target_id", ASCENDING)])],
            "risk_assessments": [
                IndexModel([("risk_id", ASCENDING)], unique=True),
                IndexModel([("subject_type", ASCENDING),
                            ("subject_id", ASCENDING)])],
            "reagents": [IndexModel([("reagent_id", ASCENDING)], unique=True),
                         IndexModel([("kind", ASCENDING),
                                     ("status", ASCENDING)])],
            "stability_studies": [
                IndexModel([("study_id", ASCENDING)], unique=True),
                IndexModel([("product_id", ASCENDING)])],
            "qa_vehicles": [IndexModel([("vehicle_id", ASCENDING)], unique=True)],
            "qa_drivers": [IndexModel([("driver_id", ASCENDING)], unique=True)],
            "qa_routes": [IndexModel([("route_id", ASCENDING)], unique=True)],
            "vehicles": [IndexModel([("vehicle_id", ASCENDING)], unique=True)],
            "drivers": [IndexModel([("driver_id", ASCENDING)], unique=True)],
            "routes": [IndexModel([("route_id", ASCENDING)], unique=True)],
            "dock_appointments": [
                IndexModel([("appointment_id", ASCENDING)], unique=True),
                IndexModel([("gate_entry_id", ASCENDING)],
                           unique=True, sparse=True)],
            "delivery_attempts": [
                IndexModel([("shipment_id", ASCENDING)])],
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
            "dispenses": "dispense_id",
            "change_requests": "change_id",
            "transfer_orders": "transfer_id",
            "qc_samples": "sample_id",
        }
        # partial-unique guards for one-per-parent documents
        partial_uniq = [
            # one dispense per prescription
            ("dispenses", [("rx_id", ASCENDING)], {"rx_id": {"$type": "string"}}),
            # one customer invoice per sales order
            ("customer_invoices", [("sales_order_id", ASCENDING)],
             {"sales_order_id": {"$type": "string"}}),
            # one supplier invoice per (PO, supplier invoice number)
            ("supplier_invoices", [("po_id", ASCENDING),
                                   ("supplier_invoice_number", ASCENDING)],
             {"po_id": {"$type": "string"},
              "supplier_invoice_number": {"$type": "string"}}),
            # one PENDING approval per (entity, category) — duplicate
            # decision-queue items for the same payment/PO/vendor would risk
            # double execution of the approval callback
            ("approvals", [("entity_type", ASCENDING), ("entity_id", ASCENDING),
                           ("category", ASCENDING)],
             {"entity_type": {"$type": "string"},
              "entity_id": {"$type": "string"},
              "category": {"$type": "string"}, "status": "PENDING"}),
        ]
        for coll, keys, flt in partial_uniq:
            try:
                await db[coll].create_index(
                    keys, unique=True, partialFilterExpression=flt)
            except Exception as e:  # pragma: no cover
                log.warning("partial unique index failed %s: %s", coll, e)
                # Databases created before a unique index was added keep a
                # non-unique index with the same auto-generated name; the
                # create above is silently ignored. Reconcile: drop the stale
                # index and recreate it as truly unique, otherwise duplicate
                # dispenses / invoices / GRNs remain possible on upgraded DBs.
                try:
                    idx_name = "_".join(f"{k[0]}_1" for k in keys)
                    await db[coll].drop_index(idx_name)
                    await db[coll].create_index(
                        keys, unique=True, partialFilterExpression=flt)
                    log.info("reconciled unique index %s.%s", coll, idx_name)
                except Exception as e2:  # pragma: no cover
                    log.warning("unique index reconcile failed %s: %s",
                                coll, e2)
        # idempotency keys expire after their TTL (expires_at datetime)
        try:
            await db.idempotency_keys.create_index(
                [("expires_at", ASCENDING)], expireAfterSeconds=0)
        except Exception as e:  # pragma: no cover
            log.warning("idempotency TTL index failed: %s", e)
        for coll, field in uniq.items():
            try:
                # explicit name: a plain non-unique index on the same field
                # (created above) would otherwise shadow/conflict with this
                # unique one and the constraint would silently not exist
                await db[coll].create_index(
                    [(field, ASCENDING)], unique=True, sparse=True,
                    name=f"uniq_{field}")
            except Exception as e:  # pragma: no cover
                log.warning("unique index failed %s: %s", coll, e)


db = Database()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()
