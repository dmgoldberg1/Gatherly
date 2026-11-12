"""HTTP compatibility domain operations using parameterized PostgreSQL queries."""
import re
from datetime import datetime, timezone

from .auth import hash_password, issue_token, verify_password
from .db import all_rows, one


class ServiceError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def require(condition, status, message):
    if not condition:
        raise ServiceError(status, message)


def timestamp(value):
    if not value:
        return None
    try:
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}(?::?\d{2})?)', value), 400, 'timestamp must include date, time and timezone')
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 400, 'timestamp must include timezone.')
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ServiceError(400, 'invalid timestamp.')


def user(conn, uid):
    result = one(conn, 'SELECT * FROM users WHERE id=%s', (uid,))
    require(result, 404, 'user not found.')
    return result


def following(conn, uid, target):
    return one(conn, 'SELECT 1 FROM follows WHERE follower_id=%s AND following_id=%s', (uid, target)) is not None


def event(conn, eid, uid, owner=False, lock=False):
    result = one(conn, 'SELECT * FROM events WHERE id=%s' + (' FOR UPDATE' if lock else ''), (eid,))
    require(result, 404, 'event not found.')
    if owner:
        require(result['organizer_id'] == uid, 403, 'only organizer can perform this action.')
    else:
        visible = result['organizer_id'] == uid
        if not visible:
            visible = one(conn, 'SELECT 1 FROM participations WHERE user_id=%s AND event_id=%s', (uid, eid)) is not None
        if not visible and result['status'] == 'PUBLISHED':
            visible = result['visibility'] == 'PUBLIC' or (result['visibility'] == 'FOLLOWERS_ONLY' and following(conn, uid, result['organizer_id']))
        require(visible, 403, 'event is not visible for current user.')
    return result


def register(conn, body):
    name, email, password = (body.get(k, '') for k in ('username', 'email', 'password'))
    require(all(isinstance(v, str) and v for v in (name, email, password)), 400, 'username, email and password are required.')
    require(re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', name), 400, "username must be 3-32 chars and contain only letters, digits, '_' or '-'.")
    require('@' in email[1:-1] and len(email.encode()) <= 120, 400, 'email must be valid enough for MVP.')
    require(len(password.encode()) >= 6, 400, 'password must contain at least 6 characters.')
    digest = hash_password(password)
    # Unique constraints also protect concurrent registration on different replicas.
    require(not one(conn, 'SELECT 1 FROM users WHERE username=%s', (name,)), 409, 'username already exists.')
    require(not one(conn, 'SELECT 1 FROM users WHERE email=%s', (email,)), 409, 'email already exists.')
    return one(conn, 'INSERT INTO users(username,email,password_hash,bio,avatar_url) VALUES (%s,%s,%s,%s,%s) RETURNING *',
               (name, email, digest, body.get('bio', ''), body.get('avatar_url', '')))


def login(conn, body, secret):
    result = one(conn, 'SELECT * FROM users WHERE username=%s OR email=%s ORDER BY id LIMIT 1', (body.get('username', body.get('email', '')),)*2)
    require(result and verify_password(body.get('password', ''), result['password_hash']), 401, 'invalid credentials.')
    if result['password_hash'].startswith('plain:'):
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s', (hash_password(body['password']), result['id']))
    return {'user': result, 'token': issue_token(secret, result['id'])}


EVENT_FIELDS = ('title', 'description', 'location', 'starts_at', 'ends_at', 'photo_url', 'category', 'visibility', 'participant_limit')


def event_values(body, current=None):
    values = dict(title='', description='', location='', starts_at='', ends_at='', photo_url='', category='', visibility='PUBLIC', participant_limit=0)
    if current:
        values.update({k: current[k] for k in EVENT_FIELDS})
    values.update({k: body[k] for k in EVENT_FIELDS if k in body})
    require(all(isinstance(values[k], str) and values[k] for k in ('title', 'location')), 400, 'title, location and starts_at are required.')
    for key, maximum in (('title', 120), ('location', 160), ('description', 2000)):
        require(isinstance(values[key], str) and len(values[key].encode()) <= maximum, 400, f'{key} is too long.')
    for key in ('starts_at', 'ends_at'):
        if not isinstance(values[key], datetime):
            values[key] = timestamp(values[key])
    require(values['starts_at'], 400, 'title, location and starts_at are required.')
    require(not values['ends_at'] or values['ends_at'] > values['starts_at'], 400, 'ends_at must be later than starts_at.')
    require(isinstance(values['participant_limit'], int) and 0 <= values['participant_limit'] <= 10000, 400, 'participant_limit must be in range 0..10000.')
    require(isinstance(values['visibility'], str), 400, 'invalid visibility.')
    values['visibility'] = values['visibility'].upper()
    require(values['visibility'] in ('PUBLIC', 'FOLLOWERS_ONLY', 'PRIVATE'), 400, 'Unknown event visibility: ' + values['visibility'])
    return tuple(values[k] for k in EVENT_FIELDS)


def create_event(conn, uid, body):
    values = event_values(body)
    return one(conn, 'INSERT INTO events(organizer_id,title,description,location,starts_at,ends_at,photo_url,category,visibility,participant_limit,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,\'DRAFT\') RETURNING *', (uid, *values))


def notify(conn, event_row, kind, recipients=None):
    eid = event_row['id']
    if kind == 'EVENT_PUBLISHED':
        targets = all_rows(conn, 'SELECT follower_id AS user_id FROM follows WHERE following_id=%s', (event_row['organizer_id'],))
        if recipients is not None:
            require(isinstance(recipients, list) and all(type(x) is int and x > 0 for x in recipients), 400, 'invalid recipients.')
            ids = set(recipients)
            for uid in ids:
                user(conn, uid)
                require(following(conn, uid, event_row['organizer_id']), 403, 'notification recipient must follow organizer.')
            targets = [r for r in targets if r['user_id'] in ids]
        title, text = 'Новое событие от пользователя из подписок', f"Опубликовано событие: {event_row['title']}."
    else:
        targets = all_rows(conn, "SELECT user_id FROM participations WHERE event_id=%s AND user_id<>%s AND status<>'DECLINED'", (eid, event_row['organizer_id']))
        title, text = ('Событие отменено', f"Событие {event_row['title']} отменено.") if kind == 'EVENT_CANCELLED' else ('Событие обновлено', f"У события {event_row['title']} изменились время или место.")
    with conn.cursor() as cursor:
        cursor.executemany("INSERT INTO notification_outbox(user_id,type,title,text,status) VALUES (%s,%s,%s,%s,'PENDING')", [(r['user_id'], kind, title, text) for r in targets])


def change_event(conn, eid, uid, body, action):
    current = event(conn, eid, uid, owner=True, lock=True)
    if action == 'update':
        require(current['status'] in ('DRAFT', 'PUBLISHED'), 409, 'only draft or published event can be updated.')
        values = event_values(body, current)
        updated = one(conn, 'UPDATE events SET title=%s,description=%s,location=%s,starts_at=%s,ends_at=%s,photo_url=%s,category=%s,visibility=%s,participant_limit=%s,updated_at=now() WHERE id=%s RETURNING *', (*values, eid))
        if current['status'] == 'PUBLISHED' and any(current[k] != updated[k] for k in ('starts_at', 'ends_at', 'location')):
            notify(conn, updated, 'EVENT_UPDATED')
    else:
        if action == 'publish':
            require(current['status'] == 'DRAFT', 409, 'only draft event can be published.')
            status, kind = 'PUBLISHED', 'EVENT_PUBLISHED'
        else:
            require(current['status'] != 'CANCELLED', 409, 'event is already cancelled.')
            status, kind = 'CANCELLED', 'EVENT_CANCELLED'
        updated = one(conn, 'UPDATE events SET status=%s,updated_at=now() WHERE id=%s RETURNING *', (status, eid))
        notify(conn, updated, kind, body.get('notify_user_ids') if action == 'publish' else None)
    return updated


def participate(conn, eid, uid, method, body):
    # Lock the shared event row before reading capacity or modifying the queue.
    # Every replica takes the same lock, without serializing unrelated events.
    stored = one(conn, 'SELECT * FROM events WHERE id=%s FOR UPDATE', (eid,))
    require(stored, 404, 'event not found.')
    current = one(conn, 'SELECT * FROM participations WHERE event_id=%s AND user_id=%s', (eid, uid))
    if method == 'POST':
        require(stored['status'] == 'PUBLISHED', 409, 'only published event can be joined.')
        event(conn, eid, uid)
        require(not current, 409, 'participation already exists.')
    else:
        require(current, 404, 'participation not found.')
    if method == 'DELETE':
        conn.execute('DELETE FROM participations WHERE event_id=%s AND user_id=%s', (eid, uid))
        if current['status'] == 'GOING':
            conn.execute("UPDATE participations SET status='GOING',updated_at=now() WHERE id=(SELECT id FROM participations WHERE event_id=%s AND status='WAITLISTED' ORDER BY id LIMIT 1)", (eid,))
        return {'ok': True, 'message': 'Пользователь вышел из события.'}
    status = 'GOING' if method == 'POST' else body.get('status', 'GOING').upper()
    require(status in ('GOING', 'MAYBE', 'DECLINED', 'WAITLISTED'), 400, 'Unknown participation status: ' + status)
    going = one(conn, "SELECT count(*) AS n FROM participations WHERE event_id=%s AND status='GOING'", (eid,))['n']
    if status == 'GOING' and stored['participant_limit'] and going >= stored['participant_limit'] and (not current or current['status'] != 'GOING'):
        status = 'WAITLISTED'
    if current:
        return one(conn, 'UPDATE participations SET status=%s,updated_at=now() WHERE id=%s RETURNING *', (status, current['id']))
    return one(conn, 'INSERT INTO participations(event_id,user_id,status) VALUES (%s,%s,%s) RETURNING *', (eid, uid, status))


def deliver_outbox(db):
    with db.transaction() as conn:
        records = all_rows(conn, "SELECT * FROM notification_outbox WHERE status IN ('PENDING','FAILED') ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED")
        for record in records:
            conn.execute("INSERT INTO notifications(user_id,type,title,text,status) VALUES (%s,%s,%s,%s,'NEW')", tuple(record[k] for k in ('user_id', 'type', 'title', 'text')))
            conn.execute("UPDATE notification_outbox SET status='PROCESSED',attempts=attempts+1,processed_at=now(),last_error=NULL WHERE id=%s", (record['id'],))
        return len(records)
