#!/bin/sh
set -eu
# One psql session holds the migration lock and a transaction for all new files.
# 001 is idempotent so databases created by the old init script are supported.
{
  printf '%s\n' 'BEGIN;' 'SELECT pg_advisory_xact_lock(714202601);' \
    'CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());'
  for migration in /migrations/*.sql; do
    version=$(basename "$migration")
    case "$version" in *[!a-zA-Z0-9_.-]*) exit 1;; esac
    printf "SELECT NOT EXISTS(SELECT 1 FROM schema_migrations WHERE version='%s') AS apply \\gset\n" "$version"
    printf '%s\n' '\if :apply'
    cat "$migration"
    printf "\nINSERT INTO schema_migrations(version) VALUES('%s');\n" "$version"
    printf '%s\n' '\endif'
  done
  printf '%s\n' 'COMMIT;'
} | psql -X -v ON_ERROR_STOP=1
