#!/usr/bin/env python3
"""Photo contracts and recommendations, through a running proxy."""
from io import BytesIO
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

from smoke import Client


def image_bytes(size=(640, 480)):
    output = BytesIO()
    Image.new('RGB', size, (20, 110, 180)).save(output, 'JPEG')
    return output.getvalue()


def upload(base, aid, token, content, expected=201):
    request = urllib.request.Request(base + f'/api/v1/albums/{aid}/photos', data=content,
                                    headers={'X-Upload-Token': token, 'Content-Type': 'image/jpeg'})
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    assert response.status == expected, (response.status, response.read())
    return json.loads(response.read())


def check(base):
    client = Client(base)
    suffix = secrets.token_hex(4)
    tokens, users = [], []
    for role in ('photos', 'outsider'):
        name = f'{role}-{suffix}'
        user = client.api('POST', '/auth/register', body={'username': name, 'email': name + '@test.local', 'password': 'password'}, expected=201)
        users.append(user)
        tokens.append(client.api('POST', '/auth/login', body={'username': name, 'password': 'password'})['token'])
    owner, outsider = tokens
    event = client.api('POST', '/events', token=owner, body={'title': 'Фото и рекомендации', 'description': 'Встреча фотографов', 'location': 'Online', 'starts_at': '2027-03-01T15:00:00Z'}, expected=201)
    eid = event['id']
    client.api('POST', f'/events/{eid}/publish', token=owner)
    body = {'event_id': eid, 'title': 'Наши фотографии'}
    client.api('POST', '/albums', token=outsider, body=body, expected=403)
    created = client.api('POST', '/albums', token=owner, body=body, expected=201)
    aid, upload_token = created['album']['id'], created['upload_token']
    other_album = client.api('POST', '/albums', token=owner, body=body, expected=201)
    upload(base, other_album['album']['id'], upload_token, image_bytes(), 403)
    upload(base, aid, upload_token + 'invalid', image_bytes(), 401)
    upload(base, aid, upload_token, b'not an image', 400)
    photo = upload(base, aid, upload_token, image_bytes())
    assert photo['photo']['width'] == 640 and photo['photo']['height'] == 480
    client.api('POST', f'/albums/{aid}/publish', token=outsider, body={'receipts': [photo['receipt']]}, expected=403)
    client.api('POST', f'/albums/{aid}/publish', token=owner, body={'receipts': [photo['receipt'] + 'bad']}, expected=401)
    for _ in range(2):
        album = client.api('POST', f'/albums/{aid}/publish', token=owner, body={'receipts': [photo['receipt']]})
        assert album['status'] == 'PUBLISHED'
    published = client.api('GET', f'/albums/{aid}', token=outsider)
    assert len(published['photos']) == 1
    for variant, maximum in (('thumbnail_url', 320), ('gallery_url', 1600)):
        url = published['photos'][0][variant]
        with urllib.request.urlopen(base + url if url.startswith('/') else url) as response:
            with Image.open(BytesIO(response.read())) as image:
                assert max(image.size) <= maximum
    print('PASS: album permissions, album-bound tokens, image decoding, variants, receipt validation and idempotent publication')
    return client, users, tokens, eid, aid, upload_token


if __name__ == '__main__':
    check(sys.argv[1])
