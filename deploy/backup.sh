#!/bin/sh
# Backs up the hub: config/, data/ and .env (the secrets, among them the Briar password that the
# account's data is useless without) in one archive. Run it as root (the daily timer does), because
# data/briar and data/meshtasticd-kyiv belong to the containers' users.
# Usage: deploy/backup.sh [directory]   (default: backups/ in the repository; keeps BACKUP_KEEP=14)
#
# The hub and briar-headless stop for the copy (about half a minute): SQLite in WAL mode and Briar's
# database are not safe to copy while they run. Both catch up on what they missed when they start.
# The archive holds the secrets: keep the directory private (this script makes it 0700) and copy the
# archives to another machine, encrypted (docs/deployment.md).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:-$root/backups}
keep=${BACKUP_KEEP:-14}
compose=${COMPOSE:-docker compose -f $root/deploy/docker-compose.yml}
stopped="chatko briar"

umask 077
mkdir -p "$dest"
chmod 700 "$dest"
archive=$dest/chatko-$(date -u +%Y%m%d-%H%M%S).tar.gz

# Whatever happens, the stack comes back and no half-written archive stays.
# shellcheck disable=SC2086
trap 'rm -f "$archive.part"; $compose start $stopped >/dev/null' EXIT
# shellcheck disable=SC2086
$compose stop $stopped >/dev/null

# --numeric-owner keeps the containers' users as they are, whatever /etc/passwd says on this host.
tar -C "$root" --numeric-owner -czf "$archive.part" .env config data
mv "$archive.part" "$archive"

# The newest $keep archives stay.
# shellcheck disable=SC2012
ls -1t "$dest"/chatko-*.tar.gz | tail -n +"$((keep + 1))" | while read -r old; do rm -f "$old"; done
echo "backup: $archive"
