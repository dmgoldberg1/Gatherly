"""Exercise native profiles and live CDS/RDS updates against three HTTP backends."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request
import uuid

from bench.control import ROOT, atomic_json, documents, get_json, render, resolve, verify, envoy


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.local/bench/envoy-profiles-check.json')
    args = parser.parse_args()
    # Keep this regression suite on its fixed three-backend reference profiles.
    # User profiles can require a custom binary or a different replica count;
    # the selected profile is validated by that binary in load-bench preflight.
    profiles = [ROOT / 'envoy' / name for name in (
        'least-request-4-choices.yaml', 'least-request-extension.yaml', 'least-request.yaml',
        'maglev.yaml', 'random.yaml', 'ring-hash.yaml', 'round-robin.yaml', 'weighted-round-robin.yaml')]
    configs = [resolve(ROOT / 'defaults.json', {'envoy_config_file': 'bench/envoy/' + path.name}) for path in profiles]
    tuned = resolve(ROOT / 'defaults.json', {'policy': 'least_request'})
    tuned['envoy_profile']['name'] = 'custom-route-and-health-settings'
    tuned['envoy_profile']['route'] = {'timeout': '17s'}
    tuned['envoy_profile']['cluster']['common_lb_config'] = {'healthy_panic_threshold': {'value': 0}}
    configs.append(tuned)
    for config in configs:
        envoy.validate_native(config, documents(config, ['127.0.0.1', '127.0.0.2', '127.0.0.3']))
    prefix = 'gatherly-envoy-check-' + uuid.uuid4().hex[:10]
    containers = []
    report = {'replicas': 3, 'profiles': []}
    docker('network', 'create', prefix)
    try:
        server = '''import os
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body = os.environ['REPLICA'].encode()
  self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
 def log_message(self, *args): pass
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
'''
        for i in range(3):
            name = prefix + '-' + str(i)
            containers.append(name)
            docker('run', '-d', '--name', name, '--network', prefix, '-e', 'REPLICA=' + str(i),
                   'python:3.12-slim', 'python', '-u', '-c', server)
        addresses = containers[:]
        with tempfile.TemporaryDirectory(prefix='gatherly-envoy-live-') as directory:
            path = Path(directory)
            path.chmod(0o755)
            first = configs[0]
            render(first, addresses, path)
            proxy = prefix + '-proxy'
            containers.append(proxy)
            docker('run', '-d', '--name', proxy, '--network', prefix,
                   '-p', '127.0.0.1::9901', '-p', '127.0.0.1::8080',
                   '-v', str(path / 'bootstrap.json') + ':/etc/envoy/bootstrap.json:ro',
                   '-v', str(path / 'dynamic') + ':/etc/envoy/dynamic:ro',
                   '--entrypoint', 'envoy', envoy.IMAGE, '-c', '/etc/envoy/bootstrap.json', '--concurrency', '2', '--log-level', 'error')
            ports = json.loads(docker('inspect', proxy))[0]['NetworkSettings']['Ports']
            admin = 'http://127.0.0.1:' + ports['9901/tcp'][0]['HostPort']
            base = 'http://127.0.0.1:' + ports['8080/tcp'][0]['HostPort']
            started = json.loads(docker('inspect', proxy))[0]['State']['StartedAt']
            for config in configs:
                render(config, addresses, path)
                try:
                    result = verify(config, admin, timeout=15)
                except RuntimeError:
                    failure = Path(args.output).with_suffix('.failure.json')
                    failure.parent.mkdir(parents=True, exist_ok=True)
                    failure.write_text(json.dumps({'config': config, 'actual': get_json(admin + '/config_dump'),
                                                   'expected': documents(config, addresses)}, indent=2) + '\n')
                    raise
                counts = Counter()
                for i in range(48):
                    request = urllib.request.Request(base + '/test', headers={'X-Bench-Key': str(i)})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        counts[response.read().decode()] += 1
                assert sum(counts.values()) == 48
                if config['policy'] == 'weighted-round-robin':
                    # Each Envoy worker maintains its own scheduler/counters.
                    assert all(abs(counts[name] - expected) <= 2 for name, expected in
                               [('0', 12), ('1', 24), ('2', 12)]), counts
                if config['envoy_profile']['route'].get('hash_policy'):
                    sticky = set()
                    for _ in range(8):
                        request = urllib.request.Request(base + '/test', headers={'X-Bench-Key': 'stable-key'})
                        with urllib.request.urlopen(request, timeout=5) as response:
                            sticky.add(response.read().decode())
                    assert len(sticky) == 1, sticky
                    result['stable_hash_key_verified'] = True
                result['request_distribution'] = dict(counts)
                report['profiles'].append(result)
            # Unknown native fields must fail preflight, before any cloud operation.
            invalid = json.loads(json.dumps(configs[-1]))
            invalid['envoy_profile']['cluster']['not_an_envoy_field'] = True
            try:
                envoy.validate_native(invalid, documents(invalid, addresses))
            except subprocess.CalledProcessError:
                report['invalid_native_field_rejected'] = True
            else:
                raise AssertionError('invalid native profile was accepted')
            # A rejected live update must not be mistaken for the requested profile.
            invalid['envoy_profile']['cluster']['lb_policy'] = 'INVALID_TEST_POLICY'
            _, bad_cluster, _ = documents(invalid, addresses)
            atomic_json(path / 'dynamic/cds.json', {'version_info': envoy.digest(bad_cluster), 'resources': [bad_cluster]})
            try:
                verify(invalid, admin, timeout=2)
            except RuntimeError:
                report['rejected_live_update_detected'] = True
            else:
                raise AssertionError('rejected live update passed verification')
            assert json.loads(docker('inspect', proxy))[0]['State']['StartedAt'] == started
            report['proxy_not_restarted'] = True
            # Check the migration predicate on the actual protobuf-normalized bootstrap.
            dump = get_json(admin + '/config_dump')
            bootstrap = next(s['bootstrap'] for s in dump['configs'] if 'bootstrap' in s)
            assert envoy.contains(bootstrap, documents(configs[0], addresses)[0])
            report['unchanged_bootstrap_recognized'] = True
    finally:
        for name in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
        docker('network', 'rm', prefix)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix('.failure.json').unlink(missing_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
