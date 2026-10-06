#!/bin/zsh
# A night's worth, cut and uploaded private, ready for you to look at in the morning.
# Point a launchd job or a cron line at this.
set -e
DAY=$(date -v-1d +%F)                      # last night; on Linux: date -d yesterday +%F
OUT="$HOME/Movies/Cutaway/$DAY.mp4"

cutaway build --night "$DAY" -o "$OUT" --seconds 28 --cut-len 3.2 || {
  echo "nothing worth cutting from $DAY"; exit 0; }

cutaway upload "$OUT" \
  --title "$DAY" \
  --desc "$(cat "$HOME/.config/cutaway/description.txt" 2>/dev/null)" \
  --private
