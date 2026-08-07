#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台本JSON＋テンプレートから project.json を組み立てる（動画量産の入口）。

これ1本で「新しい回を作る」が完結する。Pythonを書き換える必要はない。

    # 1. 台本を書く（templates/example-*.json をコピーして中身を差し替える）
    # 2. ナレーション音声を作る
    python3 tools/make_video.py 台本.json --voice
    # 3. project.json を組む
    python3 tools/make_video.py 台本.json
    # 4. 書き出す
    python3 renderer/render.py projects/<名前>

    # まとめて（音声→組み立て→書き出し）
    python3 tools/make_video.py 台本.json --voice --render

台本の書き方は templates/README.md を読むこと。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import _deps       # noqa: E402  依存の確認（無ければ入れ方を出して止まる）
import argparse, json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_DIR = os.path.join(ROOT, "templates")
ASSETS = os.path.join(ROOT, "assets")

# 字幕の溢れ検査はレンダラの wrap() を借りる（別実装にすると警告と結果が食い違う）
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_ve_render", os.path.join(ROOT, "renderer", "render.py"))
    _R = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_R)
except Exception:
    _R = None


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strip_doc(d):
    """_doc / _use など下線始まりの説明キーを落とす（描画設定として渡さないため）。"""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def adur(pdir, f):
    """音声の実測尺。無ければ0を返す（--voice 前に組もうとした時に気づけるように）。"""
    p = os.path.join(pdir, f)
    if not os.path.exists(p):
        return 0.0
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def ensure_assets(tpl, pdir, script):
    """テンプレートが要る素材を assets/ から projects/<名> へ配る。
    足りないものは名前を挙げて止める（書き出し後に気づくと手戻りが大きい）。"""
    want = list(tpl["assets"]["required"]) + list(tpl["assets"]["sfx"])
    want.append(script.get("bgm") or tpl["assets"]["bgm_default"])
    # 台本の各カットが使う図解画像も配る（chat型は gen_chat が生成するので対象外）
    if script["template"] != "chat":
        for b in script["beats"]:
            if b.get("image"):
                want.append(b["image"] + ".png")
    missing = []
    for f in want:
        dst = os.path.join(pdir, f)
        if os.path.exists(dst):
            continue
        for sub in ("kit", "bgm", ""):
            src = os.path.join(ASSETS, sub, f)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                break
        else:
            missing.append(f)
    if missing:
        hint = ""
        die("素材が見つかりません: " + ", ".join(missing)
            + "\n   assets/kit/ か assets/bgm/ に置いてください（templates/README.md 参照）。"
            + hint)


def gen_voice(script, pdir):
    """台本の narration からTTS用のspecを作って音声を生成する。"""
    segs = []
    for i, b in enumerate(script["beats"], 1):
        if not b.get("narration"):
            continue
        segs.append({"id": b.get("id") or f"n{i}",
                     "voice": b.get("voice") or script.get("voice", "narrator-m"),
                     "text": b.get("tts") or b["narration"]})
    if not segs:
        die("台本に narration がありません")
    spec = os.path.join(pdir, "narration.json")
    with open(spec, "w", encoding="utf-8") as f:
        json.dump({"segments": segs}, f, ensure_ascii=False, indent=1)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_voice.py"),
                        "--spec", spec, "--outdir", pdir])
    if r.returncode != 0:
        die("ナレーション生成に失敗しました（edge-tts が入っているか確認してください）")


class SE:
    """効果音を置く。⚠️ 同一トラック内で重ねない（検査に弾かれ、音も食い合う）。"""
    LEN = {"whoosh": 1.01, "kira": 0.99, "text-impact": 1.74,
           "sceneswitch": 1.01, "decision": 0.82}

    def __init__(self):
        self.clips = []

    def add(self, name, at, gain):
        at = max(at, 0)
        if self.clips and at < self.clips[-1]["end"]:
            return                      # 前の音が鳴り終わっていない → 置かずに間引く
        self.clips.append({"src": f"{name}.mp3", "start": round(at, 2),
                           "end": round(at + self.LEN[name], 2), "gain": gain})


def script_engines(paths):
    """台本群が実際に使うTTSエンジン名の集合。読めない台本は edge 扱い（安全側）。"""
    try:
        presets = json.load(open(os.path.join(ROOT, "tools", "voices.json"),
                                 encoding="utf-8"))["presets"]
    except Exception:
        return {"edge"}
    engines = set()
    for p in paths:
        try:
            sc = json.load(open(p, encoding="utf-8"))
        except Exception:
            engines.add("edge"); continue
        for b in sc.get("beats", []):
            key = b.get("voice") or sc.get("voice", "narrator-m")
            engines.add((presets.get(key) or {}).get("engine", "edge"))
    return engines or {"edge"}


def caption_overflow_warnings(tpl, beat, nid, i):
    """字幕が枠に収まるかを **実際の描画幅** で確かめる。

    文字数で判定してはいけない。同じ22字でも欧文と和文で幅が2倍違うため、
    「警告が出ても実は収まる／出なくても溢れる」になり当てにならない。
    レンダラの wrap() をそのまま呼ぶので、警告と実際の書き出しが食い違わない。
    （2026-08-03: 英語字幕が "under ¥ / 500" と割れ、通貨記号だけ行末に残った）
    """
    out = []
    if _R is None:
        return out
    cap = beat.get("caption") or ""
    if not cap:
        return out
    try:
        lay = pick_layout(tpl, cap, beat.get("layout"))
        W = tpl["canvas"]["w"]
        pad = ((tpl.get("style") or {}).get("box") or {}).get("pad", 22)
        maxw = W * lay["w"] - pad * 2
        font = _R.get_font((tpl.get("style") or {}).get("font", "Hiragino Kaku Gothic Pro"),
                           int(lay["fs"]), bold=True)
        draw = _R.ImageDraw.Draw(_R.Image.new("RGBA", (8, 8)))
        for para in cap.replace("**", "").split("\n"):
            if not para.strip():
                continue
            lines = _R.wrap(draw, para, font, maxw)
            if len(lines) > 1:
                out.append(f"beat#{i}({nid}): 字幕が枠に収まらず {len(lines)}行に折り返されます"
                           f"「{para[:20]}」→ 文言を短くするか \\n で明示改行を")
    except Exception:
        pass
    return out


def pick_layout(tpl, cap, given):
    lay = tpl["layouts"]
    if given:
        if given not in lay:
            die(f"layout '{given}' はこのテンプレートにありません。使えるのは: {', '.join(lay)}")
        return strip_doc(lay[given])
    a = tpl["auto_layout"]
    n = max(len(l) for l in cap.split("\n")) if cap else 0
    return strip_doc(lay[a["short"] if n <= a["threshold"] else a["long"]])


def build_explain(tpl, script, pdir):
    """図解＋ナレ＋字幕の型。"""
    P, gap = tpl["parts"], tpl["gap"]
    font = tpl["style"]["font"]
    se = SE()
    t = 0.0
    narr, caps, cards, spans, subs = [], [], [], [], []

    if script.get("hook"):
        h = script["hook"]
        dur = float(h.get("dur", 3.0))
        cards.append({**strip_doc(P["hook"]), "font": font, "text": h["text"],
                      "start": 0, "end": round(dur, 2)})
        if h.get("text_ja") and "hook_ja" in P:
            subs.append({**strip_doc(P["hook_ja"]), "font": font, "text": h["text_ja"],
                         "start": 0, "end": round(dur, 2)})
        spans.append([script["beats"][0]["image"], 0, dur, strip_doc(tpl["layouts"]["boxed"])])
        se.add(tpl["sfx"]["hook"]["name"], 0.05, tpl["sfx"]["hook"]["gain"])
        t += dur

    for i, b in enumerate(script["beats"], 1):
        nid = b.get("id") or f"n{i}"
        cap = b.get("caption", "")
        lay = pick_layout(tpl, cap, b.get("layout"))
        d = adur(pdir, nid + ".mp3")
        if d == 0:
            die(f"音声 {nid}.mp3 がありません。先に --voice で作ってください")
        narr.append({"src": nid + ".mp3", "start": round(t, 2), "end": round(t + d, 2)})
        if cap:
            # caption_style は台本側の見た目の上書き（色など）。
            # ここに書いておけば **再ビルドしても消えない**。patch.py で当てた色は
            # --force の作り直しで失われるので、恒久的な指定は台本に持たせる。
            caps.append({**strip_doc(P["caption"]), "font": font, "text": cap,
                         "y": lay["cap_y"], "align": lay["align"], "fontsize": lay["fs"],
                         "x": lay["x"], "w": lay["w"], "h": lay["h"], "anim": lay.get("anim"),
                         "start": round(t, 2), "end": round(t + d + gap * 0.6, 2),
                         **(b.get("caption_style") or {})})
        # 日本語の副字幕（海外向けでは現地語が主・日本語は小さく下段）。テンプレが対応する型のみ
        if b.get("caption_ja") and "caption_ja" in P:
            subs.append({**strip_doc(P["caption_ja"]), "font": font, "text": b["caption_ja"],
                         "y": round(lay["cap_y"] + lay.get("sub_dy", 0.082), 4),
                         "start": round(t, 2), "end": round(t + d + gap * 0.6, 2)})
        if not spans or spans[-1][0] != b["image"]:
            s = tpl["sfx"]["scene"]
            se.add(s["names"][len(spans) % len(s["names"])], t - 0.12, s["gain"])
        if any(c.isdigit() for c in cap):
            se.add(tpl["sfx"]["number"]["name"], t + 0.10, tpl["sfx"]["number"]["gain"])
        spans.append([b["image"], t, t + d + gap, lay])
        t += d + gap

    if script.get("outro"):
        o = script["outro"]
        dur = float(o.get("dur", 4.6))
        se.add(tpl["sfx"]["end"]["name"], t - 0.05, tpl["sfx"]["end"]["gain"])
        cards.append({**strip_doc(P["outro"]), "font": font, "text": o["text"],
                      "start": round(t, 2), "end": round(t + dur, 2)})
        if o.get("text_ja") and "outro_ja" in P:
            subs.append({**strip_doc(P["outro_ja"]), "font": font, "text": o["text_ja"],
                         "start": round(t, 2), "end": round(t + dur, 2)})
        spans.append([script["beats"][-1]["image"], t, t + dur,
                      strip_doc(tpl["layouts"]["boxed"])])
        t += dur
    total = round(t, 2)

    merged = []
    for im, a, b, lay in spans:
        if merged and merged[-1][0] == im and merged[-1][3] == lay and abs(merged[-1][2] - a) < 1e-6:
            merged[-1][2] = b
        else:
            merged.append([im, a, b, lay])
    imgs = []
    last = script["beats"][-1]["image"]
    for im, a, b, lay in merged:
        z = tpl["motion"]["zoom_last"] if im == last else tpl["motion"]["zoom"]
        imgs.append({"src": f"{im}.png", "start": round(a, 2), "end": round(b, 2),
                     "x": round((1 - lay["img_w"]) / 2, 3), "y": lay["img_y"],
                     "w": lay["img_w"], "motion": {"zoom": z}})
    return total, {"cards": cards, "captions": caps, "subcaptions": subs, "images": imgs,
                   "narration": narr, "sfx": se.clips}


def build_chat(tpl, script, pdir):
    """LINE風チャットの型。チャット画面は gen_chat.py が先に作っておく。"""
    P, gap = tpl["parts"], tpl["gap"]
    font = tpl["style"]["font"]
    se = SE()
    t = 0.0
    narr, imgs, cards = [], [], []
    se.add(tpl["sfx"]["hook"]["name"], 0.05, tpl["sfx"]["hook"]["gain"])
    prev = None

    for i, b in enumerate(script["beats"], 1):
        nid = b.get("id") or f"v{i}"
        d = adur(pdir, nid + ".mp3")
        if d == 0:
            die(f"音声 {nid}.mp3 がありません。先に --voice で作ってください")
        narr.append({"src": nid + ".mp3", "start": round(t, 2), "end": round(t + d, 2)})
        scr = b.get("screen")             # 1..N（チャット画面の番号）/ "fade" / なし
        if scr is not None:
            if prev == scr and imgs:
                imgs[-1]["end"] = round(t + d + gap, 2)
            else:
                src = "chat_fade.png" if scr == "fade" else f"chat{scr}.png"
                imgs.append({"src": src, "start": round(t, 2), "end": round(t + d + gap, 2),
                             **tpl["chat_image"]})
                s = tpl["sfx"]["scene"]
                idx = 0 if scr == "fade" else (int(scr) % len(s["names"]))
                se.add(s["names"][idx], t - 0.10, s["gain"])
            prev = scr
        if b.get("card"):
            cy = tpl["card_y"]
            y = cy["none"] if scr is None else (cy["fade"] if scr == "fade" else cy["with_chat"])
            cards.append({**strip_doc(P["card"]), "font": font, "text": b["card"], "y": y,
                          "start": round(t, 2), "end": round(t + d + gap * 0.6, 2)})
            if any(c.isdigit() for c in b["card"]):
                se.add(tpl["sfx"]["number"]["name"], t + 0.12, tpl["sfx"]["number"]["gain"])
        t += d + gap
    se.add(tpl["sfx"]["end"]["name"], t - 0.9, tpl["sfx"]["end"]["gain"])
    return round(t, 2), {"cards": cards, "captions": [], "images": imgs,
                         "narration": narr, "sfx": se.clips}


BUILDERS = {"sns-explain": build_explain, "sns-global": build_explain, "chat": build_chat}


def lint_script(script, tpl, pdir):
    """台本の誤りを (errors, warnings) で返す。errors があれば書き出しは止める。
    書き出しに10分かけてから失敗、を防ぐのが目的。素材の実在もここで確かめる。"""
    errors, warnings = [], []
    is_chat = script.get("template") == "chat"
    beats = script.get("beats") or []
    if not beats:
        errors.append("beats が空です")

    def asset_exists(fn):   # ensure_assets と同じ場所を探す
        if os.path.exists(os.path.join(pdir, fn)):
            return True
        return any(os.path.exists(os.path.join(ASSETS, sub, fn)) for sub in ("kit", "bgm", ""))

    if not script.get("source"):
        warnings.append("source（出典）が空です。数字を出すなら出所を必ず入れてください")

    for i, b in enumerate(beats, 1):
        nid = b.get("id") or (("v" if is_chat else "n") + str(i))
        if not b.get("narration"):
            warnings.append(f"beat#{i}({nid}): narration が空（この区間は無音になります）")
        if not is_chat:
            img = b.get("image")
            if not img:
                errors.append(f"beat#{i}({nid}): image が指定されていません")
            elif not asset_exists(img + ".png"):
                errors.append(f"beat#{i}({nid}): 画像 {img}.png が見つかりません"
                              "（assets/kit/ に置くか、台本の image を直す）")
            lay = b.get("layout")
            if lay and lay not in tpl.get("layouts", {}):
                errors.append(f"beat#{i}({nid}): layout '{lay}' は無効。"
                              f"使えるのは: {', '.join(tpl.get('layouts', {}))}")
            warnings += caption_overflow_warnings(tpl, b, nid, i)
        else:
            scr = b.get("screen")
            nl = len(script.get("chat", {}).get("lines", []))
            if scr not in (None, "fade") and (not isinstance(scr, int) or scr < 1 or scr > nl):
                errors.append(f"beat#{i}({nid}): screen={scr} は範囲外（1〜{nl} か 'fade'）")

    if is_chat:
        ch = script.get("chat") or {}
        if not ch.get("lines"):
            errors.append("会話型なのに chat.lines がありません")
        if not ch.get("speakers"):
            errors.append("会話型なのに chat.speakers がありません")

    bgm = script.get("bgm")
    if bgm and not asset_exists(bgm):
        warnings.append(f"BGM {bgm} が見つかりません（既定のBGMで書き出します）")
    return errors, warnings


def guard_overwrite(pdir, name, script_file, force):
    """既存プロジェクトを黙って踏み潰さない。

    make_video.py は project.json を丸ごと書き直すので、UIで作った作品や
    別の台本が作った作品を上書きすると、素材はgit外なので復旧が非常に面倒になる。
    自分（同じ台本）が作ったものだけ、確認なしで作り直してよい。
    """
    fp = os.path.join(pdir, "project.json")
    if force or not os.path.exists(fp):
        return
    try:
        with open(fp, encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        cur = {}
    made = (cur.get("meta") or {}).get("madeBy") or {}
    if made.get("script") == script_file:
        return                      # 同じ台本の作り直し＝通常運用
    owner = f"台本 {made['script']}" if made.get("script") else "手作業（UI）"
    die(f"[{script_file}] projects/{name}/ は既にあります（作ったのは{owner}）。\n"
        f"   上書きすると元の内容は戻せません。どちらかにしてください:\n"
        f"     ・台本の \"name\" を別の名前に変える（推奨）\n"
        f"     ・本当に上書きしてよいなら --force を付けて実行する")


def process_one(path, do_voice, do_render, lint_only=False, force=False):
    """台本1本を処理する。lint→（素材配置→音声→組み立て→検査→書き出し）。"""
    script = load(path)
    base = os.path.basename(path)
    for k in ("template", "name", "beats"):
        if k not in script:
            die(f"[{base}] 台本に '{k}' がありません")
    tpl_path = os.path.join(TPL_DIR, script["template"] + ".json")
    if not os.path.exists(tpl_path):
        avail = [f[:-5] for f in os.listdir(TPL_DIR) if f.endswith(".json")
                 and not f.startswith("example")]
        die(f"[{base}] テンプレート '{script['template']}' がありません。使えるのは: {', '.join(avail)}")
    tpl = load(tpl_path)

    pdir = os.path.join(ROOT, "projects", script["name"])
    guard_overwrite(pdir, script["name"], base, force)
    os.makedirs(pdir, exist_ok=True)

    label = script["name"]
    errors, warnings = lint_script(script, tpl, pdir)
    for w in warnings:
        print(f"  ⚠️ [{label}] {w}")
    if errors:
        for e in errors:
            print(f"  ❌ [{label}] {e}", file=sys.stderr)
        die(f"[{label}] 台本に {len(errors)} 件の問題があります（上を直してから書き出してください）")
    if lint_only:
        print(f"✅ [{label}] 台本チェックOK" + (f"（警告 {len(warnings)} 件）" if warnings else ""))
        return

    ensure_assets(tpl, pdir, script)

    # 会話型は台本からチャット画面を先に生成する
    if script["template"] == "chat":
        if "chat" not in script:
            die("会話型の台本には 'chat'（speakers と lines）が要ります")
        cj = os.path.join(pdir, "chat.json")
        with open(cj, "w", encoding="utf-8") as f:
            json.dump(script["chat"], f, ensure_ascii=False, indent=1)
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import gen_chat
        gen_chat.build(pdir, script["chat"])
        from PIL import Image
        n = len(script["chat"]["lines"])
        # ⚠️ 変数名に base を使わないこと。台本ファイル名の `base`（meta.madeBy.script に載る）を
        #    上書きしてしまい、保存時に「Object of type Image is not JSON serializable」で落ちる
        #    （2026-08-07 実測。会話型は最後まで通ったことが無かった）
        last_png = Image.open(os.path.join(pdir, f"chat{n}.png")).convert("RGB")
        white = Image.new("RGB", last_png.size, (238, 241, 245))
        # 締めカードの背景。白を72%混ぜて薄くする（重ねると両方読めなくなるため）
        Image.blend(last_png, white, 0.72).save(os.path.join(pdir, "chat_fade.png"))

    if do_voice:
        gen_voice(script, pdir)

    total, R = BUILDERS[script["template"]](tpl, script, pdir)
    font = tpl["style"]["font"]
    bgm = script.get("bgm") or tpl["assets"]["bgm_default"]
    full = {
        # ロゴを持たないテンプレートがある（海外向けは国内ブランドを出さない）
        "logo": ([{"src": "logo.png", "start": 0, "end": total, **tpl["parts"]["logo"]}]
                 if "logo" in tpl["parts"] else []),
        "chip": [{**strip_doc(tpl["parts"]["chip"]), "font": font,
                  "text": script.get("chip", ""), "start": 0, "end": total}],
        "source": [{**strip_doc(tpl["parts"]["source"]), "font": font,
                    "text": script.get("source", ""), "start": 0, "end": total}],
        # 帯バナーを持たないテンプレートがある（海外向けは国内向けCTAを載せない）
        "banner": ([{"src": "banner.png", "start": 0, "end": total, **tpl["parts"]["banner"]}]
                   if "banner" in tpl["parts"] else []),
        "background": [{"src": "bg.png", "start": 0, "end": total, "x": 0, "y": 0, "w": 1}],
        "bgm": [{"src": bgm, "start": 0, "end": total, "gain": script.get("bgm_gain", 0.05)}],
    }
    tracks = []
    for tr in tpl["tracks"]:
        clips = full.get(tr["role"], R.get(tr["role"], []))
        tracks.append({"id": tr["id"], "type": tr["type"], "label": tr["label"], "clips": clips})

    # madeBy: 次回この台本で作り直すときに「自分が作ったもの」と分かるようにする（guard_overwrite が読む）
    pj = {"meta": {"title": script["name"], "fps": 30, "madeBy": {"tool": "make_video.py", "script": base}},
          "canvas": tpl["canvas"], "style": tpl["style"],
          "audio": {"loudnorm": {"on": True}, "limiter": {"on": True, "db": -1.5}},
          "cuts": [], "_zorder": True, "tracks": tracks}
    fp = os.path.join(pdir, "project.json")
    with open(fp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False, indent=1)
    os.replace(fp + ".tmp", fp)
    print(f"✅ {script['name']}: {total}s  "
          + "  ".join(f"{k}{len(v)}" for k, v in R.items() if v))

    chk = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "patch.py"),
                          pdir, "--check"], capture_output=True, text=True)
    print((chk.stdout or chk.stderr).strip().splitlines()[-1] if (chk.stdout or chk.stderr) else "")

    if do_render:
        subprocess.run([sys.executable, os.path.join(ROOT, "renderer", "render.py"), pdir])


def expand_paths(args):
    """ファイル/ディレクトリ混在を台本ファイルの一覧に開く（ディレクトリは中の *.json）。"""
    out = []
    for p in args:
        if os.path.isdir(p):
            out += [os.path.join(p, f) for f in sorted(os.listdir(p))
                    if f.endswith(".json") and not f.startswith("example")]
        else:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="台本JSON→動画。複数指定/ディレクトリでバッチ。")
    ap.add_argument("script", nargs="+", help="台本JSON（複数可・ディレクトリ可）")
    ap.add_argument("--voice", action="store_true", help="ナレーション音声も作る")
    ap.add_argument("--render", action="store_true", help="組み立て後に書き出す")
    ap.add_argument("--lint", action="store_true", help="台本チェックだけ（書き出さない）")
    ap.add_argument("--force", action="store_true",
                    help="既存プロジェクトでも上書きする（既定は他の台本/UI製のものを守って中断）")
    a = ap.parse_args()

    paths = expand_paths(a.script)
    if not paths:
        die("台本が見つかりません")

    # 依存は「実際に使うものだけ」を要求する（--lint だけなら ffmpeg も要らない）。
    # 台本が ElevenLabs だけを使うのに edge-tts を強制すると、入れていない環境で
    # --voice が丸ごと使えなくなる（2026-08-03に踏んだ。gen_voice.py 直叩きで回避した）
    if not a.lint:
        need = ["ffmpeg", "ffprobe"]
        if a.voice and "edge" in script_engines(paths):
            need.append("edge-tts")
        _deps.require(*need)
    n = len(paths)
    ok, failed = [], []
    for idx, p in enumerate(paths, 1):
        if n > 1:
            print(f"\n──[{idx}/{n}] {os.path.basename(p)}──", flush=True)
        try:
            process_one(p, a.voice, a.render, lint_only=a.lint, force=a.force)
            ok.append(p)
        except SystemExit:
            # バッチ中は1本コケても止めず次へ（一晩量産で1本のミスによる全滅を防ぐ）
            if n == 1:
                raise
            print(f"  ⏭ {os.path.basename(p)} をスキップ（上のエラーを直してください）", file=sys.stderr)
            failed.append(p)
    if n > 1:
        msg = f"\n=== 完了 {len(ok)}/{n} 本"
        if failed:
            msg += "／失敗 " + str(len(failed)) + "本: " + ", ".join(os.path.basename(x) for x in failed)
        print(msg + " ===", flush=True)
        if failed:
            sys.exit(1)   # 1本でも失敗したら非0（夜間バッチの監視で拾えるように）


if __name__ == "__main__":
    main()
