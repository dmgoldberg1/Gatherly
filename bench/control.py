#!/usr/bin/env python3
"""Validate one resolved run configuration, render CDS, and verify active Envoy state."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.request

if __package__:
    from . import envoy
else:
    import envoy

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
    config.setdefault('envoy_config_file', '')
    if any((overrides or {}).get(key) not in (None, '') for key in ('policy', 'envoy_config_file')):
        config.pop('envoy_profile', None)
    for key, current in list(config.items()):
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
    config = dict(config)
    optional = {'envoy_config_file', 'envoy_profile'}
    expected = set(json.loads((ROOT / 'defaults.json').read_text())) - optional
    if expected - set(config) or set(config) - expected - optional:
        raise ValueError('unknown or missing config keys')
    if config['version'] != 1 or type(config['version']) is not int:
        raise ValueError('unsupported config version')
    if type(config['replicas']) is not int or not 1 <= config['replicas'] <= 10:
        raise ValueError('replicas must be 1..10')
    if not isinstance(config['policy'], str):
        raise ValueError('policy must be a string')
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
    config['envoy_profile'] = envoy.profile(config, ROOT)
    config['policy'] = config['envoy_profile']['name']
    config.setdefault('envoy_config_file', '')
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


def documents(config, addresses):
    config = validate(config)
    if len(addresses) != config['replicas'] or len(set(addresses)) != len(addresses):
        raise ValueError('replica addresses must be unique and match replicas')
    if any(not re.fullmatch(r'[a-zA-Z0-9_.-]+', address) for address in addresses):
        raise ValueError('invalid replica address')
    cluster = {
        '@type': 'type.googleapis.com/envoy.config.cluster.v3.Cluster',
        'name': 'gatherly', 'connect_timeout': '3s', 'type': 'STRICT_DNS',
        'lb_policy': 'ROUND_ROBIN',
        'load_assignment': {'cluster_name': 'gatherly', 'endpoints': [{'lb_endpoints': [
            {'endpoint': {'address': {'socket_address': {'address': address, 'port_value': 8080}}}} for address in addresses]}]},
        'health_checks': [{'timeout': '2s', 'interval': '3s', 'unhealthy_threshold': 2, 'healthy_threshold': 1,
                           'http_health_check': {'path': '/ready'}}],
        'circuit_breakers': {'thresholds': [{'max_connections': config['replicas'] * 100, 'max_pending_requests': 1000, 'max_requests': 10000}]},
    }
    profile = config['envoy_profile']
    if 'load_balancing_policy' in profile['cluster']:
        cluster.pop('lb_policy')
    cluster.update(copy.deepcopy(profile['cluster']))
    metadata = cluster.setdefault('metadata', {}).setdefault('filter_metadata', {})
    metadata['gatherly.bench'] = {'profile_sha256': envoy.digest(profile)}
    for endpoint, weight in zip(cluster['load_assignment']['endpoints'][0]['lb_endpoints'], profile['endpoint_weights']):
        endpoint['load_balancing_weight'] = weight
    route_config = {'name': 'gatherly-routes', 'validate_clusters': False, 'virtual_hosts': [
        {'name': 'gatherly', 'domains': ['*'], 'routes': [
            {'match': {'safe_regex': {'regex': '/(ready|metrics)(/.*)?'}}, 'direct_response': {'status': 404}},
            {'match': {'prefix': '/'}, 'route': {'cluster': 'gatherly', 'timeout': '60s', **profile['route']},
             'metadata': {'filter_metadata': {'gatherly.bench': {'profile_sha256': envoy.digest(profile)}}}}]}]}
    bootstrap = {
        'node': {'id': 'gatherly-proxy', 'cluster': 'gatherly-bench'},
        'admin': {'address': {'socket_address': {'address': '0.0.0.0', 'port_value': 9901}}},
        'dynamic_resources': {'cds_config': {'resource_api_version': 'V3', 'path_config_source': {
            'path': '/etc/envoy/dynamic/cds.json', 'watched_directory': {'path': '/etc/envoy/dynamic'}}}},
        'static_resources': {'listeners': [{'name': 'http', 'address': {'socket_address': {'address': '0.0.0.0', 'port_value': 8080}},
            'filter_chains': [{'filters': [{'name': 'envoy.filters.network.http_connection_manager', 'typed_config': {
                '@type': 'type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager',
                'stat_prefix': 'ingress', 'rds': {'route_config_name': 'gatherly-routes', 'config_source': {
                    'resource_api_version': 'V3', 'path_config_source': {
                        'path': '/etc/envoy/dynamic/rds.json', 'watched_directory': {'path': '/etc/envoy/dynamic'}}}},
                'http_filters': [{'name': 'envoy.filters.http.router', 'typed_config': {'@type': 'type.googleapis.com/envoy.extensions.filters.http.router.v3.Router'}}],
            }}]}]}]},
    }
    return bootstrap, cluster, route_config


def render(config, addresses, out):
    config = validate(config)
    bootstrap, cluster, route_config = documents(config, addresses)
    version = envoy.digest(cluster)
    atomic_json(Path(out) / 'dynamic/cds.json', {'version_info': version, 'resources': [cluster]})
    route_config['@type'] = 'type.googleapis.com/envoy.config.route.v3.RouteConfiguration'
    atomic_json(Path(out) / 'dynamic/rds.json', {'version_info': envoy.digest(config['envoy_profile']), 'resources': [route_config]})
    atomic_json(Path(out) / 'bootstrap.json', bootstrap)
    atomic_json(Path(out) / 'config.json', config)
    return version


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def reconcile(compose, bootstrap, admin_url):
    """Recreate once when migrating a running proxy's static bootstrap."""
    import subprocess
    try:
        dump = get_json(admin_url.rstrip('/') + '/config_dump')
    except (OSError, ValueError):
        return
    actual = next((section['bootstrap'] for section in dump['configs'] if 'bootstrap' in section), None)
    if actual is None or not envoy.contains(actual, bootstrap):
        subprocess.run(compose + ['up', '-d', '--no-build', '--force-recreate', 'envoy'], check=True)


def verify(config, admin_url, timeout=120):
    config = validate(config)
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        try:
            dump = get_json(admin_url.rstrip('/') + '/config_dump')
            clusters = [entry for section in dump['configs'] for entry in section.get('dynamic_active_clusters', []) if entry['cluster']['name'] == 'gatherly']
            if len(clusters) != 1:
                raise RuntimeError('expected one active Gatherly cluster')
            actual = clusters[0]['cluster']
            addresses = [e['endpoint']['address']['socket_address']['address']
                         for group in actual['load_assignment']['endpoints'] for e in group['lb_endpoints']]
            _, expected, route_config = documents(config, addresses)
            actual.setdefault('lb_policy', 'ROUND_ROBIN')
            if clusters[0].get('version_info') != envoy.digest(expected) or not envoy.contains(actual, expected):
                raise RuntimeError('requested Envoy cluster settings are not active')
            routes = [entry for section in dump['configs'] for entry in section.get('dynamic_route_configs', [])
                      if entry['route_config']['name'] == 'gatherly-routes']
            if len(routes) != 1 or routes[0].get('version_info') != envoy.digest(config['envoy_profile']) or not envoy.contains(routes[0]['route_config'], route_config):
                raise RuntimeError('requested Envoy route settings are not active')
            status = get_json(admin_url.rstrip('/') + '/clusters?format=json')
            hosts = [host for cluster in status['cluster_statuses'] if cluster['name'] == 'gatherly' for host in cluster.get('host_statuses', [])]
            healthy = [host for host in hosts if host.get('health_status', {}).get('eds_health_status', 'HEALTHY') == 'HEALTHY' and not any(v for k, v in host.get('health_status', {}).items() if k.startswith('failed_') or k == 'pending_active_hc')]
            if len(hosts) != config['replicas'] or len(healthy) != config['replicas']:
                raise RuntimeError(f'expected {config["replicas"]} healthy replicas; found {len(healthy)}/{len(hosts)}')
            return {'policy': config['policy'], 'healthy_replicas': len(healthy), 'cds_verified': True,
                    'rds_verified': True, 'profile_sha256': envoy.digest(config['envoy_profile'])}
        except (KeyError, ValueError, RuntimeError, OSError) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise RuntimeError('Envoy verification failed: ' + str(last_error))
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['resolve', 'render', 'verify', 'validate-envoy', 'reconcile'])
    parser.add_argument('--config', default=str(ROOT / 'defaults.json'))
    parser.add_argument('--overrides-env')
    parser.add_argument('--output', default='config.json')
    parser.add_argument('--addresses')
    parser.add_argument('--admin', default='http://127.0.0.1:9901')
    parser.add_argument('--check-envoy', action='store_true')
    parser.add_argument('--validation-report')
    args = parser.parse_args()
    if args.action == 'resolve':
        overrides = json.loads(os.environ[args.overrides_env]) if args.overrides_env else {}
        config = resolve(args.config, overrides)
        if args.check_envoy:
            envoy.validate_native(config, documents(config, [f'127.0.0.{i}' for i in range(1, config['replicas'] + 1)]), args.validation_report)
        atomic_json(args.output, config)
        print(json.dumps(config))
    elif args.action == 'render':
        config = json.loads(Path(args.config).read_text())
        print(render(config, json.loads(Path(args.addresses).read_text()), args.output))
    elif args.action == 'validate-envoy':
        config = validate(json.loads(Path(args.config).read_text()))
        envoy.validate_native(config, documents(config, [f'127.0.0.{i}' for i in range(1, config['replicas'] + 1)]), args.validation_report)
    elif args.action == 'reconcile':
        compose = ['docker', 'compose', '-p', 'gatherly-bench', '-f', 'compose.json']
        reconcile(compose, json.loads(Path('bootstrap.json').read_text()), args.admin)
    else:
        config = json.loads(Path(args.config).read_text())
        print(json.dumps(verify(config, args.admin)))


if __name__ == '__main__':
    main()
