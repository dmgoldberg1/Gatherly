"""Configure repository Actions settings through an authenticated GitHub CLI.

The input JSON is private: {repository, variables: {...}, secrets: {...}}.
Secret values are passed on stdin and never included in command lines or reports.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from bench.actions_setup import validate_environment
from bench.backend import state_key

SECRETS = {
    'YC_SA_KEY_JSON', 'BENCH_STATE_ACCESS_KEY', 'BENCH_STATE_SECRET_KEY',
    'BENCH_SSH_PRIVATE_KEY', 'BENCH_DB_PASSWORD', 'BENCH_AUTH_SECRET',
}
VARIABLES = {
    'YC_CLOUD_ID', 'YC_FOLDER_ID', 'BENCH_BUCKET', 'BENCH_STATE_BUCKET',
    'BENCH_STAND_NAME', 'YC_ZONE', 'BENCH_LOAD_ZONE', 'BENCH_SUBNET_ID',
    'BENCH_PREEMPTIBLE',
}


def validate_settings(settings):
    if set(settings) != {'repository', 'variables', 'secrets'}:
        raise ValueError('settings must contain repository, variables and secrets')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', settings['repository']):
        raise ValueError('repository must be owner/name')
    variables, secrets = settings['variables'], settings['secrets']
    if set(secrets) != SECRETS or set(variables) - VARIABLES:
        raise ValueError('unknown or missing Actions setting names')
    if any(not isinstance(value, str) or not value for value in (*variables.values(), *secrets.values())):
        raise ValueError('Actions setting values must be nonempty strings')
    environment = variables | secrets | {
        'AWS_ACCESS_KEY_ID': secrets['BENCH_STATE_ACCESS_KEY'],
        'AWS_SECRET_ACCESS_KEY': secrets['BENCH_STATE_SECRET_KEY'],
    }
    validate_environment(environment)
    state_key(variables.get('BENCH_STAND_NAME', 'gatherly-bench'))
    if variables.get('BENCH_PREEMPTIBLE', 'true') not in ('true', 'false'):
        raise ValueError('BENCH_PREEMPTIBLE must be true or false')
    if variables['BENCH_BUCKET'] == variables['BENCH_STATE_BUCKET']:
        raise ValueError('photo and Terraform state buckets must be separate')
    return settings


def command(gh, *args, value=None):
    result = subprocess.run([gh, *args], input=value, text=True, capture_output=True)
    if result.returncode:
        # CLI error output is deliberately excluded: it may echo submitted data.
        raise RuntimeError('GitHub CLI failed for ' + ' '.join(args[:2]) +
                           '; check authentication and repository permissions')
    return result.stdout


def api(gh, repository, path):
    return json.loads(command(gh, 'api', 'repos/' + repository + '/' + path))


def check_idle(gh, repository):
    runs = api(gh, repository, 'actions/workflows/load-bench.yml/runs?per_page=100')['workflow_runs']
    if any(run['status'] != 'completed' for run in runs):
        raise ValueError('A benchmark workflow is active; wait before changing its credentials or variables')


def configure(gh, settings):
    repository = settings['repository']
    # Check required API access before the first mutation.
    api(gh, repository, 'actions/secrets/public-key')
    api(gh, repository, 'actions/variables?per_page=100')
    check_idle(gh, repository)
    for name, value in sorted(settings['variables'].items()):
        command(gh, 'variable', 'set', name, '--repo', repository, value=value)
        print('Configured variable:', name)
    for name, value in sorted(settings['secrets'].items()):
        command(gh, 'secret', 'set', name, '--repo', repository, value=value)
        print('Configured secret:', name)


def check(gh, settings):
    repository = settings['repository']
    variables = api(gh, repository, 'actions/variables?per_page=100')['variables']
    secrets = api(gh, repository, 'actions/secrets?per_page=100')['secrets']
    # Refuse an incomplete result rather than silently ignoring extra pages.
    if len(variables) == 100 or len(secrets) == 100:
        raise ValueError('More than one page of settings; inspect repository settings directly')
    actual = {item['name']: item['value'] for item in variables}
    missing = SECRETS - {item['name'] for item in secrets}
    mismatched = [name for name, value in settings['variables'].items() if actual.get(name) != value]
    if missing or mismatched:
        raise ValueError('Missing secrets: ' + ', '.join(sorted(missing)) +
                         '; missing/different variables: ' + ', '.join(sorted(mismatched)))
    print('Verified all variable values and six secret names; GitHub never returns secret values')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'configure', 'check'])
    parser.add_argument('--settings', required=True, type=Path)
    parser.add_argument('--gh', default=shutil.which('gh') or str(Path('.local/bin/gh').resolve()))
    args = parser.parse_args()
    if os.stat(args.settings).st_mode & 0o077:
        parser.error('settings file must be private; run chmod 600 on it')
    try:
        settings = validate_settings(json.loads(args.settings.read_text()))
        if args.action == 'validate':
            print('Private Actions settings validated; no remote changes')
            return
        if args.action == 'configure':
            configure(args.gh, settings)
        check(args.gh, settings)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        # Do not expose raw JSON or subprocess input from exception tracebacks.
        if isinstance(exc, json.JSONDecodeError):
            parser.exit(1, 'Invalid settings JSON\n')
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
