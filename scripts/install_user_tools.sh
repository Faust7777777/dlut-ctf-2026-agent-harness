#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GOPATH="$ROOT/tools/go"
export GOBIN="$ROOT/tools/bin"
export PATH="$GOBIN:$PATH"
mkdir -p "$GOBIN" "$GOPATH"

if ! command -v go >/dev/null 2>&1; then
  echo "go is not installed. Install golang-go via apt or add a local Go toolchain first." >&2
  exit 2
fi

go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest
go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest
go install github.com/hahwul/dalfox/v2@latest
go install github.com/ffuf/ffuf/v2@latest

echo "Installed Go tools into $GOBIN"
