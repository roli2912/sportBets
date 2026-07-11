#!/usr/bin/env bash
# Banned-strings lint (CLAUDE.md §2.4, §12).
# No profit-guarantee language anywhere in public-facing surfaces:
# the website (apps/web) and bot/agent message templates (workers/agents).
set -euo pipefail

cd "$(dirname "$0")/.."

patterns=(
  "guaranteed"
  "sure win"
  "can't lose"
  "cant lose"
  "risk-free profit"
  "risk free profit"
)

targets=()
for dir in apps/web workers/agents; do
  [ -d "$dir" ] && targets+=("$dir")
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "banned-strings: no target directories yet, nothing to check"
  exit 0
fi

status=0
for pattern in "${patterns[@]}"; do
  if matches=$(grep -rIn -i \
      --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=__pycache__ \
      -e "$pattern" "${targets[@]}" 2>/dev/null); then
    echo "BANNED STRING '$pattern' found:"
    echo "$matches"
    status=1
  fi
done

if [ $status -eq 0 ]; then
  echo "banned-strings: clean (${targets[*]})"
fi
exit $status
