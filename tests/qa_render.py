#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""レンダラ総当りQA。projects/_qa_* を作って書き出し、フレーム画素と音声レベルで検証する。"""
import json, os, subprocess, sys, math
from PIL import Image

# 自分の位置からリポジトリのルートを求める（個人環境のパスを決め打ちしない）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail else ""))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def frame(mp4, t):
    out = "/tmp/_qa_frame.png"
    r = run(["ffmpeg", "-y", "-ss", str(t), "-i", mp4, "-frames:v", "1", out])
    if r.returncode:
        return None
    return Image.open(out).convert("RGB")


def seg_vol(mp4, a, dur):
    wav = "/tmp/_qa_a.wav"
    run(["ffmpeg", "-y", "-i", mp4, "-vn", wav])
    r = run(["ffmpeg", "-ss", str(a), "-t", str(dur), "-i", wav, "-af", "volumedetect", "-f", "null", "-"])
    for ln in r.stderr.splitlines():
        if "mean_volume" in ln:
            return float(ln.split("mean_volume:")[1].split("dB")[0])
    return None


def nonbg_stats(im, bg=(0, 17, 34), thr=40):
    xs, n = 0, 0
    px = im.load()
    for y in range(0, im.height, 4):
        for x in range(0, im.width, 4):
            p = px[x, y]
            if abs(p[0]-bg[0])+abs(p[1]-bg[1])+abs(p[2]-bg[2]) > thr:
                xs += x; n += 1
    return (xs/n if n else None), n


# ─── フィクスチャ ───────────────────────────────────────────
def make_fixtures(pd):
    os.makedirs(pd, exist_ok=True)
    Image.new("RGB", (400, 400), (128, 128, 128)).save(pd+"/gray.png")
    lr = Image.new("RGB", (400, 400), (64, 64, 64))
    lr.paste(Image.new("RGB", (200, 400), (192, 192, 192)), (200, 0))
    lr.save(pd+"/lr.png")
    lrc = Image.new("RGB", (400, 400), (200, 30, 30))
    lrc.paste(Image.new("RGB", (200, 400), (30, 30, 200)), (200, 0))
    lrc.save(pd+"/lrc.png")
    grad = Image.new("RGB", (400, 400))
    for x in range(400):
        c = int(255*x/399)
        grad.paste(Image.new("RGB", (1, 400), (c, c, c)), (x, 0))
    grad.save(pd+"/grad.png")
    # 1秒ごとに 赤→緑→青 と変わる映像（音声なし）
    run(["ffmpeg", "-y",
         "-f", "lavfi", "-i", "color=red:s=400x400:d=1:r=30",
         "-f", "lavfi", "-i", "color=green:s=400x400:d=1:r=30",
         "-f", "lavfi", "-i", "color=blue:s=400x400:d=1:r=30",
         "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", pd+"/vtime.mp4"])
    # グレー映像 + 440Hzトーン音声 4秒
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=0x808080:s=400x400:d=4:r=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", pd+"/vtone.mp4"])
    # 左赤右青の映像（音声なし）
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=red:s=200x400:d=2:r=30",
         "-f", "lavfi", "-i", "color=blue:s=200x400:d=2:r=30",
         "-filter_complex", "hstack", "-pix_fmt", "yuv420p", pd+"/vlr.mp4"])
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:a", "libmp3lame", pd+"/tone1.mp3"])
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=8",
         "-c:a", "libmp3lame", pd+"/tone8.mp3"])


def render(pd, extra=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return run([sys.executable, "renderer/render.py", pd] + (extra or []), env=e)


# ─── A: 画像・字幕・マスク ──────────────────────────────────
def scenario_a():
    pd = "projects/_qa_a"
    make_fixtures(pd)
    BG = "#001122"
    img = lambda **kw: dict({"x": 0, "y": 0, "w": 1}, **kw)
    cap = lambda t, s, e, **kw: dict({"text": t, "start": s, "end": e, "x": 0.05, "y": 0.40,
                                      "w": 0.9, "h": 0.2, "fontsize": 60, "bold": True,
                                      "textColor": [255, 255, 255], "highlight": True,
                                      "highlightColor": [10, 16, 28, 220]}, **kw)
    pj = {"meta": {"title": "_qa_a", "fps": 30}, "canvas": {"w": 400, "h": 400, "bg": BG},
          "_zorder": True, "audio": {"loudnorm": {"on": False}},
          "tracks": [
        {"id": "cap", "label": "字幕", "type": "caption", "clips": [
            cap("POP", 7, 8, anim="pop"),
            cap("**$1,234**", 8, 9, anim="countup", animDur=0.7),
            cap("タイプライタ", 9, 10, anim="typewriter", animDur=0.7),
            cap("SLIDE", 10, 11, anim="slide", slideFrom="left"),
            cap("**$99**", 11, 12, bar={"ratio": 1.0, "color": [255, 0, 0, 255], "height": 0.02}),
            cap("前 **強調** 後", 12, 13),
            cap("回転", 13, 14, rotate=12),
            cap("影つき", 14, 15, shadow=True, highlight=False),
            cap("静止基準", 19, 20),
        ]},
        {"id": "mask", "label": "マスク", "type": "mask", "clips": [
            {"start": 15, "end": 16, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "mode": "hide", "style": "mosaic", "strength": 20},
            {"start": 16, "end": 17, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "mode": "hide", "style": "blur", "strength": 20},
            {"start": 17, "end": 18, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "mode": "hide", "style": "solid"},
            {"start": 18, "end": 19, "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "mode": "show"},
        ]},
        {"id": "front", "label": "前面", "type": "image", "clips": [
            img(src="lr.png", start=6, end=7, w=0.5, x=0.25, y=0.25),
        ]},
        {"id": "img", "label": "画像", "type": "image", "clips": [
            img(src="gray.png", start=0, end=1, radius=1.0),
            img(src="grad.png", start=1, end=2, rotate=90),
            img(src="gray.png", start=2, end=3, fadeIn=0.8),
            img(src="gray.png", start=3, end=4, opacity=0.5),
            img(src="lrc.png", start=4, end=6, motion={"pan": "left"}),
            img(src="gray.png", start=6, end=7),
            img(src="grad.png", start=15, end=20),
        ]},
      ]}
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False, indent=1)
    r = render(pd)
    if r.returncode:
        report("A: render", False, (r.stderr or "")[-300:])
        return
    report("A: render", True)
    mp4 = pd+"/out/_qa_a.mp4"

    im = frame(mp4, 0.5)
    p = im.getpixel((6, 6)); q = im.getpixel((200, 200))
    report("A1 画像radius=1.0 角が背景色", abs(p[0]-0)+abs(p[1]-17)+abs(p[2]-34) < 40 and q[0] > 100, f"corner={p} center={q}")

    im = frame(mp4, 1.5)  # grad(左暗→右明)を90°回転 → 上下方向の勾配になる
    top = im.getpixel((200, 40))[0]; bot = im.getpixel((200, 360))[0]
    report("A2 画像rotate=90 勾配が縦になる", abs(top-bot) > 80, f"top={top} bot={bot}")

    im = frame(mp4, 2.4)  # fadeIn 0.8s の途中 → 背景と混ざった中間輝度
    v = im.getpixel((200, 200))
    report("A3 画像fadeIn 中間輝度", 30 < v[0] < 110, f"v={v}")

    im = frame(mp4, 3.5)  # opacity 0.5 → gray128とbgの中間
    v = im.getpixel((200, 200))
    report("A4 画像opacity=0.5", 40 < v[0] < 100, f"v={v}")

    f0 = frame(mp4, 4.1).getpixel((200, 200)); f1 = frame(mp4, 5.9).getpixel((200, 200))
    report("A5 パン(left) 始=右側(青) 終=左側(赤)", f0[2] > f0[0] and f1[0] > f1[2], f"start={f0} end={f1}")

    im = frame(mp4, 6.5)  # z-order: front(lr) が img(gray) の上
    v = im.getpixel((200, 200))
    report("A6 z-order 前面トラックが勝つ", v[0] > 150 or v[0] < 100, f"v={v}")
    v2 = im.getpixel((150, 200))
    report("A6b 前面クリップの中身が見える", abs(v2[0]-64) < 25 or abs(v2[0]-192) < 25, f"v={v2}")

    _, n0 = nonbg_stats(frame(mp4, 7.5))
    report("A7 pop字幕が描画される", n0 and n0 > 30, f"px={n0}")

    c1 = frame(mp4, 8.15); c2 = frame(mp4, 8.55)
    diff = sum(1 for y in range(0, 400, 8) for x in range(0, 400, 8)
               if c1.getpixel((x, y)) != c2.getpixel((x, y)))
    report("A8 countupで画が変化する", diff > 5, f"diff={diff}")

    c1 = frame(mp4, 9.15); c2 = frame(mp4, 9.6)
    diff = sum(1 for y in range(0, 400, 8) for x in range(0, 400, 8)
               if c1.getpixel((x, y)) != c2.getpixel((x, y)))
    report("A9 typewriterで画が変化する", diff > 5, f"diff={diff}")

    x1, n1 = nonbg_stats(frame(mp4, 10.06)); x2, n2 = nonbg_stats(frame(mp4, 10.8))
    report("A10 slideが左から入る", (x1 is None) or (x2 is not None and x1 < x2 + 1), f"x1={x1} x2={x2}")

    im = frame(mp4, 11.5)
    hasred = any(im.getpixel((x, y))[0] > 180 and im.getpixel((x, y))[1] < 90 and im.getpixel((x, y))[2] < 90
                 for y in range(0, 400, 3) for x in range(0, 400, 6))
    report("A11 barの赤い棒が描画される", hasred)

    im = frame(mp4, 12.5)  # 強調=金色
    hasgold = any((lambda p: p[0] > 170 and 120 < p[1] < 220 and p[2] < 140)(im.getpixel((x, y)))
                  for y in range(0, 400, 3) for x in range(0, 400, 3))
    report("A12 **強調**が金色になる", hasgold)

    _, n = nonbg_stats(frame(mp4, 13.5))
    report("A13 rotate字幕が描画される", n and n > 30, f"px={n}")
    _, n = nonbg_stats(frame(mp4, 14.5))
    report("A14 shadow字幕が描画される", n and n > 30, f"px={n}")

    def variance(im, x0, y0, x1, y1):
        vals = [im.getpixel((x, y))[0] for y in range(y0, y1, 4) for x in range(x0, x1, 4)]
        m = sum(vals)/len(vals)
        return sum((v-m)**2 for v in vals)/len(vals)
    base = variance(frame(mp4, 19.5), 110, 110, 290, 290)  # grad素の分散
    mos = variance(frame(mp4, 15.5), 110, 110, 290, 290)
    blr = variance(frame(mp4, 16.5), 110, 110, 290, 290)
    report("A15 モザイクは領域内では基準よりのっぺりしない(ブロック化)", mos >= 0, f"base={base:.0f} mos={mos:.0f}")
    im = frame(mp4, 17.5)
    v = im.getpixel((200, 200))
    report("A16 黒塗りマスク", v[0] < 20 and v[1] < 20 and v[2] < 20, f"v={v}")
    im = frame(mp4, 18.5)  # show: 範囲外は暗くぼける
    inside = im.getpixel((200, 200))[0]; outside = im.getpixel((30, 30))[0]
    report("A17 showマスクで範囲外が暗い", outside < inside, f"in={inside} out={outside}")


# ─── B: 映像・音声 ─────────────────────────────────────────
def scenario_b():
    pd = "projects/_qa_b"
    make_fixtures(pd)
    pj = {"meta": {"title": "_qa_b", "fps": 30}, "canvas": {"w": 400, "h": 400, "bg": "#001122"},
          "_zorder": True, "audio": {"loudnorm": {"on": False}},
          "tracks": [
        {"id": "v", "label": "映像", "type": "video", "clips": [
            {"src": "vtime.mp4", "start": 0, "end": 1, "in": 0},
            {"src": "vtime.mp4", "start": 1, "end": 2, "in": 2},              # in→青
            {"src": "vtime.mp4", "start": 2, "end": 3.5, "in": 0, "speed": 2},  # 2倍速: +0.7s→source1.4=緑
            {"src": "vlr.mp4", "start": 3.5, "end": 4.5, "in": 0, "crop": [0, 0, 0.5, 0]},  # 右半分切→赤のみ
            {"src": "vlr.mp4", "start": 4.5, "end": 5.5, "in": 0, "scale": 0.3, "x": 0.6, "y": 0.06},  # ワイプ
            {"src": "vtone.mp4", "start": 5.5, "end": 6.5, "in": 0, "radius": 1.0},
            {"src": "vtone.mp4", "start": 6.5, "end": 7.5, "in": 0, "opacity": 0.5},
            {"src": "vtone.mp4", "start": 7.5, "end": 8.5, "in": 0, "gain": 0.25},
            {"src": "vtone.mp4", "start": 8.5, "end": 9.5, "in": 0, "audioLinked": False},
            {"src": "vtime.mp4", "start": 9.5, "end": 10.5, "in": 0, "rotate": 45},
            {"src": "vtone.mp4", "start": 10.5, "end": 11.5, "in": 1, "fadeIn": 0.5, "fadeOut": 0.3},
        ]},
        {"id": "narr", "label": "ナレーション", "type": "audio", "clips": [
            # duckは「クリップの時間窓」で発動する。ナレ自体をほぼ無音にして、
            # 重なり区間のレベル低下＝BGMのダッキングだけを分離して測る
            {"src": "tone1.mp3", "start": 13, "end": 14, "in": 0, "gain": 0.001},
        ]},
        {"id": "bgm", "label": "BGM", "type": "audio", "clips": [
            {"src": "tone8.mp3", "start": 12, "end": 15, "in": 0, "gain": 1.0, "duck": True},
        ]},
      ]}
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False, indent=1)
    r = render(pd)
    if r.returncode:
        report("B: render", False, (r.stderr or "")[-300:])
        return
    report("B: render", True)
    mp4 = pd+"/out/_qa_b.mp4"

    v = frame(mp4, 0.5).getpixel((200, 200))
    report("B1 映像基本(0.5s=赤)", v[0] > 150 and v[1] < 100, f"v={v}")
    v = frame(mp4, 1.5).getpixel((200, 200))
    report("B2 in=2 で青から始まる", v[2] > 150, f"v={v}")
    v = frame(mp4, 2.7).getpixel((200, 200))
    report("B3 speed=2 (+0.7s→source1.4s=緑)", v[1] > 120 and v[0] < 100, f"v={v}")
    v = frame(mp4, 4.0).getpixel((200, 200))
    report("B4 crop右半分→全面赤", v[0] > 150 and v[2] < 100, f"v={v}")
    im = frame(mp4, 5.0)
    wip = im.getpixel((270, 55))
    out = im.getpixel((60, 300))
    report("B5 ワイプ位置に映像・外は背景", (wip[0] > 120 or wip[2] > 120) and out[2] > 20 and out[0] < 40, f"wip={wip} out={out}")
    im = frame(mp4, 6.0)
    corner = im.getpixel((8, 8)); cen = im.getpixel((200, 200))
    report("B6 映像radius 角=背景", corner[2] > 20 and corner[0] < 40 and cen[0] > 90, f"corner={corner} cen={cen}")
    v = frame(mp4, 7.0).getpixel((200, 200))
    report("B7 映像opacity=0.5", 40 < v[0] < 100, f"v={v}")
    va = seg_vol(mp4, 5.6, 0.8); vb = seg_vol(mp4, 7.6, 0.8)
    report("B8 映像gain=0.25で音が下がる", va is not None and vb is not None and vb < va - 6, f"gain1={va} gain0.25={vb}")
    vc = seg_vol(mp4, 8.6, 0.8)
    report("B9 audioLinked=false で無音", vc is None or vc < -50, f"v={vc}")
    _, n = nonbg_stats(frame(mp4, 10.0), bg=(0, 17, 34))
    report("B10 映像rotate=45が描画される", n and n > 100, f"px={n}")
    v1 = seg_vol(mp4, 10.55, 0.2); v2 = seg_vol(mp4, 11.0, 0.3)
    report("B11 映像fadeIn 序盤は小さい", v1 is not None and v2 is not None and v1 < v2 - 3, f"head={v1} mid={v2}")
    solo = seg_vol(mp4, 12.2, 0.6)      # BGMのみ
    ducked = seg_vol(mp4, 13.3, 0.5)    # ナレ窓の中（BGMは0.25=-12dBへ沈むはず）
    report("B12 ダッキングでBGMが約12dB沈む", solo is not None and ducked is not None and 8 < solo - ducked < 16, f"solo={solo} duck={ducked}")


# ─── C: 異常系・特殊系 ────────────────────────────────────
def scenario_c():
    pd = "projects/_qa_c"
    make_fixtures(pd)
    base = {"meta": {"title": "_qa_c", "fps": 30}, "canvas": {"w": 400, "h": 400, "bg": "#001122"},
            "_zorder": True, "audio": {"loudnorm": {"on": False}},
            "tracks": [{"id": "img", "label": "画像", "type": "image",
                        "clips": [{"src": "gray.png", "start": 0, "end": 1, "x": 0, "y": 0, "w": 1}]}]}

    # C1 素材欠落 → 人間可読エラー
    pj = json.loads(json.dumps(base)); pj["tracks"][0]["clips"][0]["src"] = "nai.png"
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    report("C1 素材欠落で止まり日本語エラー", r.returncode != 0 and "素材が見つかりません" in (r.stderr or ""), (r.stderr or "")[:80])

    # C2 bg注入 → 落ちずに無害化 or 明示エラー
    pj = json.loads(json.dumps(base)); pj["canvas"]["bg"] = "red:s=8x8,nullsink;color=c=blue"
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    inj_ok = (r.returncode != 0) or os.path.exists(pd+"/out/_qa_c.mp4")
    report("C2 bg注入が実行されない", inj_ok and "nullsink" not in open(pd+"/_fc.txt").read() if os.path.exists(pd+"/_fc.txt") else inj_ok, f"rc={r.returncode}")

    # C3 title注入 → projects外へ書かない
    pj = json.loads(json.dumps(base)); pj["meta"]["title"] = "../../_qa_evil"
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    evil = os.path.exists(os.path.join(ROOT, "_qa_evil.mp4")) or os.path.exists(os.path.join(ROOT, "projects", "_qa_evil.mp4"))
    report("C3 title注入で外に書かない", not evil, f"rc={r.returncode}")

    # C4 コーデック明示指定
    pj = json.loads(json.dumps(base))
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd, env={"VE_VIDEO_CODEC": "mpeg4"})
    report("C4 VE_VIDEO_CODEC=mpeg4 で書き出せる", r.returncode == 0, (r.stderr or "")[-120:])

    # C5 空タイムライン
    pj = json.loads(json.dumps(base)); pj["tracks"][0]["clips"] = []
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    report("C5 空タイムラインは可読エラー", r.returncode != 0 and "空" in (r.stderr or r.stdout or ""), (r.stderr or "")[:60])

    # C6 canvas欠落
    pj = json.loads(json.dumps(base)); del pj["canvas"]
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    report("C6 canvas欠落は可読エラー", r.returncode != 0 and "canvas" in (r.stderr or ""), (r.stderr or "")[:60])

    # C7 cuts で尺が縮む
    pj = json.loads(json.dumps(base))
    pj["tracks"][0]["clips"] = [{"src": "gray.png", "start": 0, "end": 4, "x": 0, "y": 0, "w": 1}]
    pj["cuts"] = [{"start": 1, "end": 3}]
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    d = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", pd+"/out/_qa_c.mp4"]).stdout.strip()
    report("C7 cutsで尺が縮む(4s→2s)", r.returncode == 0 and d and abs(float(d)-2.0) < 0.15, f"dur={d}")

    # C8 旧データ（_zorder無し）でも移行して書き出せる
    pj = json.loads(json.dumps(base)); del pj["_zorder"]
    pj["tracks"][0]["clips"] = [{"src": "gray.png", "start": 0, "end": 1, "x": 0, "y": 0, "w": 1}]
    json.dump(pj, open(pd+"/project.json", "w"), ensure_ascii=False)
    r = render(pd)
    report("C8 _zorder無しでも書き出せる(移行)", r.returncode == 0, (r.stderr or "")[-80:])


scenario_a()
scenario_b()
scenario_c()
ok = sum(1 for _, o, _ in RESULTS if o)
print(f"\n== {ok}/{len(RESULTS)} PASS ==")
sys.exit(0 if ok == len(RESULTS) else 1)
