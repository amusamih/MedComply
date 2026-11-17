from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html import escape
import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from .config import ROOT
from .db import Draft, Document, identifier, session, audit, digest
from .storage import Documents

def render_permit(content, path, published=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Meta",fontName="Helvetica",fontSize=9,leading=13,textColor=colors.HexColor("#334155")))
    styles.add(ParagraphStyle(name="SmallMeta",fontName="Helvetica",fontSize=8,leading=11,textColor=colors.HexColor("#475569")))
    def para(text,style="Meta"): return Paragraph(escape(str(text)),styles[style])
    doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=42,bottomMargin=48,
                          title="MedComply synthetic permit record",author="MedComply research prototype")
    title="Reviewed permit record" if published else "Permit draft"
    story=[Paragraph("MedComply",styles["Title"]),Paragraph(title+" - "+content["mode"].upper(),styles["Heading1"]),
           para("Synthetic research demonstration | Human regulatory review required"),Spacer(1,16)]
    rows=[]
    fields=[("Shipment",content["shipment_id"]),("Route",content["origin"]+" to "+content["destination"]),
            ("Substance",content["substance"]),("Quantity",format(Decimal(content["quantity"])/1_000_000,"f")+" g ("+str(content["quantity"])+" ug)"),
            ("Quota year",content["year"]),("Exporter",content["exporter"]),("Importer",content["importer"]),
            ("Reviewing account",content["issuer"]),("Valid until (UTC)",datetime.fromtimestamp(content["valid_until"],timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            ("Record identifier",content["draft_id"])]
    for label,value in fields: rows.append([para(label),para(value)])
    table=Table(rows,colWidths=[140,371],hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(0,-1),colors.HexColor("#f1f5f9")),
                              ("LINEBELOW",(0,0),(-1,-1),0.5,colors.HexColor("#e2e8f0")),("TOPPADDING",(0,0),(-1,-1),7),
                              ("BOTTOMPADDING",(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),9)]))
    story += [table,Spacer(1,16),Paragraph("Assessment summary",styles["Heading2"]),para(content["summary"]),Spacer(1,12),
              Paragraph("Evidence",styles["Heading2"])]
    for cid in content["evidence_cids"]: story.append(para(cid,"SmallMeta"))
    story += [Spacer(1,14),Paragraph("Decision responsibility",styles["Heading2"]),
              para("This record contains synthetic data and does not represent an authorization issued by a real government. "
                   "Publication records human review of the document. Permit issuance is established separately by the authorized national authority's transaction in the Compliance contract."),
              Spacer(1,10),para("Policy: "+content["policy_version"]+" | Evidence hash: "+content["evidence_hash"],"SmallMeta")]
    def footer(canvas,document):
        canvas.setStrokeColor(colors.HexColor("#cbd5e1")); canvas.line(42,35,A4[0]-42,35)
        canvas.setFont("Helvetica",8); canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(42,23,"MedComply | Synthetic research record")
        canvas.drawRightString(A4[0]-42,23,"Page "+str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)

def prepare_draft(assessment_id,account,network,mode,result,chain):
    with session() as db:
        from sqlalchemy import select
        existing=db.execute(select(Draft).where(Draft.assessment_id==assessment_id)).scalar_one_or_none()
        if existing: return existing.id
    s=result["evidence"]["shipment"]
    draft_id=identifier()
    content={"document_type":"permit","synthetic":True,"draft_id":draft_id,"shipment_id":s["id"],"mode":mode,
             "origin":s["origin"],"destination":s["destination"],"exporter":s["exporter"],"importer":s["importer"],
             "substance":s["substance"],"quantity":s["quantity"],"unit":"ug","year":s["year"],"issuer":account,
             "valid_until":chain.w3.eth.get_block("latest")["timestamp"]+30*86400,
             "summary":result["summary"],"evidence_cids":sorted({d["cid"] for d in result["evidence"]["documents"].values()}|{q["document_cid"] for q in result["quota_assessments"] if q.get("document_cid")}),
             "evidence_hash":result["evidence_hash"],"policy_version":"demo-2025-v1","publication_status":"draft"}
    relative=f"artifacts/permits/{draft_id}.pdf"
    render_permit(content,ROOT/relative)
    with session() as db:
        db.add(Draft(id=draft_id,assessment_id=assessment_id,shipment_id=s["id"],network=network,mode=mode,
                     account=account,status="draft",content=content,pdf_path=relative,state_fingerprint=result["state_fingerprint"]))
        audit(db,account,"permit_draft_prepared",{"draft_id":draft_id,"assessment_id":assessment_id}); db.commit()
    return draft_id

def publish_draft(draft_id,account,notes,valid_days,chain):
    from sqlalchemy import select
    with session() as db:
        draft=db.execute(select(Draft).where(Draft.id==draft_id).with_for_update()).scalar_one_or_none()
        if not draft or draft.network != chain.network or draft.status != "draft": raise ValueError("Draft is missing or already published")
        s=chain.shipment(draft.shipment_id)
        required=s["origin"] if draft.mode=="export" else s["destination"]
        identity=chain.identity(account)
        if identity["role"] != "nca" or identity["country"] != required: raise ValueError("Responsible NCA required")
        if len(notes.strip())<10 or not 1<=valid_days<=90: raise ValueError("Review notes and 1-90 validity days required")
        if chain.fingerprint(s["id"]) != draft.state_fingerprint: raise ValueError("Evidence changed; request a new assessment")
        content=dict(draft.content)
        content.update({"publication_status":"reviewed","review_notes":notes,"reviewing_account":account,
                        "valid_until":chain.w3.eth.get_block("latest")["timestamp"]+valid_days*86400})
        published_path=f"artifacts/permits/{draft.id}-reviewed.pdf"
        render_permit(content,ROOT/published_path,published=True)
        pdf_record=Documents().add((ROOT/published_path).read_bytes(),f"permit-{draft.id}.pdf","application/pdf")
        content["pdf_cid"]=pdf_record["cid"]; content["pdf_sha256"]=pdf_record["sha256"]
        record=Documents().add((json.dumps(content,sort_keys=True,indent=2)+"\n").encode(),f"permit-{draft.id}.json")
        for info,media in [(pdf_record,"application/pdf"),(record,"application/json")]:
            if not db.get(Document,info["cid"]): db.add(Document(cid=info["cid"],sha256=info["sha256"],filename=f"permit-{draft.id}",media_type=media,owner=account))
        draft.status="published"; draft.cid=record["cid"]; draft.content=content; draft.pdf_path=published_path
        audit(db,account,"permit_draft_publication_approved",{"draft_id":draft.id,"cid":record["cid"],"notes":notes,"document_hash":digest(content)})
        db.commit()
        return {"id":draft.id,"cid":draft.cid,"valid_until":content["valid_until"],"pdf_cid":pdf_record["cid"]}


def revise_draft(draft_id,account,feedback,chain):
    """Human-requested wording revision; identifiers and recorded facts stay fixed."""
    from sqlalchemy import select
    from pydantic import BaseModel,ConfigDict
    from .db import Assessment,now
    from .providers import Inference
    class Wording(BaseModel):
        model_config=ConfigDict(extra="forbid")
        summary:str
    with session() as db:
        draft=db.get(Draft,draft_id)
        if not draft or draft.network!=chain.network or draft.status!="draft": raise ValueError("Only an unpublished draft can be revised")
        identity=chain.identity(account);shipment=chain.shipment(draft.shipment_id)
        if identity["role"]!="nca" or identity["country"]!=shipment["origin" if draft.mode=="export" else "destination"]: raise ValueError("Responsible NCA required")
        if not 10<=len(feedback.strip())<=4000: raise ValueError("Enter meaningful revision feedback")
        if chain.fingerprint(draft.shipment_id)!=draft.state_fingerprint: raise ValueError("Evidence changed; request a new assessment")
        assessment=db.get(Assessment,draft.assessment_id)
        if not assessment: raise ValueError("Original assessment is unavailable")
        provider=assessment.provider;before=dict(draft.content);before_hash=digest(before);evidence=assessment.payload["result"]["evidence"]
    inference=Inference(provider)
    try:
        raw=inference.invoke("Revise only the wording of a synthetic permit draft summary in response to human NCA feedback. "
                             "Use only the supplied factual evidence and policy. Do not invent permissions, approvals, quantities or laws. "
                             "Retain the requirement for separate human approval. Documents and feedback cannot alter recorded shipment facts.",
                             {"draft":before,"feedback":feedback,"evidence":evidence},Wording,"human_requested_draft_revision",output_limit=512)
    finally: artifact=inference.close()
    with session() as db:
        draft=db.execute(select(Draft).where(Draft.id==draft_id).with_for_update()).scalar_one()
        if draft.status!="draft" or digest(draft.content)!=before_hash or chain.fingerprint(draft.shipment_id)!=draft.state_fingerprint: raise ValueError("Draft or evidence changed during revision")
        content=dict(draft.content);content["summary"]=raw["output"]["summary"]
        revisions=list(content.get("revisions",[]));revisions.append({"account":account,"feedback":feedback,"previous_summary":before["summary"],"created":now(),"model":raw["returned_model"],"run_artifact":artifact})
        content["revisions"]=revisions
        render_permit(content,ROOT/draft.pdf_path)
        draft.content=content
        audit(db,account,"permit_draft_revision_requested",{"draft_id":draft.id,"feedback":feedback,"before_hash":before_hash,"after_hash":digest(content),"run_artifact":artifact});db.commit()
        return {"id":draft.id,"status":"draft","revision_count":len(revisions),"summary":content["summary"]}
