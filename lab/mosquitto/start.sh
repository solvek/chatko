#!/bin/sh
# Makes the password file of the lab's users, then starts Mosquitto through the image's entrypoint.
# The password of user X is chatko-lab-X: lab values for a broker on 127.0.0.1, never for production.
set -eu
passwd=/mosquitto/data/passwd
rm -f "$passwd"
for user in hub radio lab; do
    if [ -f "$passwd" ]; then
        mosquitto_passwd -b "$passwd" "$user" "chatko-lab-$user"
    else
        mosquitto_passwd -c -b "$passwd" "$user" "chatko-lab-$user"
    fi
done
chmod 0700 "$passwd"
exec /docker-entrypoint.sh mosquitto -c /mosquitto/config/mosquitto.conf
