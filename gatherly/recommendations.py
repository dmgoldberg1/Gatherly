"""Replicas rank a shared immutable catalogue; only cold profile/auth caches use SQL."""
from io import BytesIO
import json
import os
import threading
import time

import numpy as np

from .auth import verify_token
from .db import one
from .service import require

MODEL = 'cointegrated/rubert-tiny2'
REVISION = 'e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae'


class Recommendations:
    def __init__(self, db, storage):
        self.db, self.storage = db, storage
        self.lock = threading.RLock()
        self.generation = None
        self.events, self.vectors, self.popularity = [], np.zeros((0, 312), dtype=np.float32), np.zeros(0)
        self.profiles = {}
        self.users = {}
        self.ttl = float(os.getenv('GATHERLY_PROFILE_CACHE_SECONDS', '30'))
        self.max_cache = 10000

    def refresh(self):
        try:
            manifest = json.loads(self.storage.get('catalog/current.json'))
        except FileNotFoundError:
            return
        require(manifest.get('model') == MODEL and manifest.get('revision') == REVISION, 503, 'incompatible recommendation snapshot.')
        if manifest['generation'] == self.generation:
            return
        data = self.storage.get(manifest['key'])
        with np.load(BytesIO(data), allow_pickle=False) as archive:
            events = json.loads(str(archive['events']))
            vectors = archive['vectors'].astype(np.float32)
            popularity = archive['popularity'].astype(np.float32)
            require(vectors.shape == (len(events), 312) and len(popularity) == len(events), 503, 'invalid recommendation snapshot.')
        with self.lock:
            self.events, self.vectors, self.popularity = events, vectors, popularity
            self.generation = manifest['generation']
            self.profiles.clear()

    def authenticate(self, secret, authorization):
        require(authorization is not None, 401, 'Authorization header is required.')
        uid = verify_token(secret, authorization.removeprefix('Bearer '))
        require(uid, 401, 'invalid token.')
        now = time.monotonic()
        with self.lock:
            valid = self.users.get(uid, 0) > now
        if not valid:
            with self.db.transaction() as conn:
                require(one(conn, 'SELECT 1 FROM users WHERE id=%s', (uid,)), 401, 'invalid token.')
            with self.lock:
                if len(self.users) >= self.max_cache:
                    self.users.clear()
                self.users[uid] = now + self.ttl
        return uid

    def profile(self, uid):
        now = time.monotonic()
        with self.lock:
            generation = self.generation
            cached = self.profiles.get(uid)
            if cached and cached[0] > now and cached[1] == generation:
                return cached[2], cached[3]
        with self.db.transaction() as conn:
            profile = one(conn, 'SELECT vector,excluded_event_ids FROM recommendation_profiles WHERE user_id=%s', (uid,))
            # Include current participation changes even before the embedding worker catches up.
            excluded = {r['event_id'] for r in conn.execute('SELECT event_id FROM participations WHERE user_id=%s', (uid,)).fetchall()}
        vector = np.asarray(profile['vector'], dtype=np.float32) if profile and profile['vector'] else None
        if vector is not None:
            require(vector.shape == (312,), 503, 'invalid recommendation profile.')
        with self.lock:
            if len(self.profiles) >= self.max_cache:
                self.profiles.clear()
            self.profiles[uid] = (now + self.ttl, generation, vector, excluded)
        return vector, excluded

    def invalidate(self, uid):
        with self.lock:
            self.profiles.pop(uid, None)

    def recommend(self, uid, limit, offset):
        vector, excluded = self.profile(uid)
        with self.lock:
            events, vectors, popularity, generation = self.events, self.vectors, self.popularity, self.generation
        require(generation, 503, 'recommendation catalogue is not ready.')
        if vector is None or not np.linalg.norm(vector):
            scores, source = popularity, 'popular'
        else:
            scores, source = vectors @ (vector / np.linalg.norm(vector)), 'personalized'
        now = time.time()
        order = sorted((i for i, e in enumerate(events) if e['id'] not in excluded and e['_starts_epoch'] > now), key=lambda i: (-float(scores[i]), events[i]['id']))
        items = [{k: v for k, v in events[i].items() if not k.startswith('_')} | {'score': float(scores[i])} for i in order[offset:offset + limit]]
        return {'items': items, 'count': len(items), 'limit': limit, 'offset': offset, 'source': source, 'generation': generation}
