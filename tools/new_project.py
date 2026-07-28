#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シリーズの「型」を継承して新規プロジェクトを作る（テンプレート機構）。

既存プロジェクトから以下を引き継ぎ、中身（紙芝居・字幕・ワイプ等）は空で作る:
  - canvas / style / audio / _zorder / cuts=[]（トップレベル設定）
  - トラック構成（id・label・type・並び順＝重ね順）
  - 全編表示クリップ（ロゴ・帯バナー・背景・BGMなど、元動画のほぼ全長を覆うもの）
    → 新プロジェクトの尺(--dur)に合わせて end を張り直す
  - 上記クリップが参照する素材ファイル ＋ 音声トラックが参照する小さい音素材（効果音キット）

これで「自社シリーズの型で新しい回を作って」が1コマンドになる。
中身の組み立ては従来どおり（初回はビルダー、修正は tools/patch.py）。

usage:
  python3 tools/new_project.py <新プロジェクト名> --from <既存プロジェクト> [--dur 40]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import argparse, json, os, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def die(msg):
    print("❌ " + msg, file=sys.stderr); sys.exit(1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="新しいプロジェクト名（projects/直下に作る）")
    ap.add_argument("--from", dest="src", required=True, help="型を継承する既存プロジェクト")
    ap.add_argument("--dur", type=float, default=40.0, help="仮の全長秒（全編クリップのendに使う。既定40）")
    a = ap.parse_args()

    sdir = a.src if os.path.isdir(a.src) else os.path.join(ROOT, "projects", a.src)
    sfp = os.path.join(sdir, "project.json")
    if not os.path.isfile(sfp):
        die(f"継承元がありません: {sfp}")
    tdir = os.path.join(ROOT, "projects", os.path.basename(a.name))
    if os.path.exists(os.path.join(tdir, "project.json")):
        die(f"既に存在します: {tdir}（上書きはしない）")

    src = json.load(open(sfp, encoding="utf-8"))
    total = max((c.get("end", 0) for t in src.get("tracks", []) for c in t.get("clips", [])), default=0)

    assets = set()
    def keep_asset(name):
        # ⚠️ 実在するものだけ集めると、素材ゼロ（clone直後）のとき assets が空になり
        #    「素材が無い」という判定自体が成立しなくなる。**参照名は実在に関わらず集める**。
        #    実在チェックは下の missing で行う（2026-07-17: ここが原因で警告が素通りしていた）
        if name:
            assets.add(os.path.basename(name))

    tracks = []
    kept_fulls = 0
    for t in src.get("tracks", []):
        nt = {k: v for k, v in t.items() if k != "clips"}
        nt["clips"] = []
        for c in t.get("clips", []):
            # 「全編表示」クリップだけ型として継承（開始が頭・終了がほぼ末尾）
            is_full = c.get("start", 0) <= 0.5 and total > 0 and c.get("end", 0) >= total - 1.0
            if is_full and t["type"] in ("image", "audio", "caption"):
                nc = dict(c); nc["start"] = 0; nc["end"] = round(a.dur, 2)
                nt["clips"].append(nc); kept_fulls += 1
                keep_asset(nc.get("src"))
        tracks.append(nt)
        # 効果音キット: 音声トラックが参照する素材はクリップを継承しなくてもファイルは持っていく
        if t["type"] == "audio":
            for c in t.get("clips", []):
                keep_asset(c.get("src"))

    pj = {
        "meta": {"title": os.path.basename(a.name), "fps": src.get("meta", {}).get("fps", 30)},
        "canvas": dict(src.get("canvas") or {"w": 1080, "h": 1920, "bg": "#141b26"}),
        "audio": json.loads(json.dumps(src.get("audio") or {})),
        "cuts": [],
        "_zorder": True,
        "tracks": tracks,
    }
    if src.get("style"):
        pj["style"] = json.loads(json.dumps(src["style"]))

    # ⚠️ 素材の実在を先に確認する。テンプレの素材が1つも無いのに「✅作成」と表示すると、
    #    壊れたプロジェクトが出来たことに気付けない（gitは素材を持たないので、clone直後は必ずこの状態。
    #    2026-07-17: 配布前の検証で発覚）。作る前に止めて、zip読み込みへ誘導する。
    missing = [n for n in sorted(assets) if not os.path.exists(os.path.join(sdir, n))]
    if assets and len(missing) == len(assets):
        raise SystemExit(
            f"❌ テンプレート '{os.path.basename(sdir)}' の素材が1つも見つかりません（{len(missing)}件すべて欠落）。\n"
            f"   git clone した直後は素材（画像・音声・映像）が入っていません。\n"
            f"   → 素材入りの .veproj.zip を「📥 zip読み込み」で取り込むか、\n"
            f"     テンプレートを使わず「空のプロジェクト」から始めてください。")

    os.makedirs(tdir, exist_ok=True)
    copied = 0
    for name in sorted(assets):
        sp = os.path.join(sdir, name)
        if not os.path.exists(sp):
            continue
        shutil.copy2(sp, os.path.join(tdir, name)); copied += 1
    tmp = os.path.join(tdir, "project.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(tdir, "project.json"))

    print(f"✅ {tdir}")
    print(f"   型: トラック{len(tracks)}本（並び順=重ね順を継承）／全編クリップ{kept_fulls}件を尺{a.dur}sで張り直し")
    print(f"   素材: {copied}ファイルをコピー（ロゴ/帯/背景/BGM/効果音キット）")
    if missing:
        print(f"   ⚠️ 素材{len(missing)}件がテンプレ側に無いためコピーできませんでした: "
              f"{', '.join(missing[:4])}{' ほか' if len(missing) > 4 else ''}")
        print(f"      → その素材を使うクリップは書き出しでエラーになります。素材を配置するか、該当クリップを削除してください")
    print(f"   次: ビルダーかpatch.pyで中身（紙芝居・字幕・ワイプ）を組む。エディタの📂一覧にも出ている")

if __name__ == "__main__":
    main()
