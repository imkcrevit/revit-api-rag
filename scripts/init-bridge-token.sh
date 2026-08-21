#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
secret_dir="$repo_dir/.secrets"
token_file="$secret_dir/revit-slot-1.token"

install -d -m 700 "$secret_dir"

if [[ -s "$token_file" ]]; then
  echo "Bridge token already exists: $token_file"
  exit 0
fi

openssl rand -hex -out "$token_file" 32
chmod 600 "$token_file"
echo "Bridge token created: $token_file"
echo "The token value was not printed. Copy it securely when configuring Revit."
