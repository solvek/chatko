#!/bin/sh
# Prepares the server stack: Mosquitto's TLS certificate, users, ACL, and the secrets in .env.
# Idempotent: it keeps what exists. Usage: deploy/setup.sh <public host name or IP> [gateway names...]
# (default gateway: gateway1). Prints the new passwords once; they are also in .env.
set -eu
host=${1:?usage: deploy/setup.sh <public host name or IP> [gateway names...]}
shift
[ $# -gt 0 ] || set -- gateway1
root=$(cd "$(dirname "$0")/.." && pwd)
cfg=$root/config/mosquitto
mkdir -p "$cfg/tls" "$root/data/meshtasticd-kyiv"
cp "$root/deploy/mosquitto/mosquitto.conf" "$cfg/mosquitto.conf"
touch "$root/.env"
chmod 600 "$root/.env"

# A self-signed certificate: gateway firmware does not check it (design.md §11).
if [ ! -f "$cfg/tls/server.crt" ]; then
    case $host in
        *[!0-9.]*) san="DNS:$host" ;;
        *) san="IP:$host" ;;
    esac
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$host" \
        -addext "subjectAltName=$san" -keyout "$cfg/tls/server.key" -out "$cfg/tls/server.crt" 2>/dev/null
    chmod 644 "$cfg/tls/server.key"  # the broker's user inside the container must read it
fi

# Sets VAR in .env to a new random value unless it is there; prints the name of what it made.
secret() {
    grep -q "^$1=." "$root/.env" || {
        printf '%s=%s\n' "$1" "$(openssl rand -base64 "$2")" >> "$root/.env"
        echo "new secret: $1"
    }
}
value() { grep "^$1=" "$root/.env" | cut -d= -f2-; }

users="hub"
for g in "$@"; do users="$users $g"; done
for u in $users; do
    var=MQTT_PASSWORD_$(echo "$u" | tr 'a-z-' 'A-Z_')
    secret "$var" 18
done
grep -q '^MESH_MQTT_USER=.' "$root/.env" || echo "MESH_MQTT_USER=hub" >> "$root/.env"
grep -q '^MESH_MQTT_PASSWORD=.' "$root/.env" || echo "MESH_MQTT_PASSWORD=$(value MQTT_PASSWORD_HUB)" >> "$root/.env"
# The Kyiv mesh's primary channel key: public, from the QR code at https://meshtastic.kyiv.ua/join.
grep -q '^KYIV_PRIMARY_PSK=.' "$root/.env" || echo "KYIV_PRIMARY_PSK=XOLPZHTWzHgykJxZ3pnj10mMJdr8glgblsNGLUIvd1w=" >> "$root/.env"
secret MESH_FAMILY_PSK 32

# The password file and the ACL, made from .env by the broker's own tool.
rm -f "$cfg/passwd"
first=1
for u in $users; do
    var=MQTT_PASSWORD_$(echo "$u" | tr 'a-z-' 'A-Z_')
    flag="-b"; [ $first = 1 ] && flag="-c -b"
    # shellcheck disable=SC2086
    docker run --rm -v "$cfg:/m" eclipse-mosquitto:2 mosquitto_passwd $flag /m/passwd "$u" "$(value "$var")" >/dev/null
    first=0
done
chmod 644 "$cfg/passwd"
: > "$cfg/acl"
for u in $users; do printf 'user %s\ntopic readwrite msh/EU_433/#\n\n' "$u" >> "$cfg/acl"; done
echo "broker users: $users (root topic msh/EU_433); certificate for $host in $cfg/tls"
