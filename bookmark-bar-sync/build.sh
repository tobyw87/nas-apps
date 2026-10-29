#!/bin/sh
# Builds dist/chrome (load unpacked), dist/bookmark-bar-sync-chrome.zip and dist/bookmark-bar-sync-firefox.zip
# (upload to addons.mozilla.org as "On your own" to get it signed).
# Usage: ./build.sh [https://links.tobyw87.com]
set -eu
HOST="${1:-https://links.tobyw87.com}"
HOST="${HOST%/}"
cd "$(dirname "$0")"
rm -rf dist && mkdir -p dist/chrome dist/firefox
for b in chrome firefox; do
  sed "s#__HOST__#$HOST#g" src/background.js > dist/$b/background.js
  cp src/options.html src/options.js src/options.css dist/$b/
  cp -r src/icons dist/$b/
  sed "s#__HOST__#$HOST#g" src/manifest.$b.json > dist/$b/manifest.json
  (cd dist/$b && zip -qr ../bookmark-bar-sync-$b.zip .)
done
echo "Built for $HOST"
