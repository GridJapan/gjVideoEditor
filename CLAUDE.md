# video-editor の規約

**自然言語で作り、UIで仕上げる**投稿動画づくりの内製ツール。実装は素のPython＋HTML1枚（依存ゼロ）。

<!-- 本書は毎セッション全文がコンテキストに入る。200行を上限とし、超えそうなら
     .claude/rules/（該当ファイルを触るときだけロード）か
     .claude/skills/（その作業をするときだけロード）へ出すこと。
     ここに書くのは「どのセッションでも必要な事実」だけ。 -->

## どこに何が書いてあるか

| 探しているもの | 場所 | いつ読まれるか |
|---|---|---|
| 動画の作り方・演出の作法 | `.claude/skills/making-short-videos/` | 「動画作って」等で自動 |
| 編集UIを直すときの作法 | `.claude/rules/ui-editor.md` | `ui/editor.html` を開くと自動 |
| レンダラを直すときの作法 | `.claude/rules/renderer.md` | `renderer/render.py` を開くと自動 |
| サーバを直すときの作法 | `.claude/rules/server.md` | `ui/server.py` を開くと自動 |
| Pythonスクリプトの作法 | `.claude/rules/python-tools.md` | `tools/**/*.py` 等を開くと自動 |
| project.json の全キー定義 | `schema/project.schema.json` | 手で読む（**正はこれ**） |
| 公開版へ反映するときの注意 | `public/README.md` | 手で読む（公開作業のとき） |

**同じことを二重に書かない。** 実体は上のどれか1か所だけに置く。

## 全体像

```
project.json（Single Source of Truth）
   ├─ renderer/render.py  → MP4 書き出し（Pillow + ffmpeg）
   ├─ ui/server.py        → ローカルサーバ（Python標準ライブラリのみ・port 8765）
   └─ ui/editor.html      → 編集UI（素のHTML/JS 1枚）
tools/gen_voice.py + tools/voices.json → ナレーション/セリフ生成（複数エンジン）
```

```bash
python3 renderer/render.py projects/<名>      # → projects/<名>/out/<title>.mp4
```

- **素材はプロジェクト直下に置く**（`/asset` が `basename` で解決するため、サブフォルダはUIで見えない）
- 人が使うときは `start.command`（Mac）/ `launch.bat`（Windows）をダブルクリック
- 必要なもの: Python3 ／ Pillow（初回に自動導入）／ ffmpeg ／ ナレーションを使うなら `pip install -r requirements.txt`

## サーバの扱い（Claude Code）

- devサーバは `.claude/launch.json` の `video-editor` を **preview_start で起動**する。
  `python3 ui/server.py` の手動起動はしない（ポート8765が競合し、アプリ内ブラウザが固まる）
- **⚠️ 動いているサーバを止めない。** `preview_stop` はユーザーのエディタを殺す（＝アプリ本体を落とす）。
  再起動が要るときは**必ず先に断る**。再起動後は `/api/open?name=<名>` で元のプロジェクトを開き直す

## 修正は `tools/patch.py` で当てる（ビルダーを書き直さない）

**思想は「8割は自然言語で自動生成、2割の微調整はUI」。両者が同じ `project.json` を書くので喧嘩しない。**
だからAIは**ビルダーを毎回書き直してはいけない**（UIでの手直しを丸ごと潰すため）。差分パッチを当てる。

```bash
python3 tools/patch.py <project> --show                 # 構造をID付きで読む（対象特定用）
python3 tools/patch.py <project> --ops '<JSON配列>'      # 差分を当てる（鉄則を検証してから保存）
python3 tools/patch.py <project> --ops '...' --dry-run  # 差分だけ確認
python3 tools/patch.py <project> --check                # 現状が鉄則に違反していないか検査
python3 tools/patch.py <project> --srt                  # 字幕を out/<名>.srt へ書き出し
python3 tools/patch.py <project> --srt-import 字幕.srt   # 外部SRTを取り込み（id=srtの新トラック）
```

op: `set` / `shift` / `retime` / `delete` / `add` / `ripple` / `move`（別トラックへ）/ `addtrack` / `setroot`
セレクタ: `"track"` は id か label（部分一致・絵文字無視）、`"clip"` は index / `"*"` / `{"match":{"src":"k3.png"}}`

```jsonc
[{"op":"set","track":"wipe","clip":"*","set":{"x":0.03,"y":0.60}}]   // ワイプを左下に
[{"op":"shift","track":"cap","clip":2,"by":-1.0}]                    // 3つ目の字幕を1秒早く
[{"op":"ripple","track":"cap","from":12.0,"by":-0.5}]                // 12秒以降を0.5秒詰める
```

- **clip の index は `--show` が表示する番号そのもの**（生配列の位置）。ズレが怖ければ `{"match":{...}}`
- **`setroot` でトップレベルキー（style/audio/canvas/meta/cuts）を編集できる**。生pythonで書き換えない
- 検証は **errors（保存拒否）/ warnings（保存続行）** の2段階。素材の未配置は warning
- **`schema/project.schema.json` と自動照合する。キーを増やしたら必ずschemaも更新すること**
  （schemaは飾りではなく patch.py が実際に読む）

**鉄則はこのツールが強制する**ので、AIが覚えている必要はない:
重なり禁止 / ソース長超過の禁止 / start<end / 素材の実在 / `_zorder` 必須 /
**字幕がフォントで描けるか（豆腐□の事前検出）** / **字幕のコントラスト**。

## project.json の要点

**全キーの定義は `schema/project.schema.json` が正。** 以下は特に事故りやすい箇所だけ。

```jsonc
{
 "meta":{"title":"<フォルダ名と合わせる>","fps":30},
 "canvas":{"w":1080,"h":1920,"bg":"#141b26"},
 "_zorder":true,      // これが無いと旧データ移行で並べ替えられる
 "tracks":[ /* 前面 → 背面 の順 */ ]
}
```

### 重ね順（z-order）— 最重要

**配列の先頭ほど前面。** 種別（映像/画像/字幕）は無関係で、**トラックの並びだけ**で決まる。

```
tracks: [字幕, ワイプ(video), 帯(image), 紙芝居(image), 背景(image), 音声…]
         ↑最前面                                    ↑最背面
```

レンダラ・タイムライン・プレビューの**3者が同じルール**。

### クリップの鉄則

- **1トラック内でクリップを重ねない。** 重ねたい要素は**別トラックに分ける**
  （違反すると長いクリップが他を覆い、タイムライン上で個別選択できなくなる）
- **映像は非破壊。長い元動画1本を `in=`（ソース内オフセット）で参照する。**
  区間ごとに短いmp4を切り出すと**クリップの外に素材が無くなり、端を伸ばせない＝編集ツールとして破綻する**。
  ジャンプカットは「同じソースを指すクリップを複数並べる」で表現する
- `end` は `start + (ソース長 - in)` を超えられない
- `in` は**映像と音声の両方**で意味を持つ（片方だけ扱うとズレる）

## 書き出しサイズの変更

プレビュー下の `1080×1920` をクリックして変更する。

- **`x/y/w/h` は正規化(0〜1)なので自動で追従する。** 追従しないのは**px指定の値だけ**——
  `fontsize` / `outlineWidth` / `style.marginBottom` / `style.box.pad`・`radius`
- **px指定のプロパティを新しく足したら `scalePxValues()` にも足すこと**（足し忘れると取り残される）
- 幅・高さは**偶数に丸める**（ffmpegのH.264が奇数を嫌う）

## 配布（clone する人がいる前提）

- **gitは素材を持たない**（`.gitignore` で png/mp3/mp4 を除外）。
  **`projects/sample-hello/` だけが例外**で素材ごと追跡している（約90KB）。
  clone直後に「動くもの」が1つも無いと初回起動が壊れた画面になるため
- **サンプルの素材は `tools/make_sample.py` が生成したもの**（PILの図形＋ffmpegの合成音）。
  作り直しは `python3 tools/make_sample.py`。
  **出所不明の素材をここに置かないこと**（公開版へそのまま流れて再配布できなくなる）
- **`.gitignore` の例外に他のプロジェクトを足さないこと**（動画・BGMで一気に肥大化する）。
  受け渡しは **`.veproj.zip`**（UIの「📦 zip書き出し」）で行う
- **素材が無い時は黙って進めない。** 人間可読エラーで止める。
  **「✅成功」と表示して壊れたものを作るのが最悪**

## 落とし穴（実際に踏んだもの）

- **プレビューと書き出しの不一致**は繰り返し起きる。映像に `x/y` 等を足したらプレビューにも実装する
- 同名で素材を上書きすると**ブラウザキャッシュで古い絵/音が出る**。
  URLは `assetUrl()` を通す（`/asset?name=` の直書きをしない）。**素材を差し替えたら開き直す**
- **codex は生成後の `cp` に失敗することがある。** ログに出る
  `~/.codex/generated_images/<session>/<id>.png` を回収する。プロンプトに `**` を入れると固まる

## 書き足すときのルール

**ここに書かれた鉄則は、実際に事故ってから追加されている。** 新しく足すときは:

1. **まず置き場所を選ぶ** — 特定ファイルを触るときだけ要る → `.claude/rules/`。
   動画を作るときだけ要る → `.claude/skills/making-short-videos/`。
   **どのセッションでも要る場合だけ本書へ**
2. 「なぜダメか」を1行添える（実害があったなら日付と症状）
3. 本書が200行を超えたら、超えた分ではなく**一番セッション頻度が低い節**を出す
