#!/usr/bin/env python3
"""Deploy or update only the isolated local benchmark Compose project."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

if __package__:
    from .control import atomic_json, documents, reconcile, render, validate, verify, envoy
else:
    from control import atomic_json, documents, reconcile, render, validate, verify, envoy

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local' / 'bench'


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['deploy', 'update_config', 'test', 'diagnostics'])
    parser.add_argument('--config', default='bench/defaults.json')
    args = parser.parse_args()
    config = validate(json.loads(Path(args.config).read_text()))
    os.environ['GATHERLY_ENVOY_IMAGE'] = config['envoy_profile']['image']
    if args.action in ('deploy', 'update_config'):
        envoy.validate_native(config, documents(config, [f'127.0.0.{i}' for i in range(1, config['replicas'] + 1)]))
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    env = STATE / 'secrets.env'
    if not env.exists():
        env.write_text('POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\nGATHERLY_AUTH_SECRET=' + secrets.token_hex(32) + '\n')
        env.chmod(0o600)
    runtime_file = STATE / 'runtime.json'
    if args.action == 'deploy':
        runtime = {}
        for key, default, prefix in [('GATHERLY_IMAGE', 'gatherly:python-check', 'gatherly-bench-app'), ('GATHERLY_WORKER_IMAGE', 'gatherly-worker:check', 'gatherly-bench-worker')]:
            image_id = json.loads(run(['docker', 'image', 'inspect', os.getenv(key, default)], capture_output=True).stdout)[0]['Id']
            immutable_tag = prefix + ':sha-' + image_id.removeprefix('sha256:')
            run(['docker', 'tag', image_id, immutable_tag])
            runtime[key] = immutable_tag
        atomic_json(runtime_file, runtime)
        os.environ.update(runtime)
    elif args.action in ('update_config', 'test'):
        if not runtime_file.exists():
            raise RuntimeError('Run deploy once to record the current application images')
        os.environ.update(json.loads(runtime_file.read_text()))
    compose = ['docker', 'compose', '-p', 'gatherly-bench', '--env-file', str(env), '-f', str(ROOT / 'docker-compose.bench.yml')]
    if args.action in ('deploy', 'update_config'):
        if args.action == 'update_config':
            # Update never builds or changes the application image.
            ids = run(compose + ['ps', '-q', 'gatherly'], capture_output=True).stdout.split()
            if not ids:
                raise RuntimeError('no existing benchmark deployment to update')
        run(compose + ['up', '-d', '--no-build', '--scale', f'gatherly={config["replicas"]}', 'postgres', 'migrate', 'worker', 'gatherly'])
        ids = run(compose + ['ps', '-q', 'gatherly'], capture_output=True).stdout.split()
        inspected = json.loads(run(['docker', 'inspect', *ids], capture_output=True).stdout)
        addresses = [next(iter(item['NetworkSettings']['Networks'].values()))['IPAddress'] for item in inspected]
        render(config, sorted(addresses), STATE / 'current')
        reconcile(compose, json.loads((STATE / 'current/bootstrap.json').read_text()), 'http://127.0.0.1:19901')
        run(compose + ['up', '-d', '--no-build', 'envoy'])
        atomic_json(STATE / 'verification.json', verify(config, 'http://127.0.0.1:19901', timeout=180))
        print('Verified active policy and healthy replicas')
    elif args.action == 'diagnostics':
        run(compose + ['ps', '-a'])
        with (STATE / 'logs.txt').open('w') as logs:
            subprocess.run(compose + ['logs', '--no-color', '--tail', '200'], stdout=logs, stderr=subprocess.STDOUT)
    else:
        verify(config, 'http://127.0.0.1:19901')
        network = 'gatherly-bench_default'
        data = STATE / 'run'
        data.mkdir(exist_ok=True)
        data.chmod(0o777)
        atomic_json(data / 'config.json', config)
        for name in ('load.js',):
            (data / name).write_bytes((ROOT / 'bench' / name).read_bytes())
        run(['docker', 'run', '--rm', '--network', network, '-v', str(ROOT) + ':/repo:ro', '-v', str(data) + ':/run',
             '--entrypoint', 'python', os.getenv('GATHERLY_IMAGE', 'gatherly:python-check'),
             '/repo/bench/prepare.py', '--base', 'http://envoy:8080', '--config', '/run/config.json', '--output', '/run/data'])
        results = data / 'results'
        results.mkdir(exist_ok=True)
        results.chmod(0o777)
        verify(config, 'http://127.0.0.1:19901')
        run(['docker', 'run', '--rm', '--network', network, '-e', 'BASE_URL=http://envoy:8080', '-v', str(data) + ':/work:ro',
             '-v', str(results) + ':/results', '-w', '/work', 'grafana/k6:1.6.1', 'run', 'load.js'])


if __name__ == '__main__':
    main()
