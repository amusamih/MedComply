"""Explicit local-service integration checks; no paid inference."""
import json,os
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from medcomply.api import app
from medcomply.config import LOCAL
from medcomply.chain import Chain
from medcomply.db import LoginNonce,QuotaCache,session,now,quota_id
from medcomply.quota import check_quota

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.environ.get('MEDCOMPLY_INTEGRATION')!='1',reason='Requires running local PostgreSQL, IPFS and Anvil fixture')]

def test_wallet_authentication_nonce_replay_logout_and_origin():
    wallet=Account.create();wrong=Account.create();headers={'X-MedComply':'1'}
    with TestClient(app) as client:
        challenge=client.post('/api/auth/challenge',json={'account':wallet.address,'network':'local'},headers=headers).json()
        invalid=wrong.sign_message(encode_defunct(text=challenge['message'])).signature.to_0x_hex()
        assert client.post('/api/auth/login',json={'id':challenge['id'],'signature':invalid},headers=headers).status_code==401
        signature=wallet.sign_message(encode_defunct(text=challenge['message'])).signature.to_0x_hex()
        assert client.post('/api/auth/login',json={'id':challenge['id'],'signature':signature},headers=headers).status_code==200
        assert client.get('/api/me').json()['account'].lower()==wallet.address.lower()
        assert client.post('/api/auth/login',json={'id':challenge['id'],'signature':signature},headers=headers).status_code==401
        assert client.post('/api/auth/logout',headers={**headers,'Origin':'https://unrelated.example'}).status_code==403
        assert client.post('/api/reviews',json={'action':'register_nca','arguments':{'account':wrong.address,'country':'USA'},'notes':'Unauthorized negative test.'},headers=headers).status_code==409
        assert client.post('/api/auth/local',json={'account':wallet.address},headers=headers).status_code==403
        assert client.post('/api/auth/logout',headers=headers).status_code==200
        assert client.get('/api/me').status_code==401

def test_expired_wallet_nonce_is_rejected():
    wallet=Account.create();headers={'X-MedComply':'1'}
    with TestClient(app) as client:
        c=client.post('/api/auth/challenge',json={'account':wallet.address,'network':'local'},headers=headers).json()
        with session() as db:db.get(LoginNonce,c['id']).expires=now()-1;db.commit()
        sig=wallet.sign_message(encode_defunct(text=c['message'])).signature.to_0x_hex()
        assert client.post('/api/auth/login',json={'id':c['id'],'signature':sig},headers=headers).status_code==401

def test_cap_disagreement_never_triggers_automatic_database_correction():
    c=Chain();key=quota_id('USA','morphine',2025,'export','global','local@'+c.ship.address.lower())
    with session() as db:
        row=db.get(QuotaCache,key);original=row.cap;row.cap+=1;db.commit()
    try:
        result=check_quota(c,c.shipment(2),'export','global')
        assert result['decision']=='review' and 'CAP_INTEGRITY_FAILED' in result['findings']
        with session() as db:assert db.get(QuotaCache,key).cap==original+1
    finally:
        with session() as db:db.get(QuotaCache,key).cap=original;db.commit()
