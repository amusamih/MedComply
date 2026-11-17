from __future__ import annotations
import json
from sqlalchemy import select
from web3 import Web3
from .chain import Chain, country, substance, serial
from .db import Review, Assessment, Draft, session, audit, digest, identifier, now
from .quota import check_quota, refresh_cache
from .storage import Documents

class ReviewError(ValueError): pass
H = object()

def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 9_000_000_000_000_000_000: raise ReviewError("Invalid integer quantity or identifier")
    return value

def require(condition,message):
    if not condition: raise ReviewError(message)

def describe(chain,account,action,a,notes):
    """Derive permitted transactions from verified state, never arbitrary model tool data."""
    identity=chain.identity(account); role=identity["role"]
    sid=integer(a.get("shipment_id",0))
    s=chain.shipment(sid) if sid else None
    snapshot={"identity":identity,"contracts":chain.deployment["contracts"]}
    if s: snapshot["shipment_fingerprint"]=chain.fingerprint(sid)
    operational={"request_registration","request_import_permit","request_export_permit","create_shipment","pickup","receive","record_transit"}
    if action not in operational: require(10<=len(notes.strip())<=4000,"Enter meaningful human review notes (10-4000 characters)")
    contract=fn=None; args=[]; evidence={}
    if action == "register_nca":
        require(role=="incb","INCB account required")
        target=Web3.to_checksum_address(a["account"]); iso=country(a["country"])
        snapshot["authority"]=serial(chain.reg.functions.authorities(target).call())
        contract,fn,args="Registration","registerNCA",[target,iso]
    elif action == "revoke_nca":
        require(role=="incb","INCB account required")
        target=Web3.to_checksum_address(a["account"])
        snapshot["authority"]=serial(chain.reg.functions.authorities(target).call())
        contract,fn,args="Registration","revokeNCA",[target]
    elif action == "request_registration":
        require(role=="unregistered","An unregistered actor account is required")
        requested={"customs":1,"exporter":2,"logistics":3,"importer":4}.get(a.get("role"))
        require(requested is not None,"Choose an operational role")
        doc=Documents().json(a["document_cid"])
        require(doc.get("account","").lower()==account.lower() and doc.get("country")==a["country"],"License document identity does not match")
        snapshot["actor"]=serial(chain.reg.functions.getActor(Web3.to_checksum_address(account)).call())
        contract,fn,args="Registration","requestReg",[country(a["country"]),requested,a["document_cid"]]
    elif action in ("approve_registration","update_license","revoke_registration"):
        target=Web3.to_checksum_address(a["account"]); actor=chain.reg.functions.getActor(target).call()
        require(role=="nca" and identity["country"]==actor[1].decode(),"Responsible national authority required")
        snapshot["actor"]=serial(actor)
        evidence["license_document"]=Documents().json(actor[2])
        if action=="revoke_registration": fn,args="revokeReg",[target,H]
        else:
            expiry=integer(a["expiry"],1)
            require(evidence["license_document"].get("valid_until",0)>=expiry,"Contract license expiry exceeds the reviewed document")
            fn,args=("approveReg" if action=="approve_registration" else "updateLicense"),[target,expiry,H]
        contract="Registration"
    elif action == "create_shipment":
        require(role=="exporter","Licensed exporter required")
        doc=Documents().json(a["document_cid"])
        require(doc.get("exporter","").lower()==account.lower(),"Shipment document exporter does not match")
        quantity=integer(a["quantity"],1); year=integer(a["year"],1)
        require(year<=65535 and doc.get("quantity")==quantity and doc.get("unit")=="ug" and doc.get("substance")==a["substance"] and doc.get("year")==year,"Shipment fields and document must agree in micrograms")
        require(doc.get("importer","").lower()==a["importer"].lower(),"Importer and document disagree")
        snapshot["next_shipment_id"]=chain.ship.functions.nextShipmentId().call()
        contract,fn,args="Shipping","createShipment",[Web3.to_checksum_address(a["importer"]),Web3.to_checksum_address(a["logistics"]),substance(a["substance"]),quantity,year,a["document_cid"]]
    elif action in ("request_import_permit","request_export_permit"):
        require(s is not None,"Shipment required")
        ex=action=="request_export_permit"
        require(account.lower()==s["exporter" if ex else "importer"].lower(),"Wrong shipment participant")
        with session() as db:
            draft=db.get(Draft,a["draft_id"])
            require(draft is not None and draft.status=="published" and draft.network==chain.network and draft.shipment_id==sid and draft.mode==("export" if ex else "import"),"A matching human-reviewed, published draft is required")
            evidence["draft"]=draft.content; cid=draft.cid
        contract,fn,args="Compliance","requestPermit",[sid,ex,cid]
    elif action in ("approve_import_permit","approve_export_permit","reject_import_permit","reject_export_permit","revoke_import_permit","revoke_export_permit"):
        require(s is not None,"Shipment required")
        ex="export" in action; pid=integer(a["permit_id"],1); p=chain.permit(pid,ex)
        require(p["shipmentId"]==sid and role=="nca" and identity["country"]==s["origin" if ex else "destination"],"Responsible NCA and matching permit required")
        snapshot["permit"]=p
        if action.startswith("approve"):
            with session() as db:
                draft=db.execute(select(Draft).where(Draft.cid==p["documentCID"],Draft.status=="published",Draft.network==chain.network)).scalar_one_or_none()
                require(draft is not None and draft.shipment_id==sid,"Published, reviewed permit record required")
                evidence["draft"]=draft.content; expiry=draft.content["valid_until"]
            fn,args="approvePermit",[pid,ex,expiry,H]
        else: fn,args=("rejectPermit" if action.startswith("reject") else "revokePermit"),[pid,ex,H]
        contract="Compliance"
    elif action in ("export_clearance","import_clearance"):
        require(s is not None,"Shipment required")
        ex=action=="export_clearance"
        require(role=="customs" and identity["country"]==s["origin" if ex else "destination"],"Responsible customs account required")
        contract,fn,args="Shipping",("approveExportClearance" if ex else "approveImportClearance"),[sid,H]
    elif action == "approve_shipping":
        require(s is not None and role=="incb","INCB account and shipment required")
        with session() as db:
            assessment=db.get(Assessment,a["assessment_id"])
            require(assessment is not None and assessment.network==chain.network and assessment.shipment_id==sid and assessment.agent=="global" and assessment.status=="completed","Completed global assessment required")
            result=assessment.payload["result"]; evidence["assessment"]=assessment.payload
        require(result.get("state_fingerprint")==chain.fingerprint(sid),"Assessment is stale; repeat it using current evidence")
        if result["decision"]!="approve": require(a.get("override") is True and len(notes.strip())>=40,"Explicit human override with detailed reasons required")
        for mode in ("export","import"):
            q=check_quota(chain,s,mode,"global")
            require(q["decision"]=="approve","Resolve quota discrepancies or exceedance before approval")
        contract,fn,args="Shipping","approveForShipping",[sid,H]
    elif action in ("cancel_shipment","reject_shipment"):
        require(s is not None and role=="incb","INCB account and shipment required")
        contract,fn,args="Shipping","cancelShipment",[sid,H]
    elif action == "pickup":
        require(s is not None and role=="logistics" and account.lower()==s["logistics"].lower(),"Assigned logistics account required")
        contract,fn,args="Shipping","pickup",[sid]
    elif action == "record_transit":
        require(s is not None and role=="logistics" and account.lower()==s["logistics"].lower(),"Assigned logistics account required")
        Documents().read(a["document_cid"])
        contract,fn,args="Shipping","recordTransit",[sid,a["document_cid"]]
    elif action == "receive":
        require(s is not None and role=="importer" and account.lower()==s["importer"].lower(),"Intended importer required")
        contract,fn,args="Shipping","receiveShipment",[sid]
    elif action in ("refresh_quota_cache","set_quota","correct_usage"):
        iso=a["country"]; sub=a["substance"]; year=integer(a["year"],1); mode=a["mode"]
        require(mode in ("import","export") and year<=65535,"Invalid quota key")
        require(role=="incb" or (role=="nca" and identity["country"]==iso),"Responsible regulator required")
        snapshot["quota"]=chain.quota(iso,sub,year,mode)
        snapshot["quota_document"]=chain.comp.functions.getIncbCapIpfs(year).call()
        if action=="refresh_quota_cache":
            evidence["ledger"]=chain.history(snapshot["quota"]["key"])
        elif action=="set_quota":
            national=a["scope"]=="national"; cap=integer(a["cap"],1)
            require(a["scope"] in ("national","global") and (role=="nca" if national else role=="incb"),"Quota scope authority mismatch")
            declaration=Documents().json(snapshot["quota_document"])
            matches=[r for r in declaration.get("quotas",[]) if r["country"]==iso and r["substance"]==sub and r["mode"]==mode]
            require(len(matches)==1 and declaration.get("unit")=="ug" and matches[0].get("nationalCap" if national else "globalCap")==cap,"Publish a matching quota declaration first")
            contract,fn,args="Shipping","setQuota",[country(iso),substance(sub),year,mode=="export",national,cap,H]
        else:
            require(role=="incb" and len(notes.strip())>=40,"INCB review with detailed correction reasons required")
            corrected=integer(a["corrected_usage"])
            contract,fn,args="Shipping","correctQuotaUsage",[country(iso),substance(sub),year,mode=="export",corrected,H]
    elif action == "publish_quota_reference":
        require(role=="incb","INCB account required")
        year=integer(a["year"],1); declaration=Documents().json(a["document_cid"])
        require(year<=65535 and declaration.get("year")==year and declaration.get("unit")=="ug" and isinstance(declaration.get("quotas"),list),"Invalid quota declaration")
        for row in declaration["quotas"]:
            country(row["country"]); substance(row["substance"])
            integer(row["nationalCap"],1); integer(row["globalCap"],1)
            require(row["mode"] in ("export","import"),"Invalid declaration mode")
        snapshot["previous_document"]=chain.comp.functions.getIncbCapIpfs(year).call()
        contract,fn,args="Compliance","setIncbQuotas",[year,a["document_cid"],H]
    else: raise ReviewError("Unknown action")
    return {"contract":contract,"function":fn,"args":args,"snapshot":snapshot,"evidence":evidence}

def prepare(chain,account,action,arguments,notes):
    account=Web3.to_checksum_address(account)
    material=describe(chain,account,action,arguments,notes)
    review_id=identifier()
    fingerprint=digest(material["snapshot"])
    bound={"id":review_id,"account":account,"network":chain.network,"action":action,"arguments":arguments,"notes":notes,
           "snapshot":material["snapshot"],"evidence":material["evidence"]}
    evidence_hash=digest(bound)
    args=[bytes.fromhex(evidence_hash) if arg is H else arg for arg in material["args"]]
    tx=chain.transaction(material["contract"],material["function"],args,account) if material["contract"] else None
    payload={"action":action,"arguments":arguments,"notes":notes,"transaction":tx,"bound_evidence":bound,"prepared_at_block":chain.w3.eth.block_number}
    with session() as db:
        row=Review(id=review_id,account=account.lower(),network=chain.network,action=action,payload=payload,evidence_hash=evidence_hash,
                   state_fingerprint=fingerprint,expires=now()+900)
        db.add(row); audit(db,account,"review_prepared",{"review_id":review_id,"evidence_hash":evidence_hash,"action":action}); db.commit()
    return {"id":review_id,"transaction":tx,"evidence_hash":evidence_hash,"expires":now()+900,"summary":bound}

def confirm_receipt(chain,account,review_id,transaction_hash):
    transaction_hash=Web3.to_hex(Web3.to_bytes(hexstr=transaction_hash)).lower()
    require(len(transaction_hash)==66,"Invalid transaction hash")
    with session() as db:
        row=db.execute(select(Review).where(Review.id==review_id).with_for_update()).scalar_one_or_none()
        require(row is not None and row.account==account.lower() and row.network==chain.network,"Review account mismatch")
        if row.status in ("confirmed","failed"):
            require(row.transaction_hash==transaction_hash,"Review already used for another transaction")
            return {"id":row.id,"status":row.status,"transaction_hash":row.transaction_hash}
        expected=row.payload["transaction"]
        require(expected is not None,"This review is an off-chain cache action")
        tx=chain.w3.eth.get_transaction(transaction_hash)
        require(tx["from"].lower()==account.lower() and (tx["to"] or "").lower()==expected["to"].lower(),"Transaction parties do not match review")
        require(bytes(tx["input"])==bytes.fromhex(expected["data"][2:]) and tx["value"]==0 and tx["nonce"]==expected["nonce"],"Transaction data does not match review")
        receipt=chain.w3.eth.get_transaction_receipt(transaction_hash)
        require(receipt["blockHash"]==chain.w3.eth.get_block(receipt["blockNumber"])["hash"],"Receipt was reorganized; retry after confirmation")
        confirmations=chain.w3.eth.block_number-receipt["blockNumber"]+1
        # Twelve blocks is the prototype's confirmation policy, not consensus finality.
        required=1 if chain.network=="local" else 12
        row.transaction_hash=transaction_hash
        row.status=("confirmed" if receipt["status"]==1 else "failed") if confirmations>=required else "submitted"
        audit(db,account,"transaction_"+row.status,{"review_id":row.id,"transaction_hash":transaction_hash,"receipt":serial(dict(receipt)),"review_expired_at_confirmation":row.expires<now()})
        db.commit()
        return {"id":row.id,"status":row.status,"transaction_hash":transaction_hash,"receipt":serial(dict(receipt)),"confirmations":confirmations,"required_confirmations":required}

def execute_local_or_cache(chain,account,review_id):
    with session() as db:
        row=db.execute(select(Review).where(Review.id==review_id).with_for_update()).scalar_one_or_none()
        require(row is not None and row.account==account.lower() and row.network==chain.network,"Review account mismatch")
        require(row.status=="prepared" and row.expires>=now(),"Review expired or already used")
        p=dict(row.payload)
        current=describe(chain,account,row.action,p["arguments"],p["notes"])
        require(digest(current["snapshot"])==row.state_fingerprint,"State changed; prepare a fresh review")
        if p["transaction"] is None:
            a=p["arguments"]
            refresh_cache(chain,account,a["country"],a["substance"],a["year"],a["mode"])
            row.status="confirmed"; audit(db,account,"cache_review_confirmed",{"review_id":row.id,"evidence_hash":row.evidence_hash}); db.commit()
            return {"id":row.id,"status":"confirmed","transaction_hash":None}
        require(chain.network=="local","Use the connected wallet to submit Sepolia transactions")
        tx=p["transaction"]
        require(chain.w3.eth.get_transaction_count(Web3.to_checksum_address(account),"pending")==tx["nonce"],"Wallet nonce changed; prepare a fresh review")
        # Save the broadcast hash before waiting. A failed receipt never marks the
        # workflow complete; receipt confirmation is idempotent and can be retried.
        sent=dict(tx); sent.pop("chainId",None)
        tx_hash=chain.w3.eth.send_transaction(sent).to_0x_hex()
        row.transaction_hash=tx_hash; row.status="submitted"; db.commit()
    chain.w3.eth.wait_for_transaction_receipt(tx_hash,timeout=60)
    return confirm_receipt(chain,account,review_id,tx_hash)


def recover(chain,account,review_id):
    """Recover a broadcast hash without resending, including a crash before DB save."""
    with session() as db:
        row=db.get(Review,review_id)
        require(row is not None and row.account==account.lower() and row.network==chain.network,"Review account mismatch")
        known=row.transaction_hash;expected=row.payload.get("transaction");start=row.payload.get("prepared_at_block",chain.deployment["deployment_block"])
    if known:return confirm_receipt(chain,account,review_id,known)
    require(expected is not None,"This review does not involve a transaction")
    nonce=expected["nonce"];latest=chain.w3.eth.block_number
    require(chain.w3.eth.get_transaction_count(Web3.to_checksum_address(account),latest)>nonce,"No mined transaction for this review nonce; nothing was resent")
    # Find the first block at which this nonce was consumed. Historical RPC failures
    # remain failures; they are never interpreted as permission to send again.
    lo,hi=start,latest
    while lo<hi:
        mid=(lo+hi)//2
        if chain.w3.eth.get_transaction_count(Web3.to_checksum_address(account),mid)>nonce:hi=mid
        else:lo=mid+1
    block=chain.w3.eth.get_block(lo,full_transactions=True)
    for tx in block["transactions"]:
        if tx["from"].lower()==account.lower() and tx["nonce"]==nonce:
            return confirm_receipt(chain,account,review_id,tx["hash"].to_0x_hex())
    raise ReviewError("Mined transaction was not found; retain the review and investigate the RPC history")
