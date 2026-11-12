import asyncio
import hashlib
from io import BytesIO
import json
import os
import time
import uuid
import warnings

import jwt
from PIL import Image, ImageOps, UnidentifiedImageError
from prometheus_client import Gauge, Histogram
from starlette.concurrency import run_in_threadpool

from .db import all_rows, one, serialize
from .service import ServiceError, event, require

MAX_BYTES = int(os.getenv('GATHERLY_PHOTO_MAX_BYTES', str(12 * 1024 * 1024)))
MAX_PIXELS = int(os.getenv('GATHERLY_PHOTO_MAX_PIXELS', '24000000'))
CONCURRENCY = int(os.getenv('GATHERLY_PHOTO_CONCURRENCY', '2'))
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
ACTIVE = Gauge('gatherly_photo_processing_active', 'Photo transformations currently executing')
QUEUED = Gauge('gatherly_photo_processing_queued', 'Photo requests waiting for a processing slot')
PROCESS_TIME = Histogram('gatherly_photo_processing_seconds', 'Photo transformation and storage latency')


def sign(secret, purpose, claims, lifetime):
    now = int(time.time())
    return jwt.encode({'iss': 'gatherly', 'aud': purpose, 'iat': now, 'exp': now + lifetime, **claims}, secret, algorithm='HS256')


def verify(secret, purpose, token, album_id=None):
    try:
        claims = jwt.decode(token, secret, algorithms=['HS256'], issuer='gatherly', audience=purpose,
                            options={'require': ['exp', 'iat', 'album_id', 'owner_id']})
        require(album_id is None or claims['album_id'] == album_id, 403, 'token belongs to another album.')
        return claims
    except jwt.PyJWTError:
        raise ServiceError(401, 'invalid or expired photo token.')


def create(db, uid, body, secret):
    title = body.get('title', '')
    require(isinstance(title, str) and 1 <= len(title) <= 120, 400, 'album title must contain 1..120 characters.')
    eid = body.get('event_id')
    require(type(eid) is int and eid > 0, 400, 'event_id is required.')
    with db.transaction() as conn:
        parent = event(conn, eid, uid)
        participant = one(conn, "SELECT 1 FROM participations WHERE event_id=%s AND user_id=%s AND status='GOING'", (eid, uid))
        require(parent['organizer_id'] == uid or participant, 403, 'only organizer or going participant can create album.')
        album = one(conn, 'INSERT INTO albums(event_id,owner_id,title) VALUES (%s,%s,%s) RETURNING *', (eid, uid, title))
    return {'album': serialize(album), 'upload_token': sign(secret, 'photo-upload', {'album_id': album['id'], 'owner_id': uid}, 600), 'expires_in': 600}


def process(storage, claims, raw, secret):
    with ACTIVE.track_inprogress(), PROCESS_TIME.time(), warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(raw)) as source:
                require(source.width * source.height <= MAX_PIXELS, 413, 'too many image pixels.')
                require(source.format in ('JPEG', 'PNG', 'WEBP'), 400, 'supported images: JPEG, PNG, WebP.')
                source.load()
                image = ImageOps.exif_transpose(source).convert('RGB')
            photo_id = str(uuid.uuid4())
            prefix = f"photos/{claims['album_id']}/{photo_id}"
            metadata = {'id': photo_id, 'album_id': claims['album_id'], 'owner_id': claims['owner_id'], 'width': image.width, 'height': image.height,
                        'thumbnail_key': prefix + '/thumbnail.jpg', 'gallery_key': prefix + '/gallery.jpg'}
            for field, edge in (('thumbnail_key', 320), ('gallery_key', 1600)):
                variant = image.copy()
                variant.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                output = BytesIO()
                variant.save(output, format='JPEG', quality=85, optimize=True)
                storage.put(metadata[field], output.getvalue(), 'image/jpeg')
            # The ready marker is written only after both immutable variants exist.
            storage.put(prefix + '/ready.json', json.dumps(metadata).encode(), 'application/json')
            return {'photo': metadata, 'receipt': sign(secret, 'photo-ready', metadata, 86400)}
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ServiceError(413, 'too many image pixels.')
        except (UnidentifiedImageError, OSError):
            raise ServiceError(400, 'invalid image.')


async def upload(request, album_id):
    # Authentication is entirely local: deliberately never use the session JWT or DB.
    claims = verify(request.app.state.secret, 'photo-upload', request.headers.get('x-upload-token', ''), album_id)
    require(request.app.state.photo_waiting < CONCURRENCY * 2, 429, 'photo processing queue is full.')
    request.app.state.photo_waiting += 1
    QUEUED.inc()
    waiting = True
    try:
        raw = bytearray()
        if request.headers.get('content-length'):
            require(int(request.headers['content-length']) <= MAX_BYTES, 413, 'photo is too large.')
        async for chunk in request.stream():
            raw.extend(chunk)
            require(len(raw) <= MAX_BYTES, 413, 'photo is too large.')
        async with request.app.state.photo_slots:
            QUEUED.dec()
            request.app.state.photo_waiting -= 1
            waiting = False
            return await run_in_threadpool(process, request.app.state.storage, claims, raw, request.app.state.secret)
    finally:
        if waiting:
            QUEUED.dec()
            request.app.state.photo_waiting -= 1


def publish(db, storage, secret, uid, album_id, body):
    receipts = body.get('receipts', [])
    require(isinstance(receipts, list) and 1 <= len(receipts) <= 100 and all(isinstance(x, str) and len(x) <= 4096 for x in receipts), 400, 'provide 1..100 photo receipts.')
    # Check access before object storage I/O; verification stays outside the write transaction.
    with db.transaction() as conn:
        album = one(conn, 'SELECT * FROM albums WHERE id=%s', (album_id,))
        require(album, 404, 'album not found.')
        require(album['owner_id'] == uid, 403, 'only owner can publish album.')
    photos = [verify(secret, 'photo-ready', token, album_id) for token in receipts]
    require(all(photo['owner_id'] == uid for photo in photos), 403, 'photo belongs to another owner.')
    require(len({photo['id'] for photo in photos}) == len(photos), 400, 'duplicate photos.')
    canonical = json.dumps(sorted(photo['id'] for photo in photos), separators=(',', ':'))
    publication_hash = hashlib.sha256(canonical.encode()).hexdigest()
    for photo in photos:
        try:
            ready = json.loads(storage.get(f"photos/{album_id}/{photo['id']}/ready.json"))
        except FileNotFoundError:
            raise ServiceError(409, 'photo is not ready.')
        expected = {key: photo[key] for key in ('id', 'album_id', 'owner_id', 'width', 'height', 'thumbnail_key', 'gallery_key')}
        require(ready == expected, 409, 'photo metadata mismatch.')
        require(storage.exists(photo['thumbnail_key']) and storage.exists(photo['gallery_key']), 409, 'photo is not ready.')
    with db.transaction() as conn:
        album = one(conn, 'SELECT * FROM albums WHERE id=%s FOR UPDATE', (album_id,))
        require(album and album['owner_id'] == uid, 403, 'only owner can publish album.')
        if album['status'] == 'PUBLISHED':
            require(album['publication_hash'] == publication_hash, 409, 'album is already published with different photos.')
            return serialize(album)
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO album_photos(id,album_id,thumbnail_key,gallery_key,width,height) VALUES (%s,%s,%s,%s,%s,%s)',
                               [tuple(photo[k] for k in ('id', 'album_id', 'thumbnail_key', 'gallery_key', 'width', 'height')) for photo in photos])
        album = one(conn, "UPDATE albums SET status='PUBLISHED',publication_hash=%s,published_at=now() WHERE id=%s RETURNING *", (publication_hash, album_id))
    return serialize(album)


def get_album(db, storage, secret, uid, album_id):
    with db.transaction() as conn:
        album = one(conn, 'SELECT * FROM albums WHERE id=%s', (album_id,))
        require(album, 404, 'album not found.')
        event(conn, album['event_id'], uid)
        require(album['owner_id'] == uid or album['status'] == 'PUBLISHED', 403, 'album is not published.')
        photos = all_rows(conn, 'SELECT * FROM album_photos WHERE album_id=%s ORDER BY created_at,id', (album_id,))
    result = serialize(album)
    result['photos'] = []
    for photo in photos:
        item = serialize(photo)
        item['id'] = str(item['id'])
        for field in ('thumbnail', 'gallery'):
            key = photo[field + '_key']
            token = sign(secret, 'photo-read', {'album_id': album_id, 'owner_id': uid, 'key': key}, 300)
            item[field + '_url'] = storage.url(key) or '/media?token=' + token
        result['photos'].append(item)
    return result
