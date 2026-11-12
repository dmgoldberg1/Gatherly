#!/usr/bin/env python3
"""Prove warm recommendations and image upload work while PostgreSQL is stopped.

This destructive fault test is restricted to the disposable gatherly-bench project.
"""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local/bench'
COMPOSE = ['docker', 'compose', '-p', 'gatherly-bench', '--env-file', str(STATE / 'secrets.env'), '-f', str(ROOT / 'docker-compose.bench.yml')]
account = json.loads((STATE / 'run/data/accounts.json').read_text())['accounts'][0]
replicas = json.loads((STATE / 'current/config.json').read_text())['replicas']
base = 'http://127.0.0.1:18090'


def request(path, headers, body=None):
    with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers, data=body), timeout=15) as response:
        return response.status, response.headers.get('X-Gatherly-Replica'), json.load(response)


def recommendations():
    return request('/api/v1/recommendations', {'Authorization': 'Bearer ' + account['token']})


def compose(*args):
    subprocess.run(COMPOSE + list(args), check=True)


compose('pause', 'worker')
stopped = False
try:
    # Let every replica finish loading the last snapshot before warming its cache.
    time.sleep(4)
    seen = set()
    for _ in range(replicas * 20):
        status, replica, result = recommendations()
        assert status == 200 and result['items']
        seen.add(replica)
        if len(seen) == replicas:
            break
    assert len(seen) == replicas, seen
    compose('stop', 'postgres')
    stopped = True
    for _ in range(replicas * 3):
        assert recommendations()[0] == 200
    raw = (STATE / 'run/data/medium.jpg').read_bytes()
    status, _, photo = request(f'/api/v1/albums/{account["album_id"]}/photos', {'X-Upload-Token': account['upload_token'], 'Content-Type': 'image/jpeg'}, raw)
    assert status == 201 and photo['receipt']
    print(f'PASS: authenticated warm recommendations on {replicas} replicas and photo upload succeed with PostgreSQL stopped')
finally:
    if stopped:
        compose('up', '-d', '--wait', 'postgres')
    compose('unpause', 'worker')
