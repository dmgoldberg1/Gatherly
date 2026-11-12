"""One elected background process embeds changed events and publishes snapshots."""
from io import BytesIO
import json
import hashlib
import threading
from collections import deque
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone

import numpy as np

from .db import Database, all_rows, one, serialize
from .recommendations import MODEL, REVISION
from .service import deliver_outbox
from .storage import Storage

log = logging.getLogger('gatherly.worker')


def build_profiles(users, participations, vectors, now):
    histories = {uid: [] for uid in users}
    for row in participations:
        attended_at = row.get('ends_at') or row['starts_at']
        if row['status'] == 'GOING' and attended_at < now and row['event_id'] in vectors:
            age_days = max(0, (now - attended_at).total_seconds() / 86400)
            histories[row['user_id']].append((vectors[row['event_id']], max(1e-12, math.exp(-math.log(2) * age_days / 30))))
    profiles = {}
    for uid, history in histories.items():
        if history:
            vector = np.average([v for v, _ in history], axis=0, weights=[w for _, w in history])
            norm = np.linalg.norm(vector)
            profiles[uid] = (vector / norm if norm else vector).astype(np.float32).tolist()
        else:
            profiles[uid] = None
    return profiles


def publish(db, storage, model, cache, state):
    # Read in a consistent snapshot; embedding never holds a database transaction.
    with db.transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        events = all_rows(conn, 'SELECT * FROM events ORDER BY id')
        users = [r['id'] for r in all_rows(conn, 'SELECT id FROM users')]
        participations = all_rows(conn, 'SELECT p.*,e.starts_at,e.ends_at FROM participations p JOIN events e ON e.id=p.event_id')
    now = datetime.now(timezone.utc)
    fingerprint = hashlib.sha256(json.dumps(serialize({'events': events, 'users': users, 'participations': participations,
        'past_ids': [event['id'] for event in events if (event['ends_at'] or event['starts_at']) < now]}), sort_keys=True).encode()).hexdigest()
    if state.get('fingerprint') == fingerprint:
        return
    live_ids = {event['id'] for event in events}
    for eid in list(cache):
        if eid not in live_ids:
            del cache[eid]
    changed = [(e['id'], e['title'] + '\n' + e['description']) for e in events if cache.get(e['id'], (None,))[0] != e['title'] + '\n' + e['description']]
    if changed:
        embeddings = model.encode([text for _, text in changed], normalize_embeddings=True, convert_to_numpy=True, batch_size=32, show_progress_bar=False)
        for (eid, text), embedding in zip(changed, embeddings):
            cache[eid] = (text, embedding.astype(np.float32))
    vectors = {eid: v for eid, (_, v) in cache.items()}
    now = datetime.now(timezone.utc)
    profiles = build_profiles(users, participations, vectors, now)
    exclusions = {uid: [] for uid in users}
    counts = {}
    for row in participations:
        exclusions[row['user_id']].append(row['event_id'])
        if row['status'] == 'GOING':
            counts[row['event_id']] = counts.get(row['event_id'], 0) + 1
    candidates = [e for e in events if e['status'] == 'PUBLISHED' and e['visibility'] == 'PUBLIC' and e['starts_at'] > now]
    candidate_vectors = np.stack([vectors[e['id']] for e in candidates]) if candidates else np.zeros((0, 312), dtype=np.float32)
    metadata = [serialize(e) | {'_starts_epoch': e['starts_at'].timestamp()} for e in candidates]
    output = BytesIO()
    np.savez_compressed(output, vectors=candidate_vectors, events=json.dumps(metadata, ensure_ascii=False), popularity=np.array([counts.get(e['id'], 0) for e in candidates], dtype=np.float32))
    generation = str(uuid.uuid4())
    key = f'catalog/{generation}.npz'
    storage.put(key, output.getvalue())
    with db.transaction() as conn:
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO recommendation_profiles(user_id,vector,excluded_event_ids,generation) VALUES (%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET vector=EXCLUDED.vector,excluded_event_ids=EXCLUDED.excluded_event_ids,generation=EXCLUDED.generation,updated_at=now()',
                               [(uid, profiles[uid], exclusions[uid], generation) for uid in users])
    storage.put('catalog/current.json', json.dumps({'generation': generation, 'key': key, 'model': MODEL, 'revision': REVISION}).encode(), 'application/json')
    state['fingerprint'] = fingerprint
    state.setdefault('snapshots', deque()).append(key)
    if len(state['snapshots']) > 6:
        storage.delete(state['snapshots'].popleft())
    log.info('Published generation %s (%d events, %d profiles)', generation, len(candidates), len(users))


def health_loop(db, storage):
    while True:
        healthy = False
        try:
            with db.transaction() as conn:
                one(conn, 'SELECT 1')
            healthy = True
        except Exception:
            log.warning('PostgreSQL health check failed')
        try:
            storage.put('catalog/health.json', json.dumps({'database_up': healthy, 'at': time.time()}).encode(), 'application/json')
        except Exception:
            log.exception('Could not publish shared database health')
        time.sleep(3)


def outbox_loop(db):
    while True:
        try:
            deliver_outbox(db)
        except Exception:
            log.exception('Outbox delivery deferred')
        time.sleep(1)


def main():
    from sentence_transformers import SentenceTransformer
    logging.basicConfig(level=logging.INFO)
    db, storage = Database(), Storage()
    db.pool.open()
    # A separate session lock elects one encoder across accidental duplicate workers.
    # It counts as one extra connection in the stand's budget.
    import psycopg
    try:
        with psycopg.connect(os.environ['GATHERLY_DATABASE_URL'], autocommit=True, connect_timeout=3) as election:
            if not election.execute('SELECT pg_try_advisory_lock(714202602)').fetchone()[0]:
                raise RuntimeError('An embedding worker is already running')
            threading.Thread(target=health_loop, args=(db, storage), daemon=True).start()
            threading.Thread(target=outbox_loop, args=(db,), daemon=True).start()
            model = SentenceTransformer(MODEL, revision=REVISION, device='cpu', trust_remote_code=False)
            cache, state = {}, {}
            while True:
                # A lost leadership connection terminates this process. Docker restarts
                # it and it must win the lock again before encoding any events.
                election.execute('SELECT 1')
                try:
                    publish(db, storage, model, cache, state)
                except Exception:
                    log.exception('Snapshot publication failed; retaining previous snapshot')
                time.sleep(float(os.getenv('GATHERLY_SNAPSHOT_INTERVAL', '10')))
    finally:
        db.pool.close()


if __name__ == '__main__':
    main()
