import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from bench import cloud, control, envoy


class EnvoyProfilesTest(unittest.TestCase):
    def setUp(self):
        self.base = json.loads((control.ROOT / 'defaults.json').read_text())

    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bench'
            (root / 'envoy').mkdir(parents=True)
            (root / 'defaults.json').write_text(json.dumps(self.base))
            with patch.object(control, 'ROOT', root):
                yield root

    def test_profile_selection_overrides_legacy_policy_and_survives_missing_source(self):
        with self.workspace() as root:
            path = root / 'envoy/custom.yaml'
            path.write_text('name: my-experiment\ncluster:\n  lb_policy: LEAST_REQUEST\n  least_request_lb_config:\n    choice_count: 4\n')
            relative = str(path.relative_to(control.ROOT.parent))
            config = control.resolve(control.ROOT / 'defaults.json', {'envoy_config_file': relative, 'policy': 'round_robin'})
            self.assertEqual(config['policy'], 'my-experiment')
            path.unlink()
            self.assertEqual(control.validate(config), config)
            _, cluster, _ = control.documents(config, ['one', 'two', 'three'])
            self.assertEqual(cluster['least_request_lb_config']['choice_count'], 4)

    def test_profile_paths_cannot_escape_profile_directory(self):
        for filename in ('/tmp/profile.yaml', 'bench/envoy/../../README.md', 'bench/defaults.json'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                control.resolve(control.ROOT / 'defaults.json', {'envoy_config_file': filename})
        with self.workspace() as root, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'profile.yaml'
            target.write_text('name: escape\ncluster: {lb_policy: RANDOM}')
            source = root / 'envoy/profile.yaml'
            source.symlink_to(target)
            with self.assertRaises(ValueError):
                control.resolve(control.ROOT / 'defaults.json', {'envoy_config_file': str(source.relative_to(control.ROOT.parent))})

    def test_unsafe_yaml_and_duplicate_settings_fail(self):
        for content in ('!!python/object/apply:os.system [echo unsafe]',
                        'name: duplicate\ncluster: {lb_policy: RANDOM, lb_policy: ROUND_ROBIN}'):
            with self.subTest(content=content), self.workspace() as root:
                path = root / 'envoy/profile.yaml'
                path.write_text(content)
                with self.assertRaises((ValueError, yaml.YAMLError)):
                    control.resolve(control.ROOT / 'defaults.json', {'envoy_config_file': str(path.relative_to(control.ROOT.parent))})

    def test_native_cluster_extensions_and_route_settings_are_preserved(self):
        profile = {'name': 'custom', 'cluster': {'metadata': {'filter_metadata': {'custom': {'tuning': 7}}}, 'load_balancing_policy': {'policies': [
            {'typed_extension_config': {'name': 'my.extension', 'typed_config': {'@type': 'type.example/MyAlgorithm', 'tuning': 7}}}]}},
            'route': {'timeout': '17s', 'hash_policy': [{'header': {'header_name': 'X-Bench-Key'}}]},
            'endpoint_weights': [1, 2, 1]}
        config = control.validate(self.base | {'envoy_profile': profile})
        _, cluster, routes = control.documents(config, ['one', 'two', 'three'])
        self.assertNotIn('lb_policy', cluster)
        self.assertEqual(cluster['load_balancing_policy'], profile['cluster']['load_balancing_policy'])
        self.assertNotIn('gatherly.bench', profile['cluster']['metadata']['filter_metadata'])
        self.assertEqual(routes['virtual_hosts'][0]['routes'][-1]['route']['timeout'], '17s')
        self.assertEqual([e['load_balancing_weight'] for e in cluster['load_assignment']['endpoints'][0]['lb_endpoints']], [1, 2, 1])

    def test_profile_cannot_replace_targets_and_custom_image_is_immutable(self):
        base = {'name': 'test', 'cluster': {'lb_policy': 'RANDOM'}}
        cases = [base | {'cluster': {'lb_policy': 'RANDOM', 'load_assignment': {}}},
                 base | {'route': {'cluster': 'other'}}, base | {'image': 'my-envoy:latest'},
                 base | {'cluster': {'lb_policy': 'RANDOM', 'metadata': {'filter_metadata': {'gatherly.bench': {}}}}},
                 base | {'endpoint_weights': [1, 2]}, base | {'endpoint_weights': [1, True, 1]}]
        for profile in cases:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                control.validate(self.base | {'envoy_profile': profile})
        image = 'ghcr.io/example/my-envoy@sha256:' + 'a' * 64
        config = control.validate(self.base | {'envoy_profile': base | {'image': image}})
        self.assertEqual(config['envoy_profile']['image'], image)

    def test_routing_changes_change_snapshot_and_use_rds(self):
        config = control.resolve(control.ROOT / 'defaults.json', {'envoy_config_file': 'bench/envoy/maglev.yaml'})
        with tempfile.TemporaryDirectory() as directory:
            control.render(config, ['one', 'two', 'three'], directory)
            before = json.loads((Path(directory) / 'dynamic/rds.json').read_text())
            config['envoy_profile']['route']['timeout'] = '17s'
            control.render(config, ['one', 'two', 'three'], directory)
            after = json.loads((Path(directory) / 'dynamic/rds.json').read_text())
            self.assertNotEqual(before['version_info'], after['version_info'])
            bootstrap = json.loads((Path(directory) / 'bootstrap.json').read_text())
            hcm = bootstrap['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
            self.assertIn('rds', hcm)
            self.assertNotIn('route_config', hcm)

    def test_invalid_native_profile_stops_deploy_before_terraform_or_build(self):
        environment = {'BENCH_DB_PASSWORD': 'a' * 64, 'BENCH_AUTH_SECRET': 'b' * 64}
        with patch.dict('os.environ', environment), patch.object(envoy, 'validate_native',
                side_effect=subprocess.CalledProcessError(1, 'envoy')), patch.object(cloud, 'terraform') as terraform, patch.object(cloud, 'run') as run:
            with self.assertRaises(subprocess.CalledProcessError):
                cloud.deploy(self.base, 'deploy')
            terraform.assert_not_called()
            run.assert_not_called()

    def test_static_bootstrap_migration_recreates_only_envoy_once(self):
        config = control.validate(self.base)
        bootstrap = control.documents(config, ['one', 'two', 'three'])[0]
        old = json.loads(json.dumps(bootstrap))
        hcm = old['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
        hcm.pop('rds')
        hcm['route_config'] = {'name': 'old-static-routes'}
        compose = ['docker', 'compose', '-p', 'isolated-test']
        with patch.object(control, 'get_json', side_effect=[{'configs': [{'bootstrap': old}]},
                       {'configs': [{'bootstrap': bootstrap}]}]), patch('subprocess.run') as run:
            control.reconcile(compose, bootstrap, 'http://proxy:9901')
            control.reconcile(compose, bootstrap, 'http://proxy:9901')
            run.assert_called_once_with(compose + ['up', '-d', '--no-build', '--force-recreate', 'envoy'], check=True)
