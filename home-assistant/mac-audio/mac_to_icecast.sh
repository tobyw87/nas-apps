#!/bin/bash
# Send the MacBook's sound to the NAS Icecast mount that the "PC audio to HomePods" button plays.
# Needs: brew install ffmpeg blackhole-2ch, and a Multi-Output Device (speakers + BlackHole 2ch) as the Mac's output.
# Usage: ./mac_to_icecast.sh        (asks for the Icecast source password; or set ICECAST_PASS)
# Only one source can use the mount at a time, so stop the PC sender first.
HOST="${ICECAST_HOST:-192.168.50.133}"
PORT="${ICECAST_PORT:-18000}"
MOUNT="${ICECAST_MOUNT:-pc.mp3}"
DEVICE="${AUDIO_DEVICE:-BlackHole 2ch}"
if [ -z "$ICECAST_PASS" ]; then
  printf "Icecast source password (typing is hidden), then Return: "; read -rs ICECAST_PASS; echo
fi
echo "Streaming to Icecast... leave this window open, Ctrl-C to stop"
while true; do
  ffmpeg -hide_banner -loglevel warning -f avfoundation -i ":$DEVICE" \
    -c:a libmp3lame -b:a 192k -ar 44100 -ac 2 \
    -content_type audio/mpeg -ice_name "MacBook" -f mp3 \
    "icecast://source:${ICECAST_PASS}@${HOST}:${PORT}/${MOUNT}" 2>&1 | sed "s/${ICECAST_PASS}/*****/g"
  echo "Stream stopped; retrying in 5 s (Ctrl-C to quit)"; sleep 5
done
