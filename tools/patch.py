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
            # 鉄則: 映像/音声はソース長を超えて伸ばせない
            if t["type"] in ("video", "audio") and c.get("src"):
                sd = srcdur(pdir, c["src"])
                if sd is not None:
                    lim = c["start"] + (sd - c.get("in", 0))
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

def apply_ops(pj, pdir, ops):
    log = []
    for o in ops:
        op = o.get("op")
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ops")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warning（素材未配置等）も違反として拒否する")
    a = ap.parse_args()

    pdir = a.project if os.path.isdir(a.project) else os.path.join(ROOT, "projects", a.project)
    pj, fp = load(pdir)

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
