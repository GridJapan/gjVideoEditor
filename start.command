#!/bin/bash
# video-editor 起動（macOS）: このファイルをダブルクリックするとサーバが立ち上がりブラウザが開く
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "❌ Python3 が見つかりません。https://www.python.org/downloads/ から入れてください"
  read -r -p "Enterで閉じます"; exit 1
fi
"$PY" -c "import PIL" 2>/dev/null || {
  echo "📦 Pillow を入れます（初回のみ）…"
  "$PY" -m pip install --quiet Pillow || "$PY" -m pip install --quiet --user Pillow || {
    echo "❌ Pillow の導入に失敗しました。次を実行してから開き直してください:"
    echo "     $PY -m pip install Pillow"
    read -r -p "Enterで閉じます"; exit 1; }
}
# 依存の状態を一覧で見せる（足りないものは入れ方つきで出る）
"$PY" tools/_deps.py
command -v ffmpeg >/dev/null || {
  echo ""
  echo "⚠️ ffmpeg が無いので、編集はできますが**書き出しができません**。"
  echo "   入れ方: brew install ffmpeg  （入れたらこのウィンドウを閉じて開き直す）"
  echo ""
  read -r -p "このまま編集だけ始めるなら Enter / やめるなら Ctrl+C "
}

# 既に起動していれば二重起動しない
if curl -s -m 1 http://localhost:8765/api/version >/dev/null 2>&1; then
  echo "✅ すでに起動しています → http://localhost:8765"
  open http://localhost:8765; exit 0
fi

echo "🎬 video-editor を起動します… (このウィンドウを閉じると終了します)"
"$PY" ui/server.py &
SRV=$!
for _ in $(seq 1 30); do
  curl -s -m 1 http://localhost:8765/api/version >/dev/null 2>&1 && break
  sleep 0.2
done
open http://localhost:8765
echo "   終了するには、このウィンドウで Ctrl+C を押すか、ウィンドウを閉じてください。"
wait $SRV
