#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project.json パッチ層 — 自然言語コマンドの受け皿（Phase 4）

思想:
  「ビルダーを毎回書き直す」のをやめる。自然言語の指示は、このツールで
  **project.json に狙った差分だけ当てる**。UIでの手直しを潰さない。

  AI（Claude/codex）は  --show で構造を読み、--ops でパッチを当てるだけ。
  AGENTS.md の鉄則は、ここで**バリデーションとして強制**する。
  （＝AIが鉄則を"覚えている"必要をなくす）

使い方:
  python3 tools/patch.py <project> --show                 # 構造をIDつきで出す（対象特定用）
  python3 tools/patch.py <project> --ops '<JSON配列>'      # パッチ適用（検証してから保存）
  python3 tools/patch.py <project> --ops '...' --dry-run  # 差分だけ表示
  python3 tools/patch.py <project> --ops '...' --render   # 適用後にレンダリング
  python3 tools/patch.py <project> --check                # 現状が鉄則に違反していないか検査
  python3 tools/patch.py <project> --srt                  # 字幕を out/<名前>.srt へ（YouTube用）
  python3 tools/patch.py <project> --srt-import 字幕.srt   # 外部SRTを字幕トラックに取り込み

op の種類（selectorは track: id か label、clip: index / "*" / {"match":{...}}）:
  {"op":"set",    "track":"wipe","clip":"*","set":{"x":0.05,"y":0.62}}
  {"op":"shift",  "track":"cap", "clip":2,  "by":-1.0}          # start/endを平行移動
  {"op":"retime", "track":"img", "clip":3,  "start":12.0,"end":18.0}
  {"op":"delete", "track":"sfx", "clip":5}
  {"op":"add",    "track":"cap", "clip":{"start":1,"end":3,"text":"…"}}
  {"op":"ripple", "track":"cap", "from":12.0,"by":-0.5}         # 指定秒以降をまとめてずらす
  {"op":"setroot","key":"style","merge":{"fontsize":56}}        # トップレベルkey（style/audio/canvas/meta等）
  {"op":"setroot","key":"audio","value":{...}}                  # merge=深マージ(nullで削除) / value=丸ごと置換 / "delete":true

clip の index は --show が表示する番号そのもの（生配列の位置。表示は時系列順に並ぶが番号は保存順）。
編集で番号がズレるのが怖い場合は {"match":{...}} セレクタを使う。
検証は errors（保存拒否）と warnings（素材未配置など・保存は続行）に分かれる。--strict でwarningsも拒否。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import argparse, copy, json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 字幕の見え方の検査（豆腐化・低コントラスト）はレンダラの実装を借りる。
# レンダラと別実装にすると「検査は通るのに書き出すと化ける」が起きるため。
# Pillow が無い等で読めない環境では検査だけスキップする（patch自体は動かす）。
try:
    _sys.path.insert(0, os.path.join(ROOT, "renderer"))
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_ve_render", os.path.join(ROOT, "renderer", "render.py"))
    _R = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_R)
except Exception:
    _R = None


def _luminance(c):
    """WCAG の相対輝度。c は [R,G,B(,A)]。"""
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (list(c) + [0, 0, 0])[:3]
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_ratio(fg, bg):
    """WCAG のコントラスト比（1.0〜21.0）。大きいほど読みやすい。"""
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)

def die(msg):
    print("❌ " + msg, file=sys.stderr); sys.exit(1)

def load(pdir):
    fp = os.path.join(pdir, "project.json")
    if not os.path.isfile(fp): die(f"project.json がありません: {fp}")
    return json.load(open(fp, encoding="utf-8")), fp

def dump_project(pj, fp):
    """共通保存経路（ui/server.py の save_project_text と同仕様・indent=1統一）。
    - アトミック書き込み（.tmp → os.replace）: 途中クラッシュでSSOTを半壊させない
    - 直近10世代を .history/ に残す（gitコミット前の即席undo）
    """
    hist = os.path.join(os.path.dirname(fp), ".history")
    try:
        if os.path.exists(fp):
            os.makedirs(hist, exist_ok=True)
            shutil.copy2(fp, os.path.join(hist, f"project-{int(os.path.getmtime(fp) * 1000)}.json"))
            snaps = sorted(f for f in os.listdir(hist) if f.startswith("project-") and f.endswith(".json"))
            for s in snaps[:-10]:
                os.remove(os.path.join(hist, s))
    except OSError:
        pass  # バックアップ失敗で本体の保存を止めない
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)

def srcdur(pdir, src):
    fp = os.path.join(pdir, os.path.basename(src))
    if not os.path.exists(fp): return None
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",fp],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip())
    except ValueError: return None

def find_track(pj, sel):
    """id か label（絵文字/空白を無視）で트ラックを引く"""
    def norm(s): return "".join(str(s or "").split()).lower()
    for t in pj["tracks"]:
        if t.get("id") == sel: return t
    for t in pj["tracks"]:
        if norm(t.get("label")).find(norm(sel)) >= 0: return t
    die(f"トラックが見つかりません: {sel!r}（--show で確認）")

def pick(track, sel):
    """clip セレクタ → クリップのリスト"""
    cl = track["clips"]
    if sel == "*" or sel is None: return list(cl)
    if isinstance(sel, int):
        if not (0 <= sel < len(cl)): die(f"clip index 範囲外: {sel}（0..{len(cl)-1}）")
        return [cl[sel]]
    if isinstance(sel, dict) and "match" in sel:
        out = [c for c in cl if all(c.get(k) == v for k, v in sel["match"].items())]
        if not out: die(f"match に該当するクリップがありません: {sel['match']}")
        return out
    die(f"不正な clip セレクタ: {sel!r}")

def schema_warnings(pj):
    """schema/project.schema.json と照合して、未知キー・enum違反・型違反を警告する。
    （jsonschemaライブラリに頼らない軽量チェック。依存ゼロを保つため）
    スキーマを"読まれるだけの飾り"にしないための配線。スキーマ自体が無ければ黙って何もしない。"""
    fp = os.path.join(ROOT, "schema", "project.schema.json")
    if not os.path.isfile(fp):
        return []
    try:
        sc = json.load(open(fp, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    TYPES = {"string": str, "number": (int, float), "integer": int,
             "boolean": bool, "object": dict, "array": list}

    def check(obj, props, where):
        for k, v in obj.items():
            # `_` 始まりは注釈・内部用の慣習（_doc / _zorder / _tid / _widthText …）。
            # 人が目印として付けるメモ（_id: "topbar" 等）を typo 扱いで警告しない
            if k.startswith("_"):
                continue
            spec = props.get(k)
            if spec is None:
                out.append(f"{where} 未知のキー: {k!r}（schemaに定義が無い。typoか、schemaの更新漏れ）")
                continue
            if "enum" in spec and v not in spec["enum"]:
                out.append(f"{where} {k}={v!r} は不正（許可値: {spec['enum']}）")
            t = spec.get("type")
            if t:
                allow = tuple(TYPES[x] for x in ([t] if isinstance(t, str) else t) if x in TYPES)
                # boolはintのサブクラスなので、数値型にboolを通さない
                if allow and (not isinstance(v, allow)
                              or (isinstance(v, bool) and bool not in allow)):
                    out.append(f"{where} {k} の型が不正: {type(v).__name__}（期待: {t}）")

    tprops = sc["properties"]["tracks"]["items"]["properties"]
    cprops = tprops["clips"]["items"]["properties"]
    check({k: v for k, v in pj.items() if k != "tracks"}, sc["properties"], "[トップ]")
    for t in pj["tracks"]:
        lb = t.get("label") or t.get("id") or t.get("type")
        check({k: v for k, v in t.items() if k != "clips"}, tprops, f"[{lb}]")
        for c in t.get("clips", []):
            check(c, cprops, f"[{lb}]")
    return out


def caption_legibility_warnings(pj, track, clip):
    """字幕が「読めない状態」で書き出されるのを、書き出す前に捕まえる。

    1) 豆腐(□)化 … 指定フォントがその文字のグリフを持たない
       （2026-08-03: 中国語字幕を日本語フォントで焼いて「饺子」が「□子」になった）
    2) 低コントラスト … 文字色と字幕の下敷き色が近すぎて読めない
       highlight が無い場合は下敷きが映像そのものなので判定しない（誤検出になる）
    """
    out = []
    if _R is None:
        return out
    lb = track.get("label") or track.get("id") or "字幕"
    text = clip.get("text") or ""
    style = pj.get("style") or {}
    font = clip.get("font") or style.get("font") or "Hiragino Kaku Gothic Pro"
    head = text.replace("\n", "/")[:18]

    miss = _R.missing_glyphs(font, text.replace("**", ""), bold=bool(clip.get("bold")))
    if miss:
        out.append(f"[{lb}] フォント '{font}' が描けない文字があります: {''.join(miss)}"
                   f"（「{head}」）→ 書き出すと□になります。"
                   f"簡体字なら style.font を 'Hiragino Sans GB' に")

    if clip.get("highlight"):
        fg = clip.get("textColor") or [255, 255, 255]
        bg = clip.get("highlightColor") or (style.get("box") or {}).get("color") or [18, 28, 46]
        ratio = contrast_ratio(fg, bg)
        if ratio < 3.0:      # WCAG の大きい文字の基準
            out.append(f"[{lb}] 字幕のコントラストが低すぎます（比 {ratio:.1f}:1、"
                       f"目安 3.0以上）文字{list(fg)[:3]} / 下敷き{list(bg)[:3]}（「{head}」）")
    return out


# ── CLAUDE.md の鉄則をここで強制する ────────────────────────────
def validate(pj, pdir):
    """鉄則検査。返り値は (errors, warnings)。
    errors   … 構造破壊。保存を拒否（重なり / end<=start / 負start / src欠落 / _zorder無し / 根本キー欠落）
    warnings … 作業続行可。保存は許可して表示のみ（素材の未配置=生成待ち、空字幕）
    """
    errs, warns = [], []
    if not isinstance(pj.get("canvas"), dict) or not isinstance(pj.get("tracks"), list):
        errs.append("canvas / tracks がありません（project.json が壊れています）")
        return errs, warns
    warns += schema_warnings(pj)
    for t in pj["tracks"]:
        cl = sorted(t["clips"], key=lambda c: c.get("start", 0))
        # 鉄則: 1トラック内でクリップを重ねない
        for a, b in zip(cl, cl[1:]):
            if b["start"] < a["end"] - 1e-6:
                errs.append(f"[{t.get('label')}] クリップが重なっています "
                            f"({a['start']:.2f}-{a['end']:.2f} と {b['start']:.2f}-{b['end']:.2f})"
                            f" → 重ねたい要素は別トラックに分けること")
        for c in cl:
            if c.get("start", 0) < -1e-6: errs.append(f"[{t.get('label')}] start が負: {c['start']}")
            if c.get("end", 0) <= c.get("start", 0):
                errs.append(f"[{t.get('label')}] end <= start: {c.get('start')}-{c.get('end')}")
            # 型ごとの必須キー（AI組み立てミスの早期検出）
            if t["type"] in ("video", "image", "audio") and not c.get("src"):
                errs.append(f"[{t.get('label')}] src がありません ({c.get('start')}-{c.get('end')})")
            if t["type"] == "caption" and not (c.get("text") or "").strip():
                warns.append(f"[{t.get('label')}] 空の字幕クリップ ({c.get('start')}-{c.get('end')})")
            if t["type"] == "caption" and (c.get("text") or "").strip():
                warns += caption_legibility_warnings(pj, t, c)
            # 鉄則: 映像/音声はソース長を超えて伸ばせない（loop=true の音声だけは例外＝無限リピート）
            if t["type"] in ("video", "audio") and c.get("src") \
               and not (t["type"] == "audio" and c.get("loop")):
                sd = srcdur(pdir, c["src"])
                if sd is not None:
                    spd = max(0.25, min(4.0, float(c.get("speed") or 1)))
                    lim = c["start"] + (sd - c.get("in", 0)) / spd
                    if c["end"] > lim + 0.05:
                        errs.append(f"[{t.get('label')}] {c['src']} がソース長を超えています "
                                    f"(end={c['end']:.2f} > 上限{lim:.2f}／素材{sd:.2f}s, in={c.get('in',0)})")
            if c.get("src") and not os.path.exists(os.path.join(pdir, os.path.basename(c["src"]))):
                warns.append(f"[{t.get('label')}] 素材が未配置: {c['src']}"
                             f"（生成待ちなら保存は続行される。レンダリング前に必ず配置）")
    if not pj.get("_zorder"):
        errs.append("_zorder が未設定 → 読み込み時に並べ替えられます（重ね順が壊れる）")
    return errs, warns

def deep_merge(dst, src):
    """dictを再帰マージ。値が None のキーは削除。"""
    for k, v in src.items():
        if v is None: dst.pop(k, None)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict): deep_merge(dst[k], v)
        else: dst[k] = v

KNOWN_OPS = ("set", "shift", "retime", "delete", "add", "ripple", "move",
             "addtrack", "setroot")


def apply_ops(pj, pdir, ops):
    log = []
    for o in ops:
        op = o.get("op")
        # 未知op・track欠落は find_track より先に検査する（素通しすると KeyError の生Tracebackになる）
        if op not in KNOWN_OPS:
            die(f"未知の op: {op!r}（使える op: {', '.join(KNOWN_OPS)}）")
        if op != "setroot" and op != "addtrack" and "track" not in o:
            die(f"op={op} には track が必要です（--show で id/label を確認）")
        if op == "setroot":
            # トップレベルキーの編集経路（style/audio/canvas/meta/cuts等）。tracksはトラックopで扱う
            key = o.get("key")
            if not key: die("setroot には key が必要")
            if key == "tracks": die("tracks は setroot で触れない（addtrack/move等のトラックopを使う）")
            if o.get("delete"):
                pj.pop(key, None); log.append(f'setroot {key} を削除'); continue
            if "merge" in o:
                if not isinstance(o["merge"], dict): die(f"setroot merge はオブジェクトを渡す: {key}")
                cur = pj.get(key)
                if cur is not None and not isinstance(cur, dict):
                    die(f"setroot merge は dict キーのみ対象（{key} は {type(cur).__name__}）→ value で置換する")
                deep_merge(pj.setdefault(key, {}), o["merge"])
                log.append(f'setroot {key} merge {list(o["merge"])}')
            elif "value" in o:
                pj[key] = o["value"]; log.append(f'setroot {key} = 置換')
            else:
                die("setroot には merge / value / delete のいずれかを指定")
            continue
        if op == "add":
            t = find_track(pj, o["track"]); t["clips"].append(o["clip"])
            log.append(f'add   [{t.get("label")}] +1 clip'); continue
        if op == "ripple":
            t = find_track(pj, o["track"]); by = float(o["by"]); frm = float(o["from"]); n = 0
            for c in t["clips"]:
                if c["start"] >= frm - 1e-6:
                    c["start"] = round(c["start"] + by, 3); c["end"] = round(c["end"] + by, 3); n += 1
            log.append(f'ripple[{t.get("label")}] {frm}s以降 {n}件 を {by:+}s'); continue
        if op == "addtrack":
            # after: このトラックの直後に入れる（未指定は末尾）。配列の先頭ほど前面
            nt = o["track"]; nt.setdefault("clips", [])
            if o.get("after"):
                ref = find_track(pj, o["after"]); pj["tracks"].insert(pj["tracks"].index(ref)+1, nt)
            else:
                pj["tracks"].append(nt)
            log.append(f'addtrack [{nt.get("label")}] type={nt.get("type")}'); continue
        if op == "move":
            # クリップを別トラックへ（重なり解消に使う）
            src = find_track(pj, o["track"]); dst = find_track(pj, o["to"])
            if src["type"] != dst["type"]:
                die(f'型が違うトラックへは移動できません: {src["type"]} → {dst["type"]}')
            cs = pick(src, o.get("clip", "*"))
            for c in cs: src["clips"].remove(c); dst["clips"].append(c)
            log.append(f'move  [{src.get("label")}]→[{dst.get("label")}] {len(cs)}件'); continue

        t = find_track(pj, o["track"]); cs = pick(t, o.get("clip", "*"))
        for c in cs:
            if op == "set":
                for k, v in o["set"].items():
                    if v is None: c.pop(k, None)
                    else: c[k] = v
            elif op == "shift":
                by = float(o["by"]); c["start"] = round(c["start"] + by, 3); c["end"] = round(c["end"] + by, 3)
            elif op == "retime":
                if "start" in o: c["start"] = round(float(o["start"]), 3)
                if "end" in o:   c["end"] = round(float(o["end"]), 3)
            elif op == "delete":
                t["clips"].remove(c)
            else:
                die(f"未知の op: {op}")
        log.append(f'{op:6}[{t.get("label")}] {len(cs)}件')
    return log

def show(pj, pdir):
    print(f'# {pj["meta"]["title"]}  canvas={pj["canvas"]["w"]}x{pj["canvas"]["h"]}  '
          f'_zorder={pj.get("_zorder")}  cuts={len(pj.get("cuts",[]))}')
    print("# tracks: 配列の先頭ほど前面。clip番号(n)は --ops の clip にそのまま使える（表示は時系列順・番号は保存順）")
    for i, t in enumerate(pj["tracks"]):
        print(f'\n[{i}] id={t.get("id")!r} type={t["type"]} label={t.get("label")!r} clips={len(t["clips"])}')
        # 表示は時系列順に並べるが、番号は pick() が使う生配列 index を出す（番号ズレ事故の防止）
        order = sorted(range(len(t["clips"])), key=lambda k: t["clips"][k].get("start", 0))
        for j in order:
            c = t["clips"][j]
            extra = ""
            if c.get("src"): extra += f' src={c["src"]}'
            if c.get("in") is not None: extra += f' in={c["in"]}'
            for k in ("x", "y", "w", "scale"):
                if c.get(k) is not None: extra += f" {k}={c[k]}"
            txt = (c.get("text") or "").replace("\n", "/")[:22]
            if txt: extra += f' text={txt!r}'
            print(f'    ({j}) {c.get("start",0):6.2f} - {c.get("end",0):6.2f}{extra}')
    print(f'\n全長: {max((x.get("end",0) for t in pj["tracks"] for x in t["clips"]), default=0):.2f}s')

def export_srt(pj, pdir, track_sel=None):
    """字幕トラックを .srt へ書き出す（YouTube等への字幕アップロード用）。
    既定は id="cap" のトラック。無ければ最初の caption トラック。--srt-track で指定可。"""
    tr = None
    if track_sel:
        tr = find_track(pj, track_sel)
        if tr.get("type") != "caption":
            die(f"--srt-track {track_sel!r} は caption トラックではありません（type={tr.get('type')}）")
    else:
        caps = [t for t in pj["tracks"] if t.get("type") == "caption"]
        if not caps:
            die("caption トラックがありません")
        tr = next((t for t in caps if t.get("id") == "cap"), caps[0])

    def ts(sec):
        ms = int(round(max(0.0, float(sec)) * 1000))
        h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines, n = [], 0
    for c in sorted(tr["clips"], key=lambda c: c.get("start", 0)):
        text = (c.get("text") or "").replace("**", "").strip()
        if not text:
            continue
        n += 1
        lines += [str(n), f'{ts(c["start"])} --> {ts(c["end"])}', text, ""]
    if not n:
        die(f"[{tr.get('label') or tr.get('id')}] に書き出せる字幕がありません")
    out_dir = os.path.join(pdir, "out")
    os.makedirs(out_dir, exist_ok=True)
    title = (pj.get("meta") or {}).get("title") or os.path.basename(os.path.abspath(pdir))
    fp = os.path.join(out_dir, os.path.basename(title) + ".srt")
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, fp)
    print(f"✅ {n}件 → {fp}")


def import_srt(pj, pdir, fp_json, srt_path, track_sel=None):
    """外部の .srt を字幕トラックとして取り込む。

    既定は id="srt" のトラックを**新規作成**（既存の字幕を壊さないため）。
    同名トラックが既にあれば中身を置き換える。--srt-track で既存トラックを指定した場合も置き換え。
    重なった時間は次の字幕の開始で切り詰める（1トラック内の重なり禁止の鉄則に合わせる）。"""
    import re as _re
    if not os.path.isfile(srt_path):
        die(f".srt が見つかりません: {srt_path}")
    raw = open(srt_path, encoding="utf-8-sig").read().replace("\r\n", "\n")

    def sec(ts):
        m = _re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000

    clips = []
    for block in _re.split(r"\n\s*\n", raw.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        if _re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]                      # 通し番号行を捨てる
        if not lines or "-->" not in lines[0]:
            continue
        a, b = lines[0].split("-->")
        text = "\n".join(lines[1:]).strip()
        if text:
            clips.append({"start": round(sec(a), 3), "end": round(sec(b), 3), "text": text})
    if not clips:
        die(f"{os.path.basename(srt_path)} から字幕を読み取れませんでした（SRT形式か確認）")
    clips.sort(key=lambda c: c["start"])
    for i in range(len(clips) - 1):                # 鉄則: 重なりは次の開始で切り詰める
        if clips[i]["end"] > clips[i + 1]["start"]:
            clips[i]["end"] = clips[i + 1]["start"]
    clips = [c for c in clips if c["end"] > c["start"]]

    if track_sel:
        tr = find_track(pj, track_sel)
        if tr.get("type") != "caption":
            die(f"--srt-track {track_sel!r} は caption トラックではありません")
        tr["clips"] = clips
    else:
        tr = next((t for t in pj["tracks"] if t.get("id") == "srt"), None)
        if tr is None:
            tr = {"id": "srt", "type": "caption", "label": "字幕(SRT)", "clips": clips}
            pj["tracks"].insert(0, tr)             # 先頭＝最前面
        else:
            tr["clips"] = clips
    errs, warns = validate(pj, pdir)
    for w in warns:
        print("⚠️  " + w)
    if errs:
        die("取り込み結果が鉄則に違反:\n  " + "\n  ".join(errs))
    dump_project(pj, fp_json)
    print(f"✅ {len(clips)}件を [{tr.get('label') or tr.get('id')}] へ取り込みました")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ops")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warning（素材未配置等）も違反として拒否する")
    ap.add_argument("--srt", action="store_true", help="字幕トラックを out/<名前>.srt へ書き出す")
    ap.add_argument("--srt-import", metavar="FILE", help=".srt を字幕トラックとして取り込む")
    ap.add_argument("--srt-track", help="--srt/--srt-import の対象トラック（id か label）")
    a = ap.parse_args()

    pdir = a.project if os.path.isdir(a.project) else os.path.join(ROOT, "projects", a.project)
    pj, fp = load(pdir)

    if a.srt_import:
        import_srt(pj, pdir, fp, a.srt_import, a.srt_track); return
    if a.srt or a.srt_track:
        export_srt(pj, pdir, a.srt_track); return
    if a.show: show(pj, pdir); return
    if a.check:
        errs, warns = validate(pj, pdir)
        for w in warns: print("⚠️  " + w)
        print("✅ 鉄則違反なし" if not errs else "❌ 違反:\n  " + "\n  ".join(errs))
        sys.exit(1 if (errs or (a.strict and warns)) else 0)
    if not a.ops: ap.print_help(); return

    try: ops = json.loads(a.ops)
    except json.JSONDecodeError as e: die(f"--ops がJSONとして不正: {e}")
    if isinstance(ops, dict): ops = [ops]

    before = copy.deepcopy(pj)
    log = apply_ops(pj, pdir, ops)
    errs, warns = validate(pj, pdir)
    if errs or (a.strict and warns):
        die("鉄則違反のため保存しません（変更は破棄）:\n  " + "\n  ".join(errs + (warns if a.strict else [])))
    for w in warns: print("⚠️  " + w)

    for l in log: print("  " + l)
    # 差分を出す
    for t0, t1 in zip(before["tracks"], pj["tracks"]):
        for c0, c1 in zip(t0["clips"], t1["clips"]):
            d = {k: (c0.get(k), c1.get(k)) for k in set(c0) | set(c1) if c0.get(k) != c1.get(k)}
            if d: print(f'    [{t1.get("label")}] {d}')
    if a.dry_run:
        print("（--dry-run のため保存していません）"); return

    dump_project(pj, fp)
    print(f"💾 保存: {fp}（直近10世代は .history/ に退避済み）")
    print("（エディタを開いている場合は数秒以内に自動反映される）")
    if a.render:
        subprocess.run([sys.executable, os.path.join(ROOT, "renderer", "render.py"), pdir], check=True)

if __name__ == "__main__":
    main()
