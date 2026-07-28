---
name: making-short-videos
description: video-editor で縦型ショート動画を作る・直す・書き出す。会議録画やネタから動画を起こす、ループ動画を作る、既存動画の演出（モーション・字幕・エフェクト・効果音）を直すときに使う。「動画作って」「ショート作って」「ループ動画作って」「この話を動画にして」「録画から切り抜いて」「動画のここ直して」「テロップ入れて」と言われたら起動する。演出の作法と型を提供し、成果物は projects/<名>/out/<title>.mp4 として出す。
---

# ショート動画を作る

video-editor リポジトリのルートで作業する（そこで作業すると `CLAUDE.md` が自動で入る）。

## 作り方は3通り。上から順に試す

| やり方 | いつ使う | 入口 |
|---|---|---|
| **テンプレート** | 既存の型で新しい回を作る。**まずこれ** | `python3 tools/make_video.py 台本.json --voice --render` |
| 個別ビルダー | 型に無い構成を新しく作るとき | `tools/build_*.py` を書く |
| patch.py / UI | できた動画の微調整 | `python3 tools/patch.py <名> --ops '...'` |

**新規プロジェクトは型を継承してから作る:**

```bash
python3 tools/new_project.py <新名> --from <既存名> --dur 40
```

トラック構成・全編クリップ（ロゴ/帯/背景/BGM）・style・素材キットが入った状態で来る。
中身（紙芝居・字幕・ワイプ・効果音）は空なので、ビルダー→patch.py で組む。

## 作業の順番

```
1. 素材を決める（録画の区間 or 画像生成）
2. ナレーション音声を先に作る   → reference/narration-and-audio.md
3. 音の実測尺からシーンの長さを決める
4. 画・字幕を置く               → reference/layout-and-composition.md
5. 動きと効果音を足す           → reference/motion-and-effects.md
6. 書き出して目で確認する
```

**音を先に作る。** 尺は音が決める。絵から作ると必ず合わなくなる。

会議録画などの長尺素材から作るときは 1 の前に:
文字起こし（Mac は `mlx_whisper` の `word_timestamps=True`）→ 実発言のタイムスタンプから
カット点を選ぶ。**言っていないことをテロップにしない。**

## 詳細（必要になったものだけ読む）

| 読むもの | 何が書いてあるか |
|---|---|
| [reference/motion-and-effects.md](reference/motion-and-effects.md) | モーションの流儀（**動かしすぎて「酔う」実害あり**）・集中線・ポップ登場・エフェクトの設計 |
| [reference/layout-and-composition.md](reference/layout-and-composition.md) | 40秒級の骨格・レイアウト4型・字幕の位置・強調の付け方・孤立行 |
| [reference/narration-and-audio.md](reference/narration-and-audio.md) | gen_voice.py の使い方・BGM・効果音の置き方と音量・フィラーカット |
| [reference/loop-videos.md](reference/loop-videos.md) | ループ動画の作法（**ループ点の置き方が最重要**）・繋ぎ目の実測検証 |
| [reference/defaults.md](reference/defaults.md) | 既定値（canvas/字幕/画像/ワイプ）・learn.py で人の手直しを読む |

## 必ず守ること

- **ファクト規律**: テロップ化する数字・固有名詞・製品名は、**出典の原文**と**一次ソース**の
  両方で確認してから使う（実在企業・製品の数値を誤って出さない）
- **社外秘**: 会議録画の画面共有には内部資料・コード・メールが映る。
  **webカメラ部分だけを切り出して使う**。区間ごとにレイアウトが変わるので、
  使う区間は必ず先に数フレーム抜いて確認する（手順は reference/loop-videos.md の「素材の安全確認」）
- **ロゴ・QRコードは生成しない。** 正規アセットから切り出して流用する。
  生成AIに描かせるとスキャンできない偽物になる
- **画像生成に文字を描かせない。** テロップは動画側で載せる
- **初回組み立てだけビルダー。以降の修正は必ず `patch.py`**（ビルダーを書き直すとUIでの手直しが消える）

## 完成したら

```bash
python3 tools/patch.py <名> --check          # 鉄則違反の検査
python3 renderer/render.py projects/<名>     # 書き出し
open projects/<名>/out/<名>.mp4              # 開いて見せる
```

- **書き出したフレームを実際に見て確かめる**（プレビューだけで判断しない）
- 場所の文字報告だけで終わらせず `open` で開いて渡す
- 他の人に渡すときは UI の「📦 zip書き出し」（`.veproj.zip`）。
  **git には素材が入らない**ので project.json だけ渡しても再生できない
