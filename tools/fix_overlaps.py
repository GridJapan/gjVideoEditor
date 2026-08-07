#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""1トラック内で重なったクリップを、別トラックへ退避して鉄則違反を解消する。

**見た目と音を変えない**のが要件。捨てる・詰めるのではなく「重ねたい要素は別トラックに分ける」
（CLAUDE.md の鉄則そのもの）を機械的に適用する。

    python3 tools/fix_overlaps.py <project> [--dry-run]
    python3 tools/fix_overlaps.py --all [--dry-run]

## 描画順(z)を保つ仕組み

レンダラは `reversed(tracks)` で重ねる＝**配列の先頭ほど前面**。
1トラック内では**配列の後ろのクリップほど前面**。

そこで、重なる相手より必ず「1つ前面のレーン」へ送る:

    lane(c) = 1 + max(lane(x) : x は c より前の要素で c と時間が重なる)   （無ければ 0）

lane0 は元トラックに残し、lane1..N は**元トラックの直前へ**
`[laneN, …, lane1, 元トラック]` の順に挿入する。
こうすると reversed で 元→lane1→…→laneN の順に描かれ、
**元と同じ前後関係**になる（音声は amix なので順序非依存）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402
import argparse, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def overlaps(a, b):
    return a["start"] < b["end"] - 1e-6 and a["end"] > b["start"] + 1e-6


def assign_lanes(clips):
    """重なる相手より必ず大きいレーン番号を割り当てる（前面関係を保つ）。"""
    lanes = []
    for i, c in enumerate(clips):
        hit = [lanes[j] for j in range(i) if overlaps(clips[j], c)]
        lanes.append(max(hit) + 1 if hit else 0)
    return lanes


def split_track(pj, ti):
    """tracks[ti] の重なりを解消。追加したトラック数を返す（0=重なり無し）。"""
    tr = pj["tracks"][ti]
    clips = tr.get("clips") or []
    lanes = assign_lanes(clips)
    n = max(lanes) + 1 if lanes else 1
    if n == 1:
        return 0
    base_id = tr.get("id") or tr["type"]
    base_label = tr.get("label") or base_id
    buckets = [[] for _ in range(n)]
    for c, L in zip(clips, lanes):
        buckets[L].append(c)
    tr["clips"] = buckets[0]
    # lane が大きいほど前面 → 配列では前（先頭側）。元トラックの直前へ laneN..lane1 の順で挿す
    news = []
    for L in range(n - 1, 0, -1):
        news.append({"id": f"{base_id}-{L + 1}", "type": tr["type"],
                     "label": f"{base_label} {L + 1}", "clips": buckets[L]})
    pj["tracks"][ti:ti] = news
    return len(news)


def fix(pdir, dry=False):
    fp = os.path.join(pdir, "project.json")
    with open(fp, encoding="utf-8") as f:
        pj = json.load(f)
    added, moved = 0, 0
    ti = 0
    while ti < len(pj["tracks"]):
        before = len(pj["tracks"][ti].get("clips") or [])
        k = split_track(pj, ti)
        if k:
            after = len(pj["tracks"][ti + k].get("clips") or [])
            moved += before - after
            added += k
            ti += k + 1
        else:
            ti += 1
    if not added:
        return 0, 0
    if not dry:
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, fp)
    return added, moved


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="重なったクリップを別トラックへ退避する（見た目は変えない）")
    ap.add_argument("project", nargs="?", help="projects/ 配下の名前")
    ap.add_argument("--all", action="store_true", help="全プロジェクトを対象にする")
    ap.add_argument("--dry-run", action="store_true", help="書き込まずに結果だけ出す")
    a = ap.parse_args()
    if not a.project and not a.all:
        print("❌ プロジェクト名か --all を指定してください", file=sys.stderr); sys.exit(1)
    targets = (sorted(os.path.dirname(p) for p in glob.glob(os.path.join(ROOT, "projects", "*", "project.json")))
               if a.all else [os.path.join(ROOT, "projects", os.path.basename(a.project))])
    total = 0
    for pdir in targets:
        if not os.path.exists(os.path.join(pdir, "project.json")):
            print(f"❌ project.json がありません: {pdir}", file=sys.stderr); sys.exit(1)
        added, moved = fix(pdir, a.dry_run)
        if added:
            total += 1
            print(f"{'(dry) ' if a.dry_run else ''}{os.path.basename(pdir)}: "
                  f"トラック +{added} / クリップ {moved}件を退避")
    print(f"✅ {total}件のプロジェクトを修正" + ("（--dry-run のため未保存）" if a.dry_run else ""))
