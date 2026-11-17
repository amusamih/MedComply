from __future__ import annotations
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from .db import QuotaCache, quota_id, session, audit
from .storage import Documents
from .chain import serial

def micrograms(value, unit):
    factors = {"ug":1,"µg":1,"mg":1000,"g":1_000_000,"kg":1_000_000_000}
    if isinstance(value,bool) or unit not in factors: raise ValueError("Quantity or unit is invalid")
    try: result = Decimal(str(value)) * factors[unit]
    except InvalidOperation: raise ValueError("Quantity is not numeric") from None
    if not result.is_finite() or result <= 0 or result != result.to_integral_value() or result > 9_000_000_000_000_000_000:
        raise ValueError("Quantity must be positive, finite, and exactly representable in micrograms")
    return int(result)

def refresh_cache(chain, account, country, substance, year, mode):
    block = chain.w3.eth.block_number
    q = chain.quota(country,substance,year,mode,block)
    history = chain.history(q["key"],block)
    if q["used"] != history["used"] or q["reserved"] != history["reserved"]:
        raise ValueError("Ledger reconstruction differs from the contract; cache unchanged")
    with session() as db:
        for scope,field in [("national","nationalCap"),("global","globalCap")]:
            key = quota_id(country,substance,year,mode,scope,chain.network+"@"+chain.ship.address.lower())
            row = db.get(QuotaCache,key)
            if row is None:
                row = QuotaCache(id=key,network=chain.network,country=country,substance=substance,year=year,mode=mode,scope=scope)
                db.add(row)
            row.cap=q[field]; row.used=q["used"]; row.reserved=q["reserved"]
            row.block_number=block; row.block_hash=history["block_hash"]
        audit(db,account,"quota_cache_refreshed",{"network":chain.network,"key":q["key"],"block":block,"block_hash":history["block_hash"],"used":q["used"],"reserved":q["reserved"]})
        db.commit()
    return {"contract":q,"ledger":history}

def check_quota(chain, shipment, mode, scope, *, reserved_import=False):
    """Read-only assessment; never changes usage, caps, or a disputed cache."""
    if mode not in ("export", "import") or scope not in ("national", "global"):
        raise ValueError("Explicit quota mode and scope required")
    if reserved_import and (mode != "import" or scope != "national" or shipment["state"] != "in_transit"):
        raise ValueError("Reservation audit requires a national import review in transit")
    country = shipment["origin"] if mode == "export" else shipment["destination"]
    q = chain.quota(country,shipment["substance"],shipment["year"],mode)
    history = chain.history(q["key"])
    result = {"country":country,"substance":shipment["substance"],"year":shipment["year"],"mode":mode,"scope":scope,
              "quantity":shipment["quantity"],"unit":"ug","contract":q,"ledger":history,
              "decision":"review","findings":[],"document_cid":None,"correction":None}
    findings = result["findings"]
    if not q["nationalSet"] or not q["globalSet"]:
        findings.append("QUOTA_NOT_CONFIGURED"); return result
    if q["used"] != history["used"] or q["reserved"] != history["reserved"]:
        findings.append("LEDGER_STATE_DISAGREEMENT"); return result
    # Final shipping approval enforces BOTH caps. Recheck the national allowance
    # here as well: earlier permit issuance does not reserve that allowance.
    checked_scopes = ("national", "global") if scope == "global" or reserved_import else ("national",)
    caches = {}
    with session() as db:
        for checked_scope in checked_scopes:
            row = db.get(QuotaCache,quota_id(country,shipment["substance"],shipment["year"],mode,checked_scope,chain.network+"@"+chain.ship.address.lower()))
            if row is None:
                findings.append("CACHE_UNAVAILABLE"); return result
            caches[checked_scope] = {"cap":row.cap,"used":row.used,"reserved":row.reserved,"block_number":row.block_number,"block_hash":row.block_hash}
    cached = caches[scope]
    result["database"] = cached
    result["database_by_scope"] = caches
    cid = chain.comp.functions.getIncbCapIpfs(shipment["year"]).call()
    result["document_cid"] = cid
    try:
        declaration = Documents().json(cid)
        entries = [r for r in declaration["quotas"] if r["country"] == country and r["substance"] == shipment["substance"] and r["mode"] == mode]
        if len(entries) != 1 or declaration["unit"] != "ug" or declaration["year"] != shipment["year"]:
            raise ValueError("Ambiguous declaration")
        caps = {}
        for checked_scope in checked_scopes:
            field = "nationalCap" if checked_scope == "national" else "globalCap"
            declared = entries[0][field]
            if type(declared) is not int or declared <= 0 or declared != q[field] or declared != caches[checked_scope]["cap"]:
                findings.append("CAP_INTEGRITY_FAILED"); return result
            caps[checked_scope] = declared
        cap = min(caps.values())
        result["applicable_caps"] = caps
        result["limit_basis"] = "minimum_of_national_and_global_caps" if len(checked_scopes) == 2 else "national_cap"
    except Exception:
        findings.append("QUOTA_DOCUMENT_UNUSABLE"); return result
    stale_scopes = [name for name, values in caches.items()
                    if history["used"] != values["used"] or history["reserved"] != values["reserved"]]
    if stale_scopes:
        findings.append("STALE_DATABASE_USAGE")
        result["correction"] = {"kind":"refresh_cache_from_verified_ledger","before":cached,
                                "affected_scopes":stale_scopes,"before_by_scope":caches,
                                "proposed":{"used":history["used"],"reserved":history["reserved"]},"requires_human_approval":True}
    if reserved_import:
        result["assessment_purpose"] = "import_permit_replacement"
        if history.get("complete") is not True:
            findings.append("INCOMPLETE_RESERVATION_HISTORY"); return result
        events = [e for e in history["events"] if e["args"].get("shipmentId") == shipment["id"]]
        reservations = [e for e in events if e["name"] == "QuotaReserved"]
        settlements = [e for e in events if e["name"] in ("QuotaReleased", "QuotaConsumed")]
        if len(reservations) != 1 or reservations[0]["args"].get("quantity") != shipment["quantity"] or settlements:
            findings.append("SHIPMENT_IMPORT_RESERVATION_UNVERIFIED"); return result
        if q["reserved"] < shipment["quantity"] or q["used"] + q["reserved"] > cap:
            findings.append("RESERVATION_BALANCE_INVALID"); return result
        result.update({"cap":cap,"used":q["used"],"reserved":q["reserved"],
                       "remaining":cap-q["used"]-q["reserved"],"shipment_reserved":shipment["quantity"]})
        result["decision"] = "review" if findings else "approve"
        return result
    # Initial permit assessments allocate no quota. A committed shipment is only
    # eligible for the separate reservation audit above, never another allocation.
    if shipment["state"] not in ("created","export_cleared"):
        findings.append("SHIPMENT_ALREADY_COMMITTED_OR_CLOSED"); return result
    remaining = cap - history["used"] - history["reserved"]
    result.update({"cap":cap,"used":history["used"],"reserved":history["reserved"],"remaining":remaining})
    if shipment["quantity"] > remaining:
        result["decision"]="reject"; findings.append("QUOTA_EXCEEDED")
    elif findings:
        result["decision"]="review"
    else: result["decision"]="approve"
    return result
