"""Loopback research application. Models have no signing or write interface."""
from __future__ import annotations
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, delete,func
from web3 import Web3
from . import auth, reviews
from .chain import Chain, serial
from .config import ROOT, LOCAL, manifest
from .db import initialize, session, Assessment, Review, Draft, Document, Audit, LoginSession, audit, now
from .storage import Documents
from .fixed_workflow import run_assessment, VERSION as WORKFLOW_VERSION, DEFAULT_PROVIDER
from .permits import publish_draft,revise_draft
from tools.provider_preflight import PROVIDERS

@asynccontextmanager
async def lifespan(app):
    initialize()
    # A killed worker is recorded honestly; it must be requested again explicitly.
    with session() as db:
        for row in db.scalars(select(Assessment).where(Assessment.status=="running",Assessment.created<now()-900)):
            row.status="interrupted"
        db.commit()
    yield

app=FastAPI(title="MedComply",version="0.2.0",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","testserver"])

@app.middleware("http")
async def browser_boundary(request,call_next):
    if request.method not in ("GET","HEAD","OPTIONS"):
        expected=str(request.base_url).rstrip("/")
        origin=request.headers.get("origin")
        if request.headers.get("x-medcomply")!="1" or (origin and origin!=expected):
            return JSONResponse({"detail":"Same-origin application request required"},status_code=403)
    try: response=await call_next(request)
    except Exception as exc:
        # Never serialize RPC exceptions, connection strings, request headers or keys.
        return JSONResponse({"detail":"Operation unavailable","error_type":type(exc).__name__},status_code=503)
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Cache-Control"]="no-store"
    return response

class Strict(BaseModel): model_config=ConfigDict(extra="forbid",strict=True)
class Challenge(Strict):
    account: str
    network: Literal["local","sepolia"]="local"
class Login(Strict):
    id: str
    signature: str
class LocalLogin(Strict): account: str
class Assess(Strict):
    id: str=Field(pattern=r"^[a-f0-9]{32}$")
    shipment_id: int=Field(gt=0)
    agent: Literal["national","global"]
    mode: Literal["export","import"]="export"
    provider: Literal["openai","gemini","claude","mistral","deepseek"]=DEFAULT_PROVIDER
class Action(Strict):
    action: str=Field(min_length=1,max_length=40)
    arguments: dict
    notes: str=Field(default="",max_length=4000)
class Receipt(Strict): transaction_hash: str=Field(pattern=r"^0x[a-fA-F0-9]{64}$")
class Publish(Strict):
    notes: str=Field(min_length=10,max_length=4000)
    valid_days: int=Field(default=30,ge=1,le=90)
class Revise(Strict): feedback:str=Field(min_length=10,max_length=4000)

def user(request:Request):
    try: return auth.authenticated(request.cookies.get("medcomply_session"))
    except ValueError as exc: raise HTTPException(401,str(exc)) from None

def chain_for(u): return Chain(u["network"])
def cookie(response,token): response.set_cookie("medcomply_session",token,httponly=True,samesite="strict",max_age=8*3600,path="/")
def record(row):
    return {column.name:getattr(row,column.name) for column in row.__table__.columns}

@app.get("/api/config")
def config():
    networks={}
    for network in ("local","sepolia"):
        path=LOCAL/f"deployment-{network}.json"
        if path.exists():
            m=manifest(network)
            networks[network]={"chain_id":m["chain_id"],"contracts":m["contracts"]}
    demo_path=LOCAL/"demo.json"
    roles=json.loads(demo_path.read_text())["roles"] if demo_path.exists() else {}
    return {"networks":networks,"demo_roles":roles,"synthetic":True,"providers":{p:v["model"] for p,v in PROVIDERS.items()},
            "confirmation_policy":{"local":1,"sepolia":12},"policy":"demo-2025-v1", "workflow_version":WORKFLOW_VERSION,"default_provider":DEFAULT_PROVIDER}

@app.get("/api/health")
def health():
    with session() as db: db.execute(select(1))
    return {"database":"ready","application":"ready"}

@app.post("/api/auth/challenge")
def challenge(body:Challenge):
    try:
        account=Web3.to_checksum_address(body.account)
        chain=Chain(body.network)
        return auth.challenge(account,body.network,chain.w3.eth.chain_id)
    except ValueError: raise HTTPException(400,"Invalid account or network") from None

@app.post("/api/auth/login")
def login(body:Login,response:Response):
    try: token=auth.login(body.id,body.signature)
    except (ValueError,TypeError): raise HTTPException(401,"Invalid or expired wallet signature") from None
    cookie(response,token)
    return {"authenticated":True}

@app.post("/api/auth/local")
def local_login(body:LocalLogin,request:Request,response:Response):
    # Known Anvil accounts are public test identities. This route never signs on Sepolia.
    if request.client.host not in ("127.0.0.1","::1","testclient"):
        raise HTTPException(403,"Local demonstration only")
    chain=Chain("local")
    try: account=Web3.to_checksum_address(body.account)
    except ValueError: raise HTTPException(400,"Invalid account") from None
    if account not in chain.w3.eth.accounts: raise HTTPException(403,"Use an unlocked Anvil demonstration identity")
    cookie(response,auth.create_session(account,"local"))
    return {"authenticated":True,"network":"local"}

@app.post("/api/auth/logout")
def logout(request:Request,response:Response):
    token=request.cookies.get("medcomply_session","")
    with session() as db:
        db.execute(delete(LoginSession).where(LoginSession.token_hash==hashlib.sha256(token.encode()).hexdigest())); db.commit()
    response.delete_cookie("medcomply_session")
    return {"authenticated":False}

@app.get("/api/me")
def me(u=Depends(user)):
    return {**chain_for(u).identity(u["account"]),"network":u["network"]}

@app.get("/api/shipments")
def shipments(u=Depends(user)):
    chain=chain_for(u)
    return [chain.shipment(i) for i in range(1,chain.ship.functions.nextShipmentId().call())]

@app.get("/api/shipments/{shipment_id}")
def shipment(shipment_id:int,u=Depends(user)):
    chain=chain_for(u)
    if not 0<shipment_id<chain.ship.functions.nextShipmentId().call(): raise HTTPException(404,"Shipment missing")
    s=chain.shipment(shipment_id)
    if s["state"]=="missing": raise HTTPException(404,"Shipment missing")
    permits=[]
    for name,ex in [("Import",False),("Export",True)]:
        for log in chain.logs("Compliance","PermitRequested"):
            a=log["args"]
            if a["shipmentId"]==shipment_id and a["isExport"]==ex:
                permits.append({"id":a["permitId"],"mode":name.lower(),**chain.permit(a["permitId"],ex)})
    with session() as db:
        assessments=[record(r) for r in db.scalars(select(Assessment).where(Assessment.network==u["network"],Assessment.shipment_id==shipment_id).order_by(Assessment.created.desc()).limit(30))]
        drafts=[record(r) for r in db.scalars(select(Draft).where(Draft.network==u["network"],Draft.shipment_id==shipment_id).order_by(Draft.created.desc()))]
    quotas={mode:chain.quota(s["origin"] if mode=="export" else s["destination"],s["substance"],s["year"],mode) for mode in ("export","import")}
    return {"shipment":s,"permits":permits,"assessments":assessments,"drafts":drafts,"quotas":quotas}

@app.post("/api/assessments",status_code=202)
def assess(body:Assess,tasks:BackgroundTasks,u=Depends(user)):
    chain=chain_for(u); identity=chain.identity(u["account"]); s=chain.shipment(body.shipment_id)
    allowed=identity["role"]=="incb" if body.agent=="global" else identity["role"]=="nca" and identity["country"]==s["origin" if body.mode=="export" else "destination"]
    if not allowed: raise HTTPException(403,"Responsible regulator required")
    with session() as db:
        existing=db.get(Assessment,body.id)
        if existing:
            if (existing.account,existing.network,existing.shipment_id,existing.agent,existing.provider,existing.payload.get("mode"))!=(u["account"],u["network"],body.shipment_id,body.agent,body.provider,body.mode):
                raise HTTPException(409,"Request identifier already used for a different assessment")
            return record(existing)
        running=db.scalar(select(Assessment).where(Assessment.account==u["account"],Assessment.status=="running"))
        if running: raise HTTPException(409,"Wait for your running assessment to finish")
        row=Assessment(id=body.id,shipment_id=body.shipment_id,network=u["network"],agent=body.agent,provider=body.provider,account=u["account"],payload={"mode":body.mode})
        db.add(row); audit(db,u["account"],"assessment_requested",body.model_dump()); db.commit()
        answer=record(row)
    tasks.add_task(run_assessment,body.id)
    return answer

@app.get("/api/assessments/{assessment_id}")
def assessment(assessment_id:str,u=Depends(user)):
    with session() as db:
        row=db.get(Assessment,assessment_id)
        if not row or row.network!=u["network"]: raise HTTPException(404,"Assessment missing")
        return record(row)

@app.get("/api/drafts/{draft_id}/pdf")
def draft_pdf(draft_id:str,u=Depends(user)):
    with session() as db:
        row=db.get(Draft,draft_id)
        if not row or row.network!=u["network"]: raise HTTPException(404,"Draft missing")
        path=(ROOT/row.pdf_path).resolve()
        if not path.is_relative_to(ROOT/"artifacts/permits"): raise HTTPException(403,"Invalid path")
        return FileResponse(path,media_type="application/pdf",filename=f"permit-{draft_id}.pdf")

@app.post("/api/drafts/{draft_id}/publish")
def publish(draft_id:str,body:Publish,u=Depends(user)):
    try: return publish_draft(draft_id,u["account"],body.notes,body.valid_days,chain_for(u))
    except ValueError as exc: raise HTTPException(409,str(exc)) from None

@app.post("/api/drafts/{draft_id}/revise")
def revise(draft_id:str,body:Revise,u=Depends(user)):
    try:return revise_draft(draft_id,u["account"],body.feedback,chain_for(u))
    except ValueError as exc:raise HTTPException(409,str(exc)) from None

@app.post("/api/documents")
async def upload(file:UploadFile=File(...),u=Depends(user)):
    data=await file.read(2_000_001)
    if not data:raise HTTPException(400,"Choose a nonempty document")
    if len(data)>2_000_000: raise HTTPException(413,"Document limit is 2 MB")
    if file.content_type not in ("application/json","application/pdf","text/plain"): raise HTTPException(415,"Use JSON, PDF, or plain text")
    if file.content_type=="application/json":
        try: json.loads(data)
        except (ValueError,UnicodeError): raise HTTPException(400,"Invalid JSON") from None
    filename=Path(file.filename or "document").name[:180]
    info=Documents().add(data,filename,file.content_type)
    with session() as db:
        if not db.get(Document,info["cid"]): db.add(Document(cid=info["cid"],sha256=info["sha256"],filename=filename,media_type=file.content_type,owner=u["account"]))
        audit(db,u["account"],"document_uploaded",info); db.commit()
    return info

@app.get("/api/documents/{cid}")
def document(cid:str,u=Depends(user)):
    with session() as db: row=db.get(Document,cid)
    if not row: raise HTTPException(404,"Document missing")
    return Response(Documents().read(cid,row.sha256),media_type=row.media_type,headers={"Content-Disposition":"attachment; filename=document"})

@app.post("/api/reviews")
def prepare(body:Action,u=Depends(user)):
    try: return reviews.prepare(chain_for(u),u["account"],body.action,body.arguments,body.notes)
    except (reviews.ReviewError,KeyError,ValueError) as exc:
        detail=str(exc) if isinstance(exc,reviews.ReviewError) else "Invalid action fields or current state"
        raise HTTPException(409,detail) from None

@app.get("/api/reviews")
def list_reviews(u=Depends(user)):
    with session() as db:
        return [record(r) for r in db.scalars(select(Review).where(Review.account==u["account"],Review.network==u["network"]).order_by(Review.created.desc()).limit(50))]

@app.post("/api/reviews/{review_id}/confirm")
def execute(review_id:str,u=Depends(user)):
    try: return reviews.execute_local_or_cache(chain_for(u),u["account"],review_id)
    except reviews.ReviewError as exc: raise HTTPException(409,str(exc)) from None

@app.post("/api/reviews/{review_id}/receipt")
def receipt(review_id:str,body:Receipt,u=Depends(user)):
    try: return reviews.confirm_receipt(chain_for(u),u["account"],review_id,body.transaction_hash)
    except reviews.ReviewError as exc: raise HTTPException(409,str(exc)) from None

@app.post("/api/reviews/{review_id}/recover")
def recover_review(review_id:str,u=Depends(user)):
    try:return reviews.recover(chain_for(u),u["account"],review_id)
    except reviews.ReviewError as exc:raise HTTPException(409,str(exc)) from None

@app.get("/api/quotas")
def quotas(u=Depends(user)):
    chain=chain_for(u); keys=set()
    configurations=chain.logs("Shipping","QuotaConfigured")
    for log in configurations:
        a=log["args"]; keys.add(serial(a["key"]))
    result=[]
    for key in keys:
        # Keys are accompanied by their country/substance/year in the configuration events.
        logs=[log for log in configurations if serial(log["args"]["key"])==key]
        a=logs[-1]["args"]
        result.append({"key":key,"configuration":serial(a),"ledger":chain.history(key)})
    return result

@app.get("/api/audit")
def audit_log(u=Depends(user)):
    identity=chain_for(u).identity(u["account"])
    with session() as db:
        query=select(Audit).order_by(Audit.created.desc()).limit(100)
        if identity["role"] not in ("incb","nca"): query=query.where(func.lower(Audit.account)==u["account"])
        return [record(r) for r in db.scalars(query)]

app.mount("/",StaticFiles(directory=ROOT/"src/medcomply/web",html=True),name="web")
