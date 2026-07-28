#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""サンプルプロジェクト（clone直後に動く1本）を素材ごと生成する。

**このスクリプトが同梱素材の出所そのもの。**
配布物に第三者の画像・音源を含めないために、サンプルの素材は
すべてここで生成する（PIL で図形／ffmpeg の合成音）。
出所不明のファイルをリポジトリに置かないこと。

使い方:
  python3 tools/make_sample.py                      # projects/sample-hello を作り直す
  python3 tools/make_sample.py --name my-sample     # 別名で作る
  python3 tools/make_sample.py --assets-only        # 素材だけ（project.json は触らない）

生成物:
  logo.png / s1.png / s2.png / s3.png   … PIL で描いた図形（文字なし）
  impact.mp3 / transition.mp3           … ffmpeg の合成音（sine + ノイズ）
  project.json                          … 上を使う9秒のデモ
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（標準出力をUTF-8化）
import _deps       # noqa: E402  依存の確認
import argparse, json, math, os, shutil, subprocess, sys
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1920, 1080   # 既定は16:9。縦・正方形は書き出しサイズの変更で作れる

# 特定ブランドを想起させない配色。暗い背景＋寒暖2色で、字幕が乗っても読める
INK = (18, 24, 38)
DEEP = (26, 34, 52)
PAPER = (242, 245, 250)
ACCENT = (74, 144, 226)
WARM = (232, 156, 74)
MUTED = (120, 132, 156)


def die(msg):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)




# ---------------------------------------------------------------- 画像

def _canvas(bg):
    im = Image.new("RGB", (W, H), bg)
    return im, ImageDraw.Draw(im)


def slide_shapes(path):
    """導入・締めの背景。**中央を空けておく**（前面に円マスクの画像を重ねるため）。"""
    im, d = _canvas(INK)
    for i in range(9):                                   # 斜めの帯（奥行き）
        x = -600 + i * 260
        d.polygon([(x, H), (x + 150, H), (x + 900, 0), (x + 750, 0)],
                  fill=DEEP if i % 2 else (22, 29, 45))
    # 左右に寄せた図形。中央は主役（前面画像・字幕）のために空ける
    d.rounded_rectangle([W * 0.05, H * 0.20, W * 0.05 + 150, H * 0.20 + 150],
                        radius=28, fill=ACCENT)
    d.ellipse([W * 0.10, H * 0.56, W * 0.10 + 96, H * 0.56 + 96], fill=WARM)
    d.ellipse([W * 0.86, H * 0.22, W * 0.86 + 132, H * 0.22 + 132], outline=ACCENT, width=12)
    d.rounded_rectangle([W * 0.88, H * 0.60, W * 0.88 + 108, H * 0.60 + 108],
                        radius=20, outline=WARM, width=10)
    for i in range(5):                                   # 細い横線でリズム
        y = H * (0.34 + i * 0.075)
        d.line([(W * 0.14, y), (W * 0.24, y)], fill=(46, 60, 88), width=5)
        d.line([(W * 0.76, y), (W * 0.86, y)], fill=(46, 60, 88), width=5)
    im.save(path)


def slide_grid(path):
    """角丸デモ用。**正方形で作る** — radius は短辺基準なので、縦長だと円が小さくなる。"""
    S = 1080
    im = Image.new("RGB", (S, S), INK)
    d = ImageDraw.Draw(im)
    for y in range(0, S, 84):                            # 方眼
        d.line([(0, y), (S, y)], fill=(30, 40, 60), width=2)
    for x in range(0, S, 84):
        d.line([(x, 0), (x, S)], fill=(30, 40, 60), width=2)
    c = S / 2
    for i in range(10):                                  # 同心リング
        r = 48 + i * 50
        d.ellipse([c - r, c - r, c + r, c + r],
                  outline=ACCENT if i % 2 == 0 else WARM, width=9)
    d.ellipse([c - 38, c - 38, c + 38, c + 38], fill=PAPER)
    im.save(path)


def slide_bars(path):
    """締めの画。棒グラフ風（具体的な数字は入れない）。"""
    im, d = _canvas(INK)
    base = H * 0.70
    d.rectangle([0, base, W, H], fill=DEEP)
    vals = [0.30, 0.52, 0.41, 0.68, 0.58, 0.88]
    bw, gap = 108, 42
    x0 = (W - (len(vals) * bw + (len(vals) - 1) * gap)) / 2
    for i, v in enumerate(vals):
        x = x0 + i * (bw + gap)
        top = base - (H * 0.44) * v
        hot = (v == max(vals))
        d.rounded_rectangle([x, top, x + bw, base], radius=12, fill=WARM if hot else ACCENT)
        if hot:                                          # 最大値だけ光らせる
            d.ellipse([x + bw / 2 - 16, top - 46, x + bw / 2 + 16, top - 14], fill=PAPER)
    for i in range(3):                                   # 目盛り
        y = base - (H * 0.44) * (i + 1) / 3
        d.line([(60, y), (W - 60, y)], fill=(38, 50, 74), width=2)
    im.save(path)


# 手元にブランドロゴがあればそれを使う。無ければ抽象マークを描く。
# assets/ は公開版に含めないので、**公開版では自動的に抽象マークになる**
# （社内は自社ロゴ入り、公開は権利フリー、を切り替えなしで両立させるため）
BRAND_LOGOS = [os.path.join(ROOT, "assets", "kit", "logo.png")]


def logo_mark(path):
    """ロゴ。素材キットにあればそれを、無ければ実在ブランドを模さない抽象マークを置く。"""
    for src in BRAND_LOGOS:
        if os.path.exists(src):
            shutil.copy2(src, path)
            return "brand"
    s = 320
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, s - 8, s - 8], radius=64, fill=PAPER + (255,))
    d.polygon([(122, 96), (122, 224), (236, 160)], fill=INK + (255,))     # ▶
    d.rectangle([78, 96, 100, 224], fill=ACCENT + (255,))
    im.save(path)
    return "generated"



# ---------------------------------------------------------------- 効果音

def _ffmpeg(args, out):
    # -map_metadata -1 … ffmpeg の版が入る TSSE タグを落とす。
    # これと anoisesrc の seed 固定で、生成物がバイト単位で再現可能になる
    # （公開版を作り直すたびに音声が差分として出るのを防ぐ）
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args, "-map_metadata", "-1", out],
                       capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        die(f"効果音の生成に失敗しました: {os.path.basename(out)}\n{(r.stderr or '')[-400:]}")


def sfx_impact(path):
    """低い一撃音。カード出しに合わせる用（sine 2本 ＋ ノイズの立ち上がり）。"""
    _ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=110:duration=0.55",
        "-f", "lavfi", "-i", "sine=frequency=165:duration=0.55",
        "-f", "lavfi", "-i", "anoisesrc=duration=0.09:color=pink:amplitude=0.6:seed=1031",
        "-filter_complex",
        "[0]volume=1.0,afade=t=out:st=0:d=0.55:curve=exp[a];"
        "[1]volume=0.5,afade=t=out:st=0:d=0.35:curve=exp[b];"
        "[2]highpass=f=400,afade=t=out:st=0:d=0.09[c];"
        "[a][b][c]amix=inputs=3:duration=longest:normalize=0,"
        "alimiter=limit=0.9,aformat=sample_rates=48000:channel_layouts=stereo",
        "-c:a", "libmp3lame", "-b:a", "128k",
    ], path)


def sfx_transition(path):
    """場面転換のスウッシュ（ノイズを帯域掃引）。"""
    _ffmpeg([
        "-f", "lavfi", "-i", "anoisesrc=duration=0.42:color=white:amplitude=0.5:seed=2048",
        "-af",
        "bandpass=f=1200:width_type=o:w=2,"
        "afade=t=in:st=0:d=0.10,afade=t=out:st=0.16:d=0.26,"
        "volume=1.4,alimiter=limit=0.9,"
        "aformat=sample_rates=48000:channel_layouts=stereo",
        "-c:a", "libmp3lame", "-b:a", "128k",
    ], path)


# ---------------------------------------------------------------- プロジェクト

def audio_dur(path):
    """mp3の実尺。エンコード時のパディングで指定値とズレるので実測する
    （クリップ長が素材長を超えると patch.py --check に弾かれる）。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path],
                       capture_output=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        die(f"尺を測れませんでした: {path}")


def project_json(title, d_impact, d_trans, logo_w=0.062):
    """12秒のデモ。強調文字・円マスク・寄り引き・効果音を一通り見せる。"""
    si = round(d_impact - 0.02, 2)          # 効果音は実尺いっぱいまで使わない（丸め誤差ぶん短く）
    st = round(d_trans - 0.02, 2)
    cap = {"align": "center", "valign": "middle", "bold": True, "outline": True,
           "shadow": True, "textColor": [255, 255, 255]}
    return {
        "meta": {"title": title, "fps": 30,
                 "madeBy": {"tool": "make_sample.py", "script": "make_sample.py"}},
        "canvas": {"w": W, "h": H, "bg": "#121826"},
        "audio": {"loudnorm": {"on": True}, "limiter": {"on": True, "db": -1.5}},
        "cuts": [], "_zorder": True,
        "tracks": [
            {"id": "logo", "type": "image", "label": "ロゴ", "clips": [
                {"src": "logo.png", "start": 0, "end": 12,
                 "x": round(0.955 - logo_w, 3), "y": 0.035, "w": logo_w}]},
            {"id": "cap", "type": "caption", "label": "字幕", "clips": [
                # フックは大きく・ポップで入れる
                {"start": 0.25, "end": 3.6, "text": "ことばで作って **そのまま編集**",
                 "x": 0.08, "y": 0.74, "w": 0.84, "h": 0.16, "fontsize": 66,
                 "anim": "pop", **cap},
                {"start": 3.9, "end": 7.4, "text": "画像は角丸にできます（**1.0 で円**）",
                 "x": 0.08, "y": 0.76, "w": 0.84, "h": 0.13, "fontsize": 52, **cap},
                {"start": 7.7, "end": 11.8, "text": "数字は**色で立たせる**",
                 "x": 0.08, "y": 0.76, "w": 0.84, "h": 0.13, "fontsize": 56, **cap},
            ]},
            {"id": "front", "type": "image", "label": "前面画像(円マスク)", "clips": [
                {"src": "s2.png", "start": 3.9, "end": 7.4,
                 "x": 0.345, "y": 0.06, "w": 0.31, "radius": 1.0,
                 "motion": {"zoom": 1.06}}]},
            {"id": "bg", "type": "image", "label": "紙芝居", "clips": [
                {"src": "s1.png", "start": 0, "end": 3.9, "x": 0, "y": 0, "w": 1},
                {"src": "s1.png", "start": 3.9, "end": 7.7, "x": 0, "y": 0, "w": 1},
                {"src": "s3.png", "start": 7.7, "end": 12, "x": 0, "y": 0, "w": 1,
                 "motion": {"zoom": 1.07}}]},
            {"id": "sfx", "type": "audio", "label": "効果音", "clips": [
                {"src": "impact.mp3", "start": 0.25, "end": round(0.25 + si, 2), "in": 0, "gain": 0.40},
                {"src": "transition.mp3", "start": 3.9, "end": round(3.9 + st, 2), "in": 0, "gain": 0.24},
                {"src": "transition.mp3", "start": 7.7, "end": round(7.7 + st, 2), "in": 0, "gain": 0.24},
                {"src": "impact.mp3", "start": 11.2, "end": round(11.2 + si, 2), "in": 0, "gain": 0.34}]},
        ],
    }



def main():
    ap = argparse.ArgumentParser(description="サンプルプロジェクトを素材ごと生成する")
    ap.add_argument("--name", default="sample-hello")
    ap.add_argument("--assets-only", action="store_true", help="素材だけ作り直す")
    a = ap.parse_args()

    _deps.require("ffmpeg", "ffprobe", "Pillow")
    pdir = os.path.join(ROOT, "projects", a.name)
    os.makedirs(pdir, exist_ok=True)
    p = lambda n: os.path.join(pdir, n)

    print("画像を生成中…")
    kind = logo_mark(p("logo.png"))
    slide_shapes(p("s1.png"))
    slide_grid(p("s2.png"))
    slide_bars(p("s3.png"))

    print("効果音を生成中…")
    sfx_impact(p("impact.mp3"))
    sfx_transition(p("transition.mp3"))

    if not a.assets_only:
        # 横長ロゴは幅を広く取る（正方形の抽象マークと同じ幅だと潰れて見える）
        with Image.open(p("logo.png")) as im:
            logo_w = 0.20 if im.width / im.height > 2 else 0.062
        pj = project_json(a.name, audio_dur(p("impact.mp3")), audio_dur(p("transition.mp3")),
                          logo_w=logo_w)
        fp = p("project.json")
        with open(fp + ".tmp", "w", encoding="utf-8") as f:
            json.dump(pj, f, ensure_ascii=False, indent=1)
        os.replace(fp + ".tmp", fp)

    total = sum(os.path.getsize(p(n)) for n in os.listdir(pdir)
                if os.path.isfile(p(n)))
    print(f"✅ projects/{a.name}（{total // 1024} KB／ロゴ: "
          + ("素材キットのブランドロゴ" if kind == "brand" else "生成した抽象マーク") + "）")
    print(f"   確認: python3 renderer/render.py projects/{a.name}")


if __name__ == "__main__":
    main()
