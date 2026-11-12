#!/usr/bin/env python3
"""Validate one resolved run configuration, render CDS, and verify active Envoy state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def seconds(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+s', value):
        raise ValueError('durations must be whole seconds, e.g. 180s')
    return int(value[:-1])


def resolve(config_file, overrides=None):
    source = Path(config_file).resolve()
    if not source.is_relative_to(ROOT.resolve()) or source.suffix != '.json':
        raise ValueError('select a versioned JSON configuration under bench/')
    config = json.loads(source.read_text())
    validate(config)
    for key, current in config.items():
        value = (overrides or {}).get(key)
        if value in (None, ''):
            continue
        if type(current) is int:
            if type(value) not in (int, str):
                raise ValueError(key + ' must be an integer')
            value = int(value)
        config[key] = value
    return validate(config)


def validate(config):
    expected = set(json.loads((ROOT / 'defaults.json').read_text()))
    if set(config) != expected:
        raise ValueError('unknown or missing config keys: ' + str(set(config) ^ expected))
    if config['version'] != 1 or type(config['version']) is not int:
        raise ValueError('unsupported config version')
    if type(config['replicas']) is not int or not 1 <= config['replicas'] <= 10:
        raise ValueError('replicas must be 1..10')
    if config['policy'] not in ('round_robin', 'least_request'):
        raise ValueError('policy must be round_robin or least_request')
    duration, start = seconds(config['duration']), seconds(config['photo_start_after'])
    if not 1 <= duration <= 3600 or not 0 <= start <= 3600:
        raise ValueError('duration must be 1..3600s, photo_start_after 0..3600s')
    for key in ('feed_rps', 'recommendations_rps', 'photo_upload_rps'):
        if type(config[key]) is not int or not 0 <= config[key] <= 10000:
            raise ValueError(key + ' must be 0..10000')
    if not any(config[key] for key in ('feed_rps', 'recommendations_rps', 'photo_upload_rps')):
        raise ValueError('at least one scenario must have positive RPS')
    if config['photo_upload_rps'] and start >= duration:
        raise ValueError('photo_start_after must be earlier than duration')
    if type(config['seed']) is not int or not 0 <= config['seed'] <= 2**31 - 1:
        raise ValueError('seed must be 0..2147483647')
    # One Uvicorn process, max two DB connections per replica; worker 2+1,
    # exporter 1, ten reserved maintenance connections. PostgreSQL allows 230.
    if config['replicas'] * 2 + 3 + 1 + 10 > 230:
        raise ValueError('PostgreSQL connection budget exceeded')
    return config


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render(config, addresses, out):
    validate(config)
    if len(addresses) != config['replicas'] or len(set(addresses)) != len(addresses):
        raise ValueError('replica addresses must be unique and match replicas')
    if any(not re.fullmatch(r'[a-zA-Z0-9_.-]+', address) for address in addresses):
        raise ValueError('invalid replica address')
    cluster = {
        '@type': 'type.googleapis.com/envoy.config.cluster.v3.Cluster',
        'name': 'gatherly', 'connect_timeout': '3s', 'type': 'STRICT_DNS',
        'lb_policy': config['policy'].upper(),
        'load_assignment': {'cluster_name': 'gatherly', 'endpoints': [{'lb_endpoints': [
            {'endpoint': {'address': {'socket_address': {'address': address, 'port_value': 8080}}}} for address in addresses]}]},
        'health_checks': [{'timeout': '2s', 'interval': '3s', 'unhealthy_threshold': 2, 'healthy_threshold': 1,
                           'http_health_check': {'path': '/ready'}}],
        'circuit_breakers': {'thresholds': [{'max_connections': config['replicas'] * 100, 'max_pending_requests': 1000, 'max_requests': 10000}]},
    }
    version = hashlib.sha256(json.dumps(cluster, sort_keys=True).encode()).hexdigest()
    # Mount the directory in Envoy; replacement of a bind-mounted file is invisible.
    atomic_json(Path(out) / 'dynamic' / 'cds.json', {'version_info': version, 'resources': [cluster]})
    bootstrap = {
        'node': {'id': 'gatherly-proxy', 'cluster': 'gatherly-bench'},
        'admin': {'address': {'socket_address': {'address': '0.0.0.0', 'port_value': 9901}}},
        'dynamic_resources': {'cds_config': {'resource_api_version': 'V3', 'path_config_source': {
            'path': '/etc/envoy/dynamic/cds.json', 'watched_directory': {'path': '/etc/envoy/dynamic'}}}},
        'static_resources': {'listeners': [{'name': 'http', 'address': {'socket_address': {'address': '0.0.0.0', 'port_value': 8080}},
            'filter_chains': [{'filters': [{'name': 'envoy.filters.network.http_connection_manager', 'typed_config': {
                '@type': 'type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager',
                'stat_prefix': 'ingress', 'route_config': {'name': 'routes', 'validate_clusters': False, 'virtual_hosts': [{'name': 'gatherly', 'domains': ['*'], 'routes': [
                    {'match': {'safe_regex': {'regex': '/(ready|metrics)(/.*)?'}}, 'direct_response': {'status': 404}},
                    {'match': {'prefix': '/'}, 'route': {'cluster': 'gatherly', 'timeout': '60s'}}]}]},
                'http_filters': [{'name': 'envoy.filters.http.router', 'typed_config': {'@type': 'type.googleapis.com/envoy.extensions.filters.http.router.v3.Router'}}],
            }}]}]}]},
    }
    atomic_json(Path(out) / 'bootstrap.json', bootstrap)
    atomic_json(Path(out) / 'config.json', config)
    return version


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def verify(config, admin_url, timeout=120):
    validate(config)
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        try:
            dump = get_json(admin_url.rstrip('/') + '/config_dump')
            clusters = [entry['cluster'] for section in dump['configs'] for entry in section.get('dynamic_active_clusters', []) if entry['cluster']['name'] == 'gatherly']
            if len(clusters) != 1 or clusters[0].get('lb_policy', 'ROUND_ROBIN') != config['policy'].upper():
                raise RuntimeError('requested Envoy policy is not active')
            status = get_json(admin_url.rstrip('/') + '/clusters?format=json')
            hosts = [host for cluster in status['cluster_statuses'] if cluster['name'] == 'gatherly' for host in cluster.get('host_statuses', [])]
            healthy = [host for host in hosts if host.get('health_status', {}).get('eds_health_status', 'HEALTHY') == 'HEALTHY' and not any(v for k, v in host.get('health_status', {}).items() if k.startswith('failed_') or k == 'pending_active_hc')]
            if len(hosts) != config['replicas'] or len(healthy) != config['replicas']:
                raise RuntimeError(f'expected {config["replicas"]} healthy replicas; found {len(healthy)}/{len(hosts)}')
            return {'policy': config['policy'], 'healthy_replicas': len(healthy), 'cds_verified': True}
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise RuntimeError('Envoy verification failed: ' + str(last_error))
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['resolve', 'render', 'verify'])
    parser.add_argument('--config', default=str(ROOT / 'defaults.json'))
    parser.add_argument('--overrides-env')
    parser.add_argument('--output', default='config.json')
    parser.add_argument('--addresses')
    parser.add_argument('--admin', default='http://127.0.0.1:9901')
    args = parser.parse_args()
    if args.action == 'resolve':
        overrides = json.loads(os.environ[args.overrides_env]) if args.overrides_env else {}
        config = resolve(args.config, overrides)
        atomic_json(args.output, config)
        print(json.dumps(config))
    elif args.action == 'render':
        config = json.loads(Path(args.config).read_text())
        print(render(config, json.loads(Path(args.addresses).read_text()), args.output))
    else:
        config = json.loads(Path(args.config).read_text())
        print(json.dumps(verify(config, args.admin)))


if __name__ == '__main__':
    main()
