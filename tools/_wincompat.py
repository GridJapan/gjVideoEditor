# -*- coding: utf-8 -*-
"""Windows(cp932)コンソールでの文字化け・クラッシュを防ぐ共通処理。

各ツールの先頭で `import _wincompat` するだけ（副作用で標準出力をUTF-8化する）。
理由: Windowsの日本語環境ではコンソールの既定が cp932 で、✓ や ✅ を print すると
UnicodeEncodeError で落ちる（2026-07-21 利用者の環境で発生）。
Mac/Linux では何もしない（すでにUTF-8のため）。
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")   # Python 3.7+
    except (AttributeError, ValueError):
        pass                                     # 差し替え済み等で失敗しても無害
