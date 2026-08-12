#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""紹介ページのメインビジュアルに、ロゴとキャッチを焼き込む。

**文字は画像生成に任せない。** 生成モデルは日本語を化けさせるので、
素の絵（notes/promo-source/hero_base.jpg）に、既存のロゴPNGと
システムフォントで後から合成する。差し替えもここを直すだけで済む。

    python3 tools/make_hero.py            # docs/assets/hero.jpg を作り直す
    python3 tools/make_hero.py --copy "…" # キャッチだけ差し替えて試す
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402
import argparse, os, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "notes", "promo-source", "hero_base.jpg")
LOGO = os.path.join(ROOT, "ui", "assets", "logo_editor.png")
OUT = os.path.join(ROOT, "docs", "assets", "hero.jpg")
OUT_EN = os.path.join(ROOT, "docs", "assets", "hero-en.jpg")

# ページの h1 と揃える（別々に持つとズレるので、変えるときは両方直す）
COPY = "日本語で動画を作り、字幕1つから手で直せる。"
# 英語版ページ用。日本語の画像を英語ページに出さない
COPY_EN = "Make a video in plain language. Move one subtitle by hand."

FONTS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W5.ttc",
    "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc",
]


def font(size):
    for p in FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main(copy_text, out=OUT, en=False):
    if not os.path.exists(BASE):
        print(f"❌ 元絵がありません: {BASE}", file=sys.stderr); sys.exit(1)
    im = Image.open(BASE).convert("RGB")
    W, H = im.size

    # 置き場所は左上（実測でいちばん暗く・柄が少ない。輝度6.5／ばらつき13.6）
    x, y = int(W * 0.055), int(H * 0.13)

    # 文字の右端が明るいコマに掛かるので、左から右へ薄れる暗幕を敷く。
    # 影だけだと明るい絵の上で潰れる（実測でコントラスト比が3を切った）
    scrim = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(scrim)
    x1 = int(W * 0.72)
    for i in range(x1):
        sd.line([(i, 0), (i, H)], fill=int(150 * (1 - i / x1) ** 1.4))
    im = Image.composite(Image.new("RGB", (W, H), (4, 8, 18)), im, scrim)

    # ロゴ。暗所に載るので、そのままの白抜きで合う
    lg = Image.open(LOGO).convert("RGBA")
    lw = int(W * 0.30)
    lg = lg.resize((lw, max(1, round(lw * lg.height / lg.width))), Image.LANCZOS)
    im.paste(lg, (x, y), lg)

    # キャッチ。1行で置き、背景が明るくても読めるよう薄い影を敷く
    ty = y + lg.height + int(H * 0.045)
    f = font(int(H * (0.040 if en else 0.049)))
    d = ImageDraw.Draw(im)
    for dx, dy in ((0, 2), (2, 0), (0, -2), (-2, 0), (2, 2)):
        d.text((x + dx, ty + dy), copy_text, font=f, fill=(0, 0, 0))
    d.text((x, ty), copy_text, font=f, fill=(255, 255, 255))

    im.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    print(f"✅ {out}  {W}x{H}  {os.path.getsize(out) // 1024}KB")
    print("   ⚠️ 焼き込んだ文字は目で見て確認すること（漏洩スキャンは画像を読めません）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="メインビジュアルにロゴとキャッチを焼き込む")
    ap.add_argument("--copy", default=None, help="キャッチ（既定はページのh1と同じ）")
    a = ap.parse_args()
    if a.copy:
        main(a.copy)
    else:
        main(COPY)
        main(COPY_EN, OUT_EN, en=True)
