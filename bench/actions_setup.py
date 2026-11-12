"""Validate Actions settings and write private credentials without logging them."""
import json
import os
from pathlib import Path


def validate_environment(environment):
    names = ('YC_CLOUD_ID', 'YC_FOLDER_ID', 'BENCH_BUCKET', 'BENCH_STATE_BUCKET',
             'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'YC_SA_KEY_JSON',
             'BENCH_SSH_PRIVATE_KEY', 'BENCH_DB_PASSWORD', 'BENCH_AUTH_SECRET')
    missing = [name for name in names if not environment.get(name)]
    if missing:
        raise ValueError('Missing benchmark settings: ' + ', '.join(missing))
    password = environment['BENCH_DB_PASSWORD']
    if len(password) != 64 or any(c not in '0123456789abcdef' for c in password):
        raise ValueError('BENCH_DB_PASSWORD must contain 64 hex characters')
    if len(environment['BENCH_AUTH_SECRET'].encode()) < 32:
        raise ValueError('BENCH_AUTH_SECRET must contain at least 32 bytes')
    key = json.loads(environment['YC_SA_KEY_JSON'])
    if not isinstance(key, dict) or any(not key.get(name) for name in ('id', 'service_account_id', 'private_key')):
        raise ValueError('YC_SA_KEY_JSON must be an authorized service-account key')


def materialize(root, environment):
    validate_environment(environment)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    for filename, variable in [('yc-key.json', 'YC_SA_KEY_JSON'), ('ssh-key', 'BENCH_SSH_PRIVATE_KEY')]:
        target = root / filename
        # Create with restricted permissions before writing any secret bytes.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(environment[variable].rstrip() + '\n')
    return {'YC_SERVICE_ACCOUNT_KEY_FILE': str(root / 'yc-key.json'),
            'BENCH_SSH_KEY': str(root / 'ssh-key')}


if __name__ == '__main__':
    paths = materialize(Path('.local/bench'), os.environ)
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        for name, path in paths.items():
            stream.write(name + '=' + path + '\n')
