#!/usr/bin/env python3
"""Additional HTTP contract checks, runnable against either backend."""
import secrets
import sys

from smoke import Client


def check(base):
    client = Client(base)
    suffix = secrets.token_hex(4)
    users = []
    for role in ('owner', 'follower', 'other'):
        name = f'{role}-{suffix}'
        user = client.api('POST', '/auth/register', body={'username': name, 'email': f'{name}@test.local', 'password': 'password'}, expected=201)
        login = client.api('POST', '/auth/login', body={'email': user['email'], 'password': 'password'})
        users.append((user, login['token']))
    (owner, token), (follower, ft), (other, ot) = users
    client.api('GET', '/not-an-endpoint', expected=404)
    client.api('GET', '/me', expected=401)
    client.api('GET', '/events?limit=0', expected=400)
    client.api('GET', '/events?limit=101', expected=400)
    client.api('GET', '/events?offset=-1', expected=400)
    client.api('POST', f'/users/{owner["id"]}/follow', token=token, expected=400)
    client.api('POST', f'/users/{owner["id"]}/follow', token=ft)
    client.api('POST', f'/users/{owner["id"]}/follow', token=ft, expected=409)
    for visibility in ('PUBLIC', 'FOLLOWERS_ONLY', 'PRIVATE'):
        body = {'title': 'Контракт', 'location': 'Online', 'description': 'Исходное описание', 'starts_at': '2027-01-01T18:00:00+03:00', 'visibility': visibility, 'participant_limit': 1}
        event = client.api('POST', '/events', token=token, body=body, expected=201)
        path = f'/events/{event["id"]}'
        client.api('GET', path, token=ft, expected=403)
        patched = client.api('PATCH', path, token=token, body={'title': 'Новое название'})
        assert patched['description'] == body['description'] and patched['starts_at'] == '2027-01-01T15:00:00Z'
        client.api('POST', path + '/publish', token=token, body={'notify_user_ids': [other['id']]}, expected=403)
        assert client.api('GET', path, token=token)['status'] == 'DRAFT'
        client.api('POST', path + '/publish', token=token, body={'notify_user_ids': [follower['id']]})
        client.api('POST', path + '/publish', token=token, expected=409)
        client.api('GET', path, token=ft, expected=403 if visibility == 'PRIVATE' else 200)
        client.api('GET', path, token=ot, expected=200 if visibility == 'PUBLIC' else 403)
        client.api('GET', path + '/participants', token=ft, expected=403)
        client.api('PATCH', path, token=ft, body={'title': 'No'}, expected=403)
    print('PASS: unknown routes, error codes, pagination, follows, visibility, partial updates, selected recipients and rollback')


if __name__ == '__main__':
    check(sys.argv[1])
