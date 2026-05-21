#!/usr/bin/env bash
# Initialize a demo vault from the sample data.
# Usage: ./scripts/init-demo-vault.sh /path/to/content/dir
set -euo pipefail

DEST="${1:?Usage: $0 <content-dir>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/sample-vault"

mkdir -p "$DEST"
cp -r "$SOURCE_DIR"/* "$DEST/"
echo "Demo vault initialized at $DEST"
