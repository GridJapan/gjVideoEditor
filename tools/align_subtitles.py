# -*- coding: utf-8 -*-
"""字幕タイミングを実音声に合わせ込むツール（forced-alignment風）。

whisper(単語タイムスタンプ付きjson)と project.json を突き合わせ、既存の字幕クリップの
start/end だけを実発話に合わせて更新する（テキスト・スタイル・他キーは一切触らない）。

ジャンプカット対応: 全映像クリップ（同一ソースを in= 参照する複数クリップ）から
「ソース時刻 → タイムライン時刻」の区分線形マップを作って変換する。
カットで消えた発話に乗っている字幕は動かさず報告する。

前提: whisper を**単語タイムスタンプ付き**で実行し、jsonを作っておく（どの実装でもよい）。
  出力先の既定は OS標準の一時フォルダ内 "wh"（Mac/Linux: /tmp/wh, Windows: %TEMP%\wh）。
  第2引数でjsonのパスを直接渡してもよい。

  # macOS (Apple Silicon) — mlx_whisper は Apple Silicon 専用・最速
  python3 -m mlx_whisper source_cam.mp4 --language ja \
      --output-format json --output-dir /tmp/wh \
      --model mlx-community/whisper-large-v3-turbo --word-timestamps True

  # Windows / その他 — faster-whisper か openai-whisper（CPUでも動く）
  pip install openai-whisper
  whisper source_cam.mp4 --language ja --model medium \
      --output_format json --output_dir %TEMP%\wh --word_timestamps True

usage:
  python3 tools/align_subtitles.py <project_dir> [whisper.json] [apply] [--track <id>]
  - whisper.json 省略時は /tmp/wh/<video>.json を探す
  - 既定の対象トラックは id="cap"（タイトルやカードのトラックは触らない）
  - apply を付けると project.json を書き換える（付けなければドライラン）
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _wincompat  # noqa: E402  Windows cp932 対策
import json, glob, re, sys, os, tempfile

# whisper json の探索先。/tmp/wh（従来のMac/Linux運用）と OS標準の一時フォルダ（Winは %TEMP%）の両方。
# ※ macOSの tempfile.gettempdir() は TMPDIR を読むため /var/folders/... を返す。/tmp とは別物なので
#    片方だけにすると既存の運用が壊れる
WH_DIRS = ["/tmp/wh", os.path.join(tempfile.gettempdir(), "wh")]

def norm(t):
    return re.sub(r'[^ぁ-んァ-ヶ一-龠ーゝゞ゛゜a-zA-Z0-9]', '', t or '')

def load_words(wh_path):
    """whisper jsonから (正規化文字列, 各文字のsource開始秒, 終了秒) を作る。
    mlx_whisper標準形式(words[].word/start/end)と、短縮形式(w[].w/s/e)の両方を受ける。"""
    wh = json.load(open(wh_path, encoding="utf-8"))
    NS, nstart, nend = [], [], []
    for seg in wh["segments"]:
        for w in (seg.get("words") or seg.get("w") or []):
            word = w.get("word") or w.get("w")
            s = w.get("start", w.get("s")); e = w.get("end", w.get("e"))
            if s is None or e is None:
                continue
            for ch in norm(word):
                NS.append(ch); nstart.append(float(s)); nend.append(float(e))
    return "".join(NS), nstart, nend

def build_src2tl(proj):
    """全映像クリップから ソース秒→タイムライン秒 の区分線形マップを作る。
    戻り値: (segments, src2tl関数)。segments=[(src_a, src_b, tl_a)] ソース順。"""
    segs = []
    for t in proj["tracks"]:
        if t["type"] != "video":
            continue
        for c in t["clips"]:
            inn = float(c.get("in", 0)); dur = float(c["end"]) - float(c["start"])
            segs.append((inn, inn + dur, float(c["start"])))
    if not segs:
        raise SystemExit("映像クリップがありません（type=video）")
    segs.sort()
    def src2tl(s, snap=1.6):
        """ソース秒→タイムライン秒。カットで消えた瞬間は None。
        ただし後続クリップ先頭から snap 秒以内なら、そこへ吸着させる
        （whisperの単語粒度は発話頭のフィラーを含みがちで、クリップ開始より1秒強早く出るため）。"""
        for a, b, tl in segs:
            if a - snap <= s <= b:
                return tl + max(0.0, s - a)
        return None
    return segs, src2tl

def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    args = sys.argv[1:]
    track_id = "cap"
    if "--track" in args:
        i = args.index("--track"); track_id = args[i + 1]; del args[i:i + 2]
    pdir = os.path.abspath(args[0])
    rest = args[1:]
    apply = "apply" in rest
    wh_path = next((a for a in rest if a.endswith(".json")), None)

    proj_path = os.path.join(pdir, "project.json")
    proj = json.load(open(proj_path, encoding="utf-8"))

    segs, src2tl = build_src2tl(proj)

    if not wh_path:
        src0 = next(c["src"] for t in proj["tracks"] if t["type"] == "video" for c in t["clips"])
        base = os.path.splitext(os.path.basename(src0))[0]
        cands = []
        for d in WH_DIRS:  # 同名優先 → 無ければそのフォルダの任意のjson
            cands = glob.glob(os.path.join(d, f"{base}.json")) or glob.glob(os.path.join(d, "*.json"))
            if cands:
                break
        if not cands:
            raise SystemExit(f"whisper json が見つかりません（探索先: {' / '.join(WH_DIRS)}）。"
                             "先に文字起こしを実行するか、パスを引数で渡してください。")
        wh_path = cands[0]
    NS, nstart, nend = load_words(wh_path)

    # 対象トラック: 既定 id="cap"。無ければ候補を提示して止まる（先頭captionを勝手に触らない）
    cap_track = next((t for t in proj["tracks"] if t.get("id") == track_id), None)
    if cap_track is None or cap_track.get("type") != "caption":
        ids = [f'{t.get("id")}({t.get("label")})' for t in proj["tracks"] if t["type"] == "caption"]
        raise SystemExit(f"字幕トラック id={track_id!r} が見つかりません。--track で指定: {ids}")
    caps = [c for c in cap_track["clips"] if norm(c.get("text")) and c.get("text") != "新しい字幕"]

    # 字幕ごとの局所アライメント。
    # （全字幕連結の大域アライメントは、編集で発話順を入れ替えた字幕が誤アンカーするためやめた。
    #   代償として、同一文言が複数回発話される場合は最長一致の出現位置に付く）
    from difflib import SequenceMatcher

    def match_caption(cn):
        """正規化済み字幕テキスト cn の (source開始, source終了, 一致文字数)。"""
        m = SequenceMatcher(None, NS, cn, autojunk=False).find_longest_match(0, len(NS), 0, len(cn))
        if m.size == 0:
            return None, None, 0
        i0 = max(0, m.a - m.b)                                            # cn先頭に対応するNS位置(推定)
        i1 = min(len(NS) - 1, m.a + m.size - 1 + (len(cn) - (m.b + m.size)))  # cn末尾(推定)
        return nstart[i0], nend[i1], m.size

    updated, skipped = [], []
    prop = []  # (clip, new_start, new_end)
    for c in caps:
        cn = norm(c["text"])
        ss, ee, nhit = match_caption(cn)
        if ss is None or nhit < max(2, len(cn) * 0.3):  # 3割未満のマッチは信用しない
            skipped.append((c, "whisperと照合できず"))
            continue
        ts = src2tl(ss)
        if ts is None:
            skipped.append((c, f"発話がカットで消えている (src {ss:.1f}s)"))
            continue
        te = src2tl(ee)
        if te is None or te <= ts:  # 終端がカット内→その発話が属するクリップの末尾まで
            te = next((tl + (b - a) for a, b, tl in segs if a <= ss <= b), ts + 1.0)
        prop.append((c, ts, te))

    # 同一トラック内の重なり禁止（鉄則）: start順に整列し、endを次のstartの手前へクランプ
    prop.sort(key=lambda x: x[1])
    for i, (c, ts, te) in enumerate(prop):
        if i + 1 < len(prop):
            te = min(te, prop[i + 1][1] - 0.02)
        te = max(te, ts + 0.3)
        c["start"], c["end"] = round(max(0.0, ts), 2), round(te, 2)
        updated.append(c)

    print(f"対象 {len(caps)} 件 → 更新 {len(updated)} / 保留 {len(skipped)}  "
          f"(映像 {len(segs)} クリップ・トラック id={track_id})")
    for c in updated:
        print(f'  {c["start"]:7.2f}-{c["end"]:7.2f} {(c.get("text") or "").split(chr(10))[0][:28]}')
    for c, why in skipped:
        print(f'  ⏸ 保留: {(c.get("text") or "")[:24]!r} — {why}')

    if apply:
        # indent=1（全保存経路で統一）＋アトミック書き込み。触ったのは start/end だけ
        tmp = proj_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(proj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, proj_path)
        print(f"APPLIED: {proj_path}（start/endのみ更新。スタイル・テキストは無変更）")
    else:
        print("（ドライラン。反映するには apply を付けて再実行）")

if __name__ == "__main__":
    main()
