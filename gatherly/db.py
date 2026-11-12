import os
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def serialize(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        text = value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        if value.microsecond:
            text += '.' + f'{value.microsecond:06d}'.rstrip('0')
        return text + 'Z'
    if isinstance(value, dict):
        return {k: serialize(v) if v is not None else '' for k, v in value.items() if k != 'password_hash'}
    if isinstance(value, list):
        return [serialize(v) for v in value]
    return value


class Database:
    def __init__(self):
        self.pool = ConnectionPool(os.environ['GATHERLY_DATABASE_URL'], min_size=0,
                                   max_size=int(os.getenv('GATHERLY_DB_POOL_SIZE', '2')),
                                   max_waiting=64, timeout=3, open=False,
                                   check=ConnectionPool.check_connection,
                                   kwargs={'row_factory': dict_row, 'connect_timeout': 3,
                                           'options': '-c statement_timeout=3000 -c lock_timeout=3000'})

    @contextmanager
    def transaction(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                yield conn


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def all_rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()
