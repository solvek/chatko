#!/bin/sh
# Starts briar-headless without a terminal (D28). briar-headless asks for the account on stdin:
# nickname, password and its confirmation the first time, the password on every later start. The
# account exists once its encrypted database key is in $BRIAR_DATA_DIR/key.
# BRIAR_AUTH_TOKEN, if set, becomes the API token; otherwise briar-headless makes one in
# $BRIAR_DATA_DIR/auth_token. It reads that file verbatim, so no trailing newline.
set -eu
data=${BRIAR_DATA_DIR:?BRIAR_DATA_DIR is not set}

# Started as root: give the data directory to briar (a bind mount that Docker created belongs to
# root), then run this script again as briar.
if [ "$(id -u)" = 0 ]; then
    [ "$(stat -c %U "$data")" = briar ] || chown -R briar:briar "$data"
    exec setpriv --reuid=briar --regid=briar --init-groups "$0" "$@"
fi

: "${BRIAR_PASSWORD:?BRIAR_PASSWORD is not set}"
if [ -n "${BRIAR_AUTH_TOKEN:-}" ]; then
    (umask 077 && printf '%s' "$BRIAR_AUTH_TOKEN" > "$data/auth_token")
fi
if [ -e "$data/key/db.key" ] || [ -e "$data/key/db.key.bak" ]; then
    answers="$BRIAR_PASSWORD"
else
    : "${BRIAR_NICKNAME:?BRIAR_NICKNAME is not set (needed to create the account)}"
    answers="$BRIAR_NICKNAME
$BRIAR_PASSWORD
$BRIAR_PASSWORD"
fi
# Java does not need the secrets in its environment.
unset BRIAR_PASSWORD BRIAR_AUTH_TOKEN
# exec keeps java as PID 1, so `docker stop` reaches its shutdown hook.
exec java -jar /opt/briar-headless.jar "$@" <<END
$answers
END
