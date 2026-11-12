"""Password and JWT formats compatible with persisted Gatherly accounts."""
import hashlib
import hmac
import re
import secrets
import time

import jwt


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000)
    return f'pbkdf2-sha256$600000${salt}${digest.hex()}'


def verify_password(password, encoded):
    if encoded.startswith('plain:'):
        return hmac.compare_digest(password.encode(), encoded[6:].encode())
    if not re.fullmatch(r'pbkdf2-sha256\$600000\$[0-9a-fA-F]{32}\$[0-9a-fA-F]{64}', encoded):
        return False
    _, iterations, salt, digest = encoded.split('$')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(actual, bytes.fromhex(digest))


def issue_token(secret, user_id):
    now = int(time.time())
    return jwt.encode({'iss': 'gatherly', 'sub': str(user_id), 'iat': now, 'exp': now + 86400}, secret, algorithm='HS256')


def verify_token(secret, token):
    if not token or len(token) > 4096:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=['HS256'], issuer='gatherly', options={'require': ['exp', 'iat', 'sub']})
        subject = claims['sub']
        if not isinstance(subject, str) or not re.fullmatch(r'[0-9]+', subject):
            return None
        if not 0 < int(subject) <= 2**63 - 1 or claims['exp'] <= claims['iat'] or jwt.get_unverified_header(token).get('typ') != 'JWT':
            return None
        return int(subject)
    except (jwt.PyJWTError, TypeError, ValueError):
        return None
