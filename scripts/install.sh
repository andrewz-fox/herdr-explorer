#!/bin/sh
# Create an isolated virtualenv and install this plugin into it.
# Herdr runs this during `plugin install`. Linked checkouts use dev/bootstrap.sh.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

EDITABLE=0
DEV=0
for arg in "$@"; do
  case "$arg" in
    --editable) EDITABLE=1 ;;
    --dev) DEV=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

PYTHON=""
for candidate in \
  python3.13 python3.12 python3.11 python3 \
  /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
  /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11
do
  if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PYTHON=$candidate
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "File Explorer needs Python 3.11 or newer." >&2
  exit 1
fi

if [ ! -x "$ROOT/.venv/bin/python" ] || ! "$ROOT/.venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  "$PYTHON" -m venv "$ROOT/.venv"
fi

SPEC="."
if [ "$DEV" -eq 1 ]; then
  SPEC=".[dev]"
fi

if [ "$EDITABLE" -eq 1 ]; then
  "$ROOT/.venv/bin/python" -m pip install -e "$SPEC"
else
  "$ROOT/.venv/bin/python" -m pip install "$SPEC"
fi
