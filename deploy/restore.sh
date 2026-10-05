#!/bin/sh
# Restores the hub from a backup made by deploy/backup.sh: .env, config/ and data/. What is there now
# is moved to replaced-<time>/ next to them, never deleted. Run it as root (the files belong to the
# containers' users), on the server that will run the stack, then start it:
#   docker compose -f deploy/docker-compose.yml up -d
# Usage: deploy/restore.sh <archive>
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
compose=${COMPOSE:-docker compose -f $root/deploy/docker-compose.yml}
archive=${1:?usage: deploy/restore.sh <archive>}
[ -f "$archive" ] || { echo "restore: no such file: $archive" >&2; exit 1; }

# Refuse an archive that is not a backup of the hub before touching anything.
names=$(tar -tzf "$archive") || { echo "restore: not a readable archive: $archive" >&2; exit 1; }
for need in .env config/ data/; do
    printf '%s\n' "$names" | grep -qx "$need" \
        || { echo "restore: $archive is not a chatko backup (no $need)" >&2; exit 1; }
done
[ "$(id -u)" = 0 ] || echo "restore: warning: not root, so the files will not keep their owners" >&2

# On a new server there is no .env yet, so Compose cannot even read its file; there is nothing to stop.
# shellcheck disable=SC2086
$compose down >/dev/null 2>&1 || echo "restore: no running stack to stop" >&2
old=$root/replaced-$(date -u +%Y%m%d-%H%M%S)
for item in .env config data; do
    if [ -e "$root/$item" ]; then
        mkdir -p "$old"
        mv "$root/$item" "$old/$item"
    fi
done
tar -C "$root" --numeric-owner -xzf "$archive"
# Compose reads the secrets from the link that setup.sh makes.
ln -sfn ../.env "$root/deploy/.env"
[ ! -d "$old" ] || echo "restore: the previous state is in $old"
echo "restore: done; start the stack with: docker compose -f $root/deploy/docker-compose.yml up -d"
