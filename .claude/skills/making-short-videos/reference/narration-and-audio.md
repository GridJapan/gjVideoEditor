# ナレーション・BGM・効果音

## 目次

- ナレーション生成（tools/gen_voice.py）
- APIキーの扱い（`zsh -l -c` で包む理由）
- BGM
- 効果音の置き方と音量
- フィラー・間のカット手法

## ナレーション生成（tools/gen_voice.py）

```bash
python3 tools/gen_voice.py --list                      # ボイス一覧
python3 tools/gen_voice.py --list-eleven               # ElevenLabsアカウントのボイス（要キー）
python3 tools/gen_voice.py --spec <台本.json> --outdir <出力先>
```

```jsonc
// 台本(spec)。max_dur を書くと尺超過を警告する（字幕枠に収まらない事故の防止）
{"segments":[{"id":"L1","voice":"hisako-genki","text":"…","tts_text":"カナ調整","max_dur":6.0}]}

// セグメント側で声・演技をpresetから上書きできる（1行だけ早口に/テンションを上げる 等）
{"segments":[{"id":"L2","voice":"hisako","rate":"+30%","settings":{"style":0.8},"text":"…"}]}
//   上書き可: ref(声ID) / rate・pitch(edge) / settings・model(eleven)
```

- エンジン: `edge`（無料・キー不要・ja-JP-Keita/Nanami）/ `eleven`（要 `ELEVENLABS_API_KEY`）/
  `say`（macOS・低品質）
- `tools/voices.json` にプリセット。`fx:"robot"` でAI/ロボ声、
  `settings` で `stability`↓ `style`↑ `speed`↑＝元気に
- **これが正。** 他所にある `gen_tts.py` 類（ElevenLabs単一ボイス決め打ち・`requests`依存の旧版）は
  使わない。gen_voice.py が機能上の上位互換（複数エンジン/複数ボイス/fx/標準libのみ/max_dur）

## APIキーの扱い

- **キーは環境変数のみ**（`~/.zprofile`）。ファイルに書かない・探しにいかない
- ⚠️ **`~/.zprofile` はログインシェルしか読まない。** エージェントのbashは非ログインシェルなので
  `ELEVENLABS_API_KEY` が**見えない**。eleven系を使うときは **`zsh -l -c '…'` で包む**
  （「キーを入れたのに動かない」の正体はこれ。キーを探しに行かないこと）
- `.env` にキーを置かない。**`.env` は環境変数より優先されるため、古いキーを掴み続ける事故になる**
- 引数値が `-` で始まる場合は `--pitch=-10Hz` と等号で渡す（argparseがフラグ誤認する）

## BGM

**出所とライセンスを必ず記録してから使う。** 記録の無い音源はリポジトリに入れない
（配布物に含めると再配布になる）。在庫表を作り、ファイル名・原題・作者・ライセンス・
入手日を残すこと。

- **ループ対応版があるならそれを選ぶ**（前後に無音が無く、繋いでも切れ目が出ない）
- 配布サイトの利用規約を読む。**自動収集（スクレイピング）を禁止している所が多い。**
  素材を足すときはブラウザで人が選んで個別にDLする。一括収集スクリプトは作らない
- 動画の系統ごとに曲を変える（同じ曲だと耳では同じシリーズに聞こえる）


## 効果音の出所（再配布禁止に注意）

`assets/kit/` の効果音は**効果音ラボ**（https://soundeffect-lab.info/）から取得。
在庫と元ファイル名は `assets/kit/CREDITS.md`。

| | |
|---|---|
| 商用利用 | ✅ 無料・クレジット表記不要 |
| 動画への埋め込み | ✅ 可（音が主役でない補助的な使い方であること） |
| **素材ファイルの再配布** | ❌ **禁止** |

⚠️ **mp3ファイルを配布物に入れないこと。** 動画に組み込むのは可。
禁止用途: アダルト・違法行為・**AIの学習**・Content ID登録・**効果音自体を聞かせる動画**。

## 効果音を必ず入れる

`sfx` トラックを作らないと**無音のまま完成扱いになる**（2026-07-21: SNS系2本を効果音なしで出した）。

置き方（テンポ重視で少し多め・音量は控えめ）:

| タイミング | 音 | gain |
|---|---|---|
| 冒頭フック | text-impact | 0.22 |
| 場面転換（画像が変わる） | whoosh / sceneswitch を交互 | 0.14 |
| 数字を出すカット | kira | 0.15 |
| 締め | decision | 0.20 |

- **出だしのドーン（text-impact等）は gain 0.4 が上限目安。** 等倍(1.0)は「デカすぎて耳障り」（実害）
- 装飾音（kira等）はさらに控えめに
- ⚠️ **同一トラック内でクリップを重ねない。** 重なると検査に弾かれ、音も食い合う。
  直前のSEが鳴り終わっていなければ置かずに間引く（`se()` にガードを実装済み）

## フィラー・間のカット手法

1. whisper で `word_timestamps=True` の文字起こし
   - **Mac(Apple Silicon)**: `mlx_whisper`（最速）
   - **Windows/その他**: `openai-whisper` か `faster-whisper`（**mlxはApple Silicon専用**）
   - json置き場は `/tmp/wh`（Mac/Linux）または `%TEMP%\wh`（Win）。`tools/align_subtitles.py` が両方探す
2. **whisperはフィラー（え、えー、あの）を書き起こさない** → **単語と単語の隙間＝フィラー＋間**
3. 隙間 > 0.28秒 を対象に、単語の前後 0.06秒を残して詰める
4. 無音検出（silencedetect）では**フィラーは取れない**（発話なので無音でない）

**多数の細かいカットに `cuts` を使わない。** タイムライン全高に赤帯＋✂が並び、クリップ選択も妨げる。
**クリップを分割して詰める**（`in=` を持つクリップを並べる）方式にする。
