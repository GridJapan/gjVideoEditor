---
paths:
  - "ui/server.py"
---

# ローカルサーバ（ui/server.py）の作法

Python **標準ライブラリのみ**（依存ゼロが配布条件）。port 8765。

## OS依存コードはファイル冒頭のヘルパーに集約する

**他所に直書きしないこと。**

- ダイアログ: `dlg_open_file` / `dlg_save_name` / `dlg_choose_dir` … Mac=osascript(本物のFinder) / Win=tkinter
  - ⚠️ tkinterはGUIツールキットなのでワーカースレッドから直接呼ばず、**別プロセスで起動**している
    （ThreadingHTTPServer化済みのため、直呼びするとサーバごと落ちうる）
- ファイルマネージャ表示: `reveal_in_file_manager` … Mac=`open -R` / Win=`explorer /select,` / Linux=`xdg-open`
- レンダラ起動: **`sys.executable`**（`"python3"` 決め打ち禁止。Winは `python`、Macもshims経由で壊れうる）

`subprocess` は `encoding="utf-8", errors="replace"` を明示する。
`text=True` だけだと日本語Windowsで cp932 デコードになり、子が書いたUTF-8の日本語エラーが
化ける／`UnicodeDecodeError` で落ちる。

## ポートを黙って掴まない

`allow_reuse_address = False`。既に使われていれば人間可読エラーで終了する（issue #4）。
「起動成功に見えるのにブラウザに何も出ない」が起きるため。

## パスを引数で受け取る経路は必ず検証する

`/api/video` `/api/thumb` などは `safe_under_projects()` を通す。
`../` での脱出を止めている（実測で `../../CLAUDE.md` `/etc/passwd` とも404）。

`/asset` は `basename` 解決 → **素材はプロジェクト直下に置く**。
サブフォルダはレンダラだけ通ってUIで見えない。

## 書き出しの鮮度判定

マスターが最新かどうかは `sources_mtime()`（project.json ＋ **参照素材すべて**の mtime の最大値）と比べる。

project.json の mtime だけを見ると、素材だけを同名で差し替えたとき
（音声の作り直し・画像の差し替え。`/api/upload` が認めている正規の運用）に
「マスターは最新」と誤判定し、**古い素材のままの動画を "成功" として返す**。

サイズ0のマスターも「古い」扱いにすること（中断で壊れたファイルが残りうる）。

## 保存の作法（データ消失の防止）

**過去に2回、プロジェクトを丸ごと壊している。**

- 保存は全経路（patch.py / server.py / align_subtitles.py）で **indent=1・アトミック書き込み** に統一。
  経路ごとに形式が違うと「実質同一なのに全行差分」の幻diffが出てgitを汚す（実際に起きた）
- 直近10世代を `<プロジェクト>/.history/` に自動退避。壊した直後なら
  `.history/project-<ミリ秒>.json` から復元できる（git未コミットでも戻せる）
- `POST /api/project?from=<プロジェクト名>&v=<版>`
  - `from` … 画面が読み込んだプロジェクト名。サーバの現在と違えば **409**
  - `v` … `GET /api/project` の `X-Project-Version` ヘッダ（mtime）。他所が更新していれば **409**

## 既定プロジェクトの選び方

`pick_default_project()` が**素材の揃ったものだけ**から選ぶ（sample-hello優先）。
素材欠けを既定にすると「ツールが壊れている」と誤解される。

## Range 対応

`/api/video` は **Range必須**（206を返す）。対応しないとシークできない。
