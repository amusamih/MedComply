from __future__ import annotations
import hashlib
import secrets
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import select
from .db import LoginNonce, LoginSession, session, now

def challenge(account, network, chain_id):
    message = ("MedComply local research prototype\nSign in to " + network + " (chain " + str(chain_id) + ").\n"
               "Account: " + account + "\nNonce: " + secrets.token_urlsafe(24) + "\nExpires: " + str(now()+300))
    with session() as db:
        record = LoginNonce(account=account.lower(),network=network,message=message,expires=now()+300)
        db.add(record); db.commit()
        return {"id":record.id,"message":message,"expires":record.expires}

def create_session(account, network):
    token = secrets.token_urlsafe(40)
    with session() as db:
        db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(),account=account.lower(),network=network,expires=now()+8*3600))
        db.commit()
    return token

def login(nonce_id, signature):
    with session() as db:
        record = db.execute(select(LoginNonce).where(LoginNonce.id==nonce_id).with_for_update()).scalar_one_or_none()
        if not record or record.consumed or record.expires < now(): raise ValueError("Expired or reused login nonce")
        recovered = Account.recover_message(encode_defunct(text=record.message),signature=signature)
        if recovered.lower() != record.account: raise ValueError("Wallet signature mismatch")
        record.consumed = 1
        account, network = record.account, record.network
        db.commit()
    return create_session(account,network)

def authenticated(token):
    if not token: raise ValueError("Sign in first")
    with session() as db:
        record = db.get(LoginSession,hashlib.sha256(token.encode()).hexdigest())
        if not record or record.expires < now(): raise ValueError("Session expired")
        return {"account":record.account,"network":record.network}
