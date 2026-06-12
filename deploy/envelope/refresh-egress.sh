#!/usr/bin/env bash
# refresh-egress.sh — resolve the allowlisted hostnames into the nftables
# sets that gate the ubongo user's egress (ADR-0017). Run as root, on the
# ubongo-egress-refresh.timer (and once at install).
#
# Source of truth: /etc/ubongo/egress.hosts — one hostname per line, '#'
# comments allowed. By design this file is the ENUMERABLE answer to "what can
# leave the machine": openrouter.ai ships as the default; add each MCP
# server's host when you enable it in settings.yaml (the trust protocol wants
# that addition to be a deliberate, visible act, not an inference).
#
# Failure posture: if a hostname stops resolving, its previous addresses are
# kept (we never fail open to "no firewall"); if ALL resolution fails, the
# sets are left untouched and we exit non-zero so the timer unit logs it.

set -euo pipefail

HOSTS_FILE="${UBONGO_EGRESS_HOSTS:-/etc/ubongo/egress.hosts}"
TABLE="inet ubongo_egress"

if [[ ! -r "$HOSTS_FILE" ]]; then
    echo "refresh-egress: hosts file $HOSTS_FILE missing or unreadable" >&2
    exit 1
fi

mapfile -t hosts < <(grep -vE '^\s*(#|$)' "$HOSTS_FILE" | tr -d ' \t')
if [[ ${#hosts[@]} -eq 0 ]]; then
    echo "refresh-egress: $HOSTS_FILE lists no hosts; sets left untouched" >&2
    exit 1
fi

v4=()
v6=()
failed=0
for host in "${hosts[@]}"; do
    # getent pulls both families through the system resolver (NSS-aware).
    addrs="$(getent ahosts "$host" | awk '{print $1}' | sort -u)" || addrs=""
    if [[ -z "$addrs" ]]; then
        echo "refresh-egress: could not resolve $host (keeping previous addresses)" >&2
        failed=1
        continue
    fi
    while IFS= read -r a; do
        if [[ "$a" == *:* ]]; then v6+=("$a"); else v4+=("$a"); fi
    done <<< "$addrs"
done

if [[ ${#v4[@]} -eq 0 && ${#v6[@]} -eq 0 ]]; then
    echo "refresh-egress: nothing resolved; sets left untouched" >&2
    exit 1
fi

# Atomic swap: flush + repopulate inside one nft transaction.
{
    echo "flush set $TABLE allow4"
    echo "flush set $TABLE allow6"
    if [[ ${#v4[@]} -gt 0 ]]; then
        printf 'add element %s allow4 { %s }\n' "$TABLE" "$(IFS=,; echo "${v4[*]}")"
    fi
    if [[ ${#v6[@]} -gt 0 ]]; then
        printf 'add element %s allow6 { %s }\n' "$TABLE" "$(IFS=,; echo "${v6[*]}")"
    fi
} | nft -f -

echo "refresh-egress: ${#v4[@]} IPv4 + ${#v6[@]} IPv6 addresses for ${#hosts[@]} host(s) from $HOSTS_FILE"
exit "$failed"
