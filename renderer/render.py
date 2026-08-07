# -*- coding: utf-8 -*-
"""project.json(tracks構造) -> MP4 レンダラ。映像+字幕+画像を合成し、cuts(区間削除)も反映する
無料スタック(Pillow + ffmpeg)。

tracks:
  - type "video"  : 複数クリップ対応。単色背景に各クリップを時間ゲートで重ね、音声はstartへ遅延ミックス
                    （src / start / end / in / audioLinked / gain）。クリップ間の空きは背景色（黒）
  - type "image"  : clips を画像オーバーレイ（src / start / end / x / y / w=0-1正規化）
  - type "caption": clips を字幕として焼き込む（start / end / text / color / x / y / w / h / align / valign）
cuts: [{start,end}] タイムライン上の削除区間。映像クリップを境界で分割し、字幕/画像/音声の時刻を再マップ。

usage: python3 renderer/render.py <project_dir>
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "../tools"))
import _wincompat  # noqa: E402  Windows cp932 対策（標準出力/stderrをUTF-8化）
import _deps       # noqa: E402  依存の確認（無ければ入れ方を出して止まる）
import os, re, sys, json, math, subprocess, unicodedata
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont

# style キーが無い project.json でも既定値で描けるようにする（KeyError死の防止）
DEFAULT_STYLE = {"font": "Hiragino Kaku Gothic Pro", "fontsize": 56}

# 行頭に来られない文字（折り返し時は前行へぶら下げる）
KINSOKU_HEAD = ("、。，．・：；？！ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ"
                "ゝゞヽヾ々）」』】〉》〕｝!?),.:;]}%％…‥")
# 行末に置けない文字（開き括弧類。次行へ送る）
KINSOKU_TAIL = "（「『【〈《〔｛([{"

# フォント解決: macOSのヒラギノを優先し、無ければWindowsの游ゴシック/メイリオへ落ちる。
# （候補は上から順に「実在する最初のもの」が選ばれる。Macでは常にヒラギノがヒットする＝従来と同じ）
WIN_GOTHIC = [
    "C:/Windows/Fonts/YuGothM.ttc",   # 游ゴシック Medium
    "C:/Windows/Fonts/YuGothR.ttc",   # 游ゴシック Regular
    "C:/Windows/Fonts/meiryo.ttc",    # メイリオ
    "C:/Windows/Fonts/msgothic.ttc",  # MS ゴシック（最後の砦）
]
WIN_GOTHIC_BOLD = [
    "C:/Windows/Fonts/YuGothB.ttc",   # 游ゴシック Bold
    "C:/Windows/Fonts/meiryob.ttc",   # メイリオ ボールド
] + WIN_GOTHIC
WIN_MINCHO = [
    "C:/Windows/Fonts/yumin.ttf",     # 游明朝
    "C:/Windows/Fonts/msmincho.ttc",  # MS 明朝
] + WIN_GOTHIC
# 簡体字（中国語）用。日本語フォントは「饺・实・铺」等のグリフを持たず豆腐(□)になる
# （2026-08-01: 中国語版の動画で「饺子咖喱」が「□子□□」と出た）。
WIN_SC = [
    "C:/Windows/Fonts/msyh.ttc",      # Microsoft YaHei
    "C:/Windows/Fonts/simhei.ttf",    # SimHei
    "C:/Windows/Fonts/simsun.ttc",    # SimSun（最後の砦）
]

FONT_CANDIDATES = {
    "Hiragino Kaku Gothic Pro": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    ] + WIN_GOTHIC,
    "Hiragino Mincho ProN": [
        "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
    ] + WIN_MINCHO,
    "Hiragino Maru Gothic ProN": [
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
    ] + WIN_GOTHIC,
    # 簡体字を含む字幕はこれを使う（日本語のかな・漢字も持つので混在行も出せる）
    "Hiragino Sans GB": [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ] + WIN_SC,
}
FONT_BOLD = {
    "Hiragino Kaku Gothic Pro": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    ] + WIN_GOTHIC_BOLD,
    "Hiragino Maru Gothic ProN": [
        "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
    ] + WIN_GOTHIC_BOLD,
    "Hiragino Sans GB": [
        "/System/Library/Fonts/Hiragino Sans GB.ttc#2",   # W6（同ttc内のフェイス2）
    ] + WIN_SC,
}
FONT_FALLBACK = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
] + WIN_GOTHIC


# project.json は「信用できる入力」ではない。
# zip読み込み(/api/import-project)で他人から受け取ったものがそのままここへ来るため、
# ffmpeg のコマンドラインやパスに載る値は必ずここを通してから使う。
_COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{3,8}|[A-Za-z]{3,20})(@(0|1|0?\.\d+))?$")


def safe_color(v, default="black"):
    """canvas.bg を ffmpeg の color= に渡す前に検証する。

    `black:s=8x8,nullsink;color=c=blue` のような値を素通しすると、
    任意のフィルタチェーンを足せてしまう（`movie=` でローカルファイル読み込みも可能）。
    許すのは #RGB形式・色名・末尾の @不透明度 だけ。
    """
    s = str(v or "").strip()
    if s in ("", "none", "transparent"):
        return default
    if _COLOR_RE.match(s):
        return s
    print(f"⚠️ canvas.bg の値 {s!r} は使えないので {default} にしました "
          f"（色名か #RRGGBB で書いてください）", file=sys.stderr)
    return default


def safe_title(v, pdir):
    """meta.title を出力ファイル名に使う前に検証する。

    `../../名前` を素通しすると projects/ の外にファイルを書ける。
    Windowsで使えない文字も落とす（日本語タイトルに ASCII の ? を混ぜるのはよくある）。
    """
    s = os.path.basename(str(v or "").strip())          # パス区切りを落とす
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", s).strip(" .")
    return s or os.path.basename(os.path.abspath(pdir))


def resolve_font(name, bold=False):
    cands = (FONT_BOLD.get(name, []) if bold else []) + FONT_CANDIDATES.get(name, []) + FONT_FALLBACK
    for p in cands:
        if os.path.exists(p.partition("#")[0]):   # "#N" は .ttc のフェイス番号（get_fontが解釈する）
            return p
    raise SystemExit("日本語フォントが見つかりません（Windowsは游ゴシック/メイリオ、macOSはヒラギノを探します）")


def wrap(draw, text, font, maxw):
    """チャンク単位の自動折り返し＋行頭禁則。
    英数字・記号の連なり（HDMI / (2010) / -14 等）は不可分チャンクとして途中で折らない。
    和文は1文字単位。行頭禁則文字（、。」等）は前行へぶら下げる（僅かな幅超過を許容）。
    1チャンク単独で幅を超える場合のみ文字単位に分割する。"""
    def fits(s):
        return draw.textlength(s, font=font) <= maxw
    chunks = re.findall(r"[0-9A-Za-z()\[\]{}<>%#&@+\-*/=._,:;!?'\"…]+|.", text)
    lines, cur = [], ""
    def hard_split(s):
        # 幅超過する長チャンクを、収まる最大前置で行に切り出していく
        while not fits(s) and len(s) > 1:
            k = len(s)
            while k > 1 and not fits(s[:k]):
                k -= 1
            lines.append(s[:k]); s = s[k:]
        return s
    for ch in chunks:
        if not cur:
            if ch.isspace():
                continue  # 行頭の空白は捨てる
            cur = hard_split(ch)
        elif fits(cur + ch):
            cur += ch
        elif len(ch) == 1 and ch in KINSOKU_HEAD:
            lines.append(cur + ch); cur = ""  # ぶら下げ
        else:
            # 行末禁則: 開き括弧で行を終えない（次行の先頭へ送る）
            carry = ""
            while cur and cur[-1] in KINSOKU_TAIL:
                carry = cur[-1] + carry; cur = cur[:-1]
            if cur:
                lines.append(cur)
            nxt = carry + ch
            cur = "" if nxt.isspace() else hard_split(nxt)
    if cur:
        lines.append(cur)
    return lines


def parse_emphasis(text):
    """`**〜**` を強調マークとして取り除き、(素のテキスト, 文字ごとの強調フラグ) を返す。

    結論や数字を色で立たせるための記法。閉じ忘れた `**` はそのまま文字として残す
    （書き手のtypoで文章が消えるより、マーカーが見えたほうが気づける）。"""
    if "**" not in text:
        return text, [False] * len(text)
    out, flags, on, i = [], [], False, 0
    while i < len(text):
        if text.startswith("**", i):
            # 閉じが無い開きマーカーは通常文字として扱う
            if not on and "**" not in text[i + 2:]:
                out.append(text[i]); flags.append(on); i += 1
                continue
            on = not on; i += 2
            continue
        out.append(text[i]); flags.append(on); i += 1
    return "".join(out), flags


def get_video_clips(proj):
    """映像トラックの全クリップ。start昇順。"""
    clips = []
    for tr in proj["tracks"]:
        if tr["type"] == "video":
            clips.extend(tr["clips"])
    if not clips:
        raise SystemExit("映像トラック（type=video）が見つかりません")
    return sorted(clips, key=lambda c: c["start"])


def get_captions(proj):
    caps = []
    for tr in proj["tracks"]:
        if tr["type"] == "caption":
            caps.extend(tr["clips"])
    return caps


def get_images(proj):
    imgs = []
    for tr in proj["tracks"]:
        if tr["type"] == "image":
            imgs.extend(tr["clips"])
    return imgs


def get_audios(proj):
    """効果音/BGMトラック（type=audio）のクリップ。
    ダッキングの相手探しに使うため、所属トラックの id/label を `_tid`/`_tlabel` として持たせる
    （内部専用キー。書き出し処理は project.json を保存しないので混入しない）。"""
    auds = []
    for tr in proj["tracks"]:
        if tr["type"] == "audio":
            for c in tr["clips"]:
                c["_tid"] = tr.get("id") or ""
                c["_tlabel"] = tr.get("label") or ""
            auds.extend(tr["clips"])
    return auds


def duck_expr(clip, all_audios, base_gain):
    """BGMダッキング: 相手（既定=ナレーション）の区間だけ音量を下げる volume 式を作る。

    clip["duck"] = true か {"to":0.25,"fade":0.35,"against":"narr"}。
    式は各区間 g_i(t) = to + (1-to)*clip(距離/fade, 0, 1) の min 合成。
    区間の端で fade 秒かけて滑らかに沈み・戻る。時刻はクリップ内(0起点)。
    相手が見つからないときは None（＝通常の volume にフォールバック）。"""
    d = clip.get("duck")
    if not d:
        return None
    conf = d if isinstance(d, dict) else {}
    to = max(0.0, min(1.0, float(conf.get("to", 0.25))))
    fade = max(0.05, float(conf.get("fade", 0.35)))
    against = str(conf.get("against", "")).strip()

    def is_target(c2):
        if c2 is clip:
            return False
        tid, tlab = c2.get("_tid", ""), c2.get("_tlabel", "")
        if against:
            return against in (tid, tlab) or against in tlab
        return ("narr" in tid.lower()) or ("ナレ" in tlab)

    s0 = float(clip["start"])
    ivs = sorted((float(c2["start"]) - s0, float(c2["end"]) - s0)
                 for c2 in all_audios if is_target(c2))
    if not ivs:
        return None
    terms = [f"({to:.3f}+{1 - to:.3f}*clip((max({a:.3f}-t,t-{b:.3f}))/{fade:.3f},0,1))"
             for a, b in ivs]
    expr = terms[0]
    for t_ in terms[1:]:
        expr = f"min({expr},{t_})"
    return f"volume='{base_gain:.4f}*{expr}':eval=frame"


# ---- cuts ----
def remap_time(t, cuts):
    """カット後の時間軸へ。t がカット内なら None（=消える）。"""
    shifted = t
    for c in sorted(cuts, key=lambda x: x["start"]):
        cs, ce = c["start"], c["end"]
        if t >= ce:
            shifted -= (ce - cs)
        elif t > cs:
            return None
    return shifted


def apply_cuts_to_clips(clips, cuts, time_keys):
    """clip の time_keys(例 start/end) をカット後時間に再マップ。区間内に落ちたものは除外。"""
    out = []
    for c in clips:
        ns = remap_time(c[time_keys[0]], cuts)
        ne = remap_time(c[time_keys[1]], cuts)
        if ns is None and ne is None:
            continue  # 完全にカット内
        # 片側がカット内なら境界にクリップ
        if ns is None:
            ns = remap_time(max(x["end"] for x in cuts if x["start"] <= c[time_keys[0]] <= x["end"]), cuts)
        if ne is None:
            ne = remap_time(min(x["start"] for x in cuts if x["start"] <= c[time_keys[1]] <= x["end"]), cuts)
        if ne is None or ns is None or ne <= ns:
            continue
        nc = dict(c)
        nc[time_keys[0]], nc[time_keys[1]] = round(ns, 3), round(ne, 3)
        out.append(nc)
    return out


def split_video_by_cuts(vclips, cuts):
    """各映像クリップを cuts 境界で分割し、cut後タイムラインへ再マップ。
    分割片ごとに in(ソース内オフセット)を調整するので、どのクリップも cut をまたがない。"""
    if not cuts:
        return [dict(c) for c in vclips]
    scuts = sorted(cuts, key=lambda x: x["start"])
    out = []
    for c in vclips:
        s, e, inn = c["start"], c["end"], c.get("in", 0)
        pos = s
        pieces = []  # (abs_start, abs_end)
        for cut in scuts:
            cs, ce = max(cut["start"], s), min(cut["end"], e)
            if ce <= cs:
                continue
            if cs > pos:
                pieces.append((pos, cs))
            pos = max(pos, ce)
        if pos < e:
            pieces.append((pos, e))
        for a, b in pieces:
            ns = remap_time(a, cuts)
            if ns is None:
                continue
            nc = dict(c)
            nc["in"] = round(inn + (a - s), 3)
            nc["start"] = round(ns, 3)
            nc["end"] = round(ns + (b - a), 3)
            out.append(nc)
    return out


def _col(c, n=3):
    """[R,G,B(,A)] を n要素のタプルへ。"""
    c = list(c) + [255] * (n - len(c))
    return tuple(int(v) for v in c[:n])


_FONT_CACHE = {}

def get_font(name, size, bold=False):
    key = (name, size, bold)
    if key not in _FONT_CACHE:
        # 候補は "パス" か "パス#N"（.ttc内のフェイス番号）。
        # 太さが別ファイルではなく同一ttcの別フェイスに入っているフォントがある
        # （Hiragino Sans GB は W3=0 / W6=2。index を指定しないと常に細字になる）
        path, _, idx = resolve_font(name, bold).partition("#")
        _FONT_CACHE[key] = ImageFont.truetype(path, size, index=int(idx or 0))
    return _FONT_CACHE[key]


_CODEC = None

def pick_video_codec():
    """実在するH.264エンコーダを選ぶ。`libx264` を決め打ちしない。

    ffmpeg は配布ビルドによって libx264 を含まないことがある（GPL回避ビルド等）。
    決め打ちだと配布先で「書き出しだけ落ちる」になり、原因が分かりにくい。
    `VE_VIDEO_CODEC` で明示指定もできる。"""
    global _CODEC
    if _CODEC:
        return _CODEC
    want = os.environ.get("VE_VIDEO_CODEC", "").strip()
    cands = ([want] if want else []) + ["libx264", "libopenh264", "h264_videotoolbox", "mpeg4"]
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        _CODEC = "libx264"
        return _CODEC
    for c in cands:
        if re.search(rf"^\s*\S+\s+{re.escape(c)}\s", out, re.M):
            if c != "libx264":
                print(f"ℹ️ libx264 が無いため {c} で書き出します", file=sys.stderr)
            _CODEC = c
            return _CODEC
    _CODEC = "libx264"
    return _CODEC


_GLYPH_CACHE = {}

def missing_glyphs(name, text, bold=False):
    """フォントが text の文字を描けるか調べ、描けない文字を返す（空なら全部描ける）。

    **未定義コードポイント(U+10FFFF)を描いた結果**と各文字を突き合わせる。
    豆腐(□)は「グリフが無い時に出る代替字形」なので、これと一致する＝そのフォントは
    その文字を持たない、と判定できる。フォント名やOSに依存しない。

    2026-08-03: 中国語字幕を日本語フォントで焼き、「饺子咖喱」が「□子□□」になって
    書き出し直しになった。書き出し前に気づくための検査。
    記号・空白は対象外（フォントが持たないのが普通で、警告するとノイズになる）。
    """
    try:
        font = get_font(name, 30, bold)
        blank = font.getmask("\U0010ffff")
        blank_sig = (blank.size, blank.getbbox(), bytes(blank))
        miss = []
        for ch in dict.fromkeys(str(text or "")):
            if unicodedata.category(ch)[0] not in ("L", "N"):
                continue
            key = (name, bold, ch)
            if key not in _GLYPH_CACHE:
                m = font.getmask(ch)
                _GLYPH_CACHE[key] = (m.getbbox() is None
                                     or (m.size, m.getbbox(), bytes(m)) == blank_sig)
            if _GLYPH_CACHE[key]:
                miss.append(ch)
        return miss
    except Exception:
        # 検査できない環境（フォント未解決など）では素通しする。
        # 検査の失敗で書き出しを止めないこと。
        return []


def _render_caption_vertical(proj, cap, W, H):
    """縦書き字幕。writing-mode:vertical-rl と同じ規則:
      ・文字は1文字ずつ縦に積む（改行\n = 列区切り）
      ・列は**右から左へ**並べる（右列が1段落目）
      ・align: top=列内で上寄せ / middle=中央 / bottom=下寄せ
      ・valign: right=枠右寄せ / middle=中央 / left=枠左寄せ（列全体の位置）
    シンプルな縦書きで、句読点の90°回転や横中英数の縦中横は未対応（必要になったら足す）。"""
    style = {**DEFAULT_STYLE, **(proj.get("style") or {})}
    box = style.get("box", {"color": [0, 0, 0, 175], "radius": 18, "pad": 20})
    scratch = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    body = cap.get("text") or ""
    body, emph_flags = parse_emphasis(body)
    col = _col(cap.get("textColor") or cap.get("color") or [255, 255, 255])
    emph_col = _col(cap.get("emphasisColor") or [214, 173, 92])
    fs = int(cap.get("fontsize") or style["fontsize"])
    font_i = get_font(cap.get("font") or style["font"], fs, bool(cap.get("bold")))
    pad = box["pad"]
    bx = float(0.08 if cap.get("x") is None else cap["x"]) * W
    by = float(0.20 if cap.get("y") is None else cap["y"]) * H
    bw = float(0.20 if cap.get("w") is None else cap["w"]) * W
    bh = float(0.60 if cap.get("h") is None else cap["h"]) * H

    # 段落=列。明示改行で新しい列を作る。各列の中身は素の文字列
    cols = body.split("\n") if body else [""]
    # 対応するemph_flags（\n はフラグを持たないので、テキスト位置を追いながら切る）
    col_flags, pos = [], 0
    for c_str in cols:
        col_flags.append(emph_flags[pos:pos + len(c_str)])
        pos += len(c_str) + 1  # +1 は \n の分

    ch_h = int(fs * 1.02)          # 文字1つの縦の送り
    col_w = int(fs * 1.36)         # 列1つの横幅（＝行送り）
    total_w = col_w * len(cols)
    max_ch = max((len(c) for c in cols), default=1)
    block_h = ch_h * max_ch

    # 枠内での揃え。align=列内(上下)・valign=枠内(左右)。横書きから読み替える
    v_al = cap.get("align", "center")             # top=上寄せ / center=中央 / bottom=下寄せ
    h_al = cap.get("valign", "middle")            # left/right/middle
    if h_al == "left":     bx0 = bx
    elif h_al == "right":  bx0 = bx + bw - total_w
    else:                  bx0 = bx + (bw - total_w) / 2
    if v_al == "top":      by0_base = by
    elif v_al == "bottom": by0_base = by + bh - block_h
    else:                  by0_base = by + (bh - block_h) / 2

    # ハイライト（縦書き用の背景座布団）
    if cap.get("highlight", True) is not False:
        hi = _col(cap.get("highlightColor") or box["color"], 4)
        if cap.get("panel"):
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=box["radius"], fill=hi)
        else:
            d.rounded_rectangle([bx0 - pad, by0_base - pad,
                                 bx0 + total_w + pad, by0_base + block_h + pad],
                                radius=box["radius"], fill=hi)

    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dt = ImageDraw.Draw(tl)
    use_outline = bool(cap.get("outline"))
    ow = int(cap.get("outlineWidth", 2)) if use_outline else 0
    oc = _col(cap.get("outlineColor") or [0, 0, 0])
    shadow = bool(cap.get("shadow"))
    sh_off = max(2, int(fs * 0.06))
    sh_col = _col(cap.get("shadowColor") or [0, 0, 0]) + (
        int((0.6 if cap.get("shadowOpacity") is None else cap["shadowOpacity"]) * 255),)

    # 列を右から左へ描く（writing-mode:vertical-rl の順序）
    for ci, text in enumerate(cols):
        cx_col = bx0 + total_w - col_w * (ci + 1) + (col_w - fs) / 2   # 列の左上x
        this_h = ch_h * len(text)
        if v_al == "top":     ty0 = by0_base
        elif v_al == "bottom": ty0 = by0_base + block_h - this_h
        else:                  ty0 = by0_base + (block_h - this_h) / 2
        fl = col_flags[ci] if ci < len(col_flags) else [False] * len(text)
        for k, ch in enumerate(text):
            cy = ty0 + k * ch_h
            fill_c = emph_col if (k < len(fl) and fl[k]) else col
            if shadow:
                dt.text((cx_col + sh_off, cy + sh_off), ch, font=font_i, fill=sh_col)
            if use_outline:
                dt.text((cx_col, cy), ch, font=font_i, fill=fill_c, stroke_width=ow, stroke_fill=oc)
            else:
                dt.text((cx_col, cy), ch, font=font_i, fill=fill_c)

    out = Image.alpha_composite(img, tl)
    rot = float(cap.get("rotate") or 0)
    if abs(rot) > 0.05:
        cx = (bx + bw / 2) if cap.get("panel") else (bx0 + total_w / 2)
        cy = (by + bh / 2) if cap.get("panel") else (by0_base + block_h / 2)
        out = out.rotate(-rot, center=(cx, cy), resample=Image.BICUBIC)
    return out


def render_caption_image(proj, cap, W, H):
    """字幕1クリップを RGBA Image として描く。
    **プレビュー(/api/caption-preview)と書き出しが共有する唯一の実装**。
    ここを分岐させるとプレビューと書き出しが食い違うので、絶対に別実装を作らないこと。

    clip: textColor / bold / italic / underline / strike / align / valign / fontsize / font /
          highlight(既定true) / highlightColor / outline / outlineColor / outlineWidth /
          shadow / shadowColor / shadowOpacity / vertical(縦書き)。
          color=タイムライン色（描画には使わない）。"""
    if cap.get("vertical"):
        return _render_caption_vertical(proj, cap, W, H)
    style = {**DEFAULT_STYLE, **(proj.get("style") or {})}  # styleは任意キー（欠落でも既定値で描く）
    box = style.get("box", {"color": [0, 0, 0, 175], "radius": 18, "pad": 20})
    margin_bottom = style.get("marginBottom", 44)
    speakers = style.get("speakers", {})
    scratch = ImageDraw.Draw(Image.new("RGBA", (W, H)))

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if "speaker" in cap:  # 旧形式互換: 話者ラベルを前置し話者色を使う
        sp = speakers.get(cap["speaker"], {"label": cap.get("speaker", ""), "color": [255, 255, 255]})
        body = (sp["label"] + "｜" if sp.get("label") else "") + cap["text"]
        col = _col(sp.get("color", [255, 255, 255]))
    else:
        body = cap.get("text") or ""
        col = _col(cap.get("textColor") or cap.get("color") or [255, 255, 255])
    fs_i = int(cap.get("fontsize") or style["fontsize"])
    font_i = get_font(cap.get("font") or style["font"], fs_i, bool(cap.get("bold")))
    pad = box["pad"]
    bx = float(0.08 if cap.get("x") is None else cap["x"]) * W
    by = float(0.70 if cap.get("y") is None else cap["y"]) * H
    bw = float(0.84 if cap.get("w") is None else cap["w"]) * W
    bh = float(0.24 if cap.get("h") is None else cap["h"]) * H
    legacy = all(cap.get(k) is None for k in ("x", "y", "w", "h", "valign"))
    # 部分強調: **〜** で囲んだ範囲を emphasisColor（既定=金）で描く。
    # 結論・数字を色で立たせるための記法（囲みマーカーは描画されない）。
    body, emph_flags = parse_emphasis(body)
    emph_col = _col(cap.get("emphasisColor") or [214, 173, 92])
    lines, line_flags = [], []  # 明示改行(\n)で段落分け→各段落を枠幅で自動折り返し
    cur_pos = 0
    for para in body.split("\n"):
        wl = wrap(scratch, para, font_i, bw - pad * 2)
        wl = wl if wl else [""]
        for l in wl:
            # 折り返し後の各行が body のどこに当たるかを走査して強調フラグを切り出す
            # （wrap は行頭の空白を捨てるため、一致するまで進める）
            while cur_pos < len(body) and not body.startswith(l, cur_pos):
                cur_pos += 1
            line_flags.append(emph_flags[cur_pos:cur_pos + len(l)])
            cur_pos += len(l)
        lines.extend(wl)
    lh = int(fs_i * 1.36)
    # 比較バー: 数字だけを読ませるより、長さで見せたほうが差が一撃で伝わる。
    # 座布団の中に収めるので、その分の高さを block_h に足しておく。
    bar_cfg = cap.get("bar") or None
    bar_h = max(6, int(fs_i * float((bar_cfg or {}).get("height", 0.20)))) if bar_cfg else 0
    bar_gap = int(fs_i * 0.16) if bar_cfg else 0
    block_h = lh * len(lines) + bar_h + bar_gap
    valign = cap.get("valign", "bottom")
    if legacy:
        top = (H - margin_bottom) - block_h
    elif valign == "top":
        top = by
    elif valign == "middle":
        top = by + (bh - block_h) / 2
    else:
        top = by + bh - block_h
    align = cap.get("align", "center")
    ww = max(scratch.textlength(l, font=font_i) for l in lines)
    # countup 等で毎フレーム文字列が変わるとき、座布団の幅が伸縮して箱がガタつく。
    # `_widthText`（＝最終形の文字列）が渡っていれば、そちらで幅を測って固定する。
    if cap.get("_widthText"):
        wbody = parse_emphasis(cap["_widthText"])[0]
        wlines = []
        for para in wbody.split("\n"):
            wlines.extend(wrap(scratch, para, font_i, bw - pad * 2) or [""])
        ww = max(ww, max(scratch.textlength(l, font=font_i) for l in wlines))
    rx0 = bx if align == "left" else (bx + bw - ww if align == "right" else bx + (bw - ww) / 2)

    # ハイライト（座布団）: 既定ON。色はhighlightColor→style.box.color
    # panel=true なら文字幅でなく**枠(x,y,w,h)いっぱい**に敷く（タイトル帯・張り紙用。
    # x<0/w>1 で画面から左右はみ出す帯が作れる）
    if cap.get("highlight", True) is not False:
        hi = _col(cap.get("highlightColor") or box["color"], 4)
        if cap.get("panel"):
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=box["radius"], fill=hi)
        else:
            d.rounded_rectangle([rx0 - pad, top - pad, rx0 + ww + pad, top + block_h + pad],
                                radius=box["radius"], fill=hi)

    # 比較バー本体（座布団の内側・テキストの下）。ratio=0〜1 が満幅に対する長さ。
    # countup と一緒に使うと、数字が上がりながら棒も伸びる。
    if bar_cfg:
        y0 = top + lh * len(lines) + bar_gap
        r = max(0.0, min(1.0, float(bar_cfg.get("ratio", 0))))
        rad = bar_h // 2
        d.rounded_rectangle([rx0, y0, rx0 + ww, y0 + bar_h], radius=rad,
                            fill=_col(bar_cfg.get("trackColor") or [255, 255, 255, 46], 4))
        if r > 0.001:
            d.rounded_rectangle([rx0, y0, rx0 + max(bar_h, ww * r), y0 + bar_h], radius=rad,
                                fill=_col(bar_cfg.get("color") or [214, 173, 92, 255], 4))

    # テキストは別レイヤーに描いてイタリック時にシアー変換
    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dt = ImageDraw.Draw(tl)
    use_outline = bool(cap.get("outline"))
    ow = int(cap.get("outlineWidth", 2)) if use_outline else 0
    oc = _col(cap.get("outlineColor") or [0, 0, 0])
    shadow = bool(cap.get("shadow"))
    sh_off = max(2, int(fs_i * 0.06))
    sh_col = _col(cap.get("shadowColor") or [0, 0, 0]) + (int((0.6 if cap.get("shadowOpacity") is None else cap["shadowOpacity"]) * 255),)
    ascent, _desc = font_i.getmetrics()
    line_w = max(2, int(fs_i * 0.06))
    y = top
    for li, l in enumerate(lines):
        lw = scratch.textlength(l, font=font_i)
        x = bx if align == "left" else (bx + bw - lw if align == "right" else bx + (bw - lw) / 2)
        # 強調の有無で色の違う区間に割る（無指定なら1区間＝従来どおり）
        fl = line_flags[li] if li < len(line_flags) else [False] * len(l)
        segs, s0 = [], 0
        for k in range(1, len(l) + 1):
            if k == len(l) or fl[k] != fl[s0]:
                segs.append((l[s0:k], fl[s0])); s0 = k
        if not segs:
            segs = [(l, False)]
        sx = x
        for seg, is_e in segs:
            c = emph_col if is_e else col
            if shadow:
                dt.text((sx + sh_off, y + sh_off), seg, font=font_i, fill=sh_col)
            if use_outline:
                dt.text((sx, y), seg, font=font_i, fill=c, stroke_width=ow, stroke_fill=oc)
            else:
                dt.text((sx, y), seg, font=font_i, fill=c)
            sx += scratch.textlength(seg, font=font_i)
        if cap.get("underline"):
            uy = y + ascent + max(1, int(fs_i * 0.06))
            dt.line([(x, uy), (x + lw, uy)], fill=col, width=line_w)
        if cap.get("strike"):
            sy = y + int(ascent * 0.62)
            dt.line([(x, sy), (x + lw, sy)], fill=col, width=line_w)
        y += lh
    if cap.get("italic"):  # 上端が右へ傾くシアー（標準的なイタリック）
        k = 0.22
        pivot = top + block_h / 2
        tl = tl.transform((W, H), Image.AFFINE, (1, k, -k * pivot, 0, 1, 0), resample=Image.BICUBIC)
    out = Image.alpha_composite(img, tl)
    # 回転（張り紙演出）: 度・時計回りが正（CSSのrotateと同じ向き）。座布団ごとカード中心で回す
    rot = float(cap.get("rotate") or 0)
    if abs(rot) > 0.05:
        cx = (bx + bw / 2) if cap.get("panel") else (rx0 + ww / 2)
        cy = (by + bh / 2) if cap.get("panel") else (top + block_h / 2)
        out = out.rotate(-rot, center=(cx, cy), resample=Image.BICUBIC)  # PILは反時計が正のため符号反転
    return out


_vdims_cache = {}
def video_dims(path):
    """映像の実寸(w,h)。ffprobeで取得しキャッシュ（radius=角丸マスクの寸法計算に使う）。"""
    if path not in _vdims_cache:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        try:
            w, h = [int(x) for x in r.stdout.strip().split(",")[:2]]
        except (ValueError, IndexError):
            w, h = 16, 9
        _vdims_cache[path] = (w, h)
    return _vdims_cache[path]


_has_aud_cache = {}


def has_audio_stream(path):
    """映像ソースが音声ストリームを持つか（ffprobe・キャッシュ）。
    無音の画面収録などを [N:a] で参照すると ffmpeg が
    『matches no streams / Error binding filtergraph』で落ちるため、事前に見る。"""
    if path not in _has_aud_cache:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=index", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        _has_aud_cache[path] = bool((r.stdout or "").strip())
    return _has_aud_cache[path]


def rounded_mask(w, h, radius):
    """角丸マスク(Lモード, 白=表示)。radius 0〜1。1.0で中央の正円（直径=短辺）。
    2倍で描いて縮小（エッジのアンチエイリアス）。"""
    W2, H2 = w * 2, h * 2
    m = Image.new("L", (W2, H2), 0)
    d = ImageDraw.Draw(m)
    if radius >= 0.999:
        r = min(W2, H2) / 2
        cx, cy = W2 / 2, H2 / 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    else:
        r = max(0.0, min(1.0, radius)) * min(W2, H2) / 2
        d.rounded_rectangle([0, 0, W2 - 1, H2 - 1], radius=r, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def has_color_adjust(col):
    """color 補正が実質的に指定されているか（全部1なら無指定と同じ）。"""
    if not col:
        return False
    return any(abs(float(col.get(k, 1)) - 1) > 1e-3
               for k in ("brightness", "contrast", "saturation"))


def apply_color(im, col):
    """色調整（明るさ・コントラスト・彩度）。**CSS filter と同じ意味**にしてある:
    brightness=乗算 / contrast=0.5(=128)基準の伸縮 / saturation=グレースケールとのブレンド。
    UIプレビュー（CSS filter）と書き出しを一致させるための取り決め。
    アルファには触らない（集中線など半透明素材の透過を保つ）。"""
    b = max(0.0, min(3.0, float(col.get("brightness", 1))))
    k = max(0.0, min(3.0, float(col.get("contrast", 1))))
    s = max(0.0, min(3.0, float(col.get("saturation", 1))))
    a = im.getchannel("A")
    rgb = im.convert("RGB")
    if abs(s - 1) > 1e-3:
        rgb = ImageEnhance.Color(rgb).enhance(s)
    if abs(b - 1) > 1e-3 or abs(k - 1) > 1e-3:
        lut = [max(0, min(255, int(round((x * b - 128) * k + 128)))) for x in range(256)]
        rgb = rgb.point(lut * 3)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def prep_image(src_path, out_path, radius=0.0, rotate=0.0, color=None, flip=False):
    """画像の前処理（反転→色調整→角丸→回転）。元の透過（集中線等）を保ったまま加工する。
    戻り値 (元の幅, 元の高さ, 出力の幅, 出力の高さ)。

    ⚠️ 回転すると外接矩形が広がる（expand=True）。呼び出し側でこの比を使って
       表示幅と位置を補正しないと、**回転しただけで絵が小さくなり位置もズレる**。"""
    with Image.open(src_path) as im:
        im = im.convert("RGBA")
        w0, h0 = im.size
        if flip:
            im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if has_color_adjust(color):
            im = apply_color(im, color)
        if radius > 0:
            mask = rounded_mask(im.width, im.height, radius)
            im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
        if abs(rotate) > 0.05:
            # 時計回りを正にする（CSSのrotateと同じ向き）。PILは反時計回りが正
            im = im.rotate(-rotate, resample=Image.BICUBIC, expand=True)
        im.save(out_path)
        return w0, h0, im.width, im.height


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 既定でカウントするのは「金額」だけ。全数値を動かすと、順位の "#2" や "10 min" まで
# 数え上がって意味が壊れる（2026-08-05: #1→#2 とカウントして不成立になった）。
_MONEY_RE = re.compile(r"[$¥￥]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?円")


def _interp(raw, e):
    """"22,000" や "3.30" を、比率 e の途中の値へ。桁区切りと小数桁は維持する。"""
    if "." in raw:
        dec = len(raw.split(".")[1])
        cur = float(raw.replace(",", "")) * e
        return f"{cur:,.{dec}f}" if "," in raw else f"{cur:.{dec}f}"
    cur = int(round(int(raw.replace(",", "")) * e))
    return f"{cur:,}" if "," in raw else str(cur)


def countup_texts(text, n, all_numbers=False):
    """text 内の金額を 0 → 実値 へ変化させた n 通りの文字列を返す。

    イージングは ease-out（最初が速く、着地でゆっくり止まる）＝数字が「決まる」感じになる。
    既定は金額（$139 / 22,000円 / ¥500）のみ。`countAll` を立てると全数値を動かす。
    """
    pat = _NUM_RE if all_numbers else _MONEY_RE
    hits = list(pat.finditer(text))
    if not hits:
        return [text] * n
    out = []
    for k in range(n):
        e = 1 - (1 - (k + 1) / n) ** 3
        s = text
        for m in reversed(hits):        # 後ろから置換（前を書き換えると位置がずれる）
            seg = m.group()
            nm = _NUM_RE.search(seg)
            rep = seg[:nm.start()] + _interp(nm.group(), e) + seg[nm.end():]
            s = s[:m.start()] + rep + s[m.end():]
        out.append(s)
    return out


def typewriter_texts(text, n):
    """text を1文字ずつ出す n 通り。**強調マーカー(`**`)は数に入れない**
    （マーカーを途中で切ると片方だけ残って色が崩れるため、常にペアで進める）。"""
    body, flags = parse_emphasis(text)
    out = []
    for k in range(n):
        cut = max(1, int(round(len(body) * (k + 1) / n)))
        s, on = [], False
        for j, ch in enumerate(body[:cut]):
            if flags[j] and not on:
                s.append("**"); on = True
            elif not flags[j] and on:
                s.append("**"); on = False
            s.append(ch)
        if on:
            s.append("**")
        out.append("".join(s))
    return out


# 連番PNG（フレームごとに画が変わる）を要するアニメ
SEQ_ANIMS = ("countup", "typewriter")


def build_caption_pngs(proj, pdir, W, H, captions):
    """書き出し用: 各字幕クリップのPNGをファイルに落としてパスを返す。

    SEQ_ANIMS のクリップだけは **連番PNGのディレクトリ**を返す。
    文字や数字が変わる＝画そのものが変わるので、静止画1枚では表現できない。
    """
    png_dir = os.path.join(pdir, "png")
    os.makedirs(png_dir, exist_ok=True)
    paths = []
    for i, cap in enumerate(captions):
        if (cap.get("anim") or "") in SEQ_ANIMS:
            fps = int((proj.get("meta") or {}).get("fps", 30))
            dur = min(float(cap.get("animDur", 0.9)),
                      max(0.1, float(cap["end"]) - float(cap["start"])))
            n = max(2, int(round(dur * fps)))
            d = os.path.join(png_dir, f"cap_{i:03d}_seq")
            os.makedirs(d, exist_ok=True)
            for old in os.listdir(d):
                if old.endswith(".png"):
                    os.remove(os.path.join(d, old))
            final = cap.get("text", "")
            bar0 = cap.get("bar")
            seq = (typewriter_texts(final, n) if cap["anim"] == "typewriter"
                   else countup_texts(final, n, bool(cap.get("countAll"))))
            for k, t in enumerate(seq):
                e = 1 - (1 - (k + 1) / n) ** 3
                f = {**cap, "text": t, "_widthText": final}
                if bar0:      # 棒も数字と同じイージングで伸ばす
                    f["bar"] = {**bar0, "ratio": float(bar0.get("ratio", 0)) * e}
                # _widthText で座布団の幅を最終形に固定する（毎フレーム伸縮するとガタつく）
                render_caption_image(proj, f, W, H).save(os.path.join(d, f"{k:04d}.png"))
            paths.append({"seq": d, "n": n, "fps": fps})
            continue
        p = os.path.join(png_dir, f"cap_{i:03d}.png")
        render_caption_image(proj, cap, W, H).save(p)
        paths.append(p)
    return paths


def render(pdir, rng=None):
    # 素材を調べる前に依存を見る（ffmpeg が無いのに「素材が無い」と言われても混乱するだけ）
    _deps.require("ffmpeg", "ffprobe")
    with open(os.path.join(pdir, "project.json"), encoding="utf-8") as f:
        proj = json.load(f)
    # 必須キーの検証（欠落時はスタックトレースでなく人間可読なエラーで止める）
    canvas = proj.get("canvas")
    if not isinstance(canvas, dict) or "w" not in canvas or "h" not in canvas:
        raise SystemExit("project.json に canvas{w,h} がありません（tools/patch.py <proj> --check で状態を確認）")
    if not isinstance(proj.get("tracks"), list) or not proj["tracks"]:
        raise SystemExit("project.json に tracks がありません（tools/patch.py <proj> --check で状態を確認）")
    W, H = canvas["w"], canvas["h"]
    bg = safe_color(canvas.get("bg", "black"))
    fps = proj.get("meta", {}).get("fps", 30)

    # z-order移行（冪等）: 旧データ(映像が先頭=背景前提)を 前面→背面 [画像,字幕,音声,映像] へ。
    if not proj.get("_zorder"):
        order = {"image": 0, "caption": 1, "mask": 2, "audio": 3, "video": 4}
        proj["tracks"] = sorted(proj["tracks"], key=lambda t: order.get(t["type"], 1))
    tracks = proj["tracks"]
    cuts = proj.get("cuts", [])

    # 重ね順(z): 可視トラックを 下(配列末尾)→上(先頭) の順に重ねる＝上のトラックほど前面。
    # 非表示トラックはスキップ。字幕/画像/映像を種類混在のまま1本のoverlayチェーンに並べる。
    visual = []
    for tr in reversed(tracks):
        if tr.get("hidden"):
            continue
        if tr["type"] == "video":
            vc = split_video_by_cuts(tr["clips"], cuts) if cuts else [dict(c) for c in tr["clips"]]
            for c in sorted(vc, key=lambda x: x["start"]):
                visual.append({"kind": "video", "clip": c, "muted": bool(tr.get("muted"))})
        elif tr["type"] == "image":
            ic = apply_cuts_to_clips(tr["clips"], cuts, ("start", "end")) if cuts else tr["clips"]
            for c in ic:
                visual.append({"kind": "image", "clip": c})
        elif tr["type"] == "caption":
            cc = apply_cuts_to_clips(tr["clips"], cuts, ("start", "end")) if cuts else tr["clips"]
            for c in cc:
                visual.append({"kind": "caption", "clip": c})
        elif tr["type"] == "mask":
            mc = apply_cuts_to_clips(tr["clips"], cuts, ("start", "end")) if cuts else tr["clips"]
            for c in mc:
                visual.append({"kind": "mask", "clip": c})

    # 音声トラック（非ミュートのみ）。ダッキングの相手探し用に所属トラックを `_tid`/`_tlabel` で持たせる
    audios = []
    for tr in tracks:
        if tr["type"] == "audio" and not tr.get("muted"):
            ac = apply_cuts_to_clips(tr["clips"], cuts, ("start", "end")) if cuts else tr["clips"]
            for c in ac:
                c["_tid"] = tr.get("id") or ""
                c["_tlabel"] = tr.get("label") or ""
            audios.extend(ac)

    # ⚠️ 素材の実在チェックは映像・画像・音声を**まとめて**、ffmpegに渡す前に行う。
    #    ここを通さないと `Error opening input file ...` という生の英語エラーになり、
    #    何をどうすればいいのか分からない（2026-07-17 配布前の検証で発覚。映像だけ親切だった）。
    KIND_JA = {"video": "映像", "image": "画像", "audio": "音声"}
    missing = []   # (種別, ファイル名) 重複なし・登場順
    for kind, clip in ([(v["kind"], v["clip"]) for v in visual if v["kind"] in ("video", "image")]
                       + [("audio", c) for c in audios]):
        src = clip.get("src")
        if not src or os.path.exists(os.path.join(pdir, src)):
            continue
        if (KIND_JA[kind], src) not in missing:
            missing.append((KIND_JA[kind], src))
    if missing:
        lines = "\n".join(f"    - [{k}] {n}" for k, n in missing)
        raise SystemExit(
            f"素材が見つかりません（{len(missing)}件）: {os.path.basename(os.path.abspath(pdir))}\n{lines}\n"
            f"  対処:\n"
            f"    ・git clone した直後は素材（画像・音声・映像）が入っていません。\n"
            f"      素材入りの .veproj.zip を「zip読み込み」ボタンで取り込んでください。\n"
            f"    ・素材を手で置く場合は、プロジェクト直下（{pdir}）に上の名前で配置します。\n"
            f"    ・不要なクリップなら、エディタで削除してから書き出してください。")

    # 字幕pngをvisual内のcaption順で生成し、各itemに紐付け
    cap_items = [v for v in visual if v["kind"] == "caption"]
    cap_pngs = build_caption_pngs(proj, pdir, W, H, [v["clip"] for v in cap_items])
    for v, p in zip(cap_items, cap_pngs):
        v["png"] = p

    ends = [v["clip"]["end"] for v in visual] + [c["end"] for c in audios]
    total_dur = max(ends or [0])
    if total_dur <= 0:
        raise SystemExit("タイムラインが空です")

    # -i入力の割当: visual順(映像/画像/字幕png) → 音声クリップ → 角丸マスクpng。maskは入力不要(prevを加工)
    # 映像/音声は入力側 -ss/-t でシークして必要区間だけデコードする（長尺ソースの頭からの
    # 全デコードを排除。フィルタ側の trim/atrim は0起点になる）
    png_dir = os.path.join(pdir, "png")
    os.makedirs(png_dir, exist_ok=True)
    inputs = []  # (pre_args, path)
    for i, v in enumerate(visual):
        if v["kind"] == "mask":
            v["idx"] = None; continue
        v["idx"] = len(inputs)
        if v["kind"] == "video":
            c = v["clip"]
            vin = float(c.get("in", 0)); vdur = float(c["end"]) - float(c["start"])
            # 再生速度: 出力尺 vdur に対しソースは vdur*speed ぶん必要（読み足りないと尻切れになる）
            spd = max(0.25, min(4.0, float(c.get("speed") or 1)))
            inputs.append((["-ss", f"{vin:.3f}", "-t", f"{vdur * spd + 0.5:.3f}"],
                           os.path.join(pdir, c["src"])))
        elif v["kind"] == "caption":
            c = v["clip"]
            if isinstance(v["png"], dict):      # countup: 連番PNGを画像シーケンスとして読む
                seq = v["png"]
                inputs.append((["-framerate", str(seq["fps"])],
                               os.path.join(seq["seq"], "%04d.png")))
            elif (c.get("anim") or "") in ("pop", "slide"):
                # ポップ/スライドは t 式で毎フレーム動く → 静止png 1フレームでは動かないため
                # -loop 1 で end まで連続フレーム化する（フレームレートは出力と揃える）
                inputs.append((["-loop", "1", "-framerate", str(fps),
                                "-t", f"{float(c['end']) + 0.5:.3f}"], v["png"]))
            else:
                inputs.append(([], v["png"]))
        else:  # image — radius(角丸)/rotate(回転)/color(色調整)/flip(反転)はPIL前処理
            #        （既存の透過と乗算するので集中線等にも安全。ffmpegのeqはアルファを壊すため使わない）
            c = v["clip"]
            path = os.path.join(pdir, c["src"])
            radius = float(c.get("radius") or 0)
            rot = float(c.get("rotate") or 0)
            v["grow"] = (1.0, 1.0)     # 回転で外接矩形が広がった比（幅, 高さ）
            if radius > 0 or abs(rot) > 0.05 or has_color_adjust(c.get("adjust")) or c.get("flip"):
                rp = os.path.join(png_dir, f"prep_{i:03d}.png")
                w0, h0, w1, h1 = prep_image(path, rp, radius, rot,
                                            c.get("adjust"), bool(c.get("flip")))
                v["grow"] = (w1 / w0, (h1 / w1) / (h0 / w0))   # 幅の比 と アスペクトの変化
                path = rp
            # フェードは「画が変わる」ので、静止1フレームでは動かない。
            # pop字幕と同じく -loop 1 で連続フレーム化する（zoompan を使うクリップは
            # zoompan 自身がフレームを生成するため不要）
            has_kb = bool((c.get("motion") or {}).get("zoom")) or bool((c.get("motion") or {}).get("pan"))
            if (float(c.get("fadeIn") or 0) > 0 or float(c.get("fadeOut") or 0) > 0) and not has_kb:
                v["looped"] = True
                inputs.append((["-loop", "1", "-framerate", str(fps),
                                "-t", f"{float(c['end']) - float(c['start']) + 0.5:.3f}"], path))
            else:
                inputs.append(([], path))
    aud_idx0 = len(inputs)
    for c in audios:
        ain = float(c.get("in", 0)); adur = float(c["end"]) - float(c["start"])
        pre = ["-ss", f"{ain:.3f}", "-t", f"{adur + 0.5:.3f}"]
        if c.get("loop"):
            # ループBGM: ソースを無限リピートして必要秒数だけ読む（クリップをソース長より
            # 長く伸ばせる唯一の例外。patch.py の長さ検査も loop 時は免除している）
            pre = ["-stream_loop", "-1"] + pre
        inputs.append((pre, os.path.join(pdir, c["src"])))
    # 映像クリップの radius: scale後の実寸で角丸マスクを作り alphamerge する（入力は音声の後ろに追加）
    for i, v in enumerate(visual):
        if v["kind"] != "video":
            continue
        c = v["clip"]
        radius = float(c.get("radius") or 0)
        if radius <= 0:
            v["mask_idx"] = None; continue
        sw, sh = video_dims(os.path.join(pdir, c["src"]))
        crop = c.get("crop")
        l, t_, r, b = ([float(x) for x in (list(crop) + [0, 0, 0, 0])[:4]] if (crop and any(crop))
                       else (0.0, 0.0, 0.0, 0.0))
        kw, kh = max(0.05, 1 - l - r), max(0.05, 1 - t_ - b)
        sc = float(c.get("scale") or 1)
        ow = max(2, int(W * sc) // 2 * 2)
        oh = max(2, int(round(ow * (sh * kh) / (sw * kw) / 2)) * 2)
        mp = os.path.join(png_dir, f"vmask_{i:03d}.png")
        rounded_mask(ow, oh, radius).save(mp)
        v["mask_idx"] = len(inputs)
        v["rgeom"] = (ow, oh)
        inputs.append(([], mp))

    out_dir = os.path.join(pdir, "out")
    os.makedirs(out_dir, exist_ok=True)
    title = safe_title((proj.get("meta") or {}).get("title"), pdir)
    # 区間書き出し（--range A B）: 全体を待たずに途中だけ確認する用。
    # マスター(<title>.mp4)を部分出力で潰さないよう、別名で書き出す
    if rng:
        ra = max(0.0, float(rng[0])); rb = min(total_dur, float(rng[1]))
        if rb - ra < 0.05:
            raise SystemExit(f"--range が不正です: {rng[0]}〜{rng[1]}（この動画の尺は {total_dur:.2f}秒）")
        outname = f"{title}_{ra:g}-{rb:g}s.mp4"
    else:
        outname = title + ".mp4"
    out_path = os.path.join(out_dir, outname)

    parts = [f"color=c={bg}:s={W}x{H}:r={fps}:d={total_dur:.3f}[bg]"]
    prev = "bg"
    for i, v in enumerate(visual):
        c, idx = v["clip"], v["idx"]
        if v["kind"] == "video":
            vin = float(c.get("in", 0)); vdur = float(c["end"]) - float(c["start"]); vs = float(c["start"])
            sc = float(c.get("scale") or 1)
            crop = c.get("crop")  # [left, top, right, bottom] 正規化インセット
            opacity = float(c.get("opacity") if c.get("opacity") is not None else 1)
            fin = float(c.get("fadeIn") or 0); fout = float(c.get("fadeOut") or 0)
            spd = max(0.25, min(4.0, float(c.get("speed") or 1)))
            vf = [f"trim=0:{vdur * spd:.3f}"]  # 入力側-ssシーク済みのため0起点。setptsはalphamerge後（マスクと0起点で同期させるため）
            if abs(spd - 1) > 1e-3:
                # 速度変更は切り出し直後に掛ける。以降の fade 等は出力時間軸(vdur)で計算できる
                vf.append(f"setpts=PTS/{spd:.4f}")
            if c.get("flip"):
                vf.append("hflip")
            # 色調整。意味は画像側 apply_color と同じ（CSS filter 準拠）。
            # brightness は乗算なので eq(加算) ではなく colorchannelmixer で掛ける
            col = c.get("adjust") or {}
            if has_color_adjust(col):
                cb = max(0.0, min(2.0, float(col.get("brightness", 1))))
                ck = max(0.0, min(3.0, float(col.get("contrast", 1))))
                cs = max(0.0, min(3.0, float(col.get("saturation", 1))))
                if abs(cb - 1) > 1e-3:
                    vf.append(f"colorchannelmixer=rr={cb:.3f}:gg={cb:.3f}:bb={cb:.3f}")
                if abs(ck - 1) > 1e-3 or abs(cs - 1) > 1e-3:
                    vf.append(f"eq=contrast={ck:.3f}:saturation={cs:.3f}")
            if crop and any(crop):
                l, t_, r, b = [float(x) for x in (list(crop) + [0, 0, 0, 0])[:4]]
                vf.append(f"crop=iw*{max(0.05, 1 - l - r):.4f}:ih*{max(0.05, 1 - t_ - b):.4f}:iw*{l:.4f}:ih*{t_:.4f}")
            if v.get("mask_idx") is not None:
                ow, oh = v["rgeom"]
                vf.append(f"scale={ow}:{oh}")  # マスクPNGと寸法を厳密一致させる（-2任せにしない）
            else:
                vf.append(f"scale={max(2, int(W * sc))}:-2")
            post = []  # alphamerge後に適用するフィルタ（radius無しなら同一チェーンに続ける）
            if opacity < 1:
                post.append(f"colorchannelmixer=aa={opacity:.3f}")
            if fin > 0:
                post.append(f"fade=t=in:st=0:d={fin:.3f}:alpha=1")
            if fout > 0:
                post.append(f"fade=t=out:st={max(0, vdur - fout):.3f}:d={fout:.3f}:alpha=1")
            # 回転（時計回りが正）。alphamerge/透明化のあとに掛けて、はみ出す角を透明で埋める。
            # ow/oh を rotw/roth にしないと角が切れる。位置は下の overlay 側で中心を戻す
            vrot = float(c.get("rotate") or 0)
            if abs(vrot) > 0.05:
                rad = vrot * math.pi / 180.0
                if v.get("mask_idx") is None and not (opacity < 1 or fin > 0 or fout > 0):
                    vf.append("format=yuva420p")     # c=none で透明に埋めるにはアルファが要る
                post.append(f"rotate={rad:.6f}:ow=rotw({rad:.6f}):oh=roth({rad:.6f}):c=none")
            post.append(f"setpts=PTS-STARTPTS+{vs}/TB")
            if v.get("mask_idx") is not None:
                # 角丸/円ワイプ: 事前生成した同寸マスクをアルファに合成（マスクは静止画1フレーム=repeatlastで全フレームに効く）
                parts.append(f"[{idx}:v]{','.join(vf)}[vp{i}]")
                parts.append(f"[vp{i}][{v['mask_idx']}:v]alphamerge,{','.join(post)}[vv{i}]")
            else:
                if opacity < 1 or fin > 0 or fout > 0:
                    vf.append("format=yuva420p")
                parts.append(f"[{idx}:v]{','.join(vf + post)}[vv{i}]")
            # x/y 指定があれば正規化左上座標で配置（ワイプ/PiP）。無ければ従来（scale=1は左上0,0・それ以外は中央）
            if c.get("x") is not None and c.get("y") is not None:
                px, py = float(c["x"]) * W, float(c["y"]) * H
                if abs(vrot) > 0.05:
                    # 回転で外接矩形が広がったぶん左上へ戻す（やらないと右下へズレる）
                    sw, sh = video_dims(os.path.join(pdir, c["src"]))
                    cr2 = c.get("crop")
                    l2, t2, r2, b2 = ([float(x) for x in (list(cr2) + [0, 0, 0, 0])[:4]]
                                      if (cr2 and any(cr2)) else (0.0, 0.0, 0.0, 0.0))
                    bw = W * sc
                    bh = bw * (sh * max(0.05, 1 - t2 - b2)) / (sw * max(0.05, 1 - l2 - r2))
                    a = abs(math.radians(vrot))
                    nw = bw * math.cos(a) + bh * math.sin(a)
                    nh = bw * math.sin(a) + bh * math.cos(a)
                    px -= (nw - bw) / 2; py -= (nh - bh) / 2
                ox, oy = str(int(px)), str(int(py))
            else:
                ox, oy = ("0", "0") if (sc == 1 and not (crop and any(crop))) else ("(W-w)/2", "(H-h)/2")
            parts.append(f"[{prev}][vv{i}]overlay={ox}:{oy}:enable='between(t,{vs},{c['end']})'[o{i}]")
        elif v["kind"] == "image":
            # 回転で外接矩形が広がったぶん、表示幅を増やして位置を戻す
            # （やらないと「回転しただけで小さくなり、左上へズレる」）
            gw, ga = v.get("grow", (1.0, 1.0))
            iw0 = c.get("w", 0.3) * W
            iw = max(2, int(iw0 * gw))
            ix = int(c.get("x", 0) * W - (iw - iw0) / 2)
            try:
                with Image.open(os.path.join(pdir, c["src"])) as _im0:
                    _ar = _im0.height / _im0.width
            except OSError:
                _ar = 9 / 16
            ih0 = iw0 * _ar                      # 回転前の表示高さ
            ih = iw * _ar * ga                   # 回転後の表示高さ
            iy = int(c.get("y", 0) * H - (ih - ih0) / 2)
            fin = float(c.get("fadeIn") or 0); fout = float(c.get("fadeOut") or 0)
            opacity = float(c.get("opacity") if c.get("opacity") is not None else 1)
            idur = float(c["end"]) - float(c["start"])
            # フェード/不透明度（適用順: format→fade→opacity。fadeはアルファで効かせ背景に沈める）
            post_im = []
            if fin > 0 or fout > 0 or opacity < 1:
                post_im.append("format=rgba")
            if fin > 0:
                post_im.append(f"fade=t=in:st=0:d={fin:.3f}:alpha=1")
            if fout > 0:
                post_im.append(f"fade=t=out:st={max(0, idur - fout):.3f}:d={fout:.3f}:alpha=1")
            if opacity < 1:
                post_im.append(f"colorchannelmixer=aa={opacity:.3f}")
            mo = c.get("motion") or {}
            zf = float(mo.get("zoom") or 0)
            pan = (mo.get("pan") or "").lower()
            pan_only = bool(pan) and (not zf or abs(zf - 1) < 1e-3)
            if pan_only:
                zf = 1.1   # パンには切り出しの余白が要る。ズーム未指定なら固定1.1で全域を横断する
            if zf and abs(zf - 1) > 1e-3:
                # Ken Burns: クリップ長をかけて中央基準ズーム（zoom>1=寄り / zoom<1=1/zoomから等倍へ=引き）
                # ⚠️ ガタつき対策: zoompanは切り出し座標を**入力解像度の整数px**に丸めるため、
                #    ゆっくりズームでは1pxずつ跳んで「小刻みな揺れ」になる（実測: 隣接フレーム差分が交互スパイク）。
                #    入力を拡大してから zoompan することで丸め誤差を実効 1/倍率 px に落とす。
                # ⚠️ 倍率は**動的**に選ぶ（issue #2 の高速化。2026-07-23実測）:
                #    切り出しの移動が 0.89px/frame → 滑らか / 0.44 → 軽い跳び / 0.22 → 明確なジッタ。
                #    そこで「0.8px/frame 以上になる最小の倍率(2/4/8)」を計算する。
                #    ズームが速い・カットが短いほど低倍率で済み 2〜5倍速くなる。
                #    遅く長いズームは従来どおり8倍が選ばれるためジッタは再発しない。
                #    （一律8倍だと10秒動画の書き出しが80秒。ss4=29秒 / ss2=14秒）
                try:
                    with Image.open(os.path.join(pdir, c["src"])) as _im:
                        _w0, _h0 = _im.size
                except OSError:
                    _w0, _h0 = (16, 9)
                iwe = max(2, iw // 2 * 2)
                oh = max(2, int(round(iwe * _h0 / _w0)) // 2 * 2)
                dur = idur; N = max(1, int(round(dur * fps)))
                ppf1 = (iwe / 2) * abs(1 - 1 / zf) / N   # 倍率1のときの切り出し移動(px/frame)
                if pan:
                    # パンは切り出し窓が余白ぶん横断する＝移動量が大きい。倍率選定にも反映する
                    ppf1 = max(ppf1, iwe * abs(1 - 1 / zf) / N)
                ss = 2
                while ss < 8 and ppf1 * ss < 0.8:
                    ss *= 2
                if pan_only:
                    zx = f"{zf:.4f}"        # パン単独: ズームは固定し、窓の移動だけを見せる
                elif zf >= 1:
                    zx = f"1+({zf - 1:.4f})*on/{N}"
                else:
                    z0 = 1.0 / zf
                    zx = f"{z0:.4f}-({z0 - 1:.4f})*on/{N}"
                # Ken Burns パン: pan は「切り出し窓が動く方向」（left=窓が左へ＝被写体は右へ流れる）。
                # 窓は端から端まで p=on/N で直線移動。指定の無い軸は中央固定
                p = f"(on/{N})"
                xx, yy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
                if pan == "left":
                    xx = f"(iw-iw/zoom)*(1-{p})"
                elif pan == "right":
                    xx = f"(iw-iw/zoom)*{p}"
                elif pan == "up":
                    yy = f"(ih-ih/zoom)*(1-{p})"
                elif pan == "down":
                    yy = f"(ih-ih/zoom)*{p}"
                # 出力は等倍（以前は2倍→縮小だったが、ジッタ・画質とも差が出ない実測を得て等倍化。
                #  2026-07-23実測: ジグザグ度0.22/0.22で同一、平均画素差0.97/255、時間は2割減）
                kb_post = ("," + ",".join(post_im)) if post_im else ""
                parts.append(f"[{idx}:v]scale={iwe * ss}:-2,zoompan=z='{zx}'"
                             f":x='{xx}':y='{yy}'"
                             f":d={N}:s={iwe}x{oh}:fps={fps}{kb_post},"
                             f"setpts=PTS+{c['start']}/TB[im{i}]")
            elif v.get("looped"):
                # フェードあり: -loop 1 で連続フレーム化済み。0起点でfadeを掛けてから時刻を戻す
                parts.append(f"[{idx}:v]scale={iw}:-1,{','.join(post_im)},"
                             f"setpts=PTS-STARTPTS+{c['start']}/TB[im{i}]")
            elif post_im:      # opacityのみ（静止でよい）
                parts.append(f"[{idx}:v]scale={iw}:-1,{','.join(post_im)}[im{i}]")
            else:
                parts.append(f"[{idx}:v]scale={iw}:-1[im{i}]")
            parts.append(f"[{prev}][im{i}]overlay={ix}:{iy}:enable='between(t,{c['start']},{c['end']})'[o{i}]")
        elif v["kind"] == "mask":
            # 指定範囲をモザイク/ぼかし/黒塗りで隠す(hide)、または範囲だけ残して他をぼかす(show)
            rw = max(2, int(round(float(c.get("w", 0.4)) * W)) // 2 * 2)
            rh = max(2, int(round(float(c.get("h", 0.2)) * H)) // 2 * 2)
            rx = max(0, min(W - rw, int(round(float(c.get("x", 0.3)) * W))))
            ry = max(0, min(H - rh, int(round(float(c.get("y", 0.4)) * H))))
            mode = c.get("mode", "hide"); style = c.get("style", "mosaic")
            strg = max(2, int(c.get("strength", 12)))
            gate = f"enable='between(t,{c['start']},{c['end']})'"
            if mode == "show":  # 範囲外をぼかし＋暗く、範囲だけ鮮明
                parts.append(
                    f"[{prev}]split=3[mk{i}0][mk{i}a][mk{i}b];"
                    f"[mk{i}a]boxblur={strg}:2,eq=brightness=-0.25[mk{i}bl];"
                    f"[mk{i}b]crop={rw}:{rh}:{rx}:{ry}[mk{i}k];"
                    f"[mk{i}bl][mk{i}k]overlay={rx}:{ry}[mk{i}sh];"
                    f"[mk{i}0][mk{i}sh]overlay=0:0:{gate}[o{i}]")
            elif style == "solid":
                parts.append(f"[{prev}]drawbox=x={rx}:y={ry}:w={rw}:h={rh}:color=black@1:t=fill:{gate}[o{i}]")
            else:  # mosaic or blur
                if style == "blur":
                    proc = f"boxblur={strg}:2"
                else:  # mosaic: 縮小→拡大でピクセル化
                    proc = f"scale=max(2\\,{rw}/{strg}):max(2\\,{rh}/{strg}):flags=neighbor,scale={rw}:{rh}:flags=neighbor"
                parts.append(
                    f"[{prev}]split[mk{i}a][mk{i}b];"
                    f"[mk{i}b]crop={rw}:{rh}:{rx}:{ry},{proc}[mk{i}m];"
                    f"[mk{i}a][mk{i}m]overlay={rx}:{ry}:{gate}[o{i}]")
        else:  # caption
            if isinstance(v.get("png"), dict):
                # countup: 数字が動き終わったら最終フレームを clone で引き延ばし、
                # クリップの終わりまで「決まった数字」を出したままにする。
                seq = v["png"]
                hold = max(0.0, float(c["end"]) - float(c["start"]) - seq["n"] / seq["fps"])
                parts.append(f"[{idx}:v]tpad=stop_mode=clone:stop_duration={hold + 0.5:.3f},"
                             f"setpts=PTS-STARTPTS+{float(c['start']):.3f}/TB[cu{i}]")
                parts.append(f"[{prev}][cu{i}]overlay=0:0"
                             f":enable='between(t,{c['start']},{c['end']})'[o{i}]")
            elif (c.get("anim") or "") == "slide":
                # スライドイン: 画面外(既定は左)から 0.32秒で定位置へ。ease-out で「すっと止まる」。
                # 字幕pngは全画面サイズなので、overlay の x/y を動かすだけでよい。
                vs = float(c["start"]); dur = float(c.get("animDur", 0.32))
                frm = (c.get("slideFrom") or "left").lower()
                prog = f"(1-pow(1-min(max(t-{vs},0)/{dur},1),3))"
                dx = {"left": f"-main_w*(1-{prog})", "right": f"main_w*(1-{prog})"}.get(frm, "0")
                dy = {"top": f"-main_h*(1-{prog})", "bottom": f"main_h*(1-{prog})"}.get(frm, "0")
                parts.append(f"[{idx}:v]fade=t=in:st={vs}:d=0.10:alpha=1[sl{i}]")
                parts.append(f"[{prev}][sl{i}]overlay=x='{dx}':y='{dy}'"
                             f":enable='between(t,{c['start']},{c['end']})'[o{i}]")
            elif (c.get("anim") or "") == "pop":
                # ポップ登場＝スタンプ: 1.9倍から0.16秒で等倍へ「叩きつけ」（ease-in=着地直前が最速）＋
                # 最初の0.06秒フェードイン。ドーンと同時に画面に貼り付く演出。
                # ⚠️ 逆（小さく縮んで収まる・ゆっくり0.3秒）は「ドーンなのに小さくなる」で不成立（2026-07-17実害）
                vs = float(c["start"])
                parts.append(f"[{idx}:v]scale=w='iw*(1.9-0.9*pow(min(max(t-{vs},0)/0.16,1),2))'"
                             f":h=-1:eval=frame:flags=bicubic,"
                             f"fade=t=in:st={vs}:d=0.06:alpha=1[cp{i}]")
                parts.append(f"[{prev}][cp{i}]overlay=x='(main_w-w)/2':y='(main_h-h)/2'"
                             f":enable='between(t,{c['start']},{c['end']})'[o{i}]")
            else:
                parts.append(f"[{prev}][{idx}:v]overlay=0:0:enable='between(t,{c['start']},{c['end']})'[o{i}]")
        prev = f"o{i}"

    # 音声: 映像クリップの音声(トラック非ミュート & audioLinked!=false) + 効果音/BGMクリップ
    alabels = []
    for i, v in enumerate(visual):
        if v["kind"] != "video" or v["muted"]:
            continue
        c = v["clip"]
        if c.get("audioLinked", True) is False:
            continue
        if not has_audio_stream(os.path.join(pdir, c["src"])):
            continue   # 無音映像（画面収録など）。[N:a] を張るとグラフ構築ごと落ちる
        vin = float(c.get("in", 0)); vdur = float(c["end"]) - float(c["start"])
        gain = float(c.get("gain", 1.0)); delay_ms = int(round(float(c["start"]) * 1000))
        fin = float(c.get("fadeIn") or 0); fout = float(c.get("fadeOut") or 0)
        spd = max(0.25, min(4.0, float(c.get("speed") or 1)))
        af = [f"atrim=0:{vdur * spd:.3f}", "asetpts=PTS-STARTPTS"]  # 入力側-ssシーク済み
        if abs(spd - 1) > 1e-3:
            # atempo は 0.5〜2 の範囲しか受けないため、範囲外は分割して連結する
            s_ = spd
            while s_ > 2.0:
                af.append("atempo=2.0"); s_ /= 2.0
            while s_ < 0.5:
                af.append("atempo=0.5"); s_ /= 0.5
            if abs(s_ - 1) > 1e-3:
                af.append(f"atempo={s_:.4f}")
        af.append(f"volume={gain}")
        # ジャンプカット境界のプチノイズ防止: 全ピースに8msのマイクロフェード（明示fadeIn/Outと共存）
        af.append("afade=t=in:st=0:d=0.008")
        af.append(f"afade=t=out:st={max(0, vdur - 0.008):.3f}:d=0.008")
        if fin > 0:
            af.append(f"afade=t=in:st=0:d={fin:.3f}")
        if fout > 0:
            af.append(f"afade=t=out:st={max(0, vdur - fout):.3f}:d={fout:.3f}")
        af.append(f"adelay={delay_ms}:all=1")
        parts.append(f"[{v['idx']}:a]{','.join(af)}[vaud{i}]")
        alabels.append(f"vaud{i}")
    for k, c in enumerate(audios):
        idx = aud_idx0 + k
        ain = float(c.get("in", 0)); adur = float(c["end"]) - float(c["start"])
        gain = float(c.get("gain", 1.0)); delay_ms = int(round(float(c["start"]) * 1000))
        # ダッキング: duck 指定があれば、相手（既定=ナレーション）の区間だけ自動で沈む。
        # ⚠️ 検証で音量を測るときは、mp4 を直接 -ss シークすると AAC で正しく測れない。
        #    いったん音声を丸ごと wav 化してから区間を volumedetect すること（2026-08-06 実測）
        vol = duck_expr(c, audios, gain) or f"volume={gain}"
        fin = float(c.get("fadeIn") or 0); fout = float(c.get("fadeOut") or 0)
        af = [f"atrim=0:{adur}", "asetpts=PTS-STARTPTS",
              # 効果音/BGMも境界プチノイズ防止の8msマイクロフェードを常時付与（入力側-ssシーク済み）
              "afade=t=in:st=0:d=0.008",
              f"afade=t=out:st={max(0, adur - 0.008):.3f}:d=0.008"]
        if fin > 0:
            af.append(f"afade=t=in:st=0:d={fin:.3f}")
        if fout > 0:
            af.append(f"afade=t=out:st={max(0, adur - fout):.3f}:d={fout:.3f}")
        af += [vol, f"adelay={delay_ms}:all=1"]
        parts.append(f"[{idx}:a]{','.join(af)}[aud{k}]")
        alabels.append(f"aud{k}")

    # ⚠️ 末尾の asetpts=N/SR/TB は省略禁止。atempo（倍速音声）の PTS が amix を経て
    #    非単調になると、AAC エンコーダが**エラーを出さずに音声を途中で打ち切る**
    #    （2026-08-06 実測: 倍速クリップの開始時刻で音声が切れ、stream duration が 0.01s になった）。
    #    サンプル数ベースで振り直せば全構成で安全。
    if not alabels:
        amap = None
    elif len(alabels) == 1:
        parts.append(f"[{alabels[0]}]asetpts=N/SR/TB[aout]")
        amap = "[aout]"
    else:
        mix_in = "".join(f"[{l}]" for l in alabels)
        parts.append(f"{mix_in}amix=inputs={len(alabels)}:normalize=0:dropout_transition=0,"
                     f"asetpts=N/SR/TB[aout]")
        amap = "[aout]"

    # ラウドネス正規化（既定ON: YouTube想定 -14 LUFS / TP -1.5）。audio.loudnorm={"on":false} で無効化
    ln_conf = (proj.get("audio") or {}).get("loudnorm") or {}
    if amap and ln_conf.get("on", True):
        I = float(ln_conf.get("i", -14)); TP = float(ln_conf.get("tp", -1.5)); LRA = float(ln_conf.get("lra", 11))
        # loudnormは内部で192kHzへ上げるため、後段のaresampleで48kへ戻すのが必須
        parts.append(f"{amap}loudnorm=I={I}:TP={TP}:LRA={LRA},aresample=48000[anorm]")
        amap = "[anorm]"

    lim_conf = (proj.get("audio") or {}).get("limiter") or {}
    if amap and lim_conf.get("on"):
        lim = 10 ** (float(lim_conf.get("db", -3)) / 20)
        parts.append(f"{amap}alimiter=level=false:limit={max(0.0625, min(1.0, lim)):.6f}[alim]")
        amap = "[alim]"

    fc_path = os.path.join(pdir, "_fc.txt")
    with open(fc_path, "w", encoding="utf-8") as f:
        f.write(";".join(parts))

    # 一時ファイルへ書いてから差し替える。
    # 直接 out_path に書くと、ffmpeg は開いた時点で中身を切り詰めるため、
    # 途中で失敗・中断すると「壊れた数十バイトのmp4」が残る。しかも mtime は新しいので
    # サーバの鮮度判定（project.jsonより新しければスキップ）を通り、
    # 次の書き出しが再生できないファイルを"成功"として返してしまう
    tmp_path = os.path.join(out_dir, "." + outname[:-4] + ".rendering.mp4")
    # 前回が強制終了(kill)された場合は後始末が走らないので、ここで掃除しておく
    for stale in os.listdir(out_dir):
        if stale.startswith(".") and stale.endswith(".rendering.mp4"):
            try:
                os.remove(os.path.join(out_dir, stale))
            except OSError:
                pass
    cmd = ["ffmpeg", "-y"]
    for pre, p in inputs:
        cmd += pre + ["-i", p]
    cmd += ["-filter_complex_script", fc_path, "-map", f"[{prev}]"]
    cmd += (["-map", amap] if amap else ["-an"])
    # 出力側の -ss はグラフ全体を評価しつつ範囲外フレームを捨てる＝合成結果はフル書き出しと同一
    cmd += (["-ss", f"{ra:.3f}", "-t", f"{rb - ra:.3f}"] if rng else ["-t", f"{total_dur:.3f}"])
    if os.environ.get("VE_KEEP_FC"):
        print("CMD:", " ".join(cmd + ["..."]), file=sys.stderr)
    cmd += ["-c:v", pick_video_codec(), "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",   # 頭出し再生できる形に（SNS投稿用）
            tmp_path]

    nvid = sum(1 for v in visual if v["kind"] == "video")
    nimg = sum(1 for v in visual if v["kind"] == "image")
    print(f"レンダリング: 映像{nvid} + 字幕{len(cap_items)} + 画像{nimg} + 音声{len(audios)}"
          + (f" / カット{len(cuts)}箇所" if cuts else "") + f" -> {out_path}")

    def _drop_tmp():
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    try:
        r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    except KeyboardInterrupt:
        _drop_tmp()
        print("中断しました（既存の書き出しは残してあります）", file=sys.stderr)
        sys.exit(130)
    if r.returncode != 0:
        # stderr に出すこと。stdout に流すと server.py が r.stderr しか拾わないため
        # ブラウザには「書き出し失敗: 500」しか出ず、原因が一切わからなくなる
        print((r.stderr or "")[-1500:], file=sys.stderr)
        _drop_tmp()
        sys.exit(1)
    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        print("ffmpeg は正常終了しましたが出力が空でした", file=sys.stderr)
        _drop_tmp()
        sys.exit(1)
    os.replace(tmp_path, out_path)
    try:
        if not os.environ.get('VE_KEEP_FC'):
            os.remove(fc_path)          # 成功したらフィルタグラフの一時ファイルは残さない
    except OSError:
        pass
    print("OK:", out_path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="project.json を MP4 に書き出す")
    ap.add_argument("pdir", nargs="?", default=".")
    ap.add_argument("--range", nargs=2, type=float, metavar=("開始秒", "終了秒"),
                    help="この区間だけ書き出す（out/<title>_A-Bs.mp4。全体を待たずに途中確認する用）")
    args = ap.parse_args()
    render(args.pdir, args.range)
