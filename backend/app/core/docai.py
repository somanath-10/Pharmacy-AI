"""Shared Document Intelligence (Document AI) pipeline.

Document → classification → extraction (OpenAI structured or offline fallback)
→ master-data matching → deterministic validation → confidence → routing hint.
Used for customer POs, quotations, licences, CoAs, invoices, prescriptions,
packing lists, ASN docs, contracts, QC reports, batch records and PODs.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.ai_gateway import (
    ai,
    offline_classify,
    offline_extract_invoice,
    offline_extract_prescription,
    offline_summarize,
)
from app.core.database import db, now_iso
from app.core.errors import NotFound

log = logging.getLogger("pharmaos.docai")

DOC_TYPES = [
    "prescription", "supplier_invoice", "customer_po", "quotation",
    "certificate_of_analysis", "drug_licence", "packing_list", "asn_document",
    "contract", "qc_report", "batch_record", "pod", "rfq_response", "unknown",
]


async def register_document(
    filename: str,
    content: bytes,
    content_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    uploaded_by: Optional[dict] = None,
    doc_type_hint: Optional[str] = None,
) -> dict:
    from app.core.storage import storage

    ref, mode = await storage.put(content, filename, content_type)
    text = extract_text(content)
    doc = {
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "storage_ref": ref,
        "storage_mode": mode,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "doc_type": doc_type_hint or "unknown",
        "text_preview": text[:4000],
        "classification": None,
        "extraction": None,
        "matching": None,
        "validation": None,
        "confidence": None,
        "processing_status": "REGISTERED",
        "uploaded_by": uploaded_by,
        "created_at": now_iso(),
    }
    res = await db.db.documents.insert_one(doc)
    doc["_id"] = res.inserted_id
    doc["document_id"] = str(res.inserted_id)
    await db.db.documents.update_one(
        {"_id": res.inserted_id}, {"$set": {"document_id": doc["document_id"]}}
    )
    return doc


def extract_text(content: bytes) -> str:
    """Best-effort text extraction: plain text / pdf text via pypdf if present."""
    if content[:5] == b"%PDF-":
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return "\n".join((p.extract_text() or "") for p in reader.pages[:10])
        except Exception:
            return ""
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return ""


async def classify(document_id: str) -> str:
    doc = await _get(document_id)
    if doc.get("doc_type") and doc["doc_type"] != "unknown":
        label = doc["doc_type"]
        fb = False
    else:
        label = offline_classify(doc["filename"], doc.get("text_preview", ""))
        fb = True
        res = await ai.extract_json(
            "Classify the pharmaceutical business document into exactly one of: "
            + ", ".join(DOC_TYPES)
            + '. Return {"doc_type": "..."}.',
            doc.get("text_preview", "")[:3000],
        )
        if res.get("ok") and res.get("data", {}).get("doc_type") in DOC_TYPES:
            label = res["data"]["doc_type"]
            fb = False
    await db.db.documents.update_one(
        {"document_id": document_id},
        {"$set": {"classification": {"doc_type": label, "fallback": fb,
                                     "at": now_iso()},
                  "doc_type": label, "processing_status": "CLASSIFIED"}},
    )
    return label


EXTRACTORS = {
    "supplier_invoice": "_extract_invoice",
    "prescription": "_extract_rx",
    "customer_po": "_extract_customer_po",
    "quotation": "_extract_quotation",
    "drug_licence": "_extract_licence",
    "certificate_of_analysis": "_extract_coa",
}


def _extractor(doc_type: str):
    return globals().get(EXTRACTORS.get(doc_type, "")) or _extract_generic


async def extract(document_id: str) -> dict:
    doc = await _get(document_id)
    doc_type = doc.get("doc_type") or await classify(document_id)
    text = doc.get("text_preview", "")
    data, conf, fb = await _extractor(doc_type)(text)
    await db.db.documents.update_one(
        {"document_id": document_id},
        {"$set": {"extraction": {"data": data, "confidence": conf, "fallback": fb},
                  "processing_status": "EXTRACTED", "confidence": conf}},
    )
    return data


async def _extract_invoice(text: str):
    data = offline_extract_invoice(text)
    fb = True
    res = await ai.extract_json(
        'Extract supplier invoice fields as JSON: {"invoice_number","po_number",'
        '"invoice_date","due_date","currency","subtotal","tax_amount","total_amount",'
        '"line_items":[{"description","quantity","uom","unit_price","amount"}]}',
        text[:6000],
    )
    if res.get("ok") and res.get("data"):
        merged = {**data, **{k: v for k, v in res["data"].items() if v}}
        return merged, 0.92, False
    conf = 0.6 if data else 0.2
    return data, conf, fb


async def _extract_rx(text: str):
    data = offline_extract_prescription(text)
    res = await ai.extract_json(
        'Extract prescription as JSON: {"patient","patient_age","prescriber",'
        '"medicines":[{"name","strength_mg","form","frequency","duration_days",'
        '"instructions"}]}',
        text[:6000],
    )
    if res.get("ok") and res.get("data", {}).get("medicines"):
        return res["data"], 0.93, False
    conf = 0.55 if data.get("medicines") else 0.25
    return data, conf, fb


async def _extract_customer_po(text: str):
    data = offline_extract_invoice(text)
    res = await ai.extract_json(
        'Extract customer purchase order as JSON: {"po_number","po_date","delivery_date",'
        '"line_items":[{"product","quantity","uom","unit_price"}],"ship_to"}',
        text[:6000],
    )
    if res.get("ok") and res.get("data"):
        return res["data"], 0.9, False
    return data, 0.5, True


async def _extract_quotation(text: str):
    res = await ai.extract_json(
        'Extract vendor quotation as JSON: {"vendor","quote_number","currency",'
        '"line_items":[{"product","quantity","uom","unit_price","lead_time_days"}],'
        '"validity_days","payment_terms"}',
        text[:6000],
    )
    if res.get("ok") and res.get("data"):
        return res["data"], 0.88, False
    return {}, 0.3, True


async def _extract_licence(text: str):
    data = {}
    m = re.search(r"(20B|21B|20-B|21-B|DL[-\s]?[A-Z0-9]+)", text, re.I)
    if m:
        data["licence_number"] = m.group(1)
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
    if dates:
        data["valid_till"] = dates[-1]
    return data, 0.5 if data else 0.2, True


async def _extract_coa(text: str):
    data: Dict[str, Any] = {"tests": []}
    m = re.search(r"batch\s*(?:no|number)?\s*[:\-]?\s*([A-Z0-9-]+)", text, re.I)
    if m:
        data["batch_number"] = m.group(1)
    for m in re.finditer(r"([A-Za-z ]{3,40})\s*(?:[:=])\s*(Pass|Fail|\d+\.?\d*)", text, re.I):
        data["tests"].append({"test": m.group(1).strip(), "result": m.group(2)})
    return data, 0.5 if data["tests"] else 0.2, True


async def _extract_generic(text: str):
    summary = offline_summarize(text)
    return {"summary": summary}, 0.35, True


async def match_to_masters(document_id: str) -> dict:
    """Master-data matching: suppliers/products/customers against known masters."""
    doc = await _get(document_id)
    data = (doc.get("extraction") or {}).get("data") or {}
    result: Dict[str, Any] = {"vendor": None, "customer": None, "products": []}

    vendor = None
    for probe in (data.get("supplier"), data.get("vendor"), doc.get("filename")):
        if not probe:
            continue
        vendor = await db.db.vendors.find_one(
            {"name": {"$regex": re.escape(str(probe)[:30]), "$options": "i"}}
        )
        if vendor:
            break
    if vendor:
        result["vendor"] = {"vendor_id": vendor["vendor_id"], "name": vendor["name"],
                            "match": "name"}

    customer = None
    for probe in (data.get("customer"), data.get("bill_to")):
        if probe:
            customer = await db.db.customers.find_one(
                {"name": {"$regex": re.escape(str(probe)[:30]), "$options": "i"}}
            )
            if customer:
                break
    if customer:
        result["customer"] = {"customer_id": customer["customer_id"], "name": customer["name"]}

    for item in data.get("line_items", []) or []:
        name = item.get("product") or item.get("description") or ""
        prod = await db.db.products.find_one(
            {"name": {"$regex": re.escape(name[:30]), "$options": "i"}}
        )
        result["products"].append({
            "raw": name, "product_id": prod["product_id"] if prod else None,
            "sku": prod["sku"] if prod else None, "matched": bool(prod),
        })

    await db.db.documents.update_one(
        {"document_id": document_id},
        {"$set": {"matching": result, "processing_status": "MATCHED"}},
    )
    return result


async def validate(document_id: str) -> dict:
    """Deterministic validation after extraction (sum checks, dates, masters)."""
    doc = await _get(document_id)
    data = (doc.get("extraction") or {}).get("data") or {}
    issues: List[str] = []
    ok = True

    if doc["doc_type"] == "supplier_invoice":
        lines = data.get("line_items") or []
        total = data.get("total_amount")
        if lines and total:
            try:
                s = sum(float(l.get("amount") or 0) for l in lines)
                if abs(s - float(total)) > 0.01:
                    issues.append(f"Line total {s} != invoice total {total}")
                    ok = False
            except (TypeError, ValueError):
                issues.append("Non-numeric amounts")
                ok = False
        if not data.get("invoice_number"):
            issues.append("Missing invoice number")
            ok = False

    if doc["doc_type"] == "prescription":
        if not data.get("medicines"):
            issues.append("No medicines detected")
            ok = False
        if not data.get("prescriber"):
            issues.append("Prescriber not identified")
            ok = False

    confidence = (doc.get("extraction") or {}).get("confidence") or 0
    route = "AUTO_PROCESS" if ok and confidence >= 0.75 else (
        "HUMAN_REVIEW" if ok else "EXCEPTION"
    )
    validation = {"ok": ok, "issues": issues, "route": route,
                  "confidence": confidence, "at": now_iso()}
    await db.db.documents.update_one(
        {"document_id": document_id},
        {"$set": {"validation": validation, "processing_status": route}},
    )
    return validation


async def process(document_id: str) -> dict:
    await classify(document_id)
    await extract(document_id)
    await match_to_masters(document_id)
    v = await validate(document_id)
    from app.core.events import bus

    doc = await _get(document_id)
    await bus.publish("document.processed",
                      {"document_id": document_id, "doc_type": doc["doc_type"],
                       "route": v["route"]})
    return {"doc_type": doc["doc_type"], "extraction": doc.get("extraction"),
            "matching": doc.get("matching"), "validation": v}


async def _get(document_id: str) -> dict:
    doc = await db.db.documents.find_one({"document_id": document_id})
    if not doc:
        raise NotFound(f"Document {document_id} not found")
    return doc
