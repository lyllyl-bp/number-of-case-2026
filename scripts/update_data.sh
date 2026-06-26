#!/usr/bin/env bash
set -euo pipefail

python3 scripts/update_wc_data.py --file data/matches.js --competition WC --season 2026
