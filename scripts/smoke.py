#!/usr/bin/env python3
"""End-to-end smoke against an explicitly supplied demo URL; creates test accounts/events."""
import concurrent.futures
import json
import secrets
import sys
import urllib.error
import urllib.request

class Client:
    def __init__(self, base):
        self.base = base.rstrip('/')
    def request(self, method, path, body=None, token='', expected=200):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try: response = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as error: response = error
        with response:
            raw = response.read().decode()
            assert response.status == expected, (method, path, response.status, expected, raw[:200])
            return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw
    def api(self, method, path, **kwargs):
        return self.request(method, '/api/v1' + path, **kwargs)

def smoke(base):
    client = Client(base)
    client.request('GET', '/health')
    suffix = secrets.token_hex(4)
    accounts=[]
    for role in ['owner', 'guest', 'waiting']:
        username=f'{role}-{suffix}'
        user=client.api('POST', '/auth/register', body={'username':username,'email':username+'@example.test','password':'smoke-password'},expected=201)
        login=client.api('POST','/auth/login',body={'username':username,'password':'smoke-password'})
        assert 'password_hash' not in user
        accounts.append((user,login['token']))
    (owner,ot),(guest,gt),(waiting,wt)=accounts
    client.api('GET','/me',token=ot)
    client.api('GET','/me',token=ot+'bad',expected=401)
    client.api('POST',f'/users/{owner["id"]}/follow',token=gt)
    body={'title':'Smoke: Привет 👋','description':'demo smoke test','location':'Online','starts_at':'2027-09-05T18:00:00+03:00','ends_at':'2027-09-05T16:00:00Z','participant_limit':1}
    event=client.api('POST','/events',body=body,token=ot,expected=201)
    eid=event['id']
    assert event['starts_at']=='2027-09-05T15:00:00Z'
    client.api('POST',f'/events/{eid}/publish',token=ot)
    notes=client.api('GET','/notifications?limit=100',token=gt)['items']
    assert any(n['type']=='EVENT_PUBLISHED' for n in notes)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        joins=list(pool.map(lambda token: client.api('POST',f'/events/{eid}/participants',token=token,expected=201),[gt,wt]))
    assert sorted(p['status'] for p in joins)==['GOING','WAITLISTED'],joins
    going_token = [gt,wt][next(i for i,p in enumerate(joins) if p['status']=='GOING')]
    client.api('DELETE',f'/events/{eid}/participants/me',token=going_token)
    participants=client.api('GET',f'/events/{eid}/participants',token=ot)
    assert len(participants)==1 and participants[0]['status']=='GOING'
    client.api('PATCH',f'/events/{eid}',body={'location':'New place'},token=ot)
    client.api('POST',f'/events/{eid}/cancel',token=ot)
    client.api('POST',f'/events/{eid}/participants',token=going_token,expected=409)
    owned=client.api('GET','/me/events?limit=100',token=ot)['items']
    assert any(e['id']==eid and e['status']=='CANCELLED' for e in owned)
    client.api('PATCH',f'/notifications/{notes[0]["id"]}/read',token=gt)
    client.api('PATCH',f'/notifications/{notes[0]["id"]}/read',token=wt,expected=404)
    print('PASS: auth, Unicode, timezones, follow, publish, concurrent capacity, waitlist promotion, edit, cancel, owned events, notifications')
    return client,accounts,event

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: python3 scripts/smoke.py http(s)://demo-address')
    smoke(sys.argv[1])
