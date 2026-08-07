#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UIパーツ画像（codex生成）を最適化して editor.html に埋め込む。

  python3 tools/make_ui_assets.py --raw <生成PNGのフォルダ>   # 取り込み＋最適化＋埋め込み
  python3 tools/make_ui_assets.py                             # ui/assets/ から埋め込みだけ再生成

- 取り込み: 透明余白をトリム → 目的サイズへ縮小 → パレット量子化 → ui/assets/<名>.png
- 埋め込み: ui/editor.html の <style id="uiimgs"> の中身を --img-<名> のdata URIで書き換える
  （HTML1枚で完結＝依存ゼロ配布・サーバ再起動不要のため、ファイル参照ではなく埋め込みにしている）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402
import argparse, base64, io, os, re, sys

try:
    from PIL import Image
except ImportError:
    print("❌ Pillow がありません: pip install Pillow", file=sys.stderr); sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "ui", "assets")
HTML = os.path.join(ROOT, "ui", "editor.html")

# 目的サイズ。ロゴ=高さ基準 / イラスト=幅基準 / その他(アイコン)=最大辺96px
SIZES = {"logo_editor": ("h", 96), "logo_viewer": ("h", 96), "empty_inspector": ("w", 520)}
DEFAULT = ("m", 96)


def trim(im):
    bbox = im.getchannel("A").point(lambda x: 255 if x > 10 else 0).getbbox()
    return im.crop(bbox) if bbox else im


def fit(im, mode, px):
    w, h = im.size
    s = px / {"h": h, "w": w, "m": max(w, h)}[mode]
    if s < 1:
        im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    return im


def save_opt(im, path):
    """RGBAとパレット化の小さい方で保存（見た目差は縮小後は実質なし）。"""
    b1 = io.BytesIO(); im.save(b1, "PNG", optimize=True)
    b2 = io.BytesIO(); im.quantize(colors=255, method=Image.FASTOCTREE).save(b2, "PNG", optimize=True)
    data = min((b2, b1), key=lambda b: len(b.getvalue())).getvalue()
    with open(path, "wb") as f:
        f.write(data)
    return data


def ingest(raw_dir):
    os.makedirs(ASSETS, exist_ok=True)
    for fn in sorted(os.listdir(raw_dir)):
        if not fn.endswith(".png"):
            continue
        name = fn[:-4]
        im = trim(Image.open(os.path.join(raw_dir, fn)).convert("RGBA"))
        mode, px = SIZES.get(name, DEFAULT)
        im = fit(im, mode, px)
        data = save_opt(im, os.path.join(ASSETS, name + ".png"))
        print(f"  {name}: {im.size[0]}x{im.size[1]} {len(data) // 1024}KB")


def embed():
    if not os.path.isdir(ASSETS):
        print("❌ ui/assets/ がありません（--raw で取り込みから始めてください）", file=sys.stderr)
        sys.exit(1)
    rules = []
    total = 0
    for fn in sorted(os.listdir(ASSETS)):
        if not fn.endswith(".png"):
            continue
        data = open(os.path.join(ASSETS, fn), "rb").read()
        b64 = base64.b64encode(data).decode()
        rules.append(f"--img-{fn[:-4].replace('_', '-')}:url(data:image/png;base64,{b64});")
        total += len(b64)
    css = "/* tools/make_ui_assets.py が生成（編集しない）。元PNGは ui/assets/ */\n:root{" \
          + "".join(rules) + "}"
    html = open(HTML, encoding="utf-8").read()
    new, n = re.subn(r'(<style id="uiimgs">).*?(</style>)', r"\1" + css.replace("\\", "\\\\") + r"\2",
                     html, count=1, flags=re.S)
    if n != 1:
        print('❌ editor.html に <style id="uiimgs"> が見つかりません', file=sys.stderr); sys.exit(1)
    # ファビコン: ロゴ左端のマーク部分を正方形に切り出して32pxに
    logo = os.path.join(ASSETS, "logo_editor.png")
    if os.path.exists(logo):
        im = Image.open(logo).convert("RGBA")
        im = im.crop((0, 0, im.height, im.height)).resize((32, 32), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, "PNG", optimize=True)
        fav = base64.b64encode(b.getvalue()).decode()
        new, n2 = re.subn(r'(<link id="favicon"[^>]*href=")[^"]*(")',
                          r"\1data:image/png;base64," + fav + r"\2", new, count=1)
        if n2 == 1:
            print("ファビコン: 32x32 をロゴマークから生成")
    tmp = HTML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, HTML)
    print(f"埋め込み: {len(rules)}点 / base64合計 {total // 1024}KB -> ui/editor.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="UIパーツ画像の最適化と埋め込み")
    ap.add_argument("--raw", help="codex生成PNGのフォルダ（取り込み＋最適化してから埋め込む）")
    a = ap.parse_args()
    if a.raw:
        ingest(a.raw)
    embed()
