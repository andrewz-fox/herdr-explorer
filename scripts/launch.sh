#!/bin/sh
# Launch File Explorer with the plugin virtualenv.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "File Explorer is not installed yet." >&2
  echo "Installed plugins should have been built by Herdr. Linked plugins need: dev/bootstrap.sh" >&2
  exit 1
fi

exec "$PYTHON" -m herdr_explorer "$@"
