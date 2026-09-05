#!/bin/sh
set -eu
umask 077
mkdir -p backups
backup="backups/gatherly-$(date -u +%Y%m%dT%H%M%SZ).dump"
trap 'rm -f "$backup.partial"' EXIT
# pg_dump is consistent while the application serves traffic.
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U gatherly -d gatherly -Fc > "$backup.partial"
mv "$backup.partial" "$backup"
printf '%s\n' "$backup"
# Keep seven days locally; copy backups off the VM to survive disk loss.
find backups -name 'gatherly-*.dump' -mtime +7 -delete
