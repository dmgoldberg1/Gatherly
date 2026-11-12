"""Versioned native Envoy profiles; resolved snapshots need only the stdlib."""
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

IMAGE = 'envoyproxy/envoy:v1.37.1'
POLICIES = ('round_robin', 'least_request', 'random', 'ring_hash', 'maglev')
MANAGED_CLUSTER = {'@type', 'name', 'type', 'cluster_type', 'load_assignment', 'eds_cluster_config'}
MANAGED_ROUTE = {'cluster', 'cluster_header', 'weighted_clusters', 'cluster_specifier_plugin',
                 'inline_cluster_specifier_plugin'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def profile(config, root):
    """Resolve once, then carry the complete profile in the effective config."""
    filename = config.get('envoy_config_file', '')
    if not isinstance(filename, str):
        raise ValueError('envoy_config_file must be a path under bench/envoy/')
    if filename:
        relative = Path(filename)
        if relative.is_absolute() or relative.parts[:2] != ('bench', 'envoy') or '..' in relative.parts or relative.suffix not in ('.yaml', '.yml', '.json'):
            raise ValueError('select an Envoy profile under bench/envoy/')
        source = (Path(root).parent / filename).resolve()
        if 'envoy_profile' not in config and not source.is_relative_to((Path(root) / 'envoy').resolve()):
            raise ValueError('select an Envoy profile under bench/envoy/')
    if 'envoy_profile' in config:
        result = copy.deepcopy(config['envoy_profile'])
    elif filename:
        import yaml
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader, node):
            pairs = loader.construct_pairs(node, deep=True)
            if any(not isinstance(key, str) for key, _ in pairs) or len({key for key, _ in pairs}) != len(pairs):
                raise ValueError('profile mappings require unique string keys')
            return dict(pairs)
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        if source.stat().st_size > 65536:
            raise ValueError('Envoy profile must be at most 64 KiB')
        result = yaml.load(source.read_text(), Loader=UniqueLoader)
    else:
        policy = config['policy']
        if policy not in POLICIES:
            raise ValueError('unknown policy; select a native profile using envoy_config_file')
        result = {'name': policy, 'cluster': {'lb_policy': policy.upper()}}
        if policy in ('ring_hash', 'maglev'):
            result['route'] = {'hash_policy': [{'header': {'header_name': 'X-Bench-Key'}}]}
    if not isinstance(result, dict) or set(result) - {'name', 'cluster', 'route', 'endpoint_weights', 'image'}:
        raise ValueError('profile accepts name, cluster, route, endpoint_weights and image')
    if not isinstance(result.get('name'), str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', result['name']):
        raise ValueError('profile name must be a short experiment name')
    cluster, route = result.get('cluster'), result.get('route', {})
    if not isinstance(cluster, dict) or not cluster or not isinstance(route, dict):
        raise ValueError('profile requires a native cluster mapping; route must be a mapping')
    if set(cluster) & MANAGED_CLUSTER or set(route) & MANAGED_ROUTE:
        raise ValueError('replica addresses, cluster identity and routing destination are managed by the stand')
    metadata = cluster.get('metadata', {})
    if not isinstance(metadata, dict) or not isinstance(metadata.get('filter_metadata', {}), dict):
        raise ValueError('cluster metadata and filter_metadata must be mappings')
    if 'gatherly.bench' in metadata.get('filter_metadata', {}):
        raise ValueError('metadata namespace gatherly.bench is reserved for profile verification')
    if ('lb_policy' in cluster) == ('load_balancing_policy' in cluster):
        raise ValueError('profile requires exactly one of lb_policy or load_balancing_policy')
    image = result.get('image', IMAGE)
    if not isinstance(image, str) or (image != IMAGE and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[a-f0-9]{64}', image)):
        raise ValueError('custom Envoy image must be pinned as repository@sha256:<digest>')
    weights = result.get('endpoint_weights', [])
    if not isinstance(weights, list) or any(type(w) is not int or not 1 <= w <= 128 for w in weights):
        raise ValueError('endpoint_weights must contain integers in 1..128')
    if weights and len(weights) != config['replicas']:
        raise ValueError('endpoint_weights must have one weight per replica, in replica order')
    result.update(route=route, image=image, endpoint_weights=weights)
    # Reject YAML objects, aliases forming cycles, non-finite numbers, etc.
    try:
        result = json.loads(json.dumps(result, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('profile must contain finite JSON-compatible values') from exc
    return result


def contains(actual, expected):
    """Compare explicit protobuf fields, allowing Envoy to fill in defaults."""
    if isinstance(expected, dict):
        def default(value):
            return value in (False, 0, '', '0s', [], {}) or (
                isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z_0-9]*', value))
        return isinstance(actual, dict) and all(contains(actual[key], value) if key in actual else default(value)
                                               for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            contains(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        # Protobuf JSON renders uint64 as strings and normalizes numeric values.
        try:
            return float(actual) == expected
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str) and re.fullmatch(r'\d+(?:\.\d+)?s', expected):
        return isinstance(actual, str) and actual.endswith('s') and float(actual[:-1]) == float(expected[:-1])
    return actual == expected


def validate_native(config, documents, output=None):
    """Validate actual native fields/extensions using the selected Envoy binary."""
    bootstrap, cluster, route_config = documents
    standalone = copy.deepcopy(bootstrap)
    standalone.pop('dynamic_resources')
    standalone['static_resources']['clusters'] = [dict(cluster)]
    standalone['static_resources']['clusters'][0].pop('@type')
    hcm = standalone['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
    hcm.pop('rds')
    hcm['route_config'] = route_config
    with tempfile.TemporaryDirectory(prefix='gatherly-envoy-validate-') as directory:
        path = Path(directory)
        path.chmod(0o755)
        (path / 'validation.json').write_text(json.dumps(standalone))
        (path / 'validation.json').chmod(0o644)
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', directory + ':/validation:ro',
                        '--entrypoint', 'envoy', config['envoy_profile']['image'], '--mode', 'validate',
                        '-c', '/validation/validation.json'], check=True)
    report = {'profile': config['envoy_profile']['name'], 'profile_sha256': digest(config['envoy_profile']),
              'image': config['envoy_profile']['image'], 'native_validation': True}
    if output:
        Path(output).write_text(json.dumps(report, indent=2) + '\n')
    return report
