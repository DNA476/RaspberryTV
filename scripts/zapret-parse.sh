#!/usr/bin/env bash
# Parse trusted, pinned strategies with the unchanged upstream parser.
# Do not use set -e: upstream functions use grep as optional predicates.
set -o pipefail
base="${RASPBERRY_TV_ZAPRET_BASE:-/opt/raspberry-tv-zapret}"
source "$base/adapter/src/lib/common.sh"
USE_GAME_FILTER_TCP="$2"
USE_GAME_FILTER_UDP="$3"
USE_GAME_FILTER=false
if $USE_GAME_FILTER_TCP || $USE_GAME_FILTER_UDP; then USE_GAME_FILTER=true; fi
parse_bat_file "$1"
printf '%s\0' "$tcp_ports" "$udp_ports" "${nfqws_params[@]}"
