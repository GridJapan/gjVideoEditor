# -*- coding: utf-8 -*-
"""依存（外部コマンド・Pythonパッケージ）の確認と、OS別の入れ方の案内。

**「無いなら無いと、入れ方つきで、止まる」**のがこのファイルの役割。
生の FileNotFoundError や ModuleNotFoundError をユーザーに見せない。
（技術者でないメンバーが英語のトレースバックを見ると、そこで作業が止まる）

使い方:
    import _deps
    _deps.require("ffmpeg", "ffprobe")     # 無ければ案内を出して終了
    _deps.require("Pillow")                # パッケージも同じ書き方でよい

    print(_deps.report())                  # 一覧（診断用）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ⚠️ 自分で _wincompat を読む。呼び出し側の import 順に依存しないため。
#    順序が逆だと「依存が無い」案内そのものが cp932 の UnicodeEncodeError で消える
#    ＝このファイルの存在意義が消える（2026-08-07 gen_chat.py が実際に逆順だった）
import _wincompat  # noqa: E402,F401
import os, shutil, subprocess, sys

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# name: (種別, 確認方法, 何に使うか, {OS: 入れ方})
DEPS = {
    "ffmpeg": ("cmd", "ffmpeg", "動画の書き出し", {
        "mac": "brew install ffmpeg",
        "win": "winget install Gyan.FFmpeg",
        "linux": "sudo apt install ffmpeg",
    }),
    "ffprobe": ("cmd", "ffprobe", "素材の尺・解像度の取得", {
        "mac": "brew install ffmpeg",          # ffmpeg に同梱
        "win": "winget install Gyan.FFmpeg",
        "linux": "sudo apt install ffmpeg",
    }),
    "Pillow": ("py", "PIL", "字幕・図形の描画", {
        "all": f'"{sys.executable}" -m pip install Pillow',
    }),
    "edge-tts": ("py", "edge_tts", "ナレーション生成（無料・APIキー不要）", {
        "all": f'"{sys.executable}" -m pip install edge-tts',
    }),
}


def _os_key():
    return "mac" if IS_MAC else ("win" if IS_WIN else "linux")


def _how(spec):
    return spec.get("all") or spec.get(_os_key()) or list(spec.values())[0]


def pyenv_pythons():
    """pyenv に入っている python を新しい順に列挙する。
    バージョンを決め打ちしない（作者の 3.11.9 を書くと他の人の環境で外れる）。"""
    base = os.path.join(os.environ.get("PYENV_ROOT") or os.path.expanduser("~/.pyenv"),
                        "versions")
    if not os.path.isdir(base):
        return []

    def vkey(name):     # 文字列順だと 3.9.1 が 3.11.9 より新しい扱いになるため数値で比べる
        return tuple(int(x) if x.isdigit() else -1 for x in name.split("."))
    try:
        vers = sorted((d for d in os.listdir(base)
                       if os.path.isdir(os.path.join(base, d))), key=vkey, reverse=True)
    except OSError:
        return []
    return [os.path.join(base, v, "bin", "python3") for v in vers]


def python_with(module):
    """module を import できる Python を返す（無ければ None）。
    ①この実行Python ②PATH上のpython3/python ③pyenv配下（新しい順）。

    ⚠️ **`sys.executable` だけを見ないこと。** pyenv等でツールを動かすPythonと
    パッケージの入ったPythonが食い違うのは普通にある。2026-08-07、edge-tts が
    別バージョンに入っている環境で `make_video.py --voice` が「入っていません」と
    拒否した一方、`gen_voice.py` は同じ環境で**実際に生成できた**
    （gen_voice 側だけが探索していた）＝案内どおりの導線が塞がれていた。"""
    for c in [sys.executable, shutil.which("python3"), shutil.which("python"),
              *pyenv_pythons()]:
        if not c or not os.path.exists(c):
            continue
        if subprocess.run([c, "-c", f"import {module}"], capture_output=True).returncode == 0:
            return c
    return None


def missing(*names):
    """入っていないものだけ返す。"""
    out = []
    for n in names:
        kind, probe, _use, _how_ = DEPS[n]
        if kind == "cmd":
            if not shutil.which(probe):
                out.append(n)
        elif python_with(probe) is None:
            out.append(n)
    return out


def have(name):
    return not missing(name)


def require(*names, hint=None):
    """1つでも欠けていたら、入れ方を出して終了する。"""
    lack = missing(*names)
    if not lack:
        return
    # ffmpeg と ffprobe は同じパッケージなので重複して案内しない
    seen, lines = set(), []
    for n in lack:
        _k, _p, use, spec = DEPS[n]
        cmd = _how(spec)
        if cmd in seen:
            continue
        seen.add(cmd)
        lines.append(f"  ・{n}（{use}）\n      {cmd}")

    print(f"\n❌ 必要なものが入っていません: {', '.join(lack)}", file=sys.stderr)
    print("\n次を実行してから、もう一度お試しください:\n", file=sys.stderr)
    print("\n".join(lines), file=sys.stderr)
    if any(DEPS[n][0] == "cmd" for n in lack):
        print("\n  ※ 入れたあとはターミナル（コマンドプロンプト）を開き直してください。"
              "\n     開いたままだと、入れたコマンドがまだ見つかりません。", file=sys.stderr)
    if hint:
        print(f"\n{hint}", file=sys.stderr)
    sys.exit(1)


def report():
    """全依存の状態を1行ずつ。診断用。"""
    rows = []
    for n, (kind, probe, use, spec) in DEPS.items():
        ok = have(n)
        mark = "✅" if ok else "❌"
        where = shutil.which(probe) if kind == "cmd" and ok else ""
        rows.append(f"{mark} {n:9} {use}" + (f"  [{where}]" if where else "")
                    + ("" if ok else f"\n     入れ方: {_how(spec)}"))
    return "\n".join(rows)


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}  [{sys.executable}]\n")
    print(report())
