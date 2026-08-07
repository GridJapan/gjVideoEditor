# -*- coding: utf-8 -*-
"""既存プロジェクトの黙った上書きを止める共通ガード。

**なぜ必要か（2026-08-07 実害）**: 引数を検証しないビルダーに `--help` を渡しただけで
`projects/makasete-shunin-lp/project.json` が作り直され、**UIで後から足したBGMトラックが
丸ごと消えた**。素材はgit外なので、この種の上書きは復旧が非常に面倒になる。

規約（`.claude/rules/python-tools.md`）:
  project.json を丸ごと書き直すツールは `meta.madeBy.tool` を見て
  **自分が作ったものだけ**を作り直す。他は中断して --force を案内する。
"""
import json, os, sys


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def guard_overwrite(pdir, tool, force=False):
    """pdir の project.json を tool が作り直してよいか判定し、駄目なら人間可読エラーで止める。

    tool … このスクリプトのファイル名（例 "build_line.py"）。
            meta.madeBy.tool と一致すれば「自分の作り直し」＝通常運用として通す。"""
    fp = os.path.join(pdir, "project.json")
    if force or not os.path.exists(fp):
        return
    try:
        with open(fp, encoding="utf-8") as f:
            made = (json.load(f).get("meta") or {}).get("madeBy") or {}
    except (OSError, ValueError):
        made = {}
    if made.get("tool") == tool:
        return
    owner = f"{made['tool']}" if made.get("tool") else "手作業（UI）"
    name = os.path.basename(os.path.abspath(pdir))
    die(f"[{tool}] projects/{name}/ は既にあります（作ったのは {owner}）。\n"
        f"   作り直すと、UIで足したトラックや調整は戻せません。どちらかにしてください:\n"
        f"     ・別の名前で作る（推奨）\n"
        f"     ・本当に上書きしてよいなら --force を付けて実行する")


def stamp(pj, tool):
    """作り直しの持ち主を記録する（次回の guard_overwrite が読む）。"""
    pj.setdefault("meta", {})["madeBy"] = {"tool": tool}
    return pj
