#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch.py 総当りQA: 全op・セレクタ・検証（errors/warnings）・SRT・dry-run。"""
import json, os, shutil, subprocess, sys
from PIL import Image

# 自分の位置からリポジトリのルートを求める（個人環境のパスを決め打ちしない）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PD = "projects/_qa_p"
RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail else ""))


def patch(args):
    return subprocess.run([sys.executable, "tools/patch.py", "_qa_p"] + args,
                          capture_output=True, text=True)


def ops(o, extra=None):
    return patch(["--ops", json.dumps(o, ensure_ascii=False)] + (extra or []))


def pj():
    return json.load(open(PD + "/project.json", encoding="utf-8"))


def reset():
    os.makedirs(PD, exist_ok=True)
    Image.new("RGB", (100, 100), (120, 120, 120)).save(PD + "/a.png")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "libmp3lame", PD + "/t2.mp3"], capture_output=True)
    base = {
        "meta": {"title": "_qa_p", "fps": 30}, "canvas": {"w": 400, "h": 400, "bg": "#101010"},
        "_zorder": True,
        "tracks": [
            {"id": "cap", "label": "🔤 字幕", "type": "caption", "clips": [
                {"text": "ひとつめ", "start": 0, "end": 2, "x": 0.05, "y": 0.4, "w": 0.9, "h": 0.2,
                 "fontsize": 40, "textColor": [255, 255, 255], "highlight": True,
                 "highlightColor": [10, 16, 28, 220]},
                {"text": "ふたつめ", "start": 3, "end": 5, "x": 0.05, "y": 0.4, "w": 0.9, "h": 0.2,
                 "fontsize": 40, "textColor": [255, 255, 255], "highlight": True,
                 "highlightColor": [10, 16, 28, 220]},
                {"text": "みっつめ", "start": 6, "end": 8, "x": 0.05, "y": 0.4, "w": 0.9, "h": 0.2,
                 "fontsize": 40, "textColor": [255, 255, 255], "highlight": True,
                 "highlightColor": [10, 16, 28, 220]},
            ]},
            {"id": "cap2", "label": "字幕2", "type": "caption", "clips": []},
            {"id": "img", "label": "画像", "type": "image", "clips": [
                {"src": "a.png", "start": 0, "end": 4, "x": 0, "y": 0, "w": 1},
                {"src": "a.png", "start": 5, "end": 8, "x": 0, "y": 0, "w": 1},
            ]},
            {"id": "bgm", "label": "BGM", "type": "audio", "clips": [
                {"src": "t2.mp3", "start": 0, "end": 2, "in": 0},
            ]},
        ]}
    json.dump(base, open(PD + "/project.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ── --show / --check ──────────────────────────────────────
reset()
r = patch(["--show"])
report("P1 --show が構造を出す", r.returncode == 0 and "ひとつめ" in r.stdout and "img" in r.stdout)
r = patch(["--check"])
report("P2 --check 鉄則違反なし", r.returncode == 0 and "違反なし" in r.stdout)

# ── set: id / label部分一致 / 絵文字無視 / match / * ──────
r = ops([{"op": "set", "track": "cap", "clip": 0, "set": {"fontsize": 50}}])
report("P3 set(track=id, clip=index)", r.returncode == 0 and pj()["tracks"][0]["clips"][0]["fontsize"] == 50)
r = ops([{"op": "set", "track": "字幕", "clip": 1, "set": {"fontsize": 51}}])
report("P4 set(track=label・絵文字無視)", r.returncode == 0 and pj()["tracks"][0]["clips"][1]["fontsize"] == 51)
r = ops([{"op": "set", "track": "img", "clip": "*", "set": {"opacity": 0.8}}])
report("P5 set(clip=*)", r.returncode == 0 and all(c.get("opacity") == 0.8 for c in pj()["tracks"][2]["clips"]))
r = ops([{"op": "set", "track": "img", "clip": {"match": {"src": "a.png"}}, "set": {"radius": 0.2}}])
report("P6 set(clip=match src)", r.returncode == 0 and all(c.get("radius") == 0.2 for c in pj()["tracks"][2]["clips"]))
r = ops([{"op": "set", "track": "cap", "clip": 0, "set": {"fontsize": None}}])
report("P7 set(None でキー削除)", r.returncode == 0 and "fontsize" not in pj()["tracks"][0]["clips"][0])

# ── shift / retime / ripple ───────────────────────────────
reset()
r = ops([{"op": "shift", "track": "cap", "clip": 1, "by": -0.5}])
c = pj()["tracks"][0]["clips"][1]
report("P8 shift -0.5", r.returncode == 0 and c["start"] == 2.5 and c["end"] == 4.5)
r = ops([{"op": "retime", "track": "cap", "clip": 0, "start": 0.5, "end": 1.5}])
c = pj()["tracks"][0]["clips"][0]
report("P9 retime", r.returncode == 0 and c["start"] == 0.5 and c["end"] == 1.5)
reset()
r = ops([{"op": "ripple", "track": "cap", "from": 3.0, "by": -1.0}])
t = pj()["tracks"][0]["clips"]
report("P10 ripple 3s以降を-1s", r.returncode == 0 and t[1]["start"] == 2 and t[2]["start"] == 5)

# ── delete / add / move / addtrack / setroot ─────────────
reset()
r = ops([{"op": "delete", "track": "cap", "clip": 2}])
report("P11 delete", r.returncode == 0 and len(pj()["tracks"][0]["clips"]) == 2)
r = ops([{"op": "add", "track": "cap2", "clip": {"text": "追加", "start": 0, "end": 1, "x": 0.05, "y": 0.1,
                                                "w": 0.9, "h": 0.15, "fontsize": 40,
                                                "textColor": [255, 255, 255], "highlight": True,
                                                "highlightColor": [10, 16, 28, 220]}}])
report("P12 add", r.returncode == 0 and len(pj()["tracks"][1]["clips"]) == 1)
r = ops([{"op": "move", "track": "cap", "clip": 1, "to": "cap2"}])  # ふたつめ(3-5)→cap2（重ならない）
p = pj()
report("P13 move 別トラックへ", r.returncode == 0 and len(p["tracks"][0]["clips"]) == 1 and len(p["tracks"][1]["clips"]) == 2)
before13 = pj()
r = ops([{"op": "move", "track": "cap2", "clip": "*", "to": "cap"}])
report("P13c move 重なりは保存拒否", r.returncode != 0 and pj() == before13, (r.stderr or r.stdout)[:60])
r = ops([{"op": "addtrack", "track": {"id": "img2", "label": "画像2", "type": "image", "clips": []}}])
report("P14 addtrack", r.returncode == 0 and any(t.get("id") == "img2" for t in pj()["tracks"]))
r = ops([{"op": "setroot", "key": "audio", "merge": {"loudnorm": {"on": False}}}])
report("P15 setroot merge", r.returncode == 0 and pj()["audio"]["loudnorm"]["on"] is False)
r = ops([{"op": "setroot", "key": "canvas", "merge": {"bg": "#222222"}}])
report("P16 setroot canvas.bg", r.returncode == 0 and pj()["canvas"]["bg"] == "#222222")

# ── 検証（errors で保存拒否）─────────────────────────────
reset()
before = pj()
r = ops([{"op": "retime", "track": "cap", "clip": 1, "start": 1.0, "end": 4.0}])  # clip0(0-2)と重なる
report("P17 重なりはエラーで保存拒否", r.returncode != 0 and pj() == before, (r.stderr or r.stdout)[:80])
r = ops([{"op": "retime", "track": "bgm", "clip": 0, "start": 0, "end": 5}])  # t2.mp3=2s
report("P18 ソース長超過はエラー", r.returncode != 0 and pj() == before, (r.stderr or r.stdout)[:80])
r = ops([{"op": "set", "track": "bgm", "clip": 0, "set": {"loop": True}},
         {"op": "retime", "track": "bgm", "clip": 0, "start": 0, "end": 5}])
report("P19 loop=trueなら超過OK", r.returncode == 0 and pj()["tracks"][3]["clips"][0]["end"] == 5)
reset()
r = ops([{"op": "retime", "track": "cap", "clip": 0, "start": 2.0, "end": 1.0}])
report("P20 start>=end はエラー", r.returncode != 0 and pj() == before)

# ── 警告（保存は続行）────────────────────────────────────
reset()
r = ops([{"op": "set", "track": "cap", "clip": 0, "set": {"text": "饺子实铺"}}])  # 日本語フォントに無いグリフ
out = (r.stdout or "") + (r.stderr or "")
report("P21 豆腐グリフは警告して保存続行", r.returncode == 0 and ("描けない" in out or "豆腐" in out or "⚠" in out), out[:100])
reset()
r = ops([{"op": "set", "track": "cap", "clip": 0,
          "set": {"textColor": [200, 200, 200], "highlightColor": [190, 190, 190, 255]}}])
out = (r.stdout or "") + (r.stderr or "")
report("P22 低コントラストは警告", r.returncode == 0 and ("コントラスト" in out or "⚠" in out), out[:100])
reset()
r = ops([{"op": "add", "track": "img", "clip": {"src": "nai.png", "start": 10, "end": 11, "x": 0, "y": 0, "w": 1}}])
out = (r.stdout or "") + (r.stderr or "")
report("P23 素材未配置は警告で保存続行", r.returncode == 0 and "未配置" in out, out[:100])

# ── dry-run ──────────────────────────────────────────────
reset()
before = pj()
r = ops([{"op": "set", "track": "cap", "clip": 0, "set": {"fontsize": 99}}], ["--dry-run"])
report("P24 --dry-run は書き込まない", r.returncode == 0 and pj() == before)

# ── SRT 入出力 ───────────────────────────────────────────
reset()
r = patch(["--srt"])
srt = PD + "/out/_qa_p.srt"
report("P25 --srt 書き出し", r.returncode == 0 and os.path.exists(srt) and "ひとつめ" in open(srt, encoding="utf-8").read())
r = patch(["--srt-import", srt])
p = pj()
srttr = [t for t in p["tracks"] if t.get("id") == "srt"]
report("P26 --srt-import 新トラックに入る", r.returncode == 0 and srttr and len(srttr[0]["clips"]) == 3)
# ラウンドトリップ
r = patch(["--srt-track", "srt", "--srt"])
s2 = open(srt, encoding="utf-8").read()
report("P27 SRTラウンドトリップ", "ひとつめ" in s2 and "みっつめ" in s2)

# ── 不正入力 ─────────────────────────────────────────────
r = ops([{"op": "set", "track": "存在しない", "clip": 0, "set": {"x": 0}}])
report("P28 不明トラックは可読エラー", r.returncode != 0, (r.stderr or r.stdout)[:80])
r = ops([{"op": "なにこれ"}])
report("P29 不明opは可読エラー", r.returncode != 0, (r.stderr or r.stdout)[:80])
r = patch(["--ops", "{壊れたjson"])
report("P30 壊れたJSONは可読エラー", r.returncode != 0, (r.stderr or r.stdout)[:80])

# ── Windows想定（cp932コンソール）───────────────────────
env = dict(os.environ); env["PYTHONIOENCODING"] = "cp932"
r = subprocess.run([sys.executable, "tools/patch.py", "_qa_p", "--check"],
                   capture_output=True, env=env)
report("P31 cp932コンソールでも落ちない", r.returncode == 0, (r.stderr or b"").decode("utf-8", "replace")[:80])

ok = sum(1 for _, o, _ in RESULTS if o)
print(f"\n== {ok}/{len(RESULTS)} PASS ==")
sys.exit(0 if ok == len(RESULTS) else 1)
