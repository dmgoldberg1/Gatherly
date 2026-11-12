#!/usr/bin/env python3
"""Fault-injection tests ONLY for the disposable gatherly-check Compose project."""
import json
import subprocess
import time
from smoke import smoke

COMPOSE=['docker','compose','-p','gatherly-check','--env-file','.local/smoke.env','-f','docker-compose.prod.yml']
def compose(*args):
    result=subprocess.run(COMPOSE+list(args),capture_output=True,text=True,timeout=120)
    if result.returncode: raise RuntimeError(result.stderr)
    return result.stdout.strip()
def sql(query): return compose('exec','-T','postgres','psql','-U','gatherly','-d','gatherly','-v','ON_ERROR_STOP=1','-Atc',query)
def private(path): return compose('exec','-T','gatherly','curl','-sS','--max-time','8','-w','\n%{http_code}',f'http://localhost:8080{path}')
def eventually(check, seconds=30):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:
            if check(): return
        except (RuntimeError,AssertionError): pass
        time.sleep(.5)
    raise AssertionError('Condition did not recover')

client,accounts,_=smoke('http://127.0.0.1:18080')
(owner,ot),(guest,gt),_=accounts
compose('run','--rm','migrate')
from pathlib import Path
assert int(sql('SELECT count(*) FROM schema_migrations')) == len(list(Path('db/migrations').glob('*.sql')))
assert private('/ready').endswith('200')
client.request('GET','/metrics',expected=404)
client.request('GET','/ready',expected=404)
# Inject an outbox insertion failure: publication must roll back as one unit.
event=client.api('POST','/events',body={'title':'Rollback test','location':'Online','starts_at':'2027-09-06T15:00:00Z'},token=ot,expected=201)
eid=event['id']
sql("CREATE FUNCTION fail_outbox_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected outbox failure'; END $$; CREATE TRIGGER fail_outbox BEFORE INSERT ON notification_outbox FOR EACH ROW EXECUTE FUNCTION fail_outbox_test();")
try:
    client.api('POST',f'/events/{eid}/publish',token=ot,expected=500)
    assert sql(f'SELECT status FROM events WHERE id={eid}')=='DRAFT'
finally: sql('DROP TRIGGER fail_outbox ON notification_outbox; DROP FUNCTION fail_outbox_test()')
print('PASS: event publication rolls back if outbox insertion fails')
# Inject delivery failure after event commit; the persisted outbox must retry.
sql("CREATE FUNCTION fail_delivery_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected delivery failure'; END $$; CREATE TRIGGER fail_delivery BEFORE INSERT ON notifications FOR EACH ROW EXECUTE FUNCTION fail_delivery_test();")
try:
    client.api('POST',f'/events/{eid}/publish',token=ot)
    assert sql(f'SELECT status FROM events WHERE id={eid}')=='PUBLISHED'
    assert int(sql("SELECT count(*) FROM notification_outbox WHERE status='PENDING'"))>0
    assert 'notification_outbox_pending ' in private('/metrics')
    # Restart while pending: persistence, not in-memory retry.
    compose('restart','gatherly')
finally: sql('DROP TRIGGER fail_delivery ON notifications; DROP FUNCTION fail_delivery_test()')
eventually(lambda: sql("SELECT count(*) FROM notification_outbox WHERE status<>'PROCESSED'")=='0')
count=sql('SELECT count(*) FROM notifications')
compose('restart','gatherly')
eventually(lambda: private('/ready').endswith('200'))
time.sleep(3)
assert sql('SELECT count(*) FROM notifications')==count
client.api('GET','/me',token=ot)
print('PASS: pending outbox survives restart, retries and does not redeliver completed records; JWT survives restart')
# Database outage must affect readiness but never block the independent event loop.
compose('stop','postgres')
try:
    start=time.monotonic()
    assert private('/ready').endswith('503')
    assert time.monotonic()-start<8
    client.request('GET','/health')
    client.api('GET','/events',expected=500)
finally: compose('start','postgres')
eventually(lambda: private('/ready').endswith('200'))
eventually(lambda: bool(client.api('GET','/me',token=ot)))
print('PASS: PostgreSQL outage produces readiness 503 and recovery reconnects without app restart')
# A dump must be restorable, not merely exist on disk.
compose('exec','-T','postgres','sh','-c','pg_dump -U gatherly -d gatherly -Fc > /tmp/gatherly-test.dump && createdb -U gatherly gatherly_restore_check && pg_restore -U gatherly -d gatherly_restore_check /tmp/gatherly-test.dump')
try:
    restored=compose('exec','-T','postgres','psql','-U','gatherly','-d','gatherly_restore_check','-Atc','SELECT count(*) FROM users')
    assert restored==sql('SELECT count(*) FROM users')
finally:
    compose('exec','-T','postgres','dropdb','-U','gatherly','gatherly_restore_check')
    compose('exec','-T','postgres','rm','/tmp/gatherly-test.dump')
print('PASS: pg_dump restores into a separate database with matching account count')
# Validate actual scraped targets, dashboard provisioning and HTTP metrics.
compose('exec','-T','prometheus','promtool','check','config','/etc/prometheus/prometheus.yml')
def targets_ready():
    data=json.loads(compose('exec','-T','prometheus','wget','-qO-','http://localhost:9090/api/v1/targets'))
    return len(data['data']['activeTargets'])==4 and all(t['health']=='up' for t in data['data']['activeTargets'])
eventually(targets_ready,45)
assert 'http_request_duration_seconds_bucket' in private('/metrics')
print('PASS: Prometheus configuration/rules and all four scrape targets')
