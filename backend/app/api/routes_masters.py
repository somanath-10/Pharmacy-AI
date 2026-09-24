"""Masters routes."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from app.core.security import get_current_principal
from app.domains import masters as masters_svc

router = APIRouter(prefix="/api/masters", tags=["masters"])


@router.get("/warehouses")
async def list_warehouses(principal: dict = Depends(get_current_principal)):
    return await masters_svc.list_warehouses()


@router.post("/warehouses")
async def create_warehouse(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_warehouse(payload)


@router.get("/products")
async def list_products(type: Optional[str] = Query(None),
                        principal: dict = Depends(get_current_principal)):
    return await masters_svc.list_products(type)


@router.post("/products")
async def create_product(payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_product(payload)


@router.get("/products/{sku}")
async def get_product(sku: str,
                      principal: dict = Depends(get_current_principal)):
    return await masters_svc.get_product(sku)


@router.get("/customers")
async def list_customers(principal: dict = Depends(get_current_principal)):
    return await masters_svc.list_customers()


@router.post("/customers")
async def create_customer(payload: dict = Body(...),
                          principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_customer(payload)


@router.get("/equipment/due-report")
async def equipment_due_report(principal: dict = Depends(get_current_principal)):
    return await masters_svc.equipment_due_report()


@router.get("/equipment")
async def list_equipment(principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    async for e in db.db.equipment.find({}):
        e.pop("_id", None)
        rows.append(e)
    return rows


@router.post("/equipment")
async def create_equipment(payload: dict = Body(...),
                           principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_equipment(payload)


@router.get("/specifications")
async def list_specifications(principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    async for s in db.db.specifications.find({}):
        s.pop("_id", None)
        rows.append(s)
    return rows


@router.post("/specifications")
async def create_specification(payload: dict = Body(...),
                               principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_specification(payload)


@router.post("/specifications/{product_id}/versions/{version}/approve")
async def approve_specification(product_id: str, version: int,
                                payload: dict = Body(default={}),
                                principal: dict = Depends(get_current_principal)):
    return await masters_svc.approve_specification(product_id, version, principal,
                                                   payload.get("notes", ""))


@router.get("/boms")
async def list_boms(principal: dict = Depends(get_current_principal)):
    rows = []
    from app.core.database import db

    async for b in db.db.boms.find({}):
        b.pop("_id", None)
        rows.append(b)
    return rows


@router.post("/boms")
async def create_bom(payload: dict = Body(...),
                     principal: dict = Depends(get_current_principal)):
    return await masters_svc.create_bom(payload)


@router.post("/prices")
async def upsert_price(payload: dict = Body(...),
                       principal: dict = Depends(get_current_principal)):
    return await masters_svc.upsert_price(payload)


# ------------------------------------------- equipment ops (Parts 16 & 18)
@router.post("/equipment/{code}/calibrate")
async def calibrate_equipment(code: str, payload: dict = Body(...),
                              principal: dict = Depends(get_current_principal)):
    return await masters_svc.calibrate_equipment(code, payload, principal)


@router.post("/equipment/{code}/maintenance")
async def maintenance_equipment(code: str, payload: dict = Body(...),
                                principal: dict = Depends(get_current_principal)):
    return await masters_svc.maintenance_equipment(code, payload, principal)


@router.post("/equipment/{code}/hold")
async def hold_equipment(code: str, payload: dict = Body(...),
                         principal: dict = Depends(get_current_principal)):
    return await masters_svc.hold_equipment(code, payload.get("reason", "HOLD"),
                                            principal)


@router.post("/equipment/{code}/use")
async def use_equipment(code: str, payload: dict = Body(...),
                        principal: dict = Depends(get_current_principal)):
    return await masters_svc.use_equipment(code, payload.get("purpose", ""),
                                           principal)


