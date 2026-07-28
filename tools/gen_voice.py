#!/usr/bin/env python3
"""video-editor 複数ボイス・複数エンジン対応ナレーション生成ツール。

エンジン非依存で行ごとに声を割り当ててTTSする。
- edge   : Microsoft Edge neural TTS（無料・キー不要・高品質）。voices.json の rate/pitch 有効
- eleven : ElevenLabs（要 ELEVENLABS_API_KEY 環境変数）。ref=voice_id
- say    : macOS 内蔵（低品質・オフラインフォールバック）
fx=robot でピッチ下げ＋フランジャーのAI/ロボット加工。

使い方:
  # ボイス一覧
  python3 tools/gen_voice.py --list
  # ElevenLabsアカウントの利用可能ボイスを取得（要キー）
  python3 tools/gen_voice.py --list-eleven
  # 台本(spec.json)から生成 → <project>/<outdir>/<id>.mp3 と duration
  python3 tools/gen_voice.py --spec path/to/narration.json --outdir <project>/vo_gen

spec.json:
  { "segments": [ {"id":"L1","voice":"narrator-m","text":"…","tts_text":"カナ調整…"}, … ] }
  voice は voices.json の preset キー。省略時は既定(narrator-m)。
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策（副作用で標準出力をUTF-8化）
import argparse, json, os, shutil, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _pyenv_pythons():
    """pyenv に入っている python を新しい順に列挙する。
    バージョンを決め打ちしない（作者の 3.11.9 を書くと他の人の環境で外れる）。"""
    base = Path(os.environ.get("PYENV_ROOT") or (Path.home() / ".pyenv")) / "versions"
    if not base.is_dir():
        return []
    def vkey(d):        # 文字列順だと 3.9.1 が 3.11.9 より新しい扱いになるため数値で比べる
        return tuple(int(x) if x.isdigit() else -1 for x in d.name.split("."))
    vers = sorted((d for d in base.iterdir() if d.is_dir()), key=vkey, reverse=True)
    return [str(v / "bin" / "python3") for v in vers]


def _edge_python():
    """edge_tts が import できるPythonを探す。
    ①この実行Python ②PATH上のpython3/python ③pyenv配下（新しい順）。
    見つからなければ①を返して、実行時に理由つきで落とす。"""
    cands = [sys.executable, shutil.which("python3"), shutil.which("python"), *_pyenv_pythons()]
    seen = set()
    for c in cands:
        if not c or c in seen or not os.path.exists(c):
            continue
        seen.add(c)
        r = subprocess.run([c, "-c", "import edge_tts"], capture_output=True)
        if r.returncode == 0:
            return c
    return sys.executable

EDGE_PY = _edge_python()


def load_presets():
    return json.loads((ROOT / "voices.json").read_text(encoding="utf-8"))["presets"]


def run(cmd, **kw):
    # 失敗理由を握り潰さない（旧実装はDEVNULLで無言死し、原因調査が手探りになっていた）
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f"コマンド失敗: {' '.join(cmd[:3])} …\n{(r.stderr or r.stdout or '')[-600:]}")
    return r


def gen_edge(ref, text, raw, rate=None, pitch=None):
    # 値が先頭マイナス(例 -10Hz)だと argparse がフラグ誤認するため、--opt=val の等号形式で渡す
    cmd = [EDGE_PY, "-m", "edge_tts", "--voice", ref, "--text", text,
           "--write-media", str(raw)]
    if rate:
        cmd += [f"--rate={rate}"]
    if pitch:
        cmd += [f"--pitch={pitch}"]
    run(cmd)


def gen_eleven(ref, text, raw, model="eleven_v3", settings=None):
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY が環境変数にありません（eleven系ボイスにはキーが必要）")
    # 既定はナチュラル。preset の settings で上書き（元気=stability↓/style↑/speed↑）
    vs = {"stability": 0.55, "similarity_boost": 0.8, "style": 0.0, "use_speaker_boost": True}
    if settings:
        vs.update(settings)
    body = json.dumps({"text": text, "model_id": model, "voice_settings": vs}).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ref}", data=body,
        headers={"xi-api-key": key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw.write_bytes(r.read())
    except urllib.error.HTTPError as e:  # 本文にクォータ超過・不正voice_id等の理由が入っている
        raise SystemExit(f"ElevenLabs APIエラー {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")


def gen_say(ref, text, raw, rate="185"):
    # macOS内蔵の say。Windows/Linuxには無いので edge（無料・キー不要）へ自動フォールバック
    if sys.platform != "darwin":
        print(f"  ⚠️ say は macOS 専用のため edge(ja-JP-Keita) で代替します")
        gen_edge("ja-JP-Keita", text, raw, "+0%", None)
        return raw
    aiff = raw.with_suffix(".aiff")
    run(["say", "-v", ref, "-r", str(rate), "-o", str(aiff), text])
    return aiff


def to_mp3(src, dst, fx=None):
    """srcを44100mono mp3へ。fx=robotならピッチ下げ＋フランジャー加工（尺維持）。"""
    af = "aresample=44100"
    if fx == "robot":
        af = ("aresample=44100,asetrate=44100*0.88,aresample=44100,"
              "atempo=1.136,aphaser=type=t:speed=1.1:decay=0.25")
    run(["ffmpeg", "-y", "-i", str(src), "-af", af, "-ar", "44100", "-ac", "1",
         "-codec:a", "libmp3lame", "-q:a", "4", str(dst)])


def probe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def list_eleven():
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY が環境変数にありません")
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices",
                                 headers={"xi-api-key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    for v in data.get("voices", []):
        labels = v.get("labels", {})
        print(f'{v["voice_id"]}  {v.get("name",""):<20} {labels.get("language","")}/{labels.get("gender","")}/{labels.get("descriptive","")}')


def generate(spec_path, outdir):
    presets = load_presets()
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    overs = []
    for s in spec["segments"]:
        pkey = s.get("voice", "narrator-m")
        p = presets.get(pkey)
        if not p:
            raise SystemExit(f"未知のvoice preset: {pkey}（voices.json参照）")
        text = s.get("tts_text") or s["text"]
        # セグメント側で声・演技を上書きできる（1行だけ早口に/テンション上げる 等）
        eng = p["engine"]; ref = s.get("ref") or p["ref"]
        raw = out / f"_{s['id']}.raw.mp3"
        src = raw
        if eng == "edge":
            gen_edge(ref, text, raw, s.get("rate", p.get("rate")), s.get("pitch", p.get("pitch")))
        elif eng == "eleven":
            vs = {**(p.get("settings") or {}), **(s.get("settings") or {})}
            gen_eleven(ref, text, raw, s.get("model", p.get("model", "eleven_v3")), vs or None)
        elif eng == "say":
            src = gen_say(ref, text, raw, p.get("rate", "185"))
        else:
            raise SystemExit(f"未知のengine: {eng}")
        mp3 = out / f"{s['id']}.mp3"
        to_mp3(src, mp3, p.get("fx"))
        try:
            src.unlink()
        except OSError:
            pass
        s["duration"] = round(probe(mp3), 3)
        # max_dur があれば尺超過を警告（枠に収まらない＝字幕とズレる）
        over = ""
        if s.get("max_dur") and s["duration"] > s["max_dur"]:
            over = f'  ⚠️ 尺超過 max_dur={s["max_dur"]}s を {s["duration"]-s["max_dur"]:.2f}s オーバー'
            overs.append(s["id"])
        print(f'{s["id"]:<4} [{pkey}/{eng}] {s["duration"]:.2f}s  {text[:24]}{over}')
    Path(spec_path).write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    total = sum(x["duration"] for x in spec["segments"])
    print(f"✓ {len(spec['segments'])}行 / 計 {total:.2f}s → {out}")
    if overs:
        print(f'⚠️ max_dur超過 {len(overs)}件: {", ".join(overs)}'
              f'\n   → 文を削るか、preset の settings.speed を上げる（voices.json）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="ローカルのボイスカタログ表示")
    ap.add_argument("--list-eleven", action="store_true", help="ElevenLabsアカウントのボイス取得(要キー)")
    ap.add_argument("--spec", help="台本json")
    ap.add_argument("--outdir", help="出力先ディレクトリ")
    a = ap.parse_args()
    if a.list:
        for k, v in load_presets().items():
            extra = " ".join(f"{x}={v[x]}" for x in ("rate", "pitch", "fx", "model") if x in v)
            print(f'{k:<14} {v["engine"]:<7} {v["label"]:<26} {extra}')
        return
    if a.list_eleven:
        list_eleven(); return
    if a.spec and a.outdir:
        generate(a.spec, a.outdir); return
    ap.print_help()


if __name__ == "__main__":
    main()
