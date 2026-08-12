#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""紹介ページのデモ動画（docs/assets/sample.mp4）を組み立てる。

**機能紹介ではなく、得が伝わる動画にする。** 機能名は1つも言わない。
カウントアップ・文字送り・スライドイン・ケンバーンズ・パン・クロスフェード・
ダッキングは、演出として自然に混ぜる（説明しない）。

usage:
  python3 tools/build_promo.py --voice     # ナレーションも作る（初回・文言変更時）
  python3 tools/build_promo.py             # 組み立てだけ
  python3 renderer/render.py projects/_promo
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402
from _guard import guard_overwrite  # noqa: E402
import argparse, json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = "build_promo.py"
NAME = "_promo"
W, H, FPS = 1920, 1080, 30
PDIR = os.path.join(ROOT, "projects", NAME)
SRC = os.path.join(ROOT, "notes", "promo-source")

WHITE = [255, 255, 255]
GOLD = [214, 173, 92]
NAVY = [10, 16, 28, 214]

# ナレーション。tts_text は誤読対策で読みをカナにする（漢字を残すと誤読する）
NARR = {
    "n1": ("動画を1本作るのに、いくつ工程があるでしょうか。",
           "動画を いっぽん 作るのに、いくつ 工程が あるでしょうか。"),
    "n2": ("作りたいものを日本語で伝えると、ここまで進みます。", None),
    "n3": ("そのまま完成でも構いません。気になるところだけ、掴んで直せます。", None),
    "n4": ("読めない字幕や足りない素材は、書き出す前に止まります。", None),
    "n5": ("縦も、横も、正方形も。ぜんぶ手元だけで終わります。", None),
    "n6": ("ジェイジェイ ビデオ エディター。エムアイティー ライセンスで公開しています。", None),
}


def die(m):
    print("❌ " + m, file=sys.stderr); sys.exit(1)


def adur(f):
    p = os.path.join(PDIR, f)
    if not os.path.exists(p):
        return 0.0
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def gen_voice():
    segs = []
    for k, (text, tts) in NARR.items():
        s = {"id": k, "voice": "narrator-m", "text": text}
        if tts:
            s["tts_text"] = tts
        segs.append(s)
    spec = os.path.join(PDIR, "narration.json")
    json.dump({"segments": segs}, open(spec, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_voice.py"),
                       "--spec", spec, "--outdir", PDIR]).returncode:
        die("ナレーション生成に失敗しました")


def ensure_assets():
    os.makedirs(PDIR, exist_ok=True)
    need = [f"p{i}_{n}.png" for i, n in
            ((1, "ask"), (2, "build"), (3, "touch"), (4, "check"), (5, "out"))]
    miss = []
    for fn in need + ["control.mp3", "bgm.mp3"]:
        if os.path.exists(os.path.join(PDIR, fn)):
            continue
        for cand in (os.path.join(SRC, fn),
                     os.path.join(ROOT, "assets", "kit", fn),
                     os.path.join(ROOT, "assets", "bgm", fn)):
            if os.path.exists(cand):
                shutil.copy2(cand, os.path.join(PDIR, fn)); break
        else:
            miss.append(fn)
    if miss:
        die("素材がありません: " + ", ".join(miss)
            + f"\n   {SRC}/ に置いてください（画像はcodexで生成）")


def build():
    cap, sub, imgs, narr, sfx, marks = [], [], [], [], [], []
    t = 0.0

    def say(key, pad=0.35):
        d = adur(key + ".mp3") or 3.0
        narr.append({"src": key + ".mp3", "start": round(t + pad, 2),
                     "end": round(t + pad + d, 2)})
        return d

    def big(text, a, b, **kw):
        cap.append(dict({"text": text, "x": 0.08, "y": 0.40, "w": 0.84, "h": 0.17,
                         "fontsize": 68, "bold": True, "align": "center",
                         "valign": "middle", "textColor": WHITE, "highlight": True,
                         "highlightColor": NAVY, "start": round(a, 2),
                         "end": round(b, 2)}, **kw))

    def small(text, a, b, y=0.615, fs=34, **kw):
        sub.append(dict({"text": text, "x": 0.08, "y": y, "w": 0.84, "h": 0.09,
                         "fontsize": fs, "bold": False, "align": "center",
                         "valign": "middle", "textColor": GOLD, "highlight": True,
                         "highlightColor": [10, 16, 28, 180], "start": round(a, 2),
                         "end": round(b, 2)}, **kw))

    def se(at, gain=0.14):
        d = adur("control.mp3") or 0.8
        if sfx and at < sfx[-1]["end"]:
            return
        sfx.append({"src": "control.mp3", "start": round(max(at, 0), 2),
                    "end": round(max(at, 0) + d, 2), "gain": gain})

    def shot(src, a, b, **kw):
        imgs.append(dict({"src": src, "start": round(a, 2), "end": round(b, 2),
                          "x": 0, "y": 0, "w": 1}, **kw))

    # ── 1. 問い。工程が順に滑り込む
    d = say("n1"); seg = max(d + 1.9, 6.2)
    shot("p1_ask.png", t, t + seg, motion={"zoom": 1.10}, fadeIn=0.6)
    big("動画を1本、作る。", t + 0.3, t + seg, y=0.30, fontsize=60)
    steps = ["企画", "台本", "音声", "字幕", "編集", "書き出し"]
    for k, s in enumerate(steps):
        a = t + 1.5 + k * 0.55
        cap.append({"text": s, "x": 0.055 + k * 0.152, "y": 0.60, "w": 0.135, "h": 0.10,
                    "fontsize": 34, "bold": True, "align": "center", "valign": "middle",
                    "textColor": WHITE, "highlight": True, "highlightColor": NAVY,
                    "start": round(a, 2), "end": round(t + seg, 2),
                    "anim": "slide", "slideFrom": "bottom"})
    se(t + 1.5)
    t += seg

    # ── 2. 頼めば組み上がる。文字送り＋ゆっくり寄る
    d = say("n2"); seg = max(d + 1.5, 5.6)
    shot("p2_build.png", t, t + seg, motion={"zoom": 1.12}, fadeIn=0.5)
    big("ぜんぶ、**ひとつづきに**。", t + 0.35, t + seg, y=0.36,
        anim="typewriter", animDur=1.3)
    small("日本語で伝えるだけ", t + 2.4, t + seg)
    se(t + 0.2)
    t += seg

    # ── 3. 直せる。横に流しながら
    d = say("n3"); seg = max(d + 1.5, 6.4)
    shot("p3_touch.png", t, t + seg, motion={"pan": "right"}, fadeIn=0.5)
    big("気になるところだけ、**直せる**。", t + 0.4, t + seg, y=0.34, fontsize=58)
    small("直したところは、あとから消えません", t + 2.8, t + seg)
    se(t + 0.2)
    t += seg

    # ── 4. 守られる。ポップで叩きつける
    d = say("n4"); seg = max(d + 1.4, 5.6)
    shot("p4_check.png", t, t + seg, motion={"zoom": 1.08}, fadeIn=0.5)
    big("書き出す前に、**止まる**。", t + 0.45, t + seg, y=0.36, anim="pop")
    small("読めない字幕・足りない素材", t + 2.4, t + seg)
    se(t + 0.35)
    t += seg

    # ── 5. 3つの比率。数が上がる
    d = say("n5"); seg = max(d + 1.6, 5.8)
    shot("p5_out.png", t, t + seg, motion={"pan": "left"}, fadeIn=0.5)
    big("縦・横・正方形", t + 0.4, t + seg, y=0.33, fontsize=62)
    small("クラウドもAPIキーも要りません", t + 2.6, t + seg)
    se(t + 0.2)
    t += seg

    # ── 6. 締め。スライドで入れる
    d = say("n6"); seg = max(d + 1.6, 5.2)
    shot("p5_out.png", t, t + seg, motion={"zoom": 1.06}, fadeIn=0.6, fadeOut=1.0,
         adjust={"brightness": 0.72})
    big("GJ VIDEO EDITOR", t + 0.4, t + seg - 0.4, y=0.38, fontsize=64,
        anim="slide", slideFrom="left")
    small("MITライセンス · github.com/GridJapan/gjVideoEditor",
          t + 1.8, t + seg - 0.4, y=0.56, fs=32)
    t += seg

    total = round(t, 2)
    bgm = [{"src": "bgm.mp3", "start": 0, "end": total, "in": 0, "gain": 0.16,
            "loop": True, "duck": True, "fadeIn": 1.2, "fadeOut": 2.0}]

    pj = {"meta": {"title": NAME, "fps": FPS, "madeBy": {"tool": TOOL}},
          "canvas": {"w": W, "h": H, "bg": "#080c14"},
          "style": {"font": "Hiragino Kaku Gothic Pro", "fontsize": 48},
          "audio": {"loudnorm": {"on": True}, "limiter": {"on": True, "db": -1.5}},
          "cuts": [], "_zorder": True,
          "tracks": [
              {"id": "cap", "type": "caption", "label": "字幕", "clips": cap},
              {"id": "sub", "type": "caption", "label": "副字幕", "clips": sub},
              {"id": "img", "type": "image", "label": "画", "clips": imgs},
              {"id": "narr", "type": "audio", "label": "ナレーション", "clips": narr},
              {"id": "sfx", "type": "audio", "label": "効果音", "clips": sfx},
              {"id": "bgm", "type": "audio", "label": "BGM", "clips": bgm},
          ]}
    fp = os.path.join(PDIR, "project.json")
    with open(fp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False, indent=1)
    os.replace(fp + ".tmp", fp)
    print(f"{NAME}: {total}s  字幕{len(cap)}+{len(sub)}  画{len(imgs)}  "
          f"ナレ{len(narr)}  効果音{len(sfx)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="紹介ページのデモ動画を組み立てる")
    ap.add_argument("--voice", action="store_true", help="ナレーションも作り直す")
    ap.add_argument("--force", action="store_true", help="既存の project.json を作り直す")
    a = ap.parse_args()
    ensure_assets()
    guard_overwrite(PDIR, TOOL, a.force)
    if a.voice:
        gen_voice()
    build()
