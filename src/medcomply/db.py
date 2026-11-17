from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import BigInteger, Integer, JSON, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import database_url

def identifier(): return uuid.uuid4().hex
def now(): return int(datetime.now(timezone.utc).timestamp())
def digest(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

class Base(DeclarativeBase): pass

class Document(Base):
    __tablename__ = "documents"
    cid: Mapped[str] = mapped_column(String(128), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(80))
    owner: Mapped[str] = mapped_column(String(42))
    created: Mapped[int] = mapped_column(BigInteger, default=now)

class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    shipment_id: Mapped[int] = mapped_column(BigInteger)
    network: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(24))
    account: Mapped[str] = mapped_column(String(42))
    status: Mapped[str] = mapped_column(String(24), default="running")
    created: Mapped[int] = mapped_column(BigInteger, default=now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    account: Mapped[str] = mapped_column(String(42))
    network: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    state_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    expires: Mapped[int] = mapped_column(BigInteger)
    transaction_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    created: Mapped[int] = mapped_column(BigInteger, default=now)

class Draft(Base):
    __tablename__ = "permit_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    assessment_id: Mapped[str] = mapped_column(String(32), unique=True)
    shipment_id: Mapped[int] = mapped_column(BigInteger)
    network: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(8))
    account: Mapped[str] = mapped_column(String(42))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    content: Mapped[dict] = mapped_column(JSON)
    pdf_path: Mapped[str] = mapped_column(String(240))
    cid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state_fingerprint: Mapped[str] = mapped_column(String(64))
    created: Mapped[int] = mapped_column(BigInteger, default=now)

class QuotaCache(Base):
    __tablename__ = "quota_cache"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    network: Mapped[str] = mapped_column(String(16), default="local")
    country: Mapped[str] = mapped_column(String(3))
    substance: Mapped[str] = mapped_column(String(32))
    year: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(8))
    cap: Mapped[int] = mapped_column(BigInteger)
    used: Mapped[int] = mapped_column(BigInteger)
    reserved: Mapped[int] = mapped_column(BigInteger)
    block_number: Mapped[int] = mapped_column(BigInteger)
    block_hash: Mapped[str] = mapped_column(String(66))

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    account: Mapped[str] = mapped_column(String(42))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created: Mapped[int] = mapped_column(BigInteger, default=now)

class LoginNonce(Base):
    __tablename__ = "login_nonces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=identifier)
    account: Mapped[str] = mapped_column(String(42))
    network: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    expires: Mapped[int] = mapped_column(BigInteger)
    consumed: Mapped[int] = mapped_column(Integer, default=0)

class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account: Mapped[str] = mapped_column(String(42))
    network: Mapped[str] = mapped_column(String(16))
    expires: Mapped[int] = mapped_column(BigInteger)

_engine = None
def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True)
    return _engine

def session(): return sessionmaker(engine(), expire_on_commit=False)()
def initialize():
    Base.metadata.create_all(engine())
    if engine().dialect.name == "postgresql":
        with engine().begin() as connection:
            connection.execute(text("ALTER TABLE quota_cache ADD COLUMN IF NOT EXISTS network VARCHAR(16) NOT NULL DEFAULT 'local'"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS reviews_unique_transaction ON reviews (network, transaction_hash) WHERE transaction_hash IS NOT NULL"))
def audit(db, account, action, payload):
    db.add(Audit(account=account, action=action, payload=payload, payload_hash=digest(payload)))

def quota_id(country, substance, year, mode, scope, network="local"):
    return f"{network}:{scope}:{country}:{substance}:{year}:{mode}"
