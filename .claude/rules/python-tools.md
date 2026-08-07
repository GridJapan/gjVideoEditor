---
paths:
  - "tools/**/*.py"
  - "renderer/**/*.py"
  - "ui/**/*.py"
---

# Python スクリプトの作法（Windows配布が前提）

利用者にはWindows(日本語=cp932)がいる。**Macで動いてもWindowsで落ちる**3類型を毎回踏むので、
新しいスクリプトを書くときは最初から対策を入れる。

## 1. `open()` / `read_text()` の encoding 未指定

cp932でUTF-8を読み `UnicodeDecodeError`。**ファイルを読むときは必ず `encoding="utf-8"` を付ける。**

## 2. 絵文字を print

`✓✅❌` 等を cp932 コンソールへ出すと `UnicodeEncodeError`。
**先頭で `import _wincompat`**（`tools/_wincompat.py` が標準出力/stderrをUTF-8化する）:

```python
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402
```

## 3. Mac専用パスの決め打ち

フォントやPythonの場所を固定で書かない。日本語が豆腐(□)になる／他の人の環境で外れる。

- フォント: `renderer/render.py` の `WIN_GOTHIC` と同じくWindowsパスへフォールバック
- Python: `sys.executable` を使う。**バージョン番号を書かない**
  （`~/.pyenv/versions/3.11.9/` のような決め打ちは実際に規約違反として残っていた）

## 検証（Windows実機が無くても再現できる）

```bash
PYTHONIOENCODING=cp932 python3 -c "...TextIOWrapper(...,encoding='cp932')..."
```

有無を対比すれば再現できる。

## 外部コマンドの存在を確認してから使う

`ffmpeg` / `ffprobe` / `edge-tts` は入っていない環境がある。
`subprocess.run` を素で呼ぶと `FileNotFoundError` の生トレースバックになる。

→ `shutil.which()` で確認し、**OS別の導入コマンドを含む人間可読エラー**で止める。
「素材が見つかりません」と同じ水準の親切さに揃えること。

## 既存プロジェクトを黙って上書きしない

`project.json` を丸ごと書き直すツールは、`meta.madeBy` を見て
**自分（同じ台本）が作ったものだけ**を作り直す。他は中断して `--force` を案内する。

素材はgit外なので、上書きすると復旧が非常に面倒になる。

**実装は `tools/_guard.py` にある。自前で書かない:**

```python
from _guard import guard_overwrite, stamp
TOOL = "build_xxx.py"
guard_overwrite(pdir, TOOL, args.force)      # main の入口で
pj["meta"]["madeBy"] = {"tool": TOOL}        # 保存する dict に刻む
```

**引数を検証せず `sys.argv[1]` を使うビルダーを書かないこと。**
2026-08-07、`--help` を渡しただけでビルダーが走り、`makasete-shunin-lp` の
**BGMトラックが丸ごと消えた**（UIで後から足したもの＝台本には無い）。
`argparse` で受けてから `guard_overwrite` を通す。

## 依存の判定は `_deps` に一本化する（`sys.executable` だけを見ない）

pyenv 等では「ツールを動かす Python」と「パッケージの入った Python」が食い違う。
`_deps.python_with("edge_tts")` が PATH と pyenv 配下まで探す。**自前で探索を書かない。**

2026-08-07: `gen_voice.py` だけが探索を持ち、`_deps` は `sys.executable` しか見ていなかったため、
**`gen_voice.py` は生成できるのに `make_video.py --voice` が「入っていません」と拒否**した
（＝ templates/README.md の「まずこれを通してください」が通らない）。

## 変数名の使い回しに注意（保存が落ちる）

2026-08-07: `make_video.py` の会話型で `base = Image.open(...)` が
台本ファイル名の `base`（`meta.madeBy.script` に載る）を上書きし、
保存時に `Object of type Image is not JSON serializable` で落ちていた。
**会話型は一度も最後まで通ったことが無かった。**
`project.json` に載る値を組み立てる変数は、途中で別用途に使い回さない。

## `_wincompat` は `_deps` より先に読む

順序が逆だと、依存不足の案内そのものが cp932 の `UnicodeEncodeError` で消える
（＝親切エラーの仕組みが、いちばん必要な場面で機能しない）。
`_deps.py` 自身も `_wincompat` を読むようにしてあるので通常は事故らないが、
新しく書くときも `_wincompat` を先頭に置くこと。

## 実在のプロジェクト名を決め打ちしない

フォールバック値・既定値・プレースホルダに**特定のプロジェクト名を書かない**。
そのプロジェクトが消えると壊れるうえ、他人に渡すときに邪魔になる。

```python
# ❌ 特定の名前を返す
# ✅ 実在するものを走査して返す。無ければ sample-hello
```


## 保存はアトミックに

`.tmp` に書いて `os.replace()`。indent=1。
（全経路で形式を揃える。揃っていないと幻diffでgitが汚れる）
