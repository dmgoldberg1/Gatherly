import json
from pathlib import Path
import tempfile
import unittest

from bench.control import ROOT, render, validate


class BenchmarkConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'defaults.json').read_text())

    def test_invalid_inputs_are_rejected_before_resource_changes(self):
        for changes in ({'replicas': 0}, {'replicas': 11}, {'replicas': 101}, {'replicas': True}, {'replicas': 1.5}, {'replicas': '3'}, {'policy': 'not_a_policy'}, {'duration': '1m'}, {'duration': '30s'}, {'feed_rps': -1}, {'seed': -1}, {'photo_start_after': '181s'}, {'feed_rps': 0, 'photo_upload_rps': 0, 'recommendations_rps': 0}, {'version': 2}, {'typo': 3}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate(self.config | changes)
        for replicas in (1, 3, 10):
            validate(self.config | {'replicas': replicas})

    def test_cds_version_is_stable_and_policy_change_keeps_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            addresses = ['10.0.0.1', '10.0.0.2', '10.0.0.3']
            first = render(self.config, addresses, directory)
            before = json.loads((Path(directory) / 'dynamic/cds.json').read_text())
            self.assertEqual(render(self.config, addresses, directory), first)
            self.assertNotEqual(render(self.config | {'policy': 'least_request'}, addresses, directory), first)
            after = json.loads((Path(directory) / 'dynamic/cds.json').read_text())
            self.assertEqual(before['resources'][0]['load_assignment'], after['resources'][0]['load_assignment'])
            with self.assertRaises(ValueError):
                render(self.config, addresses[:1], directory)
