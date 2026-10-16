#!/bin/sh
# Install dependencies for a linked working copy.
# `herdr plugin link` does not run manifest build commands.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
exec "$ROOT/scripts/install.sh" --editable --dev
