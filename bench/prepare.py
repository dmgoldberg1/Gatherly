#!/usr/bin/env python3
"""Idempotent fixtures shared by comparisons with the same seed."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import random
import sys
import time

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from smoke import Client


def prepare(base, config, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    client = Client(base)
    seed = config['seed']
    password = f'bench-password-{seed}'
    accounts = []
    for index in range(8):
        name = f'bench-{seed}-{index}'
        try:
            client.api('POST', '/auth/register', body={'username': name, 'email': name + '@bench.test', 'password': password}, expected=201)
        except AssertionError as exc:
            if "409" not in str(exc):
                raise
        logged = client.api('POST', '/auth/login', body={'username': name, 'password': password})
        accounts.append({'id': logged['user']['id'], 'token': logged['token']})
    owner = accounts[0]
    owned = []
    while True:
        batch = client.api('GET', f'/me/events?limit=100&offset={len(owned)}', token=owner['token'])['items']
        owned.extend(batch)
        if len(batch) < 100:
            break
    topics = [('Шахматы', 'Играем в шахматы и настольные игры'), ('Футбол', 'Играем в футбол на стадионе')]
    events = []
    for index in range(24):
        topic, description = topics[index % 2]
        title = f'bench-{seed}-{index}: {topic}'
        found = next((e for e in owned if e['title'] == title), None)
        if found is None:
            body = {'title': title, 'description': description, 'location': 'Bench', 'starts_at': f'{2020 if index < 4 else 2036}-01-{index + 1:02}T12:00:00Z'}
            found = client.api('POST', '/events', token=owner['token'], body=body, expected=201)
        if found['status'] == 'DRAFT':
            found = client.api('POST', f'/events/{found["id"]}/publish', token=owner['token'])
        events.append(found)
    for index, account in enumerate(accounts):
        history = {item['event']['id'] for item in client.api('GET', '/me/participations?limit=100', token=account['token'])['items']}
        # Last two accounts deliberately have no history, covering the popular fallback.
        if index < 6:
            for event in events[:4]:
                if event['id'] not in history and event['id'] % 2 == events[index % 2]['id'] % 2:
                    client.api('POST', f'/events/{event["id"]}/participants', token=account['token'], expected=201)
        created = client.api('POST', '/albums', token=account['token'], body={'event_id': events[index % 2]['id'], 'title': f'Нагрузка seed={seed}'}, expected=201) if index < 6 else None
        if created is None:
            # Permission to upload requires going participation; this is a future
            # event so the two cold-start recommendation profiles stay empty.
            event = events[4]
            if event['id'] not in history:
                client.api('POST', f'/events/{event["id"]}/participants', token=account['token'], expected=201)
            created = client.api('POST', '/albums', token=account['token'], body={'event_id': event['id'], 'title': f'Нагрузка seed={seed}'}, expected=201)
        account.update(album_id=created['album']['id'], upload_token=created['upload_token'], upload_expires_at=time.time() + 600)
    rng = random.Random(seed)
    for name, size in [('small', (320, 240)), ('medium', (1600, 1200)), ('large', (4000, 3000))]:
        image = Image.frombytes('RGB', size, rng.randbytes(size[0] * size[1] * 3))
        image.save(output / (name + '.jpg'), 'JPEG', quality=85)
    (output / 'accounts.json').write_text(json.dumps({'seed': seed, 'accounts': accounts}, indent=2))
    deadline = time.monotonic() + 120
    while True:
        try:
            for index in (1, 6):
                result = client.api('GET', '/recommendations', token=accounts[index]['token'])
                assert result['source'] == ('personalized' if index == 1 else 'popular')
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    print('Prepared deterministic accounts, histories, upload tokens, albums and three image sizes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    prepare(args.base, json.loads(Path(args.config).read_text()), args.output)
