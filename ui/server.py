# -*- coding: utf-8 -*-
"""最小ローカルWebアプリ backend（Python標準ライブラリのみ・依存ゼロ）。
project.json の読み書きと render.py 実行を仲介する。

usage: python3 ui/server.py [project_dir]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "../tools"))
import _wincompat  # noqa: E402  Windows cp932 対策（標準出力/stderrをUTF-8化）
import os, re, sys, json, shutil, subprocess, threading, urllib.parse, zipfile, io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # video-editor/
PROJECTS_DIR = os.path.join(ROOT, "projects")


def _assets_present(pdir):
    """project.json が参照する素材が実在するか（1つでも欠けたら False）。
    gitは素材を持たないので、clone直後の既存プロジェクトはすべて False になる。"""
    try:
        with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
            pj = json.load(f)
    except (OSError, ValueError):
        return False
    for tr in pj.get("tracks") or []:
        for c in tr.get("clips") or []:
            src = c.get("src")
            if src and not os.path.exists(os.path.join(pdir, os.path.basename(src))):
                return False
    return True


def pick_default_project():
    """起動時に開くプロジェクト。**素材が揃っているもの**を選ぶ。
    ⚠️ 素材欠けのプロジェクトを既定にすると、clone直後の初回起動が「全部欠けた壊れた画面」になり、
       ツールが壊れていると誤解される（2026-07-17 配布前の検証で発覚）。
    優先: sample-hello（リポジトリに素材ごと同梱）→ 素材の揃った最新更新のもの → 従来の既定。"""
    sample = os.path.join(PROJECTS_DIR, "sample-hello")
    if _assets_present(sample):
        return sample
    try:
        cands = [os.path.join(PROJECTS_DIR, n) for n in os.listdir(PROJECTS_DIR)]
        cands = [p for p in cands if os.path.isfile(os.path.join(p, "project.json"))]
        cands.sort(key=lambda p: os.path.getmtime(os.path.join(p, "project.json")), reverse=True)
        for p in cands:
            if _assets_present(p):
                return p
    except OSError:
        pass
    # 素材の揃ったものが1つも無いとき。実在するものを返す（存在しない名前を返すと初回起動が空になる）
    try:
        for n in sorted(os.listdir(PROJECTS_DIR)):
            if os.path.isfile(os.path.join(PROJECTS_DIR, n, "project.json")):
                return os.path.join(PROJECTS_DIR, n)
    except OSError:
        pass
    return sample


PROJECT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else pick_default_project()
# 既定は8765。埋まっている環境のために環境変数で変えられる（VE_PORT=8790 python3 ui/server.py）
try:
    PORT = int(os.environ.get("VE_PORT") or 8765)
except ValueError:
    print("[WARN] VE_PORT が数値でないので 8765 を使います", file=sys.stderr)
    PORT = 8765
SAVE_LOCK = threading.Lock()    # 版照合→書き込みをアトミックに（並行保存の競合防止）
RENDER_LOCK = threading.Lock()  # レンダリング/変換の同時多重実行を防止（他のAPIは並行して応答できる）


# ── OS依存はここに集約（macOS=osascriptのネイティブFinder / Windows=tkinter）──────
IS_MAC = sys.platform == "darwin"

def _osa(script):
    """macOS: osascript を実行して1行取る。キャンセル/失敗は None。"""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() if (r.returncode == 0 and r.stdout.strip()) else None

def _tk(kind, **kw):
    """Windows等: tkinter のダイアログ。
    ⚠️ GUIツールキットはメインスレッド前提のため、ThreadingHTTPServer のワーカースレッドから
    直接呼ぶと不安定。別プロセスで起動して結果だけ受け取る（サーバごと落ちるのを防ぐ）。"""
    code = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        f"v = filedialog.{kind}(**{kw!r})\n"
        "print(v if isinstance(v, str) else (v.name if hasattr(v, 'name') else ''))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    out = (r.stdout or "").strip()
    return out or None

def dlg_open_file(prompt, initialdir, filetypes_mac, filetypes_win):
    if IS_MAC:
        return _osa(f'POSIX path of (choose file with prompt "{prompt}" '
                    f'default location (POSIX file "{initialdir}") of type {{{filetypes_mac}}})')
    return _tk("askopenfilename", title=prompt, initialdir=initialdir, filetypes=filetypes_win)

def dlg_save_name(prompt, initialdir, defname):
    if IS_MAC:
        return _osa(f'POSIX path of (choose file name with prompt "{prompt}" '
                    f'default name "{defname}" default location (POSIX file "{initialdir}"))')
    return _tk("asksaveasfilename", title=prompt, initialdir=initialdir, initialfile=defname)

def dlg_choose_dir(prompt):
    if IS_MAC:
        return _osa(f'POSIX path of (choose folder with prompt "{prompt}")')
    return _tk("askdirectory", title=prompt)

def reveal_in_file_manager(fp):
    """ファイルマネージャで対象を選択表示（Mac=Finder / Win=エクスプローラ / Linux=フォルダを開く）。"""
    if IS_MAC:
        subprocess.run(["open", "-R", fp])
    elif sys.platform.startswith("win"):
        subprocess.run(["explorer", "/select,", os.path.normpath(fp)])
    else:
        subprocess.run(["xdg-open", os.path.dirname(fp)])


# ビューアで開いてよいフォルダ。projects/ に加え、ユーザーが選んだフォルダを実行中だけ許可する。
# ⚠️ 任意のパスを無条件に配信しない。**ユーザーがダイアログで選んだものだけ**をここに積む
VIEW_DIRS = []

def allow_view_dir(d):
    ap = os.path.realpath(d)
    if os.path.isdir(ap) and ap not in VIEW_DIRS:
        VIEW_DIRS.append(ap)
    return ap


def safe_under_projects(path):
    """受け取ったパスが「開いてよいフォルダ」配下に収まっているか検証して絶対パスを返す。
    ⚠️ ビューアはパスを引数で受け取るので、`../` でのディレクトリ脱出を必ずここで止める。
       realpath でシンボリックリンクも解決してから判定する。"""
    try:
        ap = os.path.realpath(path) if os.path.isabs(path) \
            else os.path.realpath(os.path.join(PROJECTS_DIR, path))
    except OSError:
        return None
    for base in [os.path.realpath(PROJECTS_DIR)] + VIEW_DIRS:
        if ap == base or ap.startswith(base + os.sep):
            return ap
    return None


VIDEO_EXT = (".mp4", ".webm", ".mov", ".gif")

def list_output_videos(scope):
    """書き出し済み動画の一覧。
    scope: "all"（projects/*/out を全部）／プロジェクト名／`dir:<絶対パス>`（任意フォルダ）。
    実体は読まずメタデータだけ返す（一覧で全部読むと固まる）。"""
    dirs = []
    if scope.startswith("dir:"):                     # ユーザーがダイアログで選んだフォルダ
        d = safe_under_projects(scope[4:])
        if d and os.path.isdir(d):
            dirs.append((os.path.basename(d.rstrip(os.sep)) or d, d))
    elif scope in ("", "all"):
        for n in sorted(os.listdir(PROJECTS_DIR)):
            d = os.path.join(PROJECTS_DIR, n, "out")
            if os.path.isdir(d):
                dirs.append((n, d))
    else:
        d = safe_under_projects(os.path.join(os.path.basename(scope), "out"))
        if d and os.path.isdir(d):
            dirs.append((os.path.basename(scope), d))
    out = []
    for proj, d in dirs:
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(VIDEO_EXT) or f.startswith("."):
                continue
            fp = os.path.join(d, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append({"project": proj, "name": f,
                        "path": os.path.relpath(fp, PROJECTS_DIR),
                        "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: x["mtime"], reverse=True)   # 新しい順
    return out


def make_thumb(fp):
    """動画の中間フレームからサムネJPEGを作り、パスを返す（<out>/.thumbs/ にキャッシュ）。
    ⚠️ キャッシュしないと一覧を開くたびに全本ffmpegが走って待たされる。"""
    d = os.path.join(os.path.dirname(fp), ".thumbs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:      # 書き込めないフォルダ（外部ドライブ・読み取り専用）はリポ内に逃がす
        d = os.path.join(ROOT, ".thumbs")
        os.makedirs(d, exist_ok=True)
    tp = os.path.join(d, os.path.basename(fp) + ".jpg")
    if os.path.exists(tp) and os.path.getmtime(tp) >= os.path.getmtime(fp):
        return tp
    dur = 1.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", fp], capture_output=True, text=True)
        dur = max(0.1, float(r.stdout.strip()))
    except (ValueError, OSError):
        pass
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur * 0.35:.2f}", "-i", fp,
                    "-frames:v", "1", "-vf", "scale=-2:240", "-q:v", "5", "-y", tp],
                   capture_output=True)
    return tp if os.path.exists(tp) else None


def save_project_text(fp, text):
    """project.json の共通保存経路（tools/patch.py と同仕様）。
    - アトミック書き込み（.tmp → os.replace）: 途中クラッシュでSSOTを半壊させない
    - 直近10世代を <プロジェクト>/.history/ に残す（gitに乗らない即席undo）
    - 呼び出し側は必ず indent=1 で整形すること（保存経路ごとの形式差＝幻差分の防止）
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
        f.write(text)
    os.replace(tmp, fp)


def project_assets(pdir):
    """project.jsonが参照している素材ファイル名（プロジェクト直下・実在するもの）を返す。"""
    with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
        pj = json.load(f)
    names = set()
    for tr in pj.get("tracks", []):
        for c in tr.get("clips", []):
            src = c.get("src")
            if src:
                name = os.path.basename(src)
                if os.path.exists(os.path.join(pdir, name)):
                    names.add(name)
    return sorted(names)


def sources_mtime(pdir):
    """project.json と、それが参照している素材すべての最終更新時刻の最大値。

    書き出し済みマスターが最新かどうかは、これと比べて判断する。
    project.json の mtime だけを見ると、素材だけを同名で差し替えたとき
    （音声の作り直し・画像の差し替え。/api/upload が認めている正規の運用）に
    「マスターは最新」と誤判定し、古い素材のままの動画を"成功"として返してしまう。
    """
    pj = os.path.join(pdir, "project.json")
    newest = os.path.getmtime(pj)
    try:
        for name in project_assets(pdir):
            newest = max(newest, os.path.getmtime(os.path.join(pdir, name)))
    except (OSError, ValueError, json.JSONDecodeError):
        return float("inf")       # 判定できないときは「古い」扱い＝安全側（作り直す）
    return newest


def unique_path(path):
    """既存なら「名前 (n)」を付番して衝突を避ける。"""
    base, ext = os.path.splitext(path)
    n, p = 1, path
    while os.path.exists(p):
        p = f"{base} ({n}){ext}"; n += 1
    return p


class Handler(BaseHTTPRequestHandler):
    def _guard_origin(self):
        """他サイトのページから localhost:8765 のAPIを叩かれるのを防ぐ（DNSリバインディング等）。
        自分自身(=Originヘッダ無し or localhost)以外からの要求は拒否。返り値Trueで拒否済み。"""
        o = self.headers.get("Origin")
        if o and urllib.parse.urlparse(o).hostname not in ("localhost", "127.0.0.1"):
            self.send_response(403); self.end_headers()
            self.wfile.write(b'{"err":"cross-origin request denied"}')
            return True
        h = (self.headers.get("Host") or "").split(":")[0]
        if h and h not in ("localhost", "127.0.0.1"):
            self.send_response(403); self.end_headers()
            self.wfile.write(b'{"err":"invalid host"}')
            return True
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def serve_range(self, fp, ctype):
        if not fp or not os.path.exists(fp):
            self._send(404, '{"err":"no source"}'); return
        size = os.path.getsize(fp)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            s, _, e = rng[6:].partition("-")
            start = int(s) if s else 0
            end = int(e) if e else size - 1
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")  # 同名上書き素材を常に最新で返す
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start); self.wfile.write(f.read(end - start + 1))
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")  # 同名上書き素材を常に最新で返す
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(fp, "rb") as f:
                self.wfile.write(f.read())

    def do_GET(self):
        if self._guard_origin(): return
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "ui", "editor.html"), encoding="utf-8") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif p.path == "/api/project":
            fp_ = os.path.join(PROJECT, "project.json")
            try:
                with open(fp_, encoding="utf-8") as f:
                    body_ = f.read()
            except OSError:
                self._send(410, '{"err":"プロジェクトが存在しません。プロジェクトを開き直してください"}'); return
            # 版スタンプ(mtime)を返す。保存時に照合し、他所で更新されていたら上書きさせない
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Project-Version", str(os.path.getmtime(fp_)))
            self.end_headers()
            self.wfile.write(body_.encode("utf-8"))
        elif p.path == "/api/version":
            # 軽量な版問い合わせ（UIが数秒間隔でポーリングし、外部更新=patch.py等を自動反映する）
            fp_ = os.path.join(PROJECT, "project.json")
            try:
                v = str(os.path.getmtime(fp_))
            except OSError:
                v = ""
            self._send(200, json.dumps({"name": os.path.basename(PROJECT), "version": v}))
        elif p.path == "/api/projects":
            items, cur = [], os.path.basename(PROJECT)
            if os.path.isdir(PROJECTS_DIR):
                for dname in sorted(os.listdir(PROJECTS_DIR)):
                    pd = os.path.join(PROJECTS_DIR, dname)
                    fp_ = os.path.join(pd, "project.json")
                    if os.path.isfile(fp_):
                        # 一覧に出す情報（ユーザーにファイルを探させないため、ここで揃える）
                        info = {"dir": dname, "title": dname, "mtime": os.path.getmtime(fp_),
                                "dur": 0, "canvas": "", "clips": 0}
                        try:
                            with open(fp_, encoding="utf-8") as f:
                                pj = json.load(f)
                            info["title"] = (pj.get("meta") or {}).get("title", dname)
                            cv = pj.get("canvas") or {}
                            info["canvas"] = f'{cv.get("w")}x{cv.get("h")}'
                            ends = [c.get("end", 0) for t in pj.get("tracks", []) for c in t.get("clips", [])]
                            info["dur"] = round(max(ends or [0]), 1)
                            info["clips"] = len(ends)
                        except Exception:
                            pass
                        items.append(info)
            if os.path.dirname(PROJECT) != PROJECTS_DIR:  # 起動引数で外部パスを開いている場合
                items.insert(0, {"dir": cur, "title": cur, "external": True})
            self._send(200, json.dumps({"current": cur, "projects": items}, ensure_ascii=False))
        elif p.path == "/src":
            # ?name=<file> で任意の動画をRange配信。無指定は先頭の映像クリップ（後方互換）
            try:
                q = urllib.parse.parse_qs(p.query)
                name = os.path.basename(q.get("name", [""])[0])
                if not name:
                    with open(os.path.join(PROJECT, "project.json"), encoding="utf-8") as f:
                        pj = json.load(f)
                    for tr in pj["tracks"]:
                        if tr["type"] == "video" and tr["clips"]:
                            name = os.path.basename(tr["clips"][0]["src"]); break
                self.serve_range(os.path.join(PROJECT, name) if name else "", "video/mp4")
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)}))
        elif p.path == "/asset":
            q = urllib.parse.parse_qs(p.query)
            name = os.path.basename(q.get("name", [""])[0])
            fp = os.path.join(PROJECT, name)
            if name and os.path.exists(fp):
                ext = name.lower().rsplit(".", 1)[-1]
                ct = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "webp": "image/webp", "gif": "image/gif",
                      "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
                      "aac": "audio/aac", "ogg": "audio/ogg", "flac": "audio/flac",
                      }.get(ext, "application/octet-stream")
                if ct.startswith("audio/"):
                    self.serve_range(fp, ct)  # Range対応でブラウザから途中シーク可能に
                else:
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ct)
            else:
                self._send(404, '{"err":"no asset"}')
        elif p.path == "/waveform":
            # ?name= 指定=その音声/動画ファイルの波形。無指定=映像トラックの音声波形。
            try:
                q = urllib.parse.parse_qs(p.query)
                name = os.path.basename(q.get("name", [""])[0])
                if name:
                    src = name
                    wave = os.path.join(PROJECT, "_wave_" + name + ".png")
                    color = "#8b6fd0"
                else:
                    with open(os.path.join(PROJECT, "project.json"), encoding="utf-8") as f:
                        pj = json.load(f)
                    src = None
                    for tr in pj["tracks"]:
                        if tr["type"] == "video" and tr["clips"]:
                            src = tr["clips"][0]["src"]; break
                    wave = os.path.join(PROJECT, "_waveform.png")
                    color = "#6f9bf0"
                if not src:
                    self._send(404, '{"err":"no source"}'); return
                src_path = os.path.join(PROJECT, src)
                if not os.path.exists(src_path):
                    self._send(404, '{"err":"no source file"}'); return
                if (not os.path.exists(wave)) or os.path.getmtime(wave) < os.path.getmtime(src_path):
                    subprocess.run(["ffmpeg", "-y", "-i", src_path, "-filter_complex",
                                    "showwavespic=s=2400x90:colors=" + color, "-frames:v", "1", wave],
                                   capture_output=True)
                if os.path.exists(wave):
                    with open(wave, "rb") as f:
                        self._send(200, f.read(), "image/png")
                else:
                    self._send(500, '{"err":"waveform failed"}')
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)}))
        elif p.path == "/api/videos":
            # ビューア: 書き出し済み動画の一覧（メタデータのみ）
            q = urllib.parse.parse_qs(p.query)
            scope = q.get("scope", ["all"])[0]
            projs = sorted(n for n in os.listdir(PROJECTS_DIR)
                           if os.path.isdir(os.path.join(PROJECTS_DIR, n, "out")))
            self._send(200, json.dumps({"videos": list_output_videos(scope),
                                        "projects": projs, "scope": scope}, ensure_ascii=False))
        elif p.path == "/api/thumb":
            q = urllib.parse.parse_qs(p.query)
            fp = safe_under_projects(urllib.parse.unquote(q.get("path", [""])[0]))
            if not fp or not os.path.isfile(fp):
                self._send(404, '{"err":"not found"}'); return
            tp = make_thumb(fp)
            if not tp:
                self._send(500, '{"err":"thumb failed"}'); return
            with open(tp, "rb") as f:
                self._send(200, f.read(), "image/jpeg")
        elif p.path == "/api/video":
            # ビューア: 動画本体。**Range必須** — 対応しないとシークできず、長い動画で全部落ちてくる
            q = urllib.parse.parse_qs(p.query)
            fp = safe_under_projects(urllib.parse.unquote(q.get("path", [""])[0]))
            if not fp or not os.path.isfile(fp):
                self._send(404, '{"err":"not found"}'); return
            ext = fp.lower().rsplit(".", 1)[-1]
            ct = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
                  "gif": "image/gif"}.get(ext, "application/octet-stream")
            size = os.path.getsize(fp)
            rng = self.headers.get("Range", "")
            m = re.match(r"bytes=(\d*)-(\d*)", rng) if rng else None
            if m:
                s = int(m.group(1)) if m.group(1) else 0
                e = int(m.group(2)) if m.group(2) else size - 1
                e = min(e, size - 1); s = min(s, e)
                with open(fp, "rb") as f:
                    f.seek(s); data = f.read(e - s + 1)
                self.send_response(206)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Range", f"bytes {s}-{e}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
            else:
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
        elif p.path.startswith("/out/"):
            name = urllib.parse.unquote(os.path.basename(p.path))
            fp = os.path.join(PROJECT, "out", name)
            if os.path.exists(fp):
                ext = name.lower().rsplit(".", 1)[-1]
                ct = {"mp4": "video/mp4", "webm": "video/webm",
                      "gif": "image/gif", "mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ct)
            else:
                self._send(404, '{"err":"not rendered yet"}')
        else:
            self._send(404, "{}")

    def do_POST(self):
        global PROJECT
        if self._guard_origin(): return
        p = urllib.parse.urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(ln) if ln else b"{}"
        if p.path == "/api/project":
            data = json.loads(body)
            if not os.path.isdir(PROJECT):  # 開いていたプロジェクトが消えた等
                self._send(410, '{"err":"プロジェクトが存在しません。プロジェクトを開き直してください"}'); return
            # 誤爆防止: ブラウザが読み込んだプロジェクトと現在のPROJECTが違えば書かない
            # （サーバ再起動や /api/open での切替後、古い画面の自動保存が別プロジェクトを潰す事故を防ぐ）
            q_ = urllib.parse.parse_qs(p.query)
            fromp = q_.get("from", [""])[0]
            if fromp and fromp != os.path.basename(PROJECT):
                self._send(409, json.dumps({"err": "編集中のプロジェクトが切り替わっています。画面を再読み込みしてください",
                                            "loaded": fromp, "current": os.path.basename(PROJECT)},
                                           ensure_ascii=False)); return
            fp = os.path.join(PROJECT, "project.json")
            with SAVE_LOCK:  # 版照合→書き込みをアトミックに（並行保存の競合防止）
                # 版照合: 画面が読み込んだ後に他所(スクリプト等)が更新していたら、古い状態で上書きさせない
                ver = q_.get("v", [""])[0]
                if ver and os.path.exists(fp):
                    cur_ver = str(os.path.getmtime(fp))
                    if ver != cur_ver:
                        self._send(409, json.dumps({"err": "プロジェクトが他で更新されています。画面を再読み込みしてください",
                                                    "loaded_version": ver, "current_version": cur_ver},
                                                   ensure_ascii=False)); return
                new = json.dumps(data, ensure_ascii=False, indent=1)  # indent=1（patch.py と統一。形式差の幻差分防止）
                try:
                    with open(fp, encoding="utf-8") as f:
                        old = f.read()
                except OSError:
                    old = None
                if new != old:  # 内容が同じなら書かない（mtime維持→不要な再レンダリング防止）
                    save_project_text(fp, new)
                # 保存後の版を返す（画面はこれを次回の照合値に使う）
                self._send(200, json.dumps({"ok": True, "version": str(os.path.getmtime(fp))}))
        elif p.path == "/api/new-project":
            # 新規プロジェクト作成。{name, template?} テンプレ指定時は tools/new_project.py で型を継承
            try:
                b = json.loads(body) if body else {}
            except Exception:
                self._send(400, '{"err":"不正なリクエスト"}'); return
            name = os.path.basename(str(b.get("name", "")).strip())
            if not name or name.startswith(".") or name in ("out", "png"):
                self._send(400, json.dumps({"err": "プロジェクト名が不正です"}, ensure_ascii=False)); return
            target = os.path.join(PROJECTS_DIR, name)
            if os.path.exists(target):
                self._send(409, json.dumps({"err": "同名のプロジェクトが既にあります"}, ensure_ascii=False)); return
            tpl = b.get("template")
            if tpl:  # 既存プロジェクトの型（トラック構成・ロゴ/帯/背景/BGM・スタイル）を継承
                r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "new_project.py"),
                                    name, "--from", os.path.basename(str(tpl)),
                                    "--dur", str(float(b.get("dur") or 40))],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    self._send(500, json.dumps({"err": (r.stderr or r.stdout)[-300:]}, ensure_ascii=False)); return
            else:    # 空プロジェクト（最小構成）
                cv = b.get("canvas") or {}
                pj = {"meta": {"title": name, "fps": 30},
                      "canvas": {"w": int(cv.get("w") or 1080), "h": int(cv.get("h") or 1920), "bg": "#141b26"},
                      "audio": {"loudnorm": {"on": True}}, "cuts": [], "_zorder": True,
                      "tracks": [{"id": "cap", "type": "caption", "label": "字幕", "clips": []},
                                 {"id": "wipe", "type": "video", "label": "映像", "clips": []},
                                 {"id": "img", "type": "image", "label": "画像", "clips": []},
                                 {"id": "sfx", "type": "audio", "label": "音声", "clips": []}]}
                os.makedirs(target, exist_ok=True)
                save_project_text(os.path.join(target, "project.json"),
                                  json.dumps(pj, ensure_ascii=False, indent=1))
            PROJECT = target
            self._send(200, json.dumps({"ok": True, "current": name}, ensure_ascii=False))
        elif p.path == "/api/caption-preview":
            # 字幕プレビュー: レンダラと同一実装(render_caption_image)で描いたPNGを返す。
            # CSSでの再現をやめ描画エンジンを1本化する経路（行高・折り返し・座布団形状の食い違いの根治）。
            # ※ ディスクのproject.jsonではなく**画面が持つ編集中のclipをそのまま受け取る**（未保存でも正確）
            try:
                b = json.loads(body)
                cap, cv = b["clip"], b["canvas"]
                pj = {"style": b.get("style") or {}}
                W, H = int(cv["w"]), int(cv["h"])
            except (ValueError, KeyError, TypeError):
                self._send(400, '{"err":"bad request"}'); return
            sys.path.insert(0, os.path.join(ROOT, "renderer"))
            try:
                from render import render_caption_image
                buf = io.BytesIO()
                render_caption_image(pj, cap, W, H).save(buf, "PNG")
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)}, ensure_ascii=False)); return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(buf.getvalue())
        elif p.path == "/api/gen-fx":
            # 集中線PNGをプロジェクト直下に生成（tools/gen_fx.py と同一実装・seed固定＝再現可能）。
            # キャンバス寸法入りのファイル名にする（既存プロジェクトが別寸法で生成済みの fx_lines.png を潰さない）
            try:
                b = json.loads(body) if body else {}
            except Exception:
                b = {}
            w = max(16, int(b.get("w") or 1080)); h = max(16, int(b.get("h") or 1920))
            name = f"fx_lines_{w}x{h}.png"
            out = os.path.join(PROJECT, name)
            if not os.path.exists(out):  # seed固定で毎回同じ絵＝あれば再利用
                sys.path.insert(0, os.path.join(ROOT, "tools"))
                try:
                    from gen_fx import gen_lines
                    gen_lines(w, h).save(out)
                except Exception as ex:
                    self._send(500, json.dumps({"err": str(ex)}, ensure_ascii=False)); return
            self._send(200, json.dumps({"ok": True, "src": name}))
        elif p.path == "/api/upload":
            # ?name=<filename> でrawバイトを受け取りプロジェクト直下へ保存
            q = urllib.parse.parse_qs(p.query)
            name = os.path.basename(q.get("name", [""])[0])
            if not name:
                self._send(400, '{"err":"no name"}'); return
            # 予約名・隠しファイルへのアップロード拒否（素材アップロードでproject.jsonを潰す事故の防止）
            # ※同名素材の上書き自体は意図された機能（音声の再生成など）なので許可のまま
            if name == "project.json" or name.startswith(".") or name.endswith(".tmp"):
                self._send(400, json.dumps({"err": f"その名前にはアップロードできません: {name}"},
                                           ensure_ascii=False)); return
            with open(os.path.join(PROJECT, name), "wb") as f:
                f.write(body)
            self._send(200, json.dumps({"ok": True, "name": name}))
        elif p.path == "/api/render":
            with RENDER_LOCK:
                r = subprocess.run(
                    [sys.executable, os.path.join(ROOT, "renderer", "render.py"), PROJECT],
                    capture_output=True, text=True)
            if r.returncode == 0:
                self._send(200, '{"ok":true}')
            else:
                self._send(500, json.dumps({"ok": False, "err": r.stderr[-800:]}))
        elif p.path == "/api/export":
            # {format: mp4|webm|gif|mp3, quality: high|std|light, height: int|null}
            # 1) マスターMP4をレンダリング（project.json 未変更ならスキップ）
            # 2) マスターから目的形式へ変換（MP4/高/元サイズはマスターをそのまま返す）
            try:
                opts = json.loads(body) if body else {}
            except Exception:
                opts = {}
            fmt = opts.get("format", "mp4")
            q = opts.get("quality", "std")
            h = opts.get("height")
            save_dir = opts.get("save_dir")  # None=ダウンロードのみ / パス=そのフォルダへコピー
            rng = opts.get("range")          # [開始秒, 終了秒] / None=全体
            if fmt not in ("mp4", "webm", "gif", "mp3") or q not in ("high", "std", "light") \
               or not (h is None or (isinstance(h, int) and 100 <= h <= 4320)):
                self._send(400, '{"err":"bad options"}'); return
            if rng is not None:
                try:
                    rng = [float(rng[0]), float(rng[1])]
                except (TypeError, ValueError, IndexError, KeyError):
                    rng = None
                if rng is not None and not (0 <= rng[0] < rng[1]):
                    self._send(400, json.dumps(
                        {"err": "範囲が不正です（開始 < 終了 になるように指定してください）"},
                        ensure_ascii=False)); return
            if save_dir is not None:
                save_dir = os.path.expanduser(str(save_dir))
                if not os.path.isdir(save_dir):
                    self._send(400, '{"err":"保存先フォルダが見つかりません"}'); return
            pj_path = os.path.join(PROJECT, "project.json")
            try:
                with open(pj_path, encoding="utf-8") as f:
                    title = json.load(f)["meta"]["title"]
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)})); return
            out_dir = os.path.join(PROJECT, "out")
            master = os.path.join(out_dir, title + ".mp4")
            with RENDER_LOCK:
                # 素材の差し替えも「古くなった」と見なす（project.json の mtime だけでは足りない）
                fresh = (os.path.exists(master)
                         and os.path.getsize(master) > 0
                         and os.path.getmtime(master) >= sources_mtime(PROJECT))
                if not fresh:
                    r = subprocess.run(
                        [sys.executable, os.path.join(ROOT, "renderer", "render.py"), PROJECT],
                        capture_output=True, encoding="utf-8", errors="replace")
                    if r.returncode != 0:
                        # render.py は原因を stderr に書くが、念のため stdout も拾う
                        # （ここを取りこぼすとブラウザには「500」しか出ず原因が追えない）
                        detail = (r.stderr or "").strip() or (r.stdout or "").strip()
                        self._send(500, json.dumps({"ok": False, "err": detail[-1500:]})); return
            if fmt == "mp4" and q == "high" and not h and not rng:
                fp = master
            else:
                seg = f"_{rng[0]:g}-{rng[1]:g}s" if rng else ""
                fp = os.path.join(out_dir, title + "_" + q + (f"_{h}p" if h else "") + seg + "." + fmt)
                # 範囲: 入力シーク(-ss)＋出力の -t。再エンコードするのでフレーム精度で切れる
                cmd = (["ffmpeg", "-y"]
                       + (["-ss", f"{rng[0]:.3f}"] if rng else []) + ["-i", master]
                       + (["-t", f"{rng[1] - rng[0]:.3f}"] if rng else []))
                # h は「短辺」解釈（縦動画で 1080 を指定しても 608x1080 に縮まないように）
                vf = f"scale=w='if(gt(iw,ih),-2,{h})':h='if(gt(iw,ih),{h},-2)'" if h else None
                if fmt == "mp4":
                    if vf: cmd += ["-vf", vf]
                    cmd += ["-c:v", "libx264",
                            "-preset", "faster" if q == "light" else "medium",
                            "-crf", {"high": "18", "std": "23", "light": "28"}[q],
                            "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "96k" if q == "light" else "128k"]
                elif fmt == "webm":
                    if vf: cmd += ["-vf", vf]
                    cmd += ["-c:v", "libvpx-vp9",
                            "-crf", {"high": "31", "std": "36", "light": "42"}[q],
                            "-b:v", "0", "-row-mt", "1", "-cpu-used", "4",
                            "-c:a", "libopus",
                            "-b:a", {"high": "128k", "std": "96k", "light": "64k"}[q]]
                elif fmt == "gif":
                    fps = {"high": "15", "std": "12", "light": "10"}[q]
                    gh = h or 480
                    cmd += ["-filter_complex",
                            f"[0:v]fps={fps},scale=w='if(gt(iw,ih),-2,{gh})':h='if(gt(iw,ih),{gh},-2)':flags=lanczos,"
                            f"split[a][b];[a]palettegen[p];[b][p]paletteuse",
                            "-an", "-loop", "0"]
                elif fmt == "mp3":
                    cmd += ["-vn", "-c:a", "libmp3lame",
                            "-b:a", {"high": "192k", "std": "128k", "light": "96k"}[q]]
                cmd.append(fp)
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    self._send(500, json.dumps({"ok": False, "err": r.stderr[-800:]})); return
            saved_to = None
            if save_dir:
                # 同名ファイルは上書きせず「名前 (n).ext」を付番（ブラウザのDL挙動に合わせる）
                base, ext = os.path.splitext(os.path.basename(fp))
                saved_to = os.path.join(save_dir, base + ext)
                n = 1
                while os.path.exists(saved_to):
                    saved_to = os.path.join(save_dir, f"{base} ({n}){ext}"); n += 1
                try:
                    shutil.copy2(fp, saved_to)
                except OSError as ex:
                    self._send(500, json.dumps({"ok": False, "err": f"保存先へコピーできません: {ex}"})); return
            self._send(200, json.dumps({"ok": True,
                                        "file": "/out/" + urllib.parse.quote(os.path.basename(fp)),
                                        "name": os.path.basename(fp),
                                        "size": os.path.getsize(fp),
                                        "saved_to": saved_to}))
        elif p.path == "/api/open":
            # プロジェクト切替: ?name=<projects/配下のフォルダ名>
            q_ = urllib.parse.parse_qs(p.query)
            name = os.path.basename(q_.get("name", [""])[0])
            pd = os.path.join(PROJECTS_DIR, name)
            if name and os.path.isfile(os.path.join(pd, "project.json")):
                PROJECT = pd
                self._send(200, json.dumps({"ok": True, "current": name}))
            else:
                self._send(404, '{"err":"プロジェクトが見つかりません"}')
        elif p.path == "/api/save-as":
            # 別名保存(ブランチ): {name, proj, force} → projects/<name>/ を作り project.json と参照素材をコピー
            try:
                body_ = json.loads(body) if body else {}
            except Exception:
                self._send(400, '{"err":"不正なリクエスト"}'); return
            name = os.path.basename(str(body_.get("name", "")).strip())
            pj = body_.get("proj")
            if not name or not isinstance(pj, dict):
                self._send(400, '{"err":"プロジェクト名が空です"}'); return
            target = os.path.join(PROJECTS_DIR, name)
            cur_name = os.path.basename(PROJECT)
            # 既存を上書きする場合は force が必要（現在のプロジェクトへの保存は除く）
            if name != cur_name and os.path.isdir(target) and not body_.get("force"):
                self._send(409, json.dumps({"err": "同名のプロジェクトが既にあります", "exists": True},
                                           ensure_ascii=False)); return
            try:
                os.makedirs(target, exist_ok=True)
                pj.setdefault("meta", {})["title"] = name
                save_project_text(os.path.join(target, "project.json"),
                                  json.dumps(pj, ensure_ascii=False, indent=1))
                # 参照素材を現プロジェクトからコピー（既にあるものはスキップ）
                copied = 0
                if os.path.abspath(target) != os.path.abspath(PROJECT):
                    for tr in pj.get("tracks", []):
                        for c in tr.get("clips", []):
                            src = c.get("src")
                            if not src:
                                continue
                            fn = os.path.basename(src)
                            s = os.path.join(PROJECT, fn); d = os.path.join(target, fn)
                            if os.path.isfile(s) and not os.path.exists(d):
                                shutil.copy2(s, d); copied += 1
                PROJECT = target
                self._send(200, json.dumps({"ok": True, "current": name, "copied": copied},
                                           ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"err": str(e)}, ensure_ascii=False))
        elif p.path == "/api/export-project":
            # プロジェクト一式をzip化: project.json + 参照素材のみ（out/等の生成物は除外）
            try:
                opts = json.loads(body) if body else {}
            except Exception:
                opts = {}
            save_dir = os.path.expanduser(str(opts.get("save_dir") or "~/Downloads"))
            if not os.path.isdir(save_dir):
                self._send(400, '{"err":"保存先フォルダが見つかりません"}'); return
            try:
                assets = project_assets(PROJECT)
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)})); return
            zpath = unique_path(os.path.join(save_dir, os.path.basename(PROJECT) + ".veproj.zip"))
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(os.path.join(PROJECT, "project.json"), "project.json")
                for name in assets:
                    zf.write(os.path.join(PROJECT, name), name)
            self._send(200, json.dumps({"ok": True, "name": os.path.basename(zpath),
                                        "size": os.path.getsize(zpath),
                                        "saved_to": zpath, "assets": len(assets)}))
        elif p.path == "/api/import-project":
            # zip(rawバイト)を受け取り projects/<zip名>/ に展開。zip slip対策で基底名のみ採用
            q_ = urllib.parse.parse_qs(p.query)
            fname = os.path.basename(q_.get("name", ["project.zip"])[0])
            base = fname
            for ext in (".veproj.zip", ".zip"):
                if base.lower().endswith(ext):
                    base = base[:-len(ext)]; break
            base = base.strip() or "imported"
            try:
                zf = zipfile.ZipFile(io.BytesIO(body))
                names = [os.path.basename(i.filename) for i in zf.infolist() if not i.is_dir()]
                if "project.json" not in names:
                    self._send(400, '{"err":"project.json がzipに含まれていません"}'); return
                target = unique_path(os.path.join(PROJECTS_DIR, base))
                os.makedirs(target)
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = os.path.basename(info.filename)
                    if not name or name.startswith("."):
                        continue
                    with zf.open(info) as src, open(os.path.join(target, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            except zipfile.BadZipFile:
                self._send(400, '{"err":"zipファイルとして読めません"}'); return
            except Exception as ex:
                self._send(500, json.dumps({"err": str(ex)})); return
            self._send(200, json.dumps({"ok": True, "dir": os.path.basename(target)}, ensure_ascii=False))
        elif p.path == "/api/choose-project":
            # OS標準の「開く」ダイアログで project.json を選ぶ → そのフォルダをプロジェクトとして開く
            fp = dlg_open_file("プロジェクトファイル（project.json）を選択", PROJECTS_DIR,
                               '"public.json"', [("project.json", "project.json"), ("JSON", "*.json")])
            if not fp:
                self._send(200, '{"ok":false,"cancelled":true}'); return
            pd = os.path.dirname(fp)
            if not os.path.isfile(os.path.join(pd, "project.json")):
                self._send(400, json.dumps({"err": "project.json のあるフォルダを選んでください"},
                                           ensure_ascii=False)); return
            PROJECT = pd
            self._send(200, json.dumps({"ok": True, "current": os.path.basename(pd)}, ensure_ascii=False))
        elif p.path == "/api/choose-save":
            # OS標準の「保存」ダイアログでプロジェクト名/場所を決める → フォルダを作り project.json と素材を保存
            try:
                body_ = json.loads(body) if body else {}
            except Exception:
                self._send(400, '{"err":"不正なリクエスト"}'); return
            pj = body_.get("proj")
            defname = str(body_.get("name") or "無題のプロジェクト")
            if not isinstance(pj, dict):
                self._send(400, '{"err":"保存データがありません"}'); return
            got = dlg_save_name("プロジェクトの保存先と名前を指定", PROJECTS_DIR, defname)
            if not got:
                self._send(200, '{"ok":false,"cancelled":true}'); return
            target = got.rstrip("/").rstrip("\\")
            if target.endswith(".json"):      # project.json を指定された場合はその親をプロジェクトとする
                target = os.path.dirname(target)
            name = os.path.basename(target)
            try:
                os.makedirs(target, exist_ok=True)
                pj.setdefault("meta", {})["title"] = name
                save_project_text(os.path.join(target, "project.json"),
                                  json.dumps(pj, ensure_ascii=False, indent=1))
                copied = 0
                if os.path.abspath(target) != os.path.abspath(PROJECT):
                    for tr in pj.get("tracks", []):
                        for c in tr.get("clips", []):
                            src = c.get("src")
                            if not src:
                                continue
                            fn = os.path.basename(src)
                            s = os.path.join(PROJECT, fn); d = os.path.join(target, fn)
                            if os.path.isfile(s) and not os.path.exists(d):
                                shutil.copy2(s, d); copied += 1
                PROJECT = target
                self._send(200, json.dumps({"ok": True, "current": name, "copied": copied},
                                           ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"err": str(e)}, ensure_ascii=False))
        elif p.path == "/api/viewer-dir":
            # ビューア: 見たいフォルダをOSのダイアログで選ぶ。
            # 選ばれたフォルダだけを VIEW_DIRS に積んで配信を許可する（任意パスは受け付けない）
            d = dlg_choose_dir("動画を見るフォルダを選択")
            if not d:
                self._send(200, '{"ok":false}'); return
            ap = allow_view_dir(d)
            n = len([f for f in os.listdir(ap) if f.lower().endswith(VIDEO_EXT)]) \
                if os.path.isdir(ap) else 0
            self._send(200, json.dumps({"ok": True, "dir": ap,
                                        "label": os.path.basename(ap.rstrip(os.sep)) or ap,
                                        "count": n}, ensure_ascii=False))
        elif p.path == "/api/choose-dir":
            # OS標準のフォルダ選択ダイアログ（Mac=Finder / Win=tkinter）
            d = dlg_choose_dir("書き出し先フォルダを選択")
            if d:
                self._send(200, json.dumps({"ok": True, "path": d}))
            else:
                self._send(200, '{"ok":false,"cancelled":true}')
        elif p.path == "/api/reveal":
            # ファイルマネージャで書き出しファイルを選択表示。name=out内 / path=フルパス
            q_ = urllib.parse.parse_qs(p.query)
            path = q_.get("path", [""])[0]
            if path:
                fp = os.path.expanduser(path)
            else:
                name = os.path.basename(q_.get("name", [""])[0])
                fp = os.path.join(PROJECT, "out", name) if name else ""
            if fp and os.path.isfile(fp):
                reveal_in_file_manager(fp)
                self._send(200, '{"ok":true}')
            else:
                self._send(404, '{"err":"no file"}')
        else:
            self._send(404, "{}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    # マルチスレッド化: レンダリング中も他のAPI（保存/一覧/素材配信）が凍らない
    # ⚠️ Windowsは SO_REUSEADDR の意味がMac/Linuxと違い、LISTEN中のポートにも
    #    そのままbindできてしまう（=起動成功に見えるが古いプロセスへ繋がる）。
    #    allow_reuse_address=False にして「使用中なら即エラーで落ちる」挙動に統一する。
    #    参考: issue #4（2026-07-23 利用者報告）
    class _Server(ThreadingHTTPServer):
        allow_reuse_address = False
    try:
        srv = _Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # WinError 10048 / EADDRINUSE: 別プロセスが既にポートを握っている
        import sys
        print(f"[ERROR] ポート {PORT} が既に使われています。", file=sys.stderr)
        print(f"        別の video-editor が起動していないか、", file=sys.stderr)
        print(f"        タスクマネージャー(Win) / アクティビティモニタ(Mac) で", file=sys.stderr)
        print(f"        古い python プロセスを終了してから再実行してください。", file=sys.stderr)
        print(f"        別のポートで開くこともできます:  VE_PORT={PORT + 1} python3 ui/server.py",
              file=sys.stderr)
        print(f"        詳細: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"video-editor UI: http://localhost:{PORT}  (project: {PROJECT})")
    srv.serve_forever()
