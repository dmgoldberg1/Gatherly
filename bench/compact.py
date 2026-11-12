"""Combine the logical stand roles on one small application VM."""
import json
import os
from pathlib import Path
import shutil

if __package__:
    from .control import atomic_json, render
else:
    from control import atomic_json, render


def generate(cloud, config, output, runtime, mode):
    server, load = output['hosts']['server'], output['hosts']['load']
    roles = {'database': dict(server, role='database'), 'proxy': dict(server, role='proxy'),
             'monitoring': dict(server, role='monitoring'), 'load': load}
    names = [f'gatherly-{i:03d}' for i in range(1, config['replicas'] + 1)]
    for i, name in enumerate(names, 1):
        roles[name] = dict(server, role='app', private_ip=f'127.1.0.{i}')
    # Reuse the same service definitions, images, migrations and Grafana provisioning.
    cloud.files(config, dict(output, hosts=roles, layout='distributed'), runtime, mode)
    destination = cloud.WORK / 'server'
    shutil.copytree(cloud.WORK / 'database', destination, dirs_exist_ok=True)
    shutil.copytree(cloud.WORK / 'proxy', destination, dirs_exist_ok=True)
    shutil.copytree(cloud.WORK / 'monitoring', destination, dirs_exist_ok=True)
    database = json.loads((cloud.WORK / 'database/compose.json').read_text())
    proxy = json.loads((cloud.WORK / 'proxy/compose.json').read_text())
    monitoring = json.loads((cloud.WORK / 'monitoring/compose.json').read_text())
    services = database['services'] | proxy['services'] | monitoring['services']
    for name in names:
        service = json.loads((cloud.WORK / name / 'compose.json').read_text())['services']['gatherly']
        service.pop('ports', None)
        service.update(cpus=1, mem_limit='1g')
        service['environment']['GATHERLY_REPLICA_ID'] = name
        service['labels'] = {'gatherly.replica': name}
        services[name] = service
    services['worker']['environment'] = {'OMP_NUM_THREADS': '1'}
    services['postgres']['command'] = ['postgres', '-c', 'max_connections=230', '-c', 'shared_buffers=128MB']
    services['prometheus']['command'] += ['--storage.tsdb.retention.size=128MB']
    services['prometheus']['mem_limit'] = '256m'
    services['grafana']['mem_limit'] = '512m'
    for service in services.values():
        service['logging'] = {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '2'}}
    compose = {'services': services, 'volumes': database['volumes'] | proxy.get('volumes', {}) | monitoring['volumes']}
    render(config, names, destination)
    targets = cloud.monitoring(output['hosts'])
    targets['scrape_configs'][0]['static_configs'] = [{'targets': [name + ':8080'], 'labels': {'replica': name}} for name in names]
    targets['scrape_configs'][-1]['static_configs'] = [{'targets': [server['private_ip'] + ':9187']}]
    atomic_json(destination / 'monitoring/config.json', targets)
    atomic_json(destination / 'compose.json', compose)
    (destination / 'compose.json').chmod(0o600)
    inventory = {'all': {'children': {'compact': {'hosts': {}}, 'load': {'hosts': {}}}}}
    for name, host, group in [('server', server, 'compact'), ('load', load, 'load')]:
        inventory['all']['children'][group]['hosts'][name] = {
            'ansible_host': host['public_ip'], 'ansible_user': 'ubuntu',
            'ansible_ssh_private_key_file': os.environ['BENCH_SSH_KEY'],
            'ansible_ssh_common_args': '-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=' + str(cloud.STATE / 'known_hosts'),
            'bench_role': group, 'bench_mode': mode, 'bench_layout': 'compact',
            'bench_files': str(cloud.WORK / name), 'bench_app_archive': str(cloud.STATE / 'app.tar'),
            'bench_worker_archive': str(cloud.STATE / 'worker.tar'),
        }
    atomic_json(cloud.WORK / 'inventory.json', inventory)
