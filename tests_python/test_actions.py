import json
import os
from pathlib import Path
import tempfile
import unittest

from bench.actions_setup import materialize
from bench.control import ROOT, resolve


class ActionsConfigurationTest(unittest.TestCase):
    def test_versioned_config_and_dispatch_overrides_resolve_to_one_config(self):
        source = ROOT / 'configs/ten-replicas-60s.json'
        config = resolve(source, {'mode': 'update_config', 'run_load_test': True,
                                  'replicas': '3', 'photo_start_after': '10s', 'feed_rps': '0'})
        self.assertEqual(config['replicas'], 3)
        self.assertEqual(config['duration'], '60s')
        self.assertEqual(config['photo_start_after'], '10s')
        self.assertEqual(config['feed_rps'], 0)
        self.assertNotIn('mode', config)
        self.assertNotIn('run_load_test', config)
        self.assertEqual(resolve(ROOT / 'defaults.json', {'replicas': ''})['replicas'], 3)

    def test_invalid_overrides_and_external_config_are_rejected(self):
        for overrides in ({'replicas': '11'}, {'replicas': True}, {'replicas': '1;exit'},
                          {'duration': '60s'}, {'policy': 'random'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                resolve(ROOT / 'defaults.json', overrides)
        with self.assertRaises(ValueError):
            resolve('/tmp/outside-benchmark.json')

    def environment(self):
        return {'YC_CLOUD_ID': 'cloud', 'YC_FOLDER_ID': 'folder', 'BENCH_BUCKET': 'photos',
                'BENCH_STATE_BUCKET': 'state', 'AWS_ACCESS_KEY_ID': 'test-key',
                'AWS_SECRET_ACCESS_KEY': 'test-secret', 'BENCH_DB_PASSWORD': 'a' * 64,
                'BENCH_AUTH_SECRET': 'b' * 32, 'BENCH_SSH_PRIVATE_KEY': 'test-ssh-key',
                'YC_SA_KEY_JSON': json.dumps({'id': 'id', 'service_account_id': 'sa', 'private_key': 'key'})}

    def test_credentials_are_private_and_persistent_secret_values_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'credentials'
            env = self.environment()
            paths = materialize(root, env)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            for path in paths.values():
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(Path(paths['BENCH_SSH_KEY']).read_text(), 'test-ssh-key\n')
            self.assertEqual(json.loads(Path(paths['YC_SERVICE_ACCOUNT_KEY_FILE']).read_text()),
                             json.loads(env['YC_SA_KEY_JSON']))

    def test_missing_or_invalid_settings_fail_before_materializing_credentials(self):
        for changes in ({'BENCH_STATE_BUCKET': ''}, {'BENCH_DB_PASSWORD': 'invalid'},
                        {'BENCH_AUTH_SECRET': 'short'}, {'YC_SA_KEY_JSON': '{}'}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'credentials'
                with self.assertRaises(ValueError):
                    materialize(root, self.environment() | changes)
                self.assertFalse(root.exists())
