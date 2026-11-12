import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from psycopg import errors
from starlette.concurrency import run_in_threadpool

from . import service, photos
from .storage import Storage
from .recommendations import Recommendations
from .auth import verify_token
from .db import Database, all_rows, one, serialize

log = logging.getLogger('gatherly')
REQUESTS = Counter('http_requests_total', 'HTTP requests', ['method', 'route', 'status'])
DURATION = Histogram('http_request_duration_seconds', 'HTTP latency', ['method', 'route'])
OUTBOX = Gauge('notification_outbox_pending', 'Pending outbox records (sampled by worker)')
DB_UP = Gauge('gatherly_database_up', 'Last shared database health sample')


def sample_outbox(app):
    with app.state.db.transaction() as conn:
        OUTBOX.set(one(conn, "SELECT count(*) AS n FROM notification_outbox WHERE status<>'PROCESSED'")['n'])


async def outbox_loop(app):
    while True:
        try:
            await run_in_threadpool(service.deliver_outbox, app.state.db)
            await run_in_threadpool(sample_outbox, app)
        except Exception:
            log.exception('Outbox delivery deferred')
        await asyncio.sleep(1)


async def catalogue_loop(app):
    while True:
        try:
            await run_in_threadpool(app.state.recommendations.refresh)
        except Exception:
            log.warning('Recommendation catalogue unavailable', exc_info=True)
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app):
    app.state.secret = os.getenv('GATHERLY_AUTH_SECRET') or secrets.token_hex(32)
    if len(app.state.secret.encode()) < 32:
        raise RuntimeError('GATHERLY_AUTH_SECRET must contain at least 32 bytes')
    app.state.storage = Storage()
    app.state.photo_slots = asyncio.Semaphore(photos.CONCURRENCY)
    app.state.photo_waiting = 0
    app.state.db = Database()
    app.state.db.pool.open()
    app.state.recommendations = Recommendations(app.state.db, app.state.storage)
    catalogue = asyncio.create_task(catalogue_loop(app))
    worker = asyncio.create_task(outbox_loop(app)) if os.getenv('GATHERLY_OUTBOX_WORKER', '1') == '1' else None
    try:
        yield
    finally:
        catalogue.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await catalogue
        if worker:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        app.state.db.pool.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def error(status, message):
    return JSONResponse({'error': message}, status_code=status)


@app.middleware('http')
async def metrics(request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    response.headers['X-Gatherly-Replica'] = os.getenv('GATHERLY_REPLICA_ID', os.getenv('HOSTNAME', 'local'))
    route = re.sub(r'/[0-9]+(?=/|$)', '/{id}', request.url.path)
    if not route.startswith('/api/v1/') and route not in ('/health', '/ready', '/metrics', '/'):
        route = 'other'
    # Unknown routes must not create unbounded metric label cardinality.
    if route.startswith('/api/v1/') and route not in KNOWN_METRIC_ROUTES:
        route = 'other'
    REQUESTS.labels(request.method, route, str(response.status_code)).inc()
    DURATION.labels(request.method, route).observe(time.monotonic() - start)
    return response


KNOWN_METRIC_ROUTES = set('''/api/v1/auth/register /api/v1/auth/login /api/v1/me /api/v1/me/events
/api/v1/me/participations /api/v1/users /api/v1/users/{id} /api/v1/users/{id}/follow
/api/v1/users/{id}/followers /api/v1/users/{id}/following /api/v1/events /api/v1/events/{id}
/api/v1/events/{id}/publish /api/v1/events/{id}/cancel /api/v1/events/{id}/participants
/api/v1/events/{id}/participants/me /api/v1/feed /api/v1/notifications /api/v1/notifications/{id}/read
/api/v1/recommendations /api/v1/albums /api/v1/albums/{id} /api/v1/albums/{id}/photos
/api/v1/albums/{id}/publish /api/v1/albums/{id}/upload-token'''.split())


ROUTE_METHODS = {route: {'GET'} for route in KNOWN_METRIC_ROUTES}
for route in ('/api/v1/auth/register', '/api/v1/auth/login', '/api/v1/events/{id}/publish', '/api/v1/events/{id}/cancel', '/api/v1/albums/{id}/publish', '/api/v1/albums/{id}/photos', '/api/v1/albums/{id}/upload-token'):
    ROUTE_METHODS[route] = {'POST'}
ROUTE_METHODS.update({
    '/api/v1/events': {'GET', 'POST'}, '/api/v1/events/{id}': {'GET', 'PATCH'},
    '/api/v1/events/{id}/participants': {'GET', 'POST'}, '/api/v1/events/{id}/participants/me': {'PATCH', 'DELETE'},
    '/api/v1/users/{id}/follow': {'POST', 'DELETE'}, '/api/v1/notifications/{id}/read': {'PATCH'},
    '/api/v1/albums': {'GET', 'POST'},
})


def authenticate(request, conn):
    token = request.headers.get('authorization')
    service.require(token is not None, 401, 'Authorization header is required.')
    uid = verify_token(request.app.state.secret, token.removeprefix('Bearer '))
    service.require(uid is not None, 401, 'invalid token.')
    service.require(one(conn, 'SELECT 1 FROM users WHERE id=%s', (uid,)), 401, 'invalid token.')
    return uid


def page(request):
    result = []
    for key, default in (('limit', '50'), ('offset', '0')):
        text = request.query_params.get(key) or default
        service.require(re.fullmatch(r'[0-9]+', text) and len(text) < 20 and int(text) <= 2**63 - 1, 400, 'invalid numeric parameter.')
        result.append(int(text))
    service.require(1 <= result[0] <= 100, 400, 'limit must be in range 1..100.')
    return tuple(result)


def paginated(items, limit, offset):
    return {'items': items, 'count': len(items), 'limit': limit, 'offset': offset}


def dispatch(request, body):
    method, path = request.method, request.url.path
    parts = path.strip('/').split('/')
    db = request.app.state.db
    status = 200
    with db.transaction() as conn:
        if method == 'POST' and path == '/api/v1/auth/register':
            return service.register(conn, body), 201
        if method == 'POST' and path == '/api/v1/auth/login':
            return service.login(conn, body, request.app.state.secret), 200
        public = method == 'GET' and (path in ('/api/v1/users', '/api/v1/events') or (len(parts) in (4, 5) and parts[2] == 'users' and (len(parts) == 4 or parts[4] in ('followers', 'following'))))
        uid = None if public else authenticate(request, conn)
        if method == 'GET' and path == '/api/v1/me':
            result = service.user(conn, uid)
        elif method == 'GET' and path in ('/api/v1/users', '/api/v1/events', '/api/v1/feed', '/api/v1/me/events', '/api/v1/me/participations', '/api/v1/notifications'):
            limit, offset = page(request)
            if path == '/api/v1/users':
                items = all_rows(conn, 'SELECT * FROM users ORDER BY id LIMIT %s OFFSET %s', (limit, offset))
            elif path == '/api/v1/events':
                items = all_rows(conn, "SELECT * FROM events WHERE status='PUBLISHED' AND visibility='PUBLIC' ORDER BY id LIMIT %s OFFSET %s", (limit, offset))
            elif path == '/api/v1/feed':
                items = all_rows(conn, "SELECT e.* FROM events e JOIN follows f ON f.following_id=e.organizer_id WHERE f.follower_id=%s AND e.status='PUBLISHED' AND e.visibility IN ('PUBLIC','FOLLOWERS_ONLY') ORDER BY e.id LIMIT %s OFFSET %s", (uid, limit, offset))
            elif path == '/api/v1/me/events':
                items = all_rows(conn, 'SELECT * FROM events WHERE organizer_id=%s ORDER BY id LIMIT %s OFFSET %s', (uid, limit, offset))
            elif path == '/api/v1/me/participations':
                rows = all_rows(conn, 'SELECT * FROM participations WHERE user_id=%s ORDER BY id LIMIT %s OFFSET %s', (uid, limit, offset))
                items = [{'participation': row, 'event': service.event(conn, row['event_id'], uid)} for row in rows]
            else:
                items = all_rows(conn, 'SELECT * FROM notifications WHERE user_id=%s ORDER BY id LIMIT %s OFFSET %s', (uid, limit, offset))
            result = paginated(items, limit, offset)
        elif method == 'POST' and path == '/api/v1/events':
            result, status = service.create_event(conn, uid, body), 201
        elif len(parts) >= 4 and parts[2] == 'users':
            target = numeric(parts[3])
            if len(parts) == 4 and method == 'GET':
                result = service.user(conn, target)
            elif len(parts) == 5 and parts[4] in ('followers', 'following') and method == 'GET':
                service.user(conn, target)
                if parts[4] == 'followers':
                    result = all_rows(conn, 'SELECT u.* FROM follows f JOIN users u ON u.id=f.follower_id WHERE f.following_id=%s ORDER BY u.id', (target,))
                else:
                    result = all_rows(conn, 'SELECT u.* FROM follows f JOIN users u ON u.id=f.following_id WHERE f.follower_id=%s ORDER BY u.id', (target,))
            elif len(parts) == 5 and parts[4] == 'follow' and method in ('POST', 'DELETE'):
                if method == 'POST':
                    service.user(conn, target)
                    service.require(uid != target, 400, 'user cannot follow himself.')
                    inserted = one(conn, 'INSERT INTO follows(follower_id,following_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING *', (uid, target))
                    service.require(inserted, 409, 'user is already followed.')
                else:
                    deleted = one(conn, 'DELETE FROM follows WHERE follower_id=%s AND following_id=%s RETURNING *', (uid, target))
                    service.require(deleted, 404, 'follow relation not found.')
                result = {'ok': True, 'message': 'Подписка создана.' if method == 'POST' else 'Подписка удалена.'}
            else:
                raise service.ServiceError(404, 'endpoint not found.')
        elif len(parts) >= 4 and parts[2] == 'events':
            eid = numeric(parts[3])
            if len(parts) == 4 and method == 'GET':
                result = service.event(conn, eid, uid)
            elif len(parts) == 4 and method == 'PATCH':
                result = service.change_event(conn, eid, uid, body, 'update')
            elif len(parts) == 5 and parts[4] in ('publish', 'cancel') and method == 'POST':
                result = service.change_event(conn, eid, uid, body, parts[4])
            elif len(parts) in (5, 6) and parts[4] == 'participants':
                if len(parts) == 5 and method == 'GET':
                    service.event(conn, eid, uid, owner=True)
                    result = all_rows(conn, 'SELECT * FROM participations WHERE event_id=%s ORDER BY id', (eid,))
                elif (len(parts) == 5 and method == 'POST') or (len(parts) == 6 and parts[5] == 'me' and method in ('PATCH', 'DELETE')):
                    result = service.participate(conn, eid, uid, method, body)
                    status = 201 if method == 'POST' else 200
                else:
                    raise service.ServiceError(404, 'endpoint not found.')
            else:
                raise service.ServiceError(404, 'endpoint not found.')
        elif len(parts) == 5 and parts[2] == 'notifications' and parts[4] == 'read' and method == 'PATCH':
            result = one(conn, "UPDATE notifications SET status='READ',read_at=now() WHERE id=%s AND user_id=%s RETURNING *", (numeric(parts[3]), uid))
            service.require(result, 404, 'notification not found.')
        else:
            raise service.ServiceError(404, 'endpoint not found.')
    if '/participants' in path and method in ('POST', 'PATCH', 'DELETE'):
        request.app.state.recommendations.invalidate(uid)
    if method in ('POST', 'PATCH') and '/events/' in path:
        try:
            service.deliver_outbox(db)
        except Exception:
            log.exception('Outbox delivery deferred')
    return result, status


def numeric(value):
    service.require(re.fullmatch(r'[0-9]+', value) and len(value) < 20 and int(value) <= 2**63 - 1, 400, 'invalid numeric parameter.')
    return int(value)


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS', 'PUT'])
async def handle(request: Request, path: str):
    if request.method == 'OPTIONS':
        return PlainTextResponse('')
    if path == 'health' and request.method == 'GET':
        return JSONResponse({'status': 'ok'})
    if path == 'metrics' and request.method == 'GET':
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    if path == 'ready' and request.method == 'GET':
        if os.getenv('GATHERLY_CACHED_READINESS', '0') == '1':
            # Replicas use the worker's shared health sample, with no database polling.
            try:
                sample = json.loads(await run_in_threadpool(request.app.state.storage.get, 'catalog/health.json'))
                healthy = sample['database_up'] and time.time() - sample['at'] < 45
                DB_UP.set(int(healthy))
                return JSONResponse({'status': 'ready' if healthy else 'unavailable'}, status_code=200 if healthy else 503)
            except Exception:
                return error(503, 'database health sample unavailable.')
        try:
            def check():
                with request.app.state.db.transaction() as conn:
                    one(conn, 'SELECT 1')
            await run_in_threadpool(check)
            DB_UP.set(1)
            return JSONResponse({'status': 'ready'})
        except Exception:
            DB_UP.set(0)
            return error(503, 'database unavailable.')
    if path == 'media':
        try:
            claims = photos.verify(request.app.state.secret, 'photo-read', request.query_params.get('token', ''))
            raw = await run_in_threadpool(request.app.state.storage.get, claims['key'])
            return Response(raw, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=60'})
        except service.ServiceError as exc:
            return error(exc.status, exc.message)
        except Exception:
            return error(404, 'photo not found.')
    if path.startswith('api/'):
        if not path.startswith('api/v1/'):
            return error(404, 'endpoint not found.')
        route = re.sub(r'^(/api/v1/(?:users|events|notifications|albums))/[^/]+', r'\1/{id}', request.url.path)
        if route not in ROUTE_METHODS or request.method not in ROUTE_METHODS[route]:
            return error(404, 'endpoint not found.')
        try:
            if path == 'api/v1/recommendations' and request.method == 'GET':
                engine = request.app.state.recommendations
                uid = await run_in_threadpool(engine.authenticate, request.app.state.secret, request.headers.get('authorization'))
                limit, offset = page(request)
                result = await run_in_threadpool(engine.recommend, uid, limit, offset)
                return JSONResponse(result)
            album_upload = re.fullmatch(r'api/v1/albums/([0-9]+)/photos', path)
            if album_upload and request.method == 'POST':
                result = await photos.upload(request, numeric(album_upload[1]))
                return JSONResponse(result, status_code=201)
            if path == 'api/v1/albums' or re.fullmatch(r'api/v1/albums/[0-9]+(?:/(?:publish|upload-token))?', path):
                result, status = await album_route(request, path)
                return JSONResponse(serialize(result), status_code=status)
            body = await read_json(request)
            result, status = await run_in_threadpool(dispatch, request, body)
            return JSONResponse(serialize(result), status_code=status)
        except service.ServiceError as exc:
            return error(exc.status, exc.message)
        except (ValueError, TypeError, AttributeError):
            return error(400, 'invalid JSON or parameter.')
        except errors.UniqueViolation:
            return error(409, 'record already exists.')
        except Exception:
            log.exception('API request failed')
            return error(500, 'internal server error.')
    root = Path(os.getenv('GATHERLY_STATIC_DIR', 'static')).resolve()
    filename = (root / (path or 'index.html')).resolve()
    if not filename.is_relative_to(root):
        return PlainTextResponse('forbidden', status_code=403)
    if filename.is_file():
        return FileResponse(filename)
    return PlainTextResponse('not found', status_code=404)


async def album_route(request, path):
    def auth():
        with request.app.state.db.transaction() as conn:
            return authenticate(request, conn)
    uid = await run_in_threadpool(auth)
    db, storage, secret = request.app.state.db, request.app.state.storage, request.app.state.secret
    if path == 'api/v1/albums' and request.method == 'GET':
        def listing():
            with db.transaction() as conn:
                limit, offset = page(request)
                eid = request.query_params.get('event_id')
                if eid:
                    eid = numeric(eid)
                    service.event(conn, eid, uid)
                    items = all_rows(conn, "SELECT * FROM albums WHERE event_id=%s AND (status='PUBLISHED' OR owner_id=%s) ORDER BY id LIMIT %s OFFSET %s", (eid, uid, limit, offset))
                else:
                    items = all_rows(conn, 'SELECT * FROM albums WHERE owner_id=%s ORDER BY id LIMIT %s OFFSET %s', (uid, limit, offset))
                return paginated(items, limit, offset)
        return await run_in_threadpool(listing), 200
    if path == 'api/v1/albums' and request.method == 'POST':
        body = await read_json(request)
        service.require(isinstance(body, dict), 400, 'JSON object required.')
        return await run_in_threadpool(photos.create, db, uid, body, secret), 201
    match = re.fullmatch(r'api/v1/albums/([0-9]+)(/(?:publish|upload-token))?', path)
    if match:
        aid = numeric(match[1])
        if not match[2] and request.method == 'GET':
            return await run_in_threadpool(photos.get_album, db, storage, secret, uid, aid), 200
        if match[2] == '/upload-token' and request.method == 'POST':
            def renew():
                with db.transaction() as conn:
                    album = one(conn, 'SELECT * FROM albums WHERE id=%s', (aid,))
                    service.require(album, 404, 'album not found.')
                    service.require(album['owner_id'] == uid, 403, 'only owner can renew upload token.')
                    service.require(album['status'] == 'DRAFT', 409, 'album is already published.')
                return {'upload_token': photos.sign(secret, 'photo-upload', {'album_id': aid, 'owner_id': uid}, 600), 'expires_in': 600}
            return await run_in_threadpool(renew), 200
        if match[2] == '/publish' and request.method == 'POST':
            body = await read_json(request)
            service.require(isinstance(body, dict), 400, 'JSON object required.')
            return await run_in_threadpool(photos.publish, db, storage, secret, uid, aid, body), 200
    raise service.ServiceError(404, 'endpoint not found.')


async def read_json(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        service.require(len(raw) <= 1024 * 1024, 413, 'request too large.')
    body = json.loads(raw) if raw else {}
    service.require(isinstance(body, dict), 400, 'JSON object required.')
    return body
