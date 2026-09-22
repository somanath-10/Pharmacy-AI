"""Idempotent seed: master data + rich demo dataset (all workspaces populated).

Run:  python -m app.seed.run          (master only)
      python -m app.seed.run --demo   (master + demo transactions)
"""
import asyncio
import sys

from app.core.config import settings
from app.core.database import db
from app.core.security import hash_password

ROLES_USERS = [
    ("admin@pharmaos.local", "Admin@123", "System Admin", ["SUPER_ADMIN"], None),
    ("buyer@pharmaos.local", "Buyer@123", "Rahul Buyer",
     ["PROCUREMENT", "BUYER"], None),
    ("vendor.manager@pharmaos.local", "Vend@123", "Priya VendorManager",
     ["VENDOR_MANAGER"], None),
    ("qa@pharmaos.local", "Qa@123", "Dr. Meera QA", ["QA"], None),
    ("qc@pharmaos.local", "Qc@123", "Arjun Analyst", ["QC"], None),
    ("plant@pharmaos.local", "Plant@123", "Vikram Plant", ["PLANT"], None),
    ("warehouse@pharmaos.local", "Wh@123", "Suresh Warehouse", ["WAREHOUSE"], None),
    ("pharmacist@pharmaos.local", "Pharm@123", "Dr. Kavya PharmD",
     ["PHARMACIST"], None),
    ("sales@pharmaos.local", "Sales@123", "Anita Sales", ["SALES"], None),
    ("finance@pharmaos.local", "Fin@123", "Karan Finance", ["FINANCE"], None),
    ("logistics@pharmaos.local", "Log@123", "Deepak Logistics",
     ["LOGISTICS"], None),
    ("compliance@pharmaos.local", "Comp@123", "Nisha Compliance",
     ["COMPLIANCE"], None),
    ("auditor@pharmaos.local", "Audit@123", "External Auditor", ["AUDITOR"], None),
    ("vendor@acmecorp.com", "Vendor@123", "Acme Corp Portal",
     ["SUPPLIER"], "VENDOR_PORTAL"),
]

MASTER_PRODUCTS = [
    # sku placeholder → counter; name, type, uom, schedule, storage, mrp
    ("Paracetamol 500mg Tablets", "FINISHED_GOOD", "BOX", None, "AMBIENT", 35.0),
    ("Amoxicillin 250mg Capsules", "FINISHED_GOOD", "BOX", "H", "AMBIENT", 120.0),
    ("Codeine Linctus 100ml", "FINISHED_GOOD", "BOTTLE", "X", "AMBIENT", 210.0),
    ("Azithromycin 500mg Tablets", "FINISHED_GOOD", "BOX", "H1", "AMBIENT", 145.0),
    ("ORS Sachet", "FINISHED_GOOD", "BOX", "OTC", "AMBIENT", 22.0),
    ("Insulin Glargine 100IU/ml", "FINISHED_GOOD", "VIAL", "H", "COLD", 480.0),
    ("Paracetamol API", "RAW_MATERIAL", "KG", None, "AMBIENT", 900.0),
    ("Microcrystalline Cellulose", "RAW_MATERIAL", "KG", None, "AMBIENT", 340.0),
    ("Magnesium Stearate", "RAW_MATERIAL", "KG", None, "AMBIENT", 520.0),
    ("PVC Blister Film", "PACKAGING_MATERIAL", "KG", None, "AMBIENT", 210.0),
    ("Alu Foil Print", "PACKAGING_MATERIAL", "KG", None, "AMBIENT", 260.0),
    ("Carton 10x10", "PACKAGING_MATERIAL", "PCS", None, "AMBIENT", 4.5),
]


async def run_seed(demo: bool = False):
    await _seed_users()
    await _seed_masters()
    if demo or settings.SEED_DEMO:
        from app.seed.demo import seed_demo

        await seed_demo()
    return {"ok": True}


async def _seed_users():
    for email, password, name, roles, vendor_marker in ROLES_USERS:
        if await db.db.users.find_one({"email": email}):
            continue
        vendor_id = None
        if vendor_marker == "VENDOR_PORTAL":
            vendor = await db.db.vendors.find_one({"code": "ACME"})
            vendor_id = vendor["vendor_id"] if vendor else None
        uid = await _next("user", "USR")
        await db.db.users.insert_one({
            "user_id": uid, "email": email, "name": name, "roles": roles,
            "vendor_id": vendor_id,
            "password_hash": hash_password(password),
            "created_at": __import__("app.core.database", fromlist=["now_iso"]).now_iso(),
        })


async def _next(name, prefix):
    doc = await db.db.counters.find_one_and_update(
        {"name": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True)
    return f"{prefix}-{int(doc['seq']):05d}"


async def _seed_masters():
    now = __import__("app.core.database", fromlist=["now_iso"]).now_iso()

    # Site & warehouses
    if not await db.db.warehouses.find_one({"code": "WH-MAIN"}):
        await db.db.warehouses.insert_one({
            "code": "WH-MAIN", "name": "Central Distribution Center",
            "type": "MAIN", "site_id": "SITE-001",
            "storage_conditions": "AMBIENT",
            "zones": ["GOODS_IN", "QUARANTINE", "MAIN", "COLD", "RETURNS"],
            "locations": [
                {"location_id": "WH-MAIN-MAIN-001", "zone": "MAIN"},
                {"location_id": "WH-MAIN-MAIN-002", "zone": "MAIN"},
                {"location_id": "WH-MAIN-COLD-001", "zone": "COLD"},
                {"location_id": "WH-MAIN-QUAR-001", "zone": "QUARANTINE"},
            ],
            "status": "ACTIVE", "created_at": now})
    if not await db.db.warehouses.find_one({"code": "WH-NARC"}):
        await db.db.warehouses.insert_one({
            "code": "WH-NARC", "name": "Narcotics Vault", "type": "NARCOTICS",
            "site_id": "SITE-001", "storage_conditions": "SECURE",
            "zones": ["VAULT"], "locations": [], "status": "ACTIVE",
            "created_at": now})
    if not await db.db.warehouses.find_one({"code": "WH-PLANT"}):
        await db.db.warehouses.insert_one({
            "code": "WH-PLANT", "name": "Plant Raw Material Store",
            "type": "PLANT", "site_id": "SITE-001",
            "storage_conditions": "AMBIENT", "zones": ["RM", "PACKAGING"],
            "locations": [
                {"location_id": "WH-PLANT-RM-001", "zone": "RM"},
                {"location_id": "WH-PLANT-PK-001", "zone": "PACKAGING"},
            ],
            "status": "ACTIVE", "created_at": now})

    # Products
    for name, ptype, uom, schedule, storage, mrp in MASTER_PRODUCTS:
        if await db.db.products.find_one({"name": name}):
            continue
        sku = await _next("product", "PRD")
        await db.db.products.insert_one({
            "sku": sku, "name": name, "type": ptype, "uom": uom,
            "schedule": schedule,
            "is_prescription": schedule in ("H", "H1", "X"),
            "is_controlled": schedule in ("X", "NARCOTIC"),
            "storage_conditions": storage, "mrp": mrp,
            "std_cost": round(mrp * 0.55, 2),
            "safety_stock": 50 if ptype == "FINISHED_GOOD" else 20,
            "reorder_point": 100 if ptype == "FINISHED_GOOD" else 40,
            "shelf_life_days": 730 if ptype == "FINISHED_GOOD" else 1825,
            "status": "ACTIVE", "created_at": now})

    # Customers
    customers = [
        ("CityCare Hospital", "HOSPITAL", 500000),
        ("MedPlus Distributors", "DISTRIBUTOR", 800000),
        ("Wellness Pharmacy Chain", "RETAIL", 250000),
        ("City Chemist", "RETAIL", 50000),
    ]
    for name, ctype, credit in customers:
        if await db.db.customers.find_one({"name": name}):
            continue
        code = await _next("customer", "CUS")
        await db.db.customers.insert_one({
            "code": code, "name": name, "type": ctype,
            "credit_limit": credit, "payment_terms_days": 30,
            "pricing_tier": "STANDARD", "status": "ACTIVE", "created_at": now})

    # Vendors
    vendors = [
        ("ACME", "Acme Pharma Ingredients", "MANUFACTURER", False),
        ("GLOBALLABS", "Global Labs Ltd", "TRADER", False),
        ("BIOCHEM", "BioChem Industries", "MANUFACTURER", True),
    ]
    for code, name, vtype, strategic in vendors:
        if await db.db.vendors.find_one({"code": code}):
            continue
        vendor_id = await _next("vendor", "VDR")
        await db.db.vendors.insert_one({
            "vendor_id": vendor_id, "code": code, "name": name,
            "vendor_type": vtype, "contact": {"email": f"sales@{code.lower()}.com"},
            "status": "REQUESTED", "payment_terms_days": 30,
            "strategic": strategic, "qualification": {"commercial": "PASS",
                                                       "qa": "PASS"},
            "risk_level": "LOW" if not strategic else "MEDIUM",
            "risk_score": 20 if not strategic else 45,
            "performance": {}, "created_at": now})
        # existing suppliers: licence on file; non-strategic auto-approve via policy
        await db.db.vendor_documents.insert_one({
            "vendor_id": vendor_id, "doc_type": "DRUG_LICENCE",
            "doc_number": f"DL-{code}", "expiry_date": "2031-01-01",
            "created_at": now})
        if not strategic:
            await db.db.vendors.update_one(
                {"vendor_id": vendor_id},
                {"$set": {"status": "APPROVED", "approved_at": now}})

    # Equipment
    equipment = [
        ("EQ-RMG-01", "Rapid Mixer Granulator 600L"),
        ("EQ-COMP-01", "Rotary Tablet Press 45 Station"),
        ("EQ-COAT-01", "Auto Coater"),
        ("EQ-BLIS-01", "Blister Packing Line"),
    ]
    for code, name in equipment:
        if await db.db.equipment.find_one({"code": code}):
            continue
        await db.db.equipment.insert_one({
            "code": code, "name": name, "site_id": "SITE-001",
            "calibration_status": "VALID", "maintenance_status": "OK",
            "cleaning_status": "VALID", "status": "RELEASED",
            "logs": [], "created_at": now})
    # one equipment intentionally out-of-calibration for the gate test path
    if not await db.db.equipment.find_one({"code": "EQ-GRAN-02"}):
        await db.db.equipment.insert_one({
            "code": "EQ-GRAN-02", "name": "Legacy Granulator (calibration overdue)",
            "site_id": "SITE-001", "calibration_status": "EXPIRED",
            "maintenance_status": "OK", "cleaning_status": "VALID",
            "status": "RELEASED", "logs": [], "created_at": now})

    # Specifications + BOM for Paracetamol finished good
    fg = await db.db.products.find_one(
        {"name": "Paracetamol 500mg Tablets"})
    if fg:
        if not await db.db.specifications.find_one({"product_id": fg["sku"]}):
            await db.db.specifications.insert_one({
                "product_id": fg["sku"], "version": 1,
                "tests": [
                    {"name": "description", "method": "VISUAL",
                     "acceptance": "White round tablets"},
                    {"name": "identification", "method": "HPLC_RT",
                     "acceptance": "Conforms"},
                    {"name": "assay", "method": "HPLC",
                     "acceptance": {"min": 95.0, "max": 105.0}},
                    {"name": "uniformity", "method": "WEIGHT_VAR",
                     "acceptance": {"max": 7.5}},
                    {"name": "dissolution", "method": "USP",
                     "acceptance": {"min": 80.0}},
                ],
                "sampling_plan": {"n": 3},
                "status": "APPROVED", "created_at": now})
        if not await db.db.boms.find_one({"product_id": fg["sku"]}):
            api = await db.db.products.find_one({"name": "Paracetamol API"})
            mcc = await db.db.products.find_one(
                {"name": "Microcrystalline Cellulose"})
            mgst = await db.db.products.find_one(
                {"name": "Magnesium Stearate"})
            film = await db.db.products.find_one({"name": "PVC Blister Film"})
            carton = await db.db.products.find_one({"name": "Carton 10x10"})
            await db.db.boms.insert_one({
                "product_id": fg["sku"], "version": 1, "batch_size": 1000,
                "uom": "BOX",
                "components": [
                    {"sku": api["sku"], "qty_per_batch": 520.0, "uom": "KG"},
                    {"sku": mcc["sku"], "qty_per_batch": 35.0, "uom": "KG"},
                    {"sku": mgst["sku"], "qty_per_batch": 6.0, "uom": "KG"},
                    {"sku": film["sku"], "qty_per_batch": 12.0, "uom": "KG"},
                    {"sku": carton["sku"], "qty_per_batch": 1010.0, "uom": "PCS"},
                ],
                "process_steps": [
                    {"step": "DISPENSING", "label": "Dispensing & Weighing"},
                    {"step": "GRANULATION", "label": "Granulation"},
                    {"step": "COMPRESSION", "label": "Compression"},
                    {"step": "PACKAGING", "label": "Blister & Carton"},
                ],
                "status": "APPROVED", "created_at": now})
        # FG specification for azithro (pharmacy chain demand)
    azithro = await db.db.products.find_one({"name": "Azithromycin 500mg Tablets"})
    if azithro and not await db.db.specifications.find_one(
            {"product_id": azithro["sku"]}):
        await db.db.specifications.insert_one({
            "product_id": azithro["sku"], "version": 1,
            "tests": [
                {"name": "description", "method": "VISUAL",
                 "acceptance": "Film coated tablets"},
                {"name": "assay", "method": "HPLC",
                 "acceptance": {"min": 93.0, "max": 107.0}},
            ],
            "sampling_plan": {"n": 2},
            "status": "APPROVED", "created_at": now})

    # Raw-material & packaging specifications (QC sampling fires on every GRN line)
    RM_TESTS = {
        "Paracetamol API": [
            {"name": "description", "method": "VISUAL", "acceptance": "White crystalline powder"},
            {"name": "identification", "method": "IR", "acceptance": "Conforms"},
            {"name": "assay", "method": "HPLC", "acceptance": {"min": 99.0, "max": 101.0}},
            {"name": "loss_on_drying", "method": "LOD", "acceptance": {"max": 0.5}},
        ],
        "Microcrystalline Cellulose": [
            {"name": "identification", "method": "IR", "acceptance": "Conforms"},
            {"name": "particle_size", "method": "SIEVE", "acceptance": {"max": 10.0}},
        ],
        "Magnesium Stearate": [
            {"name": "identification", "method": "IR", "acceptance": "Conforms"},
            {"name": "assay", "method": "TITRATION", "acceptance": {"min": 98.0, "max": 102.0}},
        ],
        "PVC Blister Film": [
            {"name": "thickness", "method": "GAUGE", "acceptance": {"min": 0.025, "max": 0.035}},
        ],
        "Alu Foil Print": [
            {"name": "print_quality", "method": "VISUAL", "acceptance": "Legible, no pinholes"},
        ],
        "Carton 10x10": [
            {"name": "dimensions", "method": "GAUGE", "acceptance": {"min": 99, "max": 101}},
        ],
    }
    for rm_name, tests in RM_TESTS.items():
        rm = await db.db.products.find_one({"name": rm_name})
        if rm and not await db.db.specifications.find_one({"product_id": rm["sku"]}):
            await db.db.specifications.insert_one({
                "product_id": rm["sku"], "version": 1,
                "tests": tests,
                "sampling_plan": {"n": 3},
                "status": "APPROVED", "created_at": now})

    # Price list
    async for p in db.db.products.find({"type": "FINISHED_GOOD"}):
        if not await db.db.price_lists.find_one(
                {"product_id": p["sku"], "tier": "STANDARD"}):
            await db.db.price_lists.insert_one({
                "product_id": p["sku"], "tier": "STANDARD",
                "price": p.get("mrp", 100), "currency": "INR",
                "valid_from": now})

    # Carriers
    if not await db.db.carriers.find_one({"code": "BLUEDEX"}):
        await db.db.carriers.insert_one({
            "code": "BLUEDEX", "carrier_id": "CAR-00001", "name": "Bluedex Express",
            "carrier_type": "ROAD", "rating": 4.5, "status": "ACTIVE",
            "temperature_controlled": True, "created_at": now})


if __name__ == "__main__":
    demo = "--demo" in sys.argv
    asyncio.run(run_seed(demo=demo))
    print(f"Seed complete (demo={demo})")
