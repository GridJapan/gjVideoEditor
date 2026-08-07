#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""サーバAPI QA（隔離ポート8790・プロジェクト projects/_qa_p）。"""
import json, os, sys, urllib.request, urllib.error

BASE = "http://127.0.0.1:8790"
RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail else ""))


def req(path, method="GET", body=None, headers=None, raw=False):
    r = urllib.request.Request(BASE + path, method=method,
                               data=(body if isinstance(body, bytes) else
                                     json.dumps(body).encode()) if body is not None else None,
                               headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=120) as res:
            data = res.read()
            return res.status, (data if raw else data.decode("utf-8", "replace")), dict(res.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as ex:
        return -1, str(ex), {}


# ── 読み取り系 ──
st, body, hd = req("/api/project")
ver = hd.get("X-Project-Version", "")
report("S1 GET /api/project", st == 200 and "_qa_p" in body and ver != "", f"ver={ver}")
st, body, _ = req("/api/projects")
report("S2 GET /api/projects 一覧", st == 200 and "_qa_p" in body)
st, body, _ = req("/api/version")
report("S3 GET /api/version", st == 200)
st, body, _ = req("/asset?name=a.png", raw=True)
report("S4 GET /asset 素材", st == 200 and len(body) > 100)
st, body, _ = req("/asset?name=../../renderer/render.py")
report("S5 /asset パストラバーサル拒否", st != 200 or "def render" not in str(body), f"st={st}")
st, body, _ = req("/asset?name=nai.png")
report("S6 /asset 無い素材は404", st == 404, f"st={st}")
st, body, _ = req("/waveform?name=t2.mp3", raw=True)
report("S7 /waveform", st == 200 and len(body) > 100, f"st={st}")

# ── 保存（版の照合）──
st, body, hd = req("/api/project")
ver = hd.get("X-Project-Version", "")
pj = json.loads(body)
st2, body2, hd2 = req(f"/api/project?from=_qa_p&v={ver}", "POST", pj,
                      {"Content-Type": "application/json"})
report("S8 保存(版一致)", st2 == 200, f"st={st2} {body2[:60]}")
st3, body3, _ = req("/api/project?from=_qa_p&v=946684800.0", "POST", pj,
                    {"Content-Type": "application/json"})
report("S9 版ずれは409", st3 == 409, f"st={st3}")

# ── アップロード ──
st, body, _ = req("/api/upload?name=up.png", "POST", open("projects/_qa_p/a.png", "rb").read())
report("S10 upload 素材", st == 200 and os.path.exists("projects/_qa_p/up.png"))
st, body, _ = req("/api/upload?name=project.json", "POST", b"{}")
report("S11 upload project.json拒否", st == 400)
st, body, _ = req("/api/upload?name=.evil", "POST", b"x")
report("S12 upload 隠しファイル拒否", st == 400)

# ── 字幕プレビュー ──
st, body, _ = req("/api/caption-preview", "POST",
                  {"clip": {"text": "テスト", "x": 0.05, "y": 0.4, "w": 0.9, "h": 0.2, "fontsize": 40,
                            "textColor": [255, 255, 255], "highlight": True,
                            "highlightColor": [10, 16, 28, 220], "start": 0, "end": 1},
                   "canvas": {"w": 400, "h": 400}, "style": {}}, raw=True)
report("S13 caption-preview PNGが返る", st == 200 and bytes(body[:4]) == b"\x89PNG", f"st={st}")
st, body, _ = req("/api/caption-preview", "POST", {"clip": {}}, raw=False)
report("S13b caption-preview 不正入力は400", st == 400, f"st={st}")

# ── レンダリング・書き出し ──
st, body, _ = req("/api/render", "POST", {})
report("S14 /api/render", st == 200, body[:120])
st, body, _ = req("/api/export", "POST", {"format": "mp4", "quality": "std"})
d = json.loads(body) if st == 200 else {}
report("S15 export mp4/std", st == 200 and d.get("ok"), body[:100])
st, body, _ = req("/api/export", "POST", {"format": "mp3", "quality": "light"})
report("S16 export mp3", st == 200, body[:100])
st, body, _ = req("/api/export", "POST", {"format": "gif", "quality": "light", "height": 240})
report("S17 export gif 240p", st == 200, body[:100])
st, body, _ = req("/api/export", "POST", {"format": "mp4", "quality": "std", "range": [0.5, 1.5]})
d = json.loads(body) if st == 200 else {}
report("S18 export 範囲指定", st == 200 and "0.5-1.5s" in (d.get("name") or ""), body[:100])
st, body, _ = req("/api/export", "POST", {"format": "mp4", "quality": "std", "range": [3, 1]})
report("S19 export 逆範囲は400", st == 400, body[:80])
st, body, _ = req("/api/export", "POST", {"format": "exe", "quality": "std"})
report("S20 export 不正形式は400", st == 400)
st, body, _ = req("/api/export", "POST", {"format": "mp4", "quality": "std", "save_dir": "/nai/basho"})
report("S21 export 保存先不在は400", st == 400, body[:80])

# ── 動画一覧・サムネ ──
st, body, _ = req("/api/videos")
report("S22 /api/videos 一覧", st == 200 and "_qa_p" in body, body[:80])
try:
    path = json.loads(body)["videos"][0]["path"]
except Exception:
    path = None
if path:
    st, body, _ = req("/api/thumb?path=" + urllib.parse.quote(path), raw=True)
    report("S23 /api/thumb", st == 200 and len(body) > 100, f"st={st}")
    st, body, _ = req("/api/thumb?path=" + urllib.parse.quote("../../CLAUDE.md"))
    report("S23b thumb パス脱出は404", st == 404, f"st={st}")
    st, body, hd = req("/api/video?path=" + urllib.parse.quote(path), headers={"Range": "bytes=0-99"}, raw=True)
    report("S23c /api/video Range=206", st == 206 and len(body) == 100, f"st={st}")
else:
    report("S23 /api/thumb", False, "一覧が読めない")

# ── zip 書き出し／取り込み ──
st, zbody, _ = req("/api/export-project", "POST", {}, raw=False)
try:
    zd = json.loads(zbody)
except Exception:
    zd = {}
report("S24 export-project(zip)", st == 200, zbody[:100])

# ── オリジン検査（DNSリバインディング対策）──
st, body, _ = req("/api/project", headers={"Origin": "http://evil.example"})
report("S25 変なOriginは拒否", st in (400, 403), f"st={st}")

# ── 新規プロジェクト→open→保存ガード ──
st, body, _ = req("/api/new-project", "POST", {"name": "_qa_p2"})
report("S26 new-project", st == 200 and os.path.isdir("projects/_qa_p2"), body[:80])
st, body, _ = req("/api/open?name=_qa_p2", "POST", {})
report("S27 open 切替", st == 200, body[:80])
# 切替後、古い画面（_qa_p を読み込んだ画面）からの保存は 409 で守られるか
st, body, _ = req(f"/api/project?from=_qa_p&v={ver}", "POST", pj,
                  {"Content-Type": "application/json"})
report("S28 切替後の旧画面保存は409", st == 409, f"st={st} {body[:60]}")
req("/api/open?name=_qa_p", "POST", {})

ok = sum(1 for _, o, _ in RESULTS if o)
print(f"\n== {ok}/{len(RESULTS)} PASS ==")
sys.exit(0 if ok == len(RESULTS) else 1)
