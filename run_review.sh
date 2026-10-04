#!/usr/bin/env bash
# Запуск агента перевірки робіт: збирання -> оцінювання моделлю -> звіт.
# Вручну:      ./run_review.sh
# За розкладом (cron, щодня о 20:00):
#   0 20 * * * /ПОВНИЙ/ШЛЯХ/до/проєкту/run_review.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi

mkdir -p logs
LOG="logs/run-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "== $(date '+%d.%m.%Y %H:%M') Збирання нових робіт"
OUT="$("$PY" review.py fetch)" || { echo "$OUT"; echo "Збирання не вдалося, див. повідомлення вище."; exit 1; }
echo "$OUT"
RUN_DIR="$(printf '%s\n' "$OUT" | sed -n 's/^RUN_DIR=//p')"
ITEMS="$("$PY" -c "import json,sys; print(len(json.load(open(sys.argv[1]))['items']))" "$RUN_DIR/manifest.json")"
if [ "$ITEMS" = "0" ]; then
  echo "Нових зданих робіт немає."
  exit 0
fi

if ls "$RUN_DIR"/packets/*.json >/dev/null 2>&1; then
  echo "== Оцінювання (claude -p)"
  claude -p "Виконай інструкції з AGENT.md. Тека запуску: $RUN_DIR" \
    --permission-mode dontAsk \
    --allowedTools "Read(./AGENT.md)" "Read(./criteria/**)" "Read(./$RUN_DIR/packets/**)" \
                   "Read(./$RUN_DIR/files/**)" "Glob" "Write(./$RUN_DIR/results/**)" \
    --disallowedTools "Bash" "WebFetch" "WebSearch" \
                      "Read(./token.json)" "Read(./credentials.json)" "Read(./state.json)" \
                      "Read(./config.yaml)" "Read(./$RUN_DIR/manifest.json)"
fi

echo "== Оновлення звіту"
"$PY" review.py apply --run "$RUN_DIR"
