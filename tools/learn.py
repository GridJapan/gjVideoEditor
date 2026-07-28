#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project.json 差分リーダー — 「人がUIで直した作法」をAIに学習させる層（Phase 4 後半）

思想:
  patch.py は「AIが書く」側。こちらは **人がUIで直した結果をAIが読む** 側。
  人の手直しを patch.py と**同じop語彙**で出すので、AIはそれを
    (1) 何をされたか理解でき  (2) そのまま別プロジェクトに再現でき
    (3) 繰り返されている癖を「作法」として AGENTS.md に書き戻せる。

  ＝「AIが作る → 人が直す → AIが直しを学ぶ → 次から直されない」のループを閉じる。

使い方:
  python3 tools/learn.py <project>                  # 直近コミット→いまの差分を読む（人の手直し）
  python3 tools/learn.py <project> --since <rev>    # 任意のリビジョンから
  python3 tools/learn.py <project> --ops            # 差分を patch.py 用 --ops JSON で出す（再現用）
  python3 tools/learn.py --conventions              # 全プロジェクトを走査して"癖"を統計で出す
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import argparse, json, os, subprocess, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 差分を人語にするときのラベルと、丸め桁
LABELS = {
    "start": "開始", "end": "終了", "in": "ソース内位置", "x": "X", "y": "Y", "w": "幅", "h": "高さ",
    "scale": "拡大率", "opacity": "不透明度", "text": "文言", "fontsize": "文字サイズ", "font": "フォント",
    "align": "横揃え", "valign": "縦揃え", "textColor": "文字色", "highlight": "背景",
    "highlightColor": "背景色", "outline": "縁取り", "outlineColor": "縁色", "shadow": "影",
    "bold": "太字", "crop": "切り抜き", "fadeIn": "フェードイン", "fadeOut": "フェードアウト",
    "volume": "音量", "src": "素材",
}
# 「作法」として意味のあるキーだけ統計する（start/end等は動画ごとに違って当然なので除く）
STYLE_KEYS = ["fontsize", "font", "align", "valign", "textColor", "highlightColor",
              "outline", "outlineColor", "shadow", "bold", "w", "x", "y"]


def die(msg):
    print("❌ " + msg, file=sys.stderr); sys.exit(1)


def git(args, cwd=ROOT):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def rel(pdir):
    return os.path.relpath(os.path.join(pdir, "project.json"), ROOT)


def load_rev(pdir, rev):
    """指定リビジョンの project.json を読む（無ければ None）"""
    out = git(["show", f"{rev}:{rel(pdir)}"])
    if out is None: return None
    try: return json.loads(out)
    except json.JSONDecodeError: return None


def key_of(c):
    """クリップの同一性キー。UIで時刻を動かされても追えるよう src/text を優先する"""
    if c.get("src"): return ("src", os.path.basename(c["src"]), round(c.get("in", 0), 2))
    if c.get("text"): return ("text", c["text"][:30])
    return ("t", round(c.get("start", 0), 2))


def selector(c, track):
    """patch.py に渡して**そのクリップ1つだけ**に当たるセレクタを作る。
    src だけの match は、同一ソースを in= 参照するジャンプカット群に全部ヒットしてしまうため、
    トラック内で一意になるまで in / text / start を足す。一意にできなければ index を使う。"""
    cands = []
    if c.get("src") is not None:
        cands.append({"src": c["src"]})
        if c.get("in") is not None:
            cands.append({"src": c["src"], "in": c["in"]})
    if c.get("text"):
        cands.append({"text": c["text"]})
        cands.append({"text": c["text"], "start": c.get("start")})
    cands.append({"start": c.get("start")})
    if c.get("src") is not None:
        cands.append({"src": c["src"], "start": c.get("start")})
    for m in cands:
        if sum(1 for x in track["clips"] if all(x.get(k) == v for k, v in m.items())) == 1:
            return {"match": m}
    return track["clips"].index(c)  # 一意にできない（完全同値が複数）→ 生配列index


def index_clips(clips):
    """key_of は同素材・同inのクリップで衝突する（効果音の使い回し等）。
    出現順の連番を足して一意にし、版をまたいでも順序で対応づける。"""
    out, seen = {}, Counter()
    for c in sorted(clips, key=lambda x: x.get("start", 0)):
        k = key_of(c); seen[k] += 1
        out[k + (seen[k],)] = c
    return out


def fmt(v):
    if isinstance(v, float): return f"{v:g}"
    if isinstance(v, str): return repr(v.replace("\n", "/")[:24])
    return str(v)


def diff_project(a, b):
    """旧a→新b。(人語ログ, patch.py用ops) を返す"""
    log, ops = [], []
    ta = {t.get("id") or t.get("label"): t for t in a.get("tracks", [])}
    tb = {t.get("id") or t.get("label"): t for t in b.get("tracks", [])}

    for k in tb.keys() - ta.keys():
        log.append(f'＋ トラック追加: {tb[k].get("label")!r} (type={tb[k]["type"]}, clips={len(tb[k]["clips"])})')
    for k in ta.keys() - tb.keys():
        log.append(f'－ トラック削除: {ta[k].get("label")!r}')

    # トラックの並び＝重ね順。変わっていれば最優先で報告する
    oa = [t.get("id") or t.get("label") for t in a.get("tracks", [])]
    ob = [t.get("id") or t.get("label") for t in b.get("tracks", [])]
    if [x for x in oa if x in ob] != [x for x in ob if x in oa]:
        log.append(f'⇅ 重ね順が変更された（前面→背面）: {" > ".join(str(x) for x in ob)}')

    for k in ob:
        if k not in ta: continue
        A, B = index_clips(ta[k]["clips"]), index_clips(tb[k]["clips"])
        label = tb[k].get("label")
        for ck in B.keys() - A.keys():
            c = B[ck]
            log.append(f'＋ [{label}] クリップ追加 {c.get("start",0):.2f}-{c.get("end",0):.2f}'
                       f'{" " + fmt(c.get("text") or c.get("src") or "")}')
            ops.append({"op": "add", "track": tb[k].get("id") or label, "clip": c})
        for ck in A.keys() - B.keys():
            c = A[ck]
            log.append(f'－ [{label}] クリップ削除 {c.get("start",0):.2f}-{c.get("end",0):.2f}'
                       f'{" " + fmt(c.get("text") or c.get("src") or "")}')
            ops.append({"op": "delete", "track": ta[k].get("id") or label, "clip": selector(c, ta[k])})
        for ck in A.keys() & B.keys():
            ca, cb = A[ck], B[ck]
            ch = {kk: (ca.get(kk), cb.get(kk)) for kk in set(ca) | set(cb) if ca.get(kk) != cb.get(kk)}
            if not ch: continue
            who = fmt(cb.get("text") or cb.get("src") or f'{cb.get("start",0):.2f}s')
            parts = [f'{LABELS.get(kk, kk)} {fmt(v0)}→{fmt(v1)}' for kk, (v0, v1) in sorted(ch.items())]
            log.append(f'  [{label}] {who}: ' + " / ".join(parts))
            ops.append({"op": "set", "track": tb[k].get("id") or label, "clip": selector(cb, tb[k]),
                        "set": {kk: v1 for kk, (v0, v1) in ch.items()}})
    return log, ops


def conventions(emit_template=False):
    """全プロジェクトを走査し、繰り返されている値＝"作法"を出す。
    emit_template=True なら、◎の値だけを patch.py に渡せる --ops JSON として出力する。"""
    stat, srcs = defaultdict(Counter), defaultdict(set)
    for name in sorted(os.listdir(os.path.join(ROOT, "projects"))):
        fp = os.path.join(ROOT, "projects", name, "project.json")
        if not os.path.isfile(fp): continue
        pj = json.load(open(fp, encoding="utf-8"))
        cv = pj.get("canvas", {})
        stat["canvas"][f'{cv.get("w")}x{cv.get("h")}'] += 1; srcs["canvas"].add(name)
        # トップレベル設定も作法として集計（字幕の既定スタイル・音声設定はシリーズで揃うため）
        for path, val in (("style.font", (pj.get("style") or {}).get("font")),
                          ("style.fontsize", (pj.get("style") or {}).get("fontsize")),
                          ("style.box", (pj.get("style") or {}).get("box")),
                          ("audio.loudnorm", (pj.get("audio") or {}).get("loudnorm")),
                          ("audio.limiter", (pj.get("audio") or {}).get("limiter"))):
            if val is None: continue
            v = json.dumps(val, ensure_ascii=False, sort_keys=True) if isinstance(val, (list, dict)) else val
            stat[path][str(v)] += 1; srcs[path].add(name)
        for t in pj.get("tracks", []):
            for c in t["clips"]:
                for kk in STYLE_KEYS:
                    if c.get(kk) is None: continue
                    if t["type"] == "caption" and kk in ("x", "y", "w"): continue  # 位置は内容依存
                    v = json.dumps(c[kk], ensure_ascii=False) if isinstance(c[kk], (list, dict)) else c[kk]
                    stat[f'{t["type"]}.{kk}'][str(v)] += 1
                    srcs[f'{t["type"]}.{kk}'].add(name)

    if emit_template:
        # ◎（8割超一致・2プロジェクト以上）だけを既定値として吐く
        root_ops, per_type = [], defaultdict(dict)
        for k in sorted(stat):
            c = stat[k]; tot = sum(c.values()); top, n = c.most_common(1)[0]
            if not (n / tot >= 0.8 and len(srcs[k]) >= 2): continue
            # 集計時に str() で潰れているので復元する。
            # ※ Pythonの str(True) は "True"（JSONの true ではない）→ 先にboolを判定する
            if top in ("True", "False"):
                val = (top == "True")
            else:
                try: val = json.loads(top)
                except (json.JSONDecodeError, TypeError):
                    val = top
                    for cast in (int, float):
                        try: val = cast(top); break
                        except ValueError: pass
            if k == "canvas": continue
            if "." not in k: continue
            a, b = k.split(".", 1)
            if a in ("style", "audio"):
                root_ops.append({"op": "setroot", "key": a, "merge": {b: val}})
            else:
                per_type[a][b] = val
        out = root_ops + [{"op": "set", "track": f"<{t}トラックのid>", "clip": "*", "set": s}
                          for t, s in sorted(per_type.items())]
        print(json.dumps(out, ensure_ascii=False, indent=1))
        print("\n# ↑ ◎の作法だけを抽出。track名を実際のidに直して patch.py --ops に渡す", file=sys.stderr)
        return

    print("# 作法（全プロジェクト横断の最頻値）")
    print("# 「同じ値が複数プロジェクトで繰り返されている」＝人の癖＝AIが従うべき既定値\n")
    for k in sorted(stat):
        c = stat[k]; tot = sum(c.values())
        top, n = c.most_common(1)[0]
        share = n / tot
        mark = "◎" if share >= 0.8 and len(srcs[k]) >= 2 else ("○" if share >= 0.5 else " ")
        print(f'{mark} {k:22} {top:>12}  ({n}/{tot}={share:.0%}, {len(srcs[k])}プロジェクト)')
        if share < 0.8:
            print(f'{"":24} 他: ' + ", ".join(f"{v}×{n2}" for v, n2 in c.most_common()[1:4]))
    print("\n◎=既定値として採用してよい / ○=傾向あり / 無印=ばらつき（都度指定）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?")
    ap.add_argument("--since", default="HEAD", help="比較元のgitリビジョン（既定: HEAD）")
    ap.add_argument("--ops", action="store_true", help="patch.py に渡せる --ops JSON を出す")
    ap.add_argument("--conventions", action="store_true", help="全プロジェクトから作法を統計で出す")
    ap.add_argument("--emit-template", action="store_true",
                    help="--conventions の◎だけを patch.py 用 --ops JSON で出す（新規プロジェクトへ既定値を流し込む）")
    a = ap.parse_args()

    if a.conventions or a.emit_template: conventions(a.emit_template); return
    if not a.project: ap.print_help(); return

    pdir = a.project if os.path.isdir(a.project) else os.path.join(ROOT, "projects", a.project)
    fp = os.path.join(pdir, "project.json")
    if not os.path.isfile(fp): die(f"project.json がありません: {fp}")

    old = load_rev(pdir, a.since)
    if old is None: die(f"{a.since} に {rel(pdir)} がありません（未コミットのプロジェクト？）")
    new = json.load(open(fp, encoding="utf-8"))

    log, ops = diff_project(old, new)
    if not log:
        print(f"（{a.since} から変更なし）"); return
    if a.ops:
        print(json.dumps(ops, ensure_ascii=False)); return

    print(f"# {a.since} → いま（＝人がUIで直した内容）\n")
    for l in log: print(l)
    print(f"\n{len(log)}件。--ops で patch.py 用JSONとして取り出せる（別プロジェクトへの再現用）")
    print("繰り返し直されている点は AGENTS.md に作法として書き戻すこと（--conventions で横断確認）")


if __name__ == "__main__":
    main()
