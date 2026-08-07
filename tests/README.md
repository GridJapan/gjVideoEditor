# 総当りQA

**すべて実測で判定する。** 「例外が出なかった＝合格」にはしない
（書き出しは無言で壊れる。フレームの画素と音声のdB値を見る）。

```bash
python3 tests/qa_render.py          # レンダラ 40項目（3〜5分）
python3 tests/qa_patch.py           # patch.py 32項目（1分）
VE_PORT=8790 python3 ui/server.py projects/_qa_p &   # 先にテスト用サーバを起こす
python3 tests/qa_server.py          # サーバAPI 31項目（2分）
```

| ファイル | 何を見るか |
|---|---|
| `qa_render.py` | 画像(角丸/回転/フェード/不透明度/パン)・字幕(pop/countup/typewriter/slide/bar/強調/影)・マスク4種・z-order・映像(in/速度/crop/ワイプ/角丸/gain/分離/回転/フェード)・ダッキング・cuts・注入攻撃・素材欠落・空タイムライン |
| `qa_patch.py` | 全op(set/shift/retime/delete/add/ripple/move/addtrack/setroot)・セレクタ(id/label部分一致/絵文字無視/index/`*`/match)・検証(重なり/ソース長/loop免除/start>=end)・警告(豆腐/コントラスト/素材未配置)・SRT入出力・不正入力・cp932 |
| `qa_server.py` | 全エンドポイントの正常系と異常系（パストラバーサル・版ずれ409・Origin検査・書き出し全形式・範囲指定） |

- 作業用の `projects/_qa_*` は**テストが作る**。git管理外。終わったら消してよい
- ⚠️ **本番プロジェクトを引数に取らないこと。** ビルダー系は上書きしうる
  （`tools/_guard.py` が止めるが、`--force` を付けると通る）

## UI（ブラウザ実機）

`tests/` に置いていない。アプリ内ブラウザで `/?p=_qa_p` を開き、
`splitAt()` / `duplicateSel()` / `pasteClip()` / `applyFx(...)` / `applyCanvasSize(...)` /
`renderInspector()` を直接呼んで、`proj` の状態と DOM を検査する。
確認済み項目は 2026-08-07 のセッション記録を参照。
