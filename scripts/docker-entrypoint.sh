#!/bin/sh
set -eu

# Docker Compose implements local secrets as root-owned bind mounts. Copy each
# configured Bridge secret into an appuser-only runtime directory, then drop
# privileges before starting the server. The token never enters the image or
# the process environment.
runtime_dir="/run/revit-api-rag-secrets"
install -d -o appuser -g appuser -m 0700 "$runtime_dir"

slot="1"
while [ "$slot" -le 5 ]; do
    eval "source_file=\${MCP_BRIDGE_SLOT_TOKEN_FILE_$slot:-}"
    if [ -n "$source_file" ]; then
        if [ ! -s "$source_file" ]; then
            echo "Configured Bridge token file is missing or empty for slot $slot" >&2
            exit 1
        fi
        runtime_file="$runtime_dir/slot-$slot.token"
        install -o appuser -g appuser -m 0400 "$source_file" "$runtime_file"
        eval "export MCP_BRIDGE_SLOT_TOKEN_FILE_$slot=\$runtime_file"
    fi
    slot=$((slot + 1))
done

exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
