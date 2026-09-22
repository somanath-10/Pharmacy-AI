"""AI Gateway — single entry point to OpenAI (with offline deterministic fallback).

Used by Document AI, agents and analytics. When OPENAI_API_KEY is absent or the
API fails, deterministic fallbacks keep every workflow functional (flagged
`fallback: true`). LLM output is treated as a draft/recommendation, never
source of truth.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings

log = logging.getLogger("pharmaos.ai")


class AIResult(dict):
    @property
    def fallback(self) -> bool:
        return bool(self.get("fallback"))


class AIGateway:
    def __init__(self):
        self._client = None

    @property
    def available(self) -> bool:
        return bool(settings.OPENAI_API_KEY)

    def _get_client(self):
        if self._client is None and self.available:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY, timeout=settings.AI_TIMEOUT_SECONDS
            )
        return self._client

    async def complete(self, system: str, user: str, json_mode: bool = False,
                       max_tokens: int = 800) -> AIResult:
        client = self._get_client()
        if client is None:
            return AIResult({"ok": False, "fallback": True,
                             "text": "", "error": "openai_not_configured"})
        try:
            kwargs: Dict[str, Any] = {
                "model": settings.OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.completions.create(**kwargs)
            return AIResult({"ok": True, "fallback": False,
                             "text": resp.choices[0].message.content or ""})
        except Exception as e:
            log.warning("openai failed → fallback: %s", e)
            return AIResult({"ok": False, "fallback": True, "text": "", "error": str(e)[:200]})

    async def extract_json(self, system: str, user: str) -> AIResult:
        res = await self.complete(system, user, json_mode=True)
        if res.get("ok"):
            try:
                return AIResult({"ok": True, "fallback": False,
                                 "data": json.loads(res["text"])})
            except Exception:
                pass
        return AIResult({"ok": False, "fallback": True, "data": None})


ai = AIGateway()


# ------------------------------------------------------------------ fallbacks
def offline_classify(filename: str, text: str) -> str:
    t = (filename + " " + text[:2000]).lower()
    patterns = [
        ("prescription", ["rx", "prescription", "dr.", "mg", "take ", "refill"]),
        ("supplier_invoice", ["invoice", "tax invoice", "gst", "amount payable", "bill to"]),
        ("customer_po", ["purchase order", "po number", "buyer", "ship to"]),
        ("quotation", ["quotation", "quote", "validity", "unit price"]),
        ("certificate_of_analysis", ["certificate of analysis", "coa", "specification",
                                     "test result"]),
        ("drug_licence", ["drug licence", "license", "20b", "21b", "wholesale"]),
        ("packing_list", ["packing list", "package", "carton"]),
        ("asn_document", ["asn", "advance shipping", "dispatch note"]),
        ("contract", ["agreement", "contract", "party", "term"]),
    ]
    for label, keys in patterns:
        if any(k in t for k in keys):
            return label
    return "unknown"


def offline_extract_invoice(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    m = re.search(r"invoice\s*(?:no|number|#)\s*[:\-]?\s*([A-Za-z0-9/_-]+)", text, re.I)
    if m:
        data["invoice_number"] = m.group(1)
    m = re.search(r"(?:po|p\.o\.|order)\s*(?:no|number|#)\s*[:\-]?\s*([A-Za-z0-9/_-]+)", text, re.I)
    if m:
        data["po_number"] = m.group(1)
    dates = re.findall(r"(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", text)
    if dates:
        data["invoice_date"] = dates[0]
    amounts = re.findall(r"(?:total|amount|grand total|payable)\D{0,20}([\d,]+(?:\.\d{1,2})?)",
                         text, re.I)
    if amounts:
        try:
            data["total_amount"] = float(amounts[-1].replace(",", ""))
        except ValueError:
            pass
    gst = re.search(r"gst(?:in)?\s*[:\-]?\s*([0-9A-Z]{15})", text, re.I)
    if gst:
        data["supplier_gstin"] = gst.group(1)
    return data


def offline_extract_prescription(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"medicines": []}
    m = re.search(r"(?:dr|doctor)\.?\s*([A-Za-z .]+)", text, re.I)
    if m:
        data["prescriber"] = m.group(1).strip()
    m = re.search(r"(?:patient|name)\s*[:\-]\s*([A-Za-z .]+)", text, re.I)
    if m:
        data["patient"] = m.group(1).strip()
    age = re.search(r"age\s*[:\-]?\s*(\d{1,3})", text, re.I)
    if age:
        data["patient_age"] = int(age.group(1))
    # "Tab Paracetamol 500mg 1-0-1 x 5 days" style lines
    for m in re.finditer(
        r"(tab|cap|syrup|inj|bottle|vial)?\.?\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
        r"\s+(\d{1,4})\s*(mg|ml|iu|mcg|g)\s*"
        r"(\d\s*-\s*\d\s*-\s*\d)?\s*(?:x\s*)?(\d+)?\s*days?",
        text,
        re.I,
    ):
        strength = float(m.group(3))
        unit = m.group(4).lower()
        # normalise to mg where sensible (volumes/units are informational)
        strength_mg = int(strength) if unit == "mg" else (
            int(strength * 1000) if unit == "g" else None)
        data["medicines"].append({
            "form": (m.group(1) or "tab").lower(),
            "name": re.sub(
                r"^(tab|tabs|tablet|tablets|cap|caps|capsule|capsules|syrup|"
                r"inj|injection|bottle|vial)\s+", "", m.group(2).strip(),
                flags=re.I),
            "strength": strength,
            "strength_unit": unit,
            "strength_mg": strength_mg,
            "frequency": (m.group(5) or "").replace(" ", "") or None,
            "duration_days": int(m.group(6)) if m.group(6) else None,
        })
    return data


def offline_summarize(text: str, max_chars: int = 400) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
