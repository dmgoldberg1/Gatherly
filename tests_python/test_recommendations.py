from datetime import datetime, timedelta, timezone
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from gatherly.auth import issue_token
from gatherly.recommendations import Recommendations
from gatherly.storage import Storage
from gatherly.worker import build_profiles


class NoDatabase:
    def transaction(self):
        raise AssertionError('warm request must not access PostgreSQL')


class RecommendationsTest(unittest.TestCase):
    def test_recent_attendance_has_more_weight_and_non_going_is_ignored(self):
        now = datetime.now(timezone.utc)
        vectors = {1: np.eye(1, 312, 0, dtype=np.float32)[0], 2: np.eye(1, 312, 1, dtype=np.float32)[0]}
        rows = [
            {'user_id': 1, 'event_id': 1, 'status': 'GOING', 'starts_at': now - timedelta(days=1)},
            {'user_id': 1, 'event_id': 2, 'status': 'GOING', 'starts_at': now - timedelta(days=31)},
            {'user_id': 2, 'event_id': 1, 'status': 'WAITLISTED', 'starts_at': now - timedelta(days=1)},
            {'user_id': 2, 'event_id': 2, 'status': 'GOING', 'starts_at': now + timedelta(days=1)},
        ]
        profiles = build_profiles([1, 2, 3], rows, vectors, now)
        self.assertAlmostEqual(profiles[1][0] / profiles[1][1], 2, places=5)
        self.assertIsNone(profiles[2])
        self.assertIsNone(profiles[3])

    def test_warm_auth_and_ranking_are_sql_free_with_exclusions_and_fallback(self):
        engine = Recommendations(NoDatabase(), None)
        engine.generation = 'test'
        engine.events = [{'id': i, '_starts_epoch': time.time() + (60 if i != 4 else -60), 'title': str(i)} for i in range(1, 5)]
        engine.vectors = np.zeros((4, 312), dtype=np.float32)
        engine.vectors[:, 0] = [1, .5, .9, 1]
        engine.popularity = np.array([1, 5, 2, 10])
        profile = np.zeros(312, dtype=np.float32)
        profile[0] = 1
        engine.users[42] = time.monotonic() + 30
        engine.profiles[42] = (time.monotonic() + 30, 'test', profile, {1})
        secret = 'a' * 64
        uid = engine.authenticate(secret, 'Bearer ' + issue_token(secret, 42))
        result = engine.recommend(uid, 10, 0)
        self.assertEqual([row['id'] for row in result['items']], [3, 2])
        self.assertEqual(result['source'], 'personalized')
        engine.profiles[42] = (time.monotonic() + 30, 'test', None, {1})
        result = engine.recommend(uid, 10, 0)
        self.assertEqual([row['id'] for row in result['items']], [2, 3])
        self.assertEqual(result['source'], 'popular')
