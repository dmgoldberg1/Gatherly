import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench import cloud


class CloudRoleIsolationTest(unittest.TestCase):
    def generate(self, layout, directory, replicas=3):
        config = json.loads((cloud.ROOT / 'bench/defaults.json').read_text())
        config['replicas'] = replicas
        roles = {'server': 'compact', 'load': 'load'} if layout == 'compact' else {
            'database': 'database', 'proxy': 'proxy', 'monitoring': 'monitoring', 'load': 'load',
            **{f'app-{i:03d}': 'app' for i in range(1, replicas + 1)}}
        hosts = {name: {'role': role, 'private_ip': f'10.53.0.{i}', 'public_ip': f'192.0.2.{i}'}
                 for i, (name, role) in enumerate(roles.items(), 1)}
        output = {'layout': layout, 'hosts': hosts, 'bucket': 'test-bucket',
                  'object_access_key': 'test-key', 'object_secret_key': 'test-secret'}
        environment = {'BENCH_DB_PASSWORD': 'a' * 64, 'BENCH_AUTH_SECRET': 'b' * 32,
                       'BENCH_SSH_KEY': '/tmp/test-bench-key'}
        with patch.object(cloud, 'WORK', Path(directory)), patch.dict(os.environ, environment):
            cloud.files(config, output, {'app_image': 'test:app', 'worker_image': 'test:worker'}, 'update_config')
        return hosts

    def test_distributed_roles_have_distinct_hosts_and_service_sets(self):
        for replicas in (1, 3, 10):
            with self.subTest(replicas=replicas), tempfile.TemporaryDirectory() as directory:
                hosts = self.generate('distributed', directory, replicas)
                self.assertEqual(len(hosts), replicas + 4)
                self.assertEqual(len({h['private_ip'] for h in hosts.values()}), replicas + 4)
                services = {name: set(json.loads((Path(directory) / name / 'compose.json').read_text())['services'])
                            for name in hosts}
                for i in range(1, replicas + 1):
                    self.assertEqual(services[f'app-{i:03d}'], {'gatherly', 'node-exporter', 'cadvisor'})
                self.assertEqual(services['proxy'], {'envoy', 'node-exporter', 'cadvisor'})
                self.assertEqual(services['monitoring'], {'prometheus', 'grafana', 'node-exporter', 'cadvisor'})
                self.assertNotIn('gatherly', services['database'])
                self.assertNotIn('envoy', services['database'])
                self.assertNotIn('prometheus', services['database'])
                targets = json.loads((Path(directory) / 'monitoring/monitoring/config.json').read_text())['scrape_configs']
                self.assertEqual(sum(len(s['targets']) for s in targets[0]['static_configs']), replicas)
                self.assertEqual(sum(len(s['targets']) for job in targets for s in job['static_configs']), 3 * replicas + 9)

    def test_compact_layout_still_combines_roles_and_bounds_replica_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            self.generate('compact', directory)
            services = json.loads((Path(directory) / 'server/compose.json').read_text())['services']
            self.assertTrue({'envoy', 'postgres', 'prometheus', 'grafana', 'worker'} <= set(services))
            apps = [s for name, s in services.items() if name.startswith('gatherly-')]
            self.assertEqual(len(apps), 3)
            self.assertTrue(all(s['cpus'] == 1 and s['mem_limit'] == '1g' for s in apps))

    def test_configuration_update_refuses_database_replacement_before_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terraform = root / 'terraform'
            terraform.mkdir()
            (root / 'ssh-key.pub').write_text('test-public-key')
            environment = {'YC_CLOUD_ID': 'test-cloud', 'YC_FOLDER_ID': 'test-folder',
                           'BENCH_BUCKET': 'test-bucket', 'BENCH_SSH_KEY': str(root / 'ssh-key'),
                           'BENCH_ADMIN_CIDR': '192.0.2.1/32'}
            planned = {'resource_changes': [{'address': 'yandex_compute_instance.host["database"]',
                                             'change': {'actions': ['delete', 'create']}}]}
            def command(args, **kwargs):
                return json.dumps(planned) if 'show' in args else ''
            config = json.loads((cloud.ROOT / 'bench/defaults.json').read_text())
            with patch.object(cloud, 'WORK', root), patch.object(cloud, 'TF', terraform), \
                    patch.dict(os.environ, environment, clear=True), patch.object(cloud, 'run', side_effect=command) as run:
                with self.assertRaisesRegex(ValueError, 'Refusing to delete'):
                    cloud.terraform(config, apply=True, update_only='config')
            self.assertFalse(any('apply' in call.args[0] for call in run.call_args_list))
