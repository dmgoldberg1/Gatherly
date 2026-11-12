"""Shared Terraform state for independent Actions runners and local migration."""
import json
import os
from pathlib import Path
import re
import shutil


def state_key(stand):
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,30}', stand):
        raise ValueError('BENCH_STAND_NAME must be a short lowercase resource name')
    return stand + '/terraform.tfstate'


def initialize(directory, bucket, stand, run, migrated=False):
    directory = Path(directory)
    if not bucket:
        if (directory / 'backend.tf').exists():
            raise ValueError('BENCH_STATE_BUCKET is required for this remote backend')
        run(['terraform', '-chdir=' + str(directory), 'init', '-input=false'])
        return
    key = state_key(stand)
    metadata = directory / '.terraform/terraform.tfstate'
    backend = json.loads(metadata.read_text()).get('backend', {}) if metadata.is_file() else {}
    same_backend = (backend.get('type') == 's3' and backend.get('config', {}).get('bucket') == bucket
                    and backend.get('config', {}).get('key') == key)
    local = directory / 'terraform.tfstate'
    if local.is_file() and local.stat().st_size and not same_backend and not migrated:
        raise ValueError('Local stand state exists; run bench/cloud.py migrate_state before switching to Actions')
    shutil.copyfile(directory / 'backend.s3.tf.example', directory / 'backend.tf')
    run(['terraform', '-chdir=' + str(directory), 'init', '-input=false', '-reconfigure',
         '-backend-config=bucket=' + bucket, '-backend-config=key=' + key])


def migrate(directory, bucket, stand, run, client=None):
    """Seed an empty backend; preserve every existing remote state, including newer ones."""
    if not bucket:
        raise ValueError('BENCH_STATE_BUCKET is required for migration')
    key = state_key(stand)
    directory = Path(directory)
    raw = (directory / 'terraform.tfstate').read_bytes()
    local = json.loads(raw)
    if not local.get('lineage') or type(local.get('serial')) is not int or not local.get('resources'):
        raise ValueError('Local state is empty or invalid; refusing migration')
    if client is None:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net', region_name='ru-central1',
                              config=Config(s3={'addressing_style': 'path'}, signature_version='s3v4'))
    from botocore.exceptions import ClientError
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response['Error']['Code'] not in ('NoSuchKey', '404'):
            raise
        response = None
    if response is not None:
        if json.loads(response['Body'].read()) != local:
            raise ValueError('Remote state differs from local state; refusing to overwrite it')
    else:
        # Conditional PUT prevents overwriting a state created by another runner.
        client.put_object(Bucket=bucket, Key=key, Body=raw, ContentType='application/json', IfNoneMatch='*')
    initialize(directory, bucket, stand, run, migrated=True)
    print('Shared state initialized for ' + stand + '; local state backup retained')
