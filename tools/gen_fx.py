#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""エフェクト素材ジェネレータ（生成AIに頼らず、PILで決定的に一瞬で作る）。

いまは集中線のみ。漫画の効果線＝枠の外周から中心へ向かうテーパー付きの線。
中央は楕円状に抜けていて、下の紙芝居イラストが見える。

使い方:
  python3 tools/gen_fx.py lines --out projects/<名>/fx_lines.png [--w 1344 --h 768]

使いどころ（流儀はCLAUDE.md「モーション・エフェクトの流儀」参照）:
  衝撃・驚きの一撃の瞬間だけ、紙芝居の前面トラックに 0.8〜1.5秒 置く。
  効果音（text-impact等）と同じタイミングに置くと効く。常時表示は禁止。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import argparse, math, os, random, sys
from PIL import Image, ImageDraw


def gen_lines(w, h, n=110, color=(20, 26, 38), alpha=210, inner=0.34, seed=7):
    """集中線PNG（RGBA・中央透過）を返す。seed固定＝再現可能。"""
    rng = random.Random(seed)
    # 2倍で描いて縮小（エッジを滑らかに）
    W, H = w * 2, h * 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2
    r_out = math.hypot(W, H) / 2 + 8          # 枠の角より外
    for _ in range(n):
        th = rng.uniform(0, 2 * math.pi)
        # 内端: 中央の楕円（半径にゆらぎ）— ここまで線が届く
        rin_x = W * inner * rng.uniform(0.82, 1.25)
        rin_y = H * inner * rng.uniform(0.82, 1.25)
        rin = (rin_x * rin_y) / math.hypot(rin_y * math.cos(th), rin_x * math.sin(th))
        # 線 = 外周で太く内端で尖る三角形
        half_w = rng.uniform(3, 13)           # 外周での半幅(px, 2倍解像度)
        a = rng.uniform(140, 255) * (alpha / 255)
        dx, dy = math.cos(th), math.sin(th)
        px, py = -dy, dx                      # 法線
        tip = (cx + dx * rin, cy + dy * rin)
        b1 = (cx + dx * r_out + px * half_w, cy + dy * r_out + py * half_w)
        b2 = (cx + dx * r_out - px * half_w, cy + dy * r_out - py * half_w)
        d.polygon([tip, b1, b2], fill=(*color, int(a)))
    return img.resize((w, h), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["lines"], help="生成する素材の種類")
    ap.add_argument("--out", required=True)
    ap.add_argument("--w", type=int, default=1344)
    ap.add_argument("--h", type=int, default=768)
    ap.add_argument("--lines", type=int, default=110, help="線の本数")
    ap.add_argument("--inner", type=float, default=0.34, help="中央の抜き（0.25=狭い〜0.45=広い）")
    a = ap.parse_args()
    img = gen_lines(a.w, a.h, n=a.lines, inner=a.inner)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    img.save(a.out)
    print(f"✅ {a.out} ({a.w}x{a.h}, 線{a.lines}本)")


if __name__ == "__main__":
    main()
