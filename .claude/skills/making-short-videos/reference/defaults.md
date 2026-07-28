# 既定値と、人の手直しから学ぶ

## 目次

- 既定値（指示が無ければこの値）
- 人がUIで直した所を読む（tools/learn.py）
- テンプレートを増やすとき

## 既定値

`learn.py --conventions` が実データから抽出した作法（2026-07-16時点）。
**指示が無ければこの値を使う。** ◎は複数プロジェクトで8割超一致＝迷わず採用。

```jsonc
canvas   : 1080x1920（縦。横1920x1080・正方1080x1080は明示指定時のみ）
字幕     : align:"center" ◎ / bold:true ◎ / font:"Hiragino Kaku Gothic Pro" ◎
           outline:true ◎ / shadow:true ◎ / textColor:[255,255,255]（強調は[255,214,80]）
           valign:"middle"（説明字幕は"top"も可） / fontsize:52〜64（内容で調整・固定値なし）
画像     : w:1, x:0 ◎（＝全幅・左端。紙芝居/背景はこれ）
ワイプ   : x:0.685, y:0.02（＝右上）／scale:0.28前後
```

**fontsize と highlightColor は動画ごとにばらついている＝作法が無い**ので、勝手に統一せず都度決める。

※ 新規動画の標準レイアウト（ロゴ右上・円ワイプ右下）は layout-and-composition.md を参照。
上記ワイプ値は旧配置を含む統計値。

## 人がUIで直した所を読む（tools/learn.py）

`patch.py` が「AIが書く」側なら、こちらは **人の手直しをAIが読む**側。
**同じop語彙**で出るので、理解・再現・一般化がそのままできる。

```bash
python3 tools/learn.py <project>               # 直近コミット→いま（＝人がUIで直した内容）
python3 tools/learn.py <project> --since <rev> # 任意の版から
python3 tools/learn.py <project> --ops         # 差分を patch.py 用JSONで出す（他プロジェクトへ再現）
python3 tools/learn.py --conventions           # 全プロジェクト横断で"癖"を統計抽出
python3 tools/learn.py --emit-template         # ◎の作法だけを patch.py 用 --ops JSON で出す
```

`--emit-template` は新規プロジェクトへ既定値を流し込む用。track名を実際のidに直して
`patch.py --ops` へ渡す（`new_project.py` で型を継承した直後に使うと早い）。

**AIの作ったものが人に直されたら、必ず `learn.py` で何を直されたか読むこと。**
同じ直しが繰り返されているなら、それは**AIが既定値を間違えている**ので、上の「既定値」に書き戻す。

## テンプレートを増やすとき

`templates/<名前>.json` を足し、`make_video.py` の `BUILDERS` に組み立て関数を登録する。

追加したら**既存動画を再生成して中身が変わらないこと**を確認する（型が壊れていない証明）。

⚠️ `git diff` はキーの並び順の違いも拾うので**判定に使わない**。値で比較する:

```bash
cp projects/<名>/project.json /tmp/before.json
python3 tools/make_video.py projects/<名>/script.json
python3 -c "
import json
a=json.load(open('/tmp/before.json')); b=json.load(open('projects/<名>/project.json'))
n=lambda p:{t['id']:t['clips'] for t in p['tracks']}
d=[k for k in set(n(a))|set(n(b)) if n(a).get(k)!=n(b).get(k)]
print('✅ 一致' if not d else f'⚠️ 差分 {d}')"
```

⚠️ レイアウト辞書の同一判定は `is` ではなく `==`（`strip_doc()` が毎回新しい辞書を返すため、
`is` だと同じ画像が別クリップに分かれる。2026-07-21実害）。
