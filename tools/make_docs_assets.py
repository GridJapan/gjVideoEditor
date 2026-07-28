#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""紹介ページ（docs/）の画像を作り直す。

docs/assets/ はコミットされるバイナリなので、**どう作ったかを残しておく**ための道具。
漏洩スキャンは画像の中身を読めないので、作り直したら必ず目で見ること。

    python3 tools/make_docs_assets.py --shot      # 編集画面のスクショを撮る（要 Chrome＋起動中のサーバ）
    python3 tools/make_docs_assets.py --split     # 「左に相棒・右に道具」の図を作る
    python3 tools/make_docs_assets.py            # 両方

⚠️ スクショは**公開版のツリーで起動したサーバ**から撮ること。
   社内プロジェクトから撮ると、社内の素材が写り込む（実際にやらかした）。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策
import argparse, os, shutil, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "assets")

FONTS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/meiryo.ttc",
]
BOLD = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/meiryob.ttc",
]

# ページと同じ配色
INK, BODY, MUTED = (15, 23, 42), (71, 85, 105), (148, 163, 184)
LINE, SOFT, WHITE = (226, 232, 240), (248, 250, 252), (255, 255, 255)
ACCENT = (37, 99, 235)


def die(m):
    print(f"❌ {m}", file=sys.stderr); sys.exit(1)


def font(sz, bold=False):
    for p in (BOLD if bold else FONTS):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    die("日本語フォントが見つかりません")


def shot(url, path, w=1600, h=720):
    """ヘッドレスChromeで編集画面を撮る。"""
    chrome = next((p for p in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"), shutil.which("chromium"),
    ] if p and os.path.exists(p)), None)
    if not chrome:
        die("Chrome が見つかりません（--split だけなら不要です）")
    r = subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={w},{h}", f"--screenshot={path}",
                        "--virtual-time-budget=4500", url],
                       capture_output=True, encoding="utf-8", errors="replace")
    if not os.path.exists(path):
        die(f"スクリーンショットに失敗: {(r.stderr or '')[-300:]}")
    print(f"✅ {os.path.relpath(path, ROOT)}")


def wrap(d, text, f, maxw):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n" or d.textlength(cur + ch, font=f) > maxw:
            lines.append(cur); cur = "" if ch == "\n" else ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def split_view(path, editor_png):
    """「左に相棒・右に道具」の図。左＝チャット、右＝実際の編集画面。"""
    W, H = 1600, 980
    im = Image.new("RGB", (W, H), SOFT)
    d = ImageDraw.Draw(im)

    # アプリの窓
    pad, bar = 40, 44
    d.rounded_rectangle([pad, pad, W - pad, H - pad], radius=14, fill=WHITE, outline=LINE, width=2)
    d.rounded_rectangle([pad, pad, W - pad, pad + bar], radius=14, fill=(241, 245, 249))
    d.rectangle([pad, pad + bar - 14, W - pad, pad + bar], fill=(241, 245, 249))
    d.line([pad, pad + bar, W - pad, pad + bar], fill=LINE, width=2)
    for i, c in enumerate([(255, 95, 87), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([pad + 20 + i * 22, pad + 16, pad + 32 + i * 22, pad + 28], fill=c)
    t = font(14)
    d.text((W / 2 - d.textlength("Claude", font=t) / 2, pad + 13), "Claude", font=t, fill=MUTED)

    inner_top = pad + bar
    split = int(W * 0.40)

    # ── 左: チャット
    d.rectangle([pad + 2, inner_top, split, H - pad - 2], fill=WHITE)
    fs, fb = font(15), font(15, True)
    y = inner_top + 26
    d.text((pad + 26, y), "Claude Code", font=font(13, True), fill=MUTED)
    y += 34

    talk = [
        ("me", "この台本で動画を作って"),
        ("ai", "6カット・38秒で組みました。\n書き出しますか？"),
        ("me", "BGMをもう少し小さく"),
        ("ai", "ゲインを 0.05 → 0.03 にしました。"),
        ("me", "ロゴは右上のままでいい"),
    ]
    maxw = split - pad - 70
    for who, msg in talk:
        f = fb if who == "me" else fs
        lines = wrap(d, msg, f, maxw - 32)
        bh = len(lines) * 26 + 22
        if who == "me":
            bw = max(d.textlength(l, font=f) for l in lines) + 32
            x0 = split - 24 - bw
            d.rounded_rectangle([x0, y, x0 + bw, y + bh], radius=12, fill=(233, 239, 250))
            for i, l in enumerate(lines):
                d.text((x0 + 16, y + 11 + i * 26), l, font=f, fill=INK)
        else:
            x0 = pad + 26
            d.rounded_rectangle([x0, y, split - 40, y + bh], radius=12, fill=SOFT,
                                outline=LINE, width=1)
            for i, l in enumerate(lines):
                d.text((x0 + 16, y + 11 + i * 26), l, font=f, fill=BODY)
        y += bh + 16

    # 入力欄
    d.rounded_rectangle([pad + 26, H - pad - 74, split - 24, H - pad - 26],
                        radius=11, fill=WHITE, outline=LINE, width=2)
    d.text((pad + 44, H - pad - 60), "指示を書く…", font=fs, fill=(203, 213, 225))

    # ── 右: 編集画面
    d.line([split, inner_top, split, H - pad - 2], fill=LINE, width=2)
    if not os.path.exists(editor_png):
        die(f"編集画面のスクショがありません: {editor_png}（先に --shot）")
    ed = Image.open(editor_png).convert("RGB")
    rw = W - pad - 2 - (split + 2)
    rh = H - pad - 2 - inner_top
    sc = max(rw / ed.width, rh / ed.height)
    ed = ed.resize((int(ed.width * sc), int(ed.height * sc)), Image.LANCZOS).crop((0, 0, rw, rh))
    im.paste(ed, (split + 2, inner_top))

    # 見出しの帯
    for x0, x1, label in [(pad + 2, split, "左：相棒（Claude Code）"),
                          (split + 2, W - pad - 2, "右：道具（編集UI）")]:
        lf = font(13, True)
        tw = d.textlength(label, font=lf)
        cx = (x0 + x1) / 2
        d.rounded_rectangle([cx - tw / 2 - 14, H - pad - 2 - 40, cx + tw / 2 + 14, H - pad - 2 - 8],
                            radius=16, fill=(15, 23, 42, 255))
        d.text((cx - tw / 2, H - pad - 2 - 33), label, font=lf, fill=WHITE)

    im.save(path)
    print(f"✅ {os.path.relpath(path, ROOT)}  ({W}x{H})")


PROMO = "_promo"          # 宣伝用プロジェクト（GJロゴ入り）。作り終えたら消す
BRAND_LOGO = os.path.join(ROOT, "assets", "kit", "logo.png")


def build_promo(logo=None):
    """紹介ページ用のプロジェクトを作る。

    **宣伝素材なのでロゴは自社のものを入れる。**
    ここを生成ロゴのままにすると、スクショとデモ動画で見た目が食い違う（実際にやらかした）。
    """
    pdir = os.path.join(ROOT, "projects", PROMO)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_sample.py"),
                        "--name", PROMO], capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die("宣伝用プロジェクトの生成に失敗:\n" + (r.stderr or r.stdout))

    src = logo or BRAND_LOGO
    if not os.path.exists(src):
        die(f"ロゴが見つかりません: {src}\n   --logo <path> で指定してください")
    shutil.copy2(src, os.path.join(pdir, "logo.png"))

    # 横長ロゴ用に配置を直す（生成ロゴは正方形なので既定値のままだと潰れて見える）
    fp = os.path.join(pdir, "project.json")
    with open(fp, encoding="utf-8") as f:
        pj = json.load(f)
    with Image.open(src) as im:
        ar = im.width / im.height
    w = 0.20 if ar > 2 else 0.10
    for t in pj["tracks"]:
        if t["id"] == "logo":
            t["clips"][0].update({"x": round(0.955 - w, 3), "y": 0.035, "w": w})
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False, indent=1)
    print(f"✅ projects/{PROMO}（ロゴ: {os.path.basename(src)}）")
    return pdir


def render_promo(pdir):
    """宣伝用プロジェクトを書き出して docs/assets へ置く。"""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "renderer", "render.py"), pdir],
                       capture_output=True, encoding="utf-8", errors="replace")
    mp4 = os.path.join(pdir, "out", PROMO + ".mp4")
    if r.returncode != 0 or not os.path.exists(mp4):
        die("書き出しに失敗:\n" + (r.stderr or r.stdout))
    shutil.copy2(mp4, os.path.join(OUT, "sample.mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.6", "-i", mp4,
                    "-frames:v", "1", "-vf", "scale=960:-1",
                    os.path.join(OUT, "sample-poster.jpg")], check=False)
    print(f"✅ {os.path.relpath(os.path.join(OUT, 'sample.mp4'), ROOT)}")
    print(f"✅ {os.path.relpath(os.path.join(OUT, 'sample-poster.jpg'), ROOT)}")


def main():
    ap = argparse.ArgumentParser(
        description="紹介ページの素材（スクショ・図・デモ動画）を作り直す",
        epilog="スクショは --url に**公開版ツリーで起動したサーバ**を指すこと")
    ap.add_argument("--shot", action="store_true", help="編集画面のスクショを撮る")
    ap.add_argument("--split", action="store_true", help="左右分割の図を作る")
    ap.add_argument("--video", action="store_true", help="デモ動画を作る（ロゴ入り）")
    ap.add_argument("--promo", action="store_true",
                    help="宣伝用プロジェクトを作るだけ（スクショ用にサーバで開く）")
    ap.add_argument("--logo", help="使うロゴ（既定: assets/kit/logo.png）")
    ap.add_argument("--keep", action="store_true", help="宣伝用プロジェクトを消さない")
    ap.add_argument("--url", default="http://localhost:8765/", help="スクショ対象のURL")
    a = ap.parse_args()
    if not (a.shot or a.split or a.video or a.promo):
        a.shot = a.split = a.video = True

    os.makedirs(OUT, exist_ok=True)
    editor = os.path.join(OUT, "editor.png")
    pdir = None

    if a.video or a.promo:
        pdir = build_promo(a.logo)
    if a.video:
        render_promo(pdir)
    if a.promo:
        print(f"\n   スクショ用に開く: /api/open?name={PROMO} → --shot --url ...")
    if a.shot:
        shot(a.url, editor)
    if a.split:
        split_view(os.path.join(OUT, "split-view.png"), editor)

    if pdir and not a.keep and not a.promo:
        shutil.rmtree(pdir, ignore_errors=True)

    print("\n⚠️ 作り直したら**必ず開いて中身を見る**こと"
          "（漏洩スキャンは画像を読めません）")


if __name__ == "__main__":
    main()
