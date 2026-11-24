#!/bin/sh
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
DEST="${DEST:-../unterricht/web/docs/public/informatik/thonny}"

rm -rf build dist
"$PYTHON" -m pip wheel --no-deps --wheel-dir dist .

mkdir -p "$DEST"
rm -f "$DEST"/thonny_templates-*.whl
cp dist/thonny_templates-*.whl "$DEST/"
