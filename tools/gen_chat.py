#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LINE風のチャット画面を段階的に描く（会話ネタの動画用）。

会話の往復そのものが中身になるネタで使う。1発言ごとに1枚PNGを吐き、
動画側でそれを紙芝居として並べると「メッセージが1つずつ増えていく」画になる。

⚠️ LINEのロゴ・商標は使わない。UIの見た目だけを借りた**一般的なチャット画面**として描く
   （実在サービスを騙るものにしない）。

使い方:
    python3 tools/gen_chat.py <出力ディレクトリ> <台本JSON>

台本JSON:
    {"title": "社内チャット",
     "speakers": {"A": {"name": "営業部 田中", "side": "left"},
                  "B": {"name": "総務部 佐藤", "side": "right"}},
     "lines": [{"who": "A", "text": "..."} , ...]}
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）。**_deps より先に**
import _deps       # noqa: E402  依存の確認
import json, os, sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1240                 # 動画の中央エリアに収まる高さ
BG = (232, 238, 245)              # チャット背景（薄い青灰）
HEADER = (18, 28, 46)             # 紺のヘッダ
BUB_L = (255, 255, 255)           # 相手の吹き出し（白）
BUB_R = (214, 173, 92)            # 自分の吹き出し（金）
TXT_L = (24, 30, 42)
TXT_R = (28, 24, 12)
NAME_C = (110, 122, 140)
PAD = 26
# フォント解決: Mac(ヒラギノ)を優先し、無ければWindows(游ゴシック/メイリオ)へ落ちる。
# ⚠️ Mac専用パス決め打ちにすると、Windowsで日本語が豆腐(□)になる（2026-07-21 対応）。
FONTS_BOLD = ["/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
              "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc",
              "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/msgothic.ttc"]
FONTS = ["/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
         "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
         "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/YuGothR.ttc",
         "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc"]


def font(sz, bold=False):
    for p in (FONTS_BOLD if bold else FONTS):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def wrap(draw, text, f, maxw):
    """句読点で切りつつ、幅に収まるよう折り返す（日本語は単語境界が無いので1文字ずつ測る）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""
            continue
        if draw.textlength(cur + ch, font=f) > maxw and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def draw_bubble(d, x, y, lines, f, side, lh):
    """吹き出しを1つ描いて、その高さを返す。"""
    tw = max(d.textlength(l, font=f) for l in lines)
    bw, bh = tw + PAD * 2, lh * len(lines) + PAD * 1.4
    bx = x if side == "left" else x - bw
    fill = BUB_L if side == "left" else BUB_R
    col = TXT_L if side == "left" else TXT_R
    d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=22, fill=fill)
    # しっぽ（三角）: 発言者の側に小さく出す
    ty = y + 22
    if side == "left":
        d.polygon([(bx - 12, ty), (bx + 2, ty - 8), (bx + 2, ty + 12)], fill=fill)
    else:
        d.polygon([(bx + bw + 12, ty), (bx + bw - 2, ty - 8), (bx + bw - 2, ty + 12)], fill=fill)
    ty = y + PAD * 0.7
    for l in lines:
        d.text((bx + PAD, ty), l, font=f, fill=col)
        ty += lh
    return bh


def build(outdir, script):
    os.makedirs(outdir, exist_ok=True)
    f_msg, f_name, f_hd = font(38), font(24), font(34, True)
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lh = int(38 * 1.42)
    maxw = int(W * 0.60)

    # 各発言の描画情報を先に確定（高さが要るので）
    items = []
    for ln in script["lines"]:
        sp = script["speakers"][ln["who"]]
        wl = wrap(scratch, ln["text"], f_msg, maxw)
        items.append((sp, wl, lh * len(wl) + PAD * 1.4 + 30 + 16))

    made = []
    for n in range(1, len(items) + 1):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W, 92], fill=HEADER)
        d.text((34, 30), script.get("title", "チャット"), font=f_hd, fill=(255, 255, 255))

        # 下詰め（新しい発言が下に出る＝実際のチャットと同じ）。
        # 溢れる場合は**古い発言から落とす**（上が中途半端に切れると読めないため）
        shown = items[:n]
        while len(shown) > 1 and sum(h for _, _, h in shown) + 16 > H - 130:
            shown = shown[1:]
        total = sum(h for _, _, h in shown) + 16
        y = max(120, H - 40 - total)
        for sp, wl, h in shown:
            side = sp["side"]
            nx = 40 if side == "left" else W - 40
            if side == "left":
                d.text((nx + 8, y), sp["name"], font=f_name, fill=NAME_C)
            else:
                nw = d.textlength(sp["name"], font=f_name)
                d.text((nx - 8 - nw, y), sp["name"], font=f_name, fill=NAME_C)
            y += 30
            y += draw_bubble(d, nx, y, wl, f_msg, side, lh) + 16
        p = os.path.join(outdir, f"chat{n}.png")
        im.save(p); made.append(p)
    print(f"✅ {len(made)}枚 → {outdir}")
    return made


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("❌ 引数が足りません。\n"
              "   使い方: python3 tools/gen_chat.py <出力ディレクトリ> <台本JSON>\n"
              "   例:     python3 tools/gen_chat.py projects/my-video-01 script.json",
              file=sys.stderr)
        sys.exit(1)
    outdir, spec = sys.argv[1], sys.argv[2]
    if not os.path.exists(spec):
        print(f"❌ 台本JSONが見つかりません: {spec}", file=sys.stderr); sys.exit(1)
    try:
        with open(spec, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as ex:
        print(f"❌ 台本JSONが壊れています: {spec}\n   {ex}", file=sys.stderr); sys.exit(1)
    build(outdir, data)
