#!/bin/sh
# Starts briar-headless without a terminal. briar-headless asks for the account on stdin: nickname,
# password and its confirmation the first time, the password on every later start. The account
# exists once its encrypted database key is in $BRIAR_DATA_DIR/key.
# BRIAR_AUTH_TOKEN, if set, becomes the API token; otherwise briar-headless makes one in
# $BRIAR_DATA_DIR/auth_token. It reads that file verbatim, so no trailing newline.
set -eu
: "${BRIAR_PASSWORD:?BRIAR_PASSWORD is not set}"
if [ -n "${BRIAR_AUTH_TOKEN:-}" ]; then
    (umask 077 && printf '%s' "$BRIAR_AUTH_TOKEN" > "$BRIAR_DATA_DIR/auth_token")
fi
if [ -e "$BRIAR_DATA_DIR/key/db.key" ] || [ -e "$BRIAR_DATA_DIR/key/db.key.bak" ]; then
    answers="$BRIAR_PASSWORD"
else
    : "${BRIAR_NICKNAME:?BRIAR_NICKNAME is not set (needed to create the account)}"
    answers="$BRIAR_NICKNAME
$BRIAR_PASSWORD
$BRIAR_PASSWORD"
fi
# exec keeps java as PID 1, so `docker stop` reaches its shutdown hook.
exec java -jar /opt/briar-headless.jar "$@" <<END
$answers
END
