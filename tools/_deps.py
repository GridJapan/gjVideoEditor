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


def missing(*names):
    """入っていないものだけ返す。"""
    out = []
    for n in names:
        kind, probe, _use, _how_ = DEPS[n]
        if kind == "cmd":
            if not shutil.which(probe):
                out.append(n)
        else:
            r = subprocess.run([sys.executable, "-c", f"import {probe}"],
                               capture_output=True)
            if r.returncode != 0:
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
