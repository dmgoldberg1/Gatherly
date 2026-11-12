#!/usr/bin/env python3
"""Cloud orchestration; mutable files stay under .local/bench or Terraform state."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import time

if __package__:
    from .control import atomic_json, render, validate
    from . import backend
else:
    from control import atomic_json, render, validate
    import backend

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT / 'infra' / 'terraform' / 'bench'
STATE = ROOT / '.local' / 'bench'
WORK = STATE / 'work'


def run(argv, capture=False, **kwargs):
    result = subprocess.run(argv, check=True, text=True, capture_output=capture, **kwargs)
    return result.stdout.strip() if capture else result


def ssh(host, *command, capture=False):
    return run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(STATE / 'known_hosts'),
                '-i', os.environ['BENCH_SSH_KEY'], 'ubuntu@' + host, *command], capture=capture)


def terraform(config, apply=False, plan_only=False, update_only=False):
    required = ('YC_CLOUD_ID', 'YC_FOLDER_ID', 'BENCH_BUCKET', 'BENCH_SSH_KEY')
    for name in required:
        if not os.environ.get(name):
            raise ValueError('missing environment setting ' + name)
    preemptible = os.getenv('BENCH_PREEMPTIBLE', 'true').lower()
    if preemptible not in ('true', 'false'):
        raise ValueError('BENCH_PREEMPTIBLE must be true or false')
    pubkey = Path(os.environ['BENCH_SSH_KEY'] + '.pub')
    if not pubkey.is_file():
        pubkey.write_text(run(['ssh-keygen', '-y', '-f', os.environ['BENCH_SSH_KEY']], capture=True) + '\n')
    cidr = os.getenv('BENCH_ADMIN_CIDR')
    if not cidr:
        with urllib.request.urlopen('https://api.ipify.org', timeout=10) as response:
            cidr = response.read().decode().strip() + '/32'
    variables = {'cloud_id': os.environ['YC_CLOUD_ID'], 'folder_id': os.environ['YC_FOLDER_ID'],
                 'zone': os.getenv('YC_ZONE', 'ru-central1-a'), 'replicas': config['replicas'],
                 'layout': os.getenv('BENCH_LAYOUT', 'distributed'),
                 'load_zone': os.getenv('BENCH_LOAD_ZONE', 'ru-central1-d'),
                 'preemptible': preemptible == 'true',
                 'ssh_public_key_path': str(pubkey.resolve()), 'admin_cidr': cidr,
                 'bucket_name': os.environ['BENCH_BUCKET'], 'stand_name': os.getenv('BENCH_STAND_NAME', 'gatherly-bench'),
                 'existing_subnet_id': os.getenv('BENCH_SUBNET_ID', '')}
    variables_file = WORK / 'variables.tfvars.json'
    atomic_json(variables_file, variables)
    backend.initialize(TF, os.getenv('BENCH_STATE_BUCKET'), variables['stand_name'], run)
    run(['terraform', '-chdir=' + str(TF), 'validate'])
    if apply and not update_only and variables['layout'] == 'distributed':
        try:
            addresses = run(['terraform', '-chdir=' + str(TF), 'state', 'list'], capture=True).splitlines()
        except subprocess.CalledProcessError as exc:
            if 'No state file' not in (exc.stderr or ''):
                raise
            addresses = []
        # Readdress the existing compact database host; never delete its disk
        # just because application and monitoring roles move to their own VMs.
        old, new = 'yandex_compute_instance.host["server"]', 'yandex_compute_instance.host["database"]'
        if old in addresses and new not in addresses:
            run(['terraform', '-chdir=' + str(TF), 'state', 'mv', old, new])
    if apply or plan_only:
        plan = WORK / 'bench.tfplan'
        run(['terraform', '-chdir=' + str(TF), 'plan', '-input=false', '-var-file=' + str(variables_file), '-out=' + str(plan)])
        planned = json.loads(run(['terraform', '-chdir=' + str(TF), 'show', '-json', str(plan)], capture=True))
        for resource in planned.get('resource_changes', []):
            if resource['address'] in ('yandex_compute_instance.host["server"]', 'yandex_compute_instance.host["database"]') and 'delete' in resource['change']['actions']:
                raise ValueError('Refusing to delete or replace the database VM; migrate its state or disk explicitly first')
        if update_only:
            for resource in planned.get('resource_changes', []):
                actions = resource['change']['actions']
                if actions in (['no-op'], ['read']):
                    continue
                address = resource['address']
                if address == 'yandex_vpc_security_group.bench' and actions == ['update']:
                    continue
                if update_only == 'config' and address.startswith('yandex_compute_instance.host["app-'):
                    continue
                raise ValueError('Configuration update would modify a persistent stand resource: ' + address)
        if plan_only:
            return {}
        run(['terraform', '-chdir=' + str(TF), 'apply', '-input=false', str(plan)])
    output = json.loads(run(['terraform', '-chdir=' + str(TF), 'output', '-json'], capture=True))
    resolved = {key: item['value'] for key, item in output.items()}
    if apply and start_stopped(resolved):
        run(['terraform', '-chdir=' + str(TF), 'apply', '-refresh-only', '-auto-approve', '-input=false', '-var-file=' + str(variables_file)])
        output = json.loads(run(['terraform', '-chdir=' + str(TF), 'output', '-json'], capture=True))
        resolved = {key: item['value'] for key, item in output.items()}
    return resolved


def compact_files(config, output, runtime, mode):
    if __package__:
        from .compact import generate
    else:
        from compact import generate
    return generate(sys.modules[__name__], config, output, runtime, mode)


def cloud_token():
    if os.getenv('YC_TOKEN'):
        return os.environ['YC_TOKEN']
    if getattr(cloud_token, 'cached', None):
        return cloud_token.cached
    import jwt
    key = json.loads(Path(os.environ['YC_SERVICE_ACCOUNT_KEY_FILE']).read_text())
    now = int(time.time())
    signed = jwt.encode({'iss': key['service_account_id'], 'aud': 'https://iam.api.cloud.yandex.net/iam/v1/tokens',
                         'iat': now, 'exp': now + 360}, key['private_key'], algorithm='PS256', headers={'kid': key['id']})
    request = urllib.request.Request('https://iam.api.cloud.yandex.net/iam/v1/tokens', data=json.dumps({'jwt': signed}).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        cloud_token.cached = json.load(response)['iamToken']
    return cloud_token.cached


def compute_request(path, method='GET'):
    request = urllib.request.Request('https://compute.api.cloud.yandex.net/compute/v1/' + path,
        data=b'{}' if method == 'POST' else None, method=method,
        headers={'Authorization': 'Bearer ' + cloud_token(), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def start_stopped(output):
    started = False
    for host in output.get('hosts', {}).values():
        info = compute_request('instances/' + host['id'])
        if info['status'] == 'STOPPED':
            compute_request('instances/' + host['id'] + ':start', method='POST')
            started = True
            deadline = time.monotonic() + 180
            while compute_request('instances/' + host['id'])['status'] != 'RUNNING':
                if time.monotonic() >= deadline:
                    raise RuntimeError('Timed out waiting for benchmark VM to start')
                time.sleep(3)
    return started


def stream_image(host, archive):
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(STATE / 'known_hosts'),
               '-i', os.environ['BENCH_SSH_KEY'], 'ubuntu@' + host, 'sudo docker load']
    with Path(archive).open('rb') as source:
        compressor = subprocess.Popen(['gzip', '-1c'], stdin=source, stdout=subprocess.PIPE)
        try:
            subprocess.run(command, stdin=compressor.stdout, check=True)
        finally:
            compressor.stdout.close()
            if compressor.wait() != 0:
                raise RuntimeError('Image compression failed')


def monitoring(hosts):
    return {'global': {'scrape_interval': '5s'}, 'scrape_configs': [
        {'job_name': 'gatherly', 'static_configs': [{'targets': [host['private_ip'] + ':8080'], 'labels': {'replica': name}} for name, host in hosts.items() if host['role'] == 'app']},
        {'job_name': 'node', 'static_configs': [{'targets': [host['private_ip'] + ':9100'], 'labels': {'node': name}} for name, host in hosts.items()]},
        {'job_name': 'cadvisor', 'static_configs': [{'targets': [host['private_ip'] + ':8081'], 'labels': {'node': name}} for name, host in hosts.items()]},
        {'job_name': 'postgres', 'static_configs': [{'targets': [(hosts.get('server') or hosts['database'])['private_ip'] + ':9187']}]},
    ]}


def files(config, output, runtime, mode):
    if output.get('layout') == 'compact':
        return compact_files(config, output, runtime, mode)
    hosts = output['hosts']
    db_ip = hosts['database']['private_ip']
    # Values are persisted secrets, never part of run artifacts or stdout.
    password, secret = os.environ['BENCH_DB_PASSWORD'], os.environ['BENCH_AUTH_SECRET']
    if len(password) != 64 or len(secret) < 32 or any(c not in '0123456789abcdef' for c in password):
        raise ValueError('BENCH_DB_PASSWORD must be 64 hex characters; auth secret at least 32 bytes')
    env = {'GATHERLY_DATABASE_URL': f'postgresql://gatherly:{password}@{db_ip}:5432/gatherly',
           'GATHERLY_AUTH_SECRET': secret, 'GATHERLY_DB_POOL_SIZE': '2', 'GATHERLY_OUTBOX_WORKER': '0',
           'GATHERLY_CACHED_READINESS': '1', 'GATHERLY_S3_BUCKET': output['bucket'],
           'AWS_ACCESS_KEY_ID': output['object_access_key'], 'AWS_SECRET_ACCESS_KEY': output['object_secret_key']}
    inventory = {'all': {'children': {role: {'hosts': {}} for role in ('app', 'database', 'proxy', 'monitoring', 'load')}}}
    for name, host in hosts.items():
        directory = WORK / name
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        compose = {'services': {}, 'volumes': {}}
        services = compose['services']
        services['node-exporter'] = {'image': 'prom/node-exporter:v1.10.2', 'network_mode': 'host', 'pid': 'host', 'command': ['--path.rootfs=/host'], 'volumes': ['/:/host:ro,rslave'], 'restart': 'unless-stopped'}
        services['cadvisor'] = {'image': 'ghcr.io/google/cadvisor:0.60.6', 'ports': ['8081:8080'], 'volumes': ['/:/rootfs:ro', '/var/run:/var/run:ro', '/sys:/sys:ro', '/var/lib/docker:/var/lib/docker:ro'], 'restart': 'unless-stopped'}
        if host['role'] == 'app':
            services['gatherly'] = {'image': runtime['app_image'], 'env_file': ['private.env'], 'environment': {'GATHERLY_REPLICA_ID': name}, 'ports': ['8080:8080'], 'labels': {'gatherly.replica': name}, 'cpus': 8, 'mem_limit': '12g', 'restart': 'unless-stopped'}
        elif host['role'] == 'database':
            services['postgres'] = {'image': 'postgres:16-alpine', 'environment': {'POSTGRES_DB': 'gatherly', 'POSTGRES_USER': 'gatherly', 'POSTGRES_PASSWORD': password},
                'command': ['postgres', '-c', 'max_connections=230', '-c', 'shared_buffers=256MB'], 'ports': ['5432:5432'], 'volumes': ['postgres_data:/var/lib/postgresql/data'], 'restart': 'unless-stopped',
                'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U gatherly -d gatherly'], 'interval': '3s', 'timeout': '3s', 'retries': 20}}
            compose['volumes']['postgres_data'] = {}
            services['migrate'] = {'image': 'postgres:16-alpine', 'environment': {'PGHOST': db_ip, 'PGUSER': 'gatherly', 'PGDATABASE': 'gatherly', 'PGPASSWORD': password},
                'entrypoint': ['sh', '/migrate.sh'], 'volumes': ['./db/migrations:/migrations:ro', './scripts/migrate.sh:/migrate.sh:ro'], 'profiles': ['tools']}
            services['worker'] = {'image': runtime['worker_image'], 'env_file': ['private.env'], 'restart': 'unless-stopped', 'mem_limit': '2g'}
            services['postgres-exporter'] = {'image': 'quay.io/prometheuscommunity/postgres-exporter:v0.18.1', 'ports': ['9187:9187'], 'environment': {'DATA_SOURCE_NAME': env['GATHERLY_DATABASE_URL'] + '?sslmode=disable'}, 'restart': 'unless-stopped'}
            shutil.copytree(ROOT / 'db', directory / 'db', dirs_exist_ok=True)
            shutil.copytree(ROOT / 'scripts', directory / 'scripts', dirs_exist_ok=True)
        elif host['role'] == 'proxy':
            addresses = [host['private_ip'] for host in hosts.values() if host['role'] == 'app']
            render(config, addresses, directory)
            services['envoy'] = {'image': 'envoyproxy/envoy:v1.37.1', 'command': ['-c', '/etc/envoy/bootstrap.json', '--concurrency', '2'], 'ports': ['8080:8080', '127.0.0.1:9901:9901'], 'volumes': ['./bootstrap.json:/etc/envoy/bootstrap.json:ro', './dynamic:/etc/envoy/dynamic:ro'], 'restart': 'unless-stopped'}
            shutil.copyfile(ROOT / 'bench/control.py', directory / 'control.py')
            shutil.copyfile(ROOT / 'bench/defaults.json', directory / 'defaults.json')
        elif host['role'] == 'monitoring':
            # Mount the directory so an atomic config replacement is visible on HUP.
            atomic_json(directory / 'monitoring/config.json', monitoring(hosts))
            services['prometheus'] = {'image': 'prom/prometheus:v3.13.1', 'command': ['--config.file=/etc/prometheus/bench/config.json', '--storage.tsdb.retention.time=3d'], 'ports': ['127.0.0.1:9090:9090'],
                'volumes': ['./monitoring:/etc/prometheus/bench:ro', 'prometheus_data:/prometheus'], 'restart': 'unless-stopped'}
            services['grafana'] = {'image': 'grafana/grafana:13.2.0', 'ports': ['127.0.0.1:3000:3000'], 'environment': {'GF_SECURITY_ADMIN_PASSWORD': secret, 'GF_USERS_ALLOW_SIGN_UP': 'false', 'GF_PLUGINS_PREINSTALL_DISABLED': 'true'},
                'volumes': ['./grafana:/etc/grafana/provisioning:ro', './dashboards:/var/lib/grafana/dashboards:ro', 'grafana_data:/var/lib/grafana'], 'restart': 'unless-stopped'}
            compose['volumes'].update(prometheus_data={}, grafana_data={})
            shutil.copytree(ROOT / 'bench/grafana', directory / 'grafana', dirs_exist_ok=True)
            shutil.copytree(ROOT / 'bench/dashboards', directory / 'dashboards', dirs_exist_ok=True)
        else:
            shutil.copytree(ROOT / 'bench', directory / 'bench', dirs_exist_ok=True)
            shutil.copytree(ROOT / 'scripts', directory / 'scripts', dirs_exist_ok=True)
            atomic_json(directory / 'bench/config.json', config)
        atomic_json(directory / 'runtime.json', runtime)
        atomic_json(directory / 'config.json', config)
        atomic_json(directory / 'compose.json', compose)
        (directory / 'compose.json').chmod(0o600)
        (directory / 'private.env').write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
        (directory / 'private.env').chmod(0o600)
        inventory['all']['children'][host['role']]['hosts'][name] = {
            'ansible_host': host['public_ip'], 'ansible_user': 'ubuntu', 'ansible_ssh_private_key_file': os.environ['BENCH_SSH_KEY'],
            'ansible_ssh_common_args': '-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=' + str(STATE / 'known_hosts'),
            'bench_role': host['role'], 'bench_mode': mode, 'bench_layout': 'distributed', 'bench_files': str(directory),
            'bench_app_archive': str(STATE / 'app.tar'), 'bench_worker_archive': str(STATE / 'worker.tar')}
    atomic_json(WORK / 'inventory.json', inventory)


def deploy(config, mode):
    # Validate secrets before Terraform can make any changes.
    for key in ('BENCH_DB_PASSWORD', 'BENCH_AUTH_SECRET'):
        if not os.environ.get(key) or len(os.environ[key]) < 32:
            raise ValueError('missing or short ' + key)
    if mode == 'deploy':
        tag = os.getenv('GITHUB_SHA', 'local')[:40]
        runtime = {'app_image': 'gatherly-bench-app:' + tag, 'worker_image': 'gatherly-bench-worker:' + tag}
        run(['docker', 'build', '-t', runtime['app_image'], str(ROOT)])
        run(['docker', 'build', '-f', str(ROOT / 'Dockerfile.worker'), '-t', runtime['worker_image'], str(ROOT)])
        run(['docker', 'save', '-o', str(STATE / 'app.tar'), runtime['app_image']])
        run(['docker', 'save', '-o', str(STATE / 'worker.tar'), runtime['worker_image']])
    output = terraform(config, apply=True, update_only='config' if mode != 'deploy' else False)
    if mode != 'deploy':
        database = (output['hosts'].get('server') or output['hosts']['database'])['public_ip']
        runtime = json.loads(ssh(database, 'sudo cat /opt/gatherly-bench/runtime.json', capture=True))
        for name in (() if output.get('layout') == 'compact' else ('app', 'worker')):
            with (STATE / (name + '.tar')).open('wb') as stream:
                subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(STATE / 'known_hosts'), '-i', os.environ['BENCH_SSH_KEY'],
                                'ubuntu@' + database, 'sudo cat /opt/gatherly-bench/' + name + '.tar'], stdout=stream, check=True)
    files(config, output, runtime, mode)
    playbook = ['ansible-playbook', '-i', str(WORK / 'inventory.json'), str(ROOT / 'infra/ansible/bench.yml')]
    if output.get('layout') == 'compact':
        run(playbook + ['--tags', 'prepare'])
        if mode == 'deploy':
            host = output['hosts']['server']['public_ip']
            for name in ('app', 'worker'):
                stream_image(host, STATE / (name + '.tar'))
            stream_image(output['hosts']['load']['public_ip'], STATE / 'app.tar')
        run(playbook + ['--tags', 'run'])
    else:
        run(playbook)
    verification = ssh((output['hosts'].get('server') or output['hosts']['proxy'])['public_ip'], 'cd /opt/gatherly-bench && sudo python3 control.py verify --config config.json', capture=True)
    atomic_json(STATE / 'verification.json', json.loads(verification))
    atomic_json(STATE / 'last-config.json', config)


def load_test(config):
    # A failed validation/preparation must never leave a previous run's report.
    for name in ('report.json', 'summary.json', 'verification.json'):
        (STATE / name).unlink(missing_ok=True)
    # A separate Actions job can have a different IPv4 address. Updating its /32
    # SSH rule is the only configuration change before using the existing stand.
    output = terraform(config, apply=True, update_only='access')
    proxy_host = output['hosts'].get('server') or output['hosts']['proxy']
    proxy, load = proxy_host['public_ip'], output['hosts']['load']['public_ip']
    proxy_private = proxy_host['private_ip']
    config_json = json.dumps(config, separators=(',', ':'))
    # Send JSON on stdin, never interpolate workflow fields into remote shell text.
    def send_text(host, path, content):
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(STATE / 'known_hosts'), '-i', os.environ['BENCH_SSH_KEY'],
                   'ubuntu@' + host, 'sudo tee ' + path + ' >/dev/null']
        run(command, input=content)
    send_text(proxy, '/opt/gatherly-bench/run-config.json', config_json)
    verification = ssh(proxy, 'cd /opt/gatherly-bench && sudo python3 control.py verify --config run-config.json', capture=True)
    atomic_json(STATE / 'verification.json', json.loads(verification))
    send_text(load, '/opt/gatherly-bench/bench/config.json', config_json)
    send_text(load, '/opt/gatherly-bench/bench/load.js', (ROOT / 'bench/load.js').read_text())
    runtime = json.loads(ssh(load, 'sudo cat /opt/gatherly-bench/runtime.json', capture=True))
    # Image names are generated by this script and checked before entering shell.
    import re
    if not re.fullmatch(r'gatherly-bench-app:[a-zA-Z0-9_.-]+', runtime['app_image']):
        raise ValueError('invalid persisted application image name')
    app_image = runtime['app_image']
    prepare = f'sudo docker run --rm --user 0 --network host -v /opt/gatherly-bench:/repo {app_image} python /repo/bench/prepare.py --base http://{proxy_private}:8080 --config /repo/bench/config.json --output /repo/bench/data'
    ssh(load, prepare)
    # Verify again after fixture preparation, immediately before measured traffic.
    ssh(proxy, 'cd /opt/gatherly-bench && sudo python3 control.py verify --config run-config.json')
    command = f'sudo mkdir -p /opt/gatherly-bench/results && sudo chmod 777 /opt/gatherly-bench/results && sudo rm -f /opt/gatherly-bench/results/report.json /opt/gatherly-bench/results/summary.json && sudo docker run --rm --network host -e GOMEMLIMIT=8GiB -e BASE_URL=http://{proxy_private}:8080 -v /opt/gatherly-bench/bench:/work:ro -v /opt/gatherly-bench/results:/results -w /work grafana/k6:1.6.1 run load.js'
    try:
        ssh(load, command)
    finally:
        for name in ('report.json', 'summary.json'):
            try:
                content = ssh(load, 'sudo cat /opt/gatherly-bench/results/' + name, capture=True)
                (STATE / name).write_text(content + '\n')
            except subprocess.CalledProcessError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['cleanup_workspace', 'migrate_state', 'plan', 'deploy', 'update_config', 'load_test', 'diagnostics'])
    parser.add_argument('--config', default='bench/defaults.json')
    args = parser.parse_args()
    config = validate(json.loads(Path(args.config).read_text()))
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    if args.action == 'migrate_state':
        backend.migrate(TF, os.getenv('BENCH_STATE_BUCKET'), os.getenv('BENCH_STAND_NAME', 'gatherly-bench'), run)
        return
    if args.action == 'cleanup_workspace':
        if WORK.is_symlink() or WORK.resolve().parent != STATE.resolve():
            raise ValueError('refusing cleanup outside the benchmark work directory')
        if WORK.exists():
            shutil.rmtree(WORK)
        print('Cleaned only .local/bench/work; Terraform state and persistent data preserved')
        return
    WORK.mkdir(parents=True, exist_ok=True)
    WORK.chmod(0o700)
    if args.action == 'plan':
        terraform(config, plan_only=True)
    elif args.action in ('deploy', 'update_config'):
        deploy(config, 'deploy' if args.action == 'deploy' else 'update_config')
    elif args.action == 'load_test':
        load_test(config)
    else:
        output = terraform(config)
        for name, host in output['hosts'].items():
            try:
                logs = ssh(host['public_ip'], 'cd /opt/gatherly-bench && sudo docker compose -p gatherly-bench -f compose.json ps -a && sudo docker compose -p gatherly-bench -f compose.json logs --no-color --tail 100', capture=True)
                (STATE / (name + '-logs.txt')).write_text(logs)
            except subprocess.CalledProcessError:
                pass


if __name__ == '__main__':
    main()
