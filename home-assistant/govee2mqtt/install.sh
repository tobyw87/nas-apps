#!/bin/sh
# One-time setup for govee2mqtt + Mosquitto. Run from /volume1/docker/govee2mqtt: sudo sh install.sh
# Asks for your Govee login and API key (typed here, stored only in .env), makes an MQTT password,
# starts both containers and prints what to enter in Home Assistant's MQTT integration.
# Never overwrites an existing .env or broker config.
set -e
cd "$(dirname "$0")"

if [ ! -f .env ] && netstat -tln 2>/dev/null | grep -q ':1883 '; then
  echo "Something already listens on port 1883 (an MQTT broker?). Stopping - tell Claude."; exit 1
fi

mkdir -p mosquitto/config mosquitto/data data
if [ ! -f mosquitto/config/mosquitto.conf ]; then
  cat > mosquitto/config/mosquitto.conf <<'CONF'
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
persistence true
persistence_location /mosquitto/data/
CONF
fi

if [ -f .env ]; then
  echo ".env already exists - keeping it."
else
  printf 'Govee account email: '; read -r GE
  printf 'Govee account password (hidden): '; stty -echo; read -r GP; stty echo; echo
  printf 'Govee API key (hidden): '; stty -echo; read -r GK; stty echo; echo
  case "$GE$GP$GK" in *"'"*) echo "Values can't contain a single quote ('). Nothing written."; exit 1;; esac
  MQPW=$(openssl rand -hex 16)
  umask 077
  cat > .env <<ENV
GOVEE_EMAIL='$GE'
GOVEE_PASSWORD='$GP'
GOVEE_API_KEY='$GK'
GOVEE_MQTT_HOST='127.0.0.1'
GOVEE_MQTT_PORT='1883'
GOVEE_MQTT_USER='ha'
GOVEE_MQTT_PASSWORD='$MQPW'
ENV
  echo "Wrote .env (readable by root only)."
fi
. ./.env

if [ ! -f mosquitto/config/passwd ]; then
  docker run --rm -v "$PWD/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
    mosquitto_passwd -c -b /mosquitto/config/passwd "$GOVEE_MQTT_USER" "$GOVEE_MQTT_PASSWORD"
fi
chown -R 1883:1883 mosquitto
chmod 0700 mosquitto/config/passwd

docker compose up -d
echo
echo "Done. In Home Assistant: Settings > Devices & services > Add integration > MQTT"
echo "  Broker: 127.0.0.1 (or 192.168.50.133)   Port: 1883"
echo "  Username: $GOVEE_MQTT_USER   Password: $GOVEE_MQTT_PASSWORD"
echo "Check the bridge with: sudo docker logs --tail 30 govee2mqtt"
