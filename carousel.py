# -*- coding: utf-8 -*-
"""
カルーセル自動生成エンジン (carousel.py)
=========================================
トピック → Geminiでコピー(見出し・本文)生成 → 背景AI生成 →
Pillowで文字を焼き込み → 完成カルーセル画像（文字込み）。

文字はAIに描かせず Pillow で合成するので、数字・固有名詞が壊れない
（教育×宅建で致命的な誤字を防ぐ）。
"""
from __future__ import annotations

import glob
import json
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

import core

# ----------------------------------------------------------------------
# ブランドカラー（前回モックの仮色：ネイビー×ゴールド×ティール）
# ----------------------------------------------------------------------
NAVY = (31, 58, 95)
GOLD = (245, 200, 90)
TEAL = (34, 142, 140)
WHITE = (255, 255, 255)
LIGHT = (232, 236, 242)
SCRIM = (10, 20, 40)

W, H = 1080, 1350          # Instagram 4:5
MARGIN = 90

COPY_MODEL = "gemini-2.5-flash"   # 文章生成はテキストモデル（安い）


# ----------------------------------------------------------------------
# フォント解決（Mac / Linux / pip同梱 すべて対応）
# ----------------------------------------------------------------------
def find_jp_font() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))
    # 1) リポジトリ同梱 fonts/
    for ext in ("ttf", "otf", "ttc"):
        hits = glob.glob(os.path.join(here, "fonts", f"*.{ext}"))
        if hits:
            return hits[0]
    # 2) pip: japanize-matplotlib (IPAexGothic) ← Streamlit Cloud用の本命
    try:
        import japanize_matplotlib  # noqa: F401
        b = os.path.dirname(japanize_matplotlib.__file__)
        hits = glob.glob(os.path.join(b, "**", "*.ttf"), recursive=True)
        if hits:
            return hits[0]
    except Exception:  # noqa: BLE001
        pass
    # 3) Mac（ローカル開発）
    for p in [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]:
        if os.path.exists(p):
            return p
    # 4) Linux Noto
    for p in glob.glob("/usr/share/fonts/**/NotoSansCJK*", recursive=True):
        return p
    return None


JP_FONT = find_jp_font()


def _font(size: int):
    if JP_FONT:
        return ImageFont.truetype(JP_FONT, size)
    return ImageFont.load_default()


# ----------------------------------------------------------------------
# コピー生成（Gemini → 構造化JSON）
# ----------------------------------------------------------------------
def _copy_schema():
    """google-genai 用の response_schema（pydantic）。"""
    from pydantic import BaseModel

    class Slide(BaseModel):
        title: str       # 見出し(15文字以内)
        body: str        # 本文(60文字以内)
        bg_prompt: str   # 背景画像プロンプト

    class CarouselSpec(BaseModel):
        cover_headline: str   # フック見出し(20文字以内)
        cover_sub: str        # サブ(15文字以内)
        cover_bg_prompt: str
        slides: list[Slide]
        cta_text: str         # LINE誘導の一文
        cta_bg_prompt: str
        caption: str          # Instagram投稿本文(150字程度・保存を促す)
        hashtags: str         # ハッシュタグ10〜15個(スペース区切り・#付き)

    return CarouselSpec


def generate_carousel_copy(client, topic: str, n_body: int = 4,
                          model: str = COPY_MODEL) -> dict:
    """トピック → カルーセル構成(dict)。失敗時は例外。"""
    from google.genai import types

    spec_model = _copy_schema()
    prompt = (
        "あなたは賃貸・不動産の教育系Instagram（保存される投稿）の編集者です。\n"
        f"トピック「{topic}」について、思わず保存したくなる教育カルーセルの構成を作ってください。\n\n"
        "# 制約\n"
        f"- 本文スライドは {n_body} 枚。\n"
        "- 表紙: フック見出し(20文字以内)＋サブ(15文字以内)。煽りすぎず信頼感を保つ。\n"
        "- 各本文スライド: 見出し(15文字以内)＋本文(60文字以内・要点を1つに絞る)。\n"
        "- CTA: エンクスの公式LINEへ自然に誘導する一文。\n"
        "- 各スライドの bg_prompt: 『暮らしのイメージ』の写真風プロンプト。"
        "特定の実在物件でなく、文字・ロゴは入れない内容にする。\n"
        "- caption: Instagram投稿本文。冒頭で続きを読ませる一文＋内容要約＋保存とLINE誘導。150字程度。\n"
        "- hashtags: 関連ハッシュタグを10〜15個、スペース区切り・#付き（賃貸/一人暮らし/部屋探し/エリア名など）。\n\n"
        "# 品質ルール（重要）\n"
        "- 数字や制度は一般的な相場・通説の範囲で。断定や誇大表現、法的に誤解を招く表現は避ける。\n"
        "- 宅建業者として信頼を損なわない、正確で誠実なトーン。\n"
    )
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=spec_model,
        temperature=0.7,
    )
    resp = client.models.generate_content(model=model, contents=[prompt], config=cfg)
    # SDKが parsed を持てばそれを、無ければ text をJSON化
    parsed = getattr(resp, "parsed", None)
    if parsed is not None:
        return parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
    return json.loads(resp.text)


# ----------------------------------------------------------------------
# 描画ヘルパ
# ----------------------------------------------------------------------
def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """日本語向け：文字単位で幅折返し（改行も尊重）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _draw_block(draw, xy, text, font, fill, max_w, line_gap=14, stroke=0) -> int:
    """折返し描画。次のyを返す。stroke>0で黒フチ（背景写真でも可読）。"""
    x, y = xy
    for line in _wrap(draw, text, font, max_w):
        draw.text((x, y), line, font=font, fill=fill,
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
        asc, desc = font.getmetrics()
        y += asc + desc + line_gap
    return y


def _scrim_uniform(base: Image.Image, alpha: int):
    """全面に黒半透明（背景写真の上の可読性確保）。"""
    layer = Image.new("RGBA", base.size, SCRIM + (alpha,))
    base.alpha_composite(layer)


def _scrim_gradient(base: Image.Image, where="bottom", strength=210):
    """上 or 下方向に黒グラデ暗幕。"""
    w, h = base.size
    grad = Image.new("L", (1, h), 0)
    px = grad.load()
    for y in range(h):
        t = y / h if where == "bottom" else (h - y) / h
        px[0, y] = int(strength * (t ** 1.6))
    mask = grad.resize((w, h))
    layer = Image.new("RGBA", base.size, SCRIM + (255,))
    layer.putalpha(mask)
    base.alpha_composite(layer)


def _prep_bg(bg_bytes: bytes | None, fallback=NAVY) -> Image.Image:
    """背景画像をW×Hにcrop。無ければ単色。"""
    base = Image.new("RGBA", (W, H), fallback + (255,))
    if not bg_bytes:
        return base
    try:
        src = Image.open(BytesIO(bg_bytes)).convert("RGBA")
        # cover crop
        sr, dr = src.width / src.height, W / H
        if sr > dr:
            nh = H
            nw = int(H * sr)
        else:
            nw = W
            nh = int(W / sr)
        src = src.resize((nw, nh))
        src = src.crop(((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H))
        base.alpha_composite(src)
    except Exception:  # noqa: BLE001
        pass
    return base


# ----------------------------------------------------------------------
# スライド描画
# ----------------------------------------------------------------------
def render_cover(spec, bg_bytes, brand) -> bytes:
    img = _prep_bg(bg_bytes, NAVY)
    _scrim_gradient(img, "bottom", 220)
    _scrim_uniform(img, 40)
    d = ImageDraw.Draw(img)
    d.text((MARGIN, 150), spec["cover_sub"], font=_font(50), fill=GOLD,
           stroke_width=3, stroke_fill=(0, 0, 0))
    _draw_block(d, (MARGIN, 230), spec["cover_headline"], _font(94), WHITE,
                W - MARGIN * 2, 18, stroke=5)
    d.text((MARGIN, H - 150), f"{brand}  ｜  保存して見返す", font=_font(38), fill=LIGHT,
           stroke_width=2, stroke_fill=(0, 0, 0))
    return _to_png(img)


def render_body(spec, idx, total, bg_bytes, brand) -> bytes:
    s = spec["slides"][idx]
    img = _prep_bg(bg_bytes, NAVY)
    _scrim_uniform(img, 150)
    d = ImageDraw.Draw(img)
    # ページ番号
    d.text((MARGIN, 110), f"{idx + 1} / {total}", font=_font(40), fill=GOLD,
           stroke_width=2, stroke_fill=(0, 0, 0))
    # 見出し
    y = _draw_block(d, (MARGIN, 220), s["title"], _font(72), GOLD, W - MARGIN * 2, 14, stroke=4)
    # 区切り
    d.line([(MARGIN, y + 10), (W - MARGIN, y + 10)], fill=(255, 255, 255, 120), width=3)
    # 本文
    _draw_block(d, (MARGIN, y + 50), s["body"], _font(52), WHITE, W - MARGIN * 2, 22, stroke=3)
    d.text((MARGIN, H - 130), brand, font=_font(36), fill=LIGHT,
           stroke_width=2, stroke_fill=(0, 0, 0))
    return _to_png(img)


def render_cta(spec, bg_bytes, brand) -> bytes:
    img = _prep_bg(bg_bytes, TEAL)
    _scrim_uniform(img, 130)
    d = ImageDraw.Draw(img)
    _draw_block(d, (MARGIN, 360), "続きは公式LINEで", _font(80), WHITE, W - MARGIN * 2, 16, stroke=5)
    _draw_block(d, (MARGIN, 600), spec["cta_text"], _font(50), LIGHT, W - MARGIN * 2, 22, stroke=3)
    d.text((MARGIN, H - 150), f"{brand}  ｜  フォロー＆保存", font=_font(38), fill=WHITE,
           stroke_width=2, stroke_fill=(0, 0, 0))
    return _to_png(img)


def _to_png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# ----------------------------------------------------------------------
# 一括レンダリング
# ----------------------------------------------------------------------
def render_carousel(spec: dict, bg_map: dict, brand: str = "@enks_chintai") -> list[tuple[str, bytes]]:
    """spec + 背景画像dict → [(filename, png_bytes)]。
    bg_map: {"cover": bytes|None, 0: bytes|None, 1:..., "cta": bytes|None}
    """
    out = []
    total = len(spec["slides"])
    out.append(("01_cover.png", render_cover(spec, bg_map.get("cover"), brand)))
    for i in range(total):
        out.append((f"{i + 2:02d}_slide{i + 1}.png",
                    render_body(spec, i, total, bg_map.get(i), brand)))
    out.append((f"{total + 2:02d}_cta.png", render_cta(spec, bg_map.get("cta"), brand)))
    return out


def bg_prompts_of(spec: dict) -> dict:
    """各スライドの背景プロンプトを {key: prompt} で返す。"""
    m = {"cover": spec.get("cover_bg_prompt", "")}
    for i, s in enumerate(spec["slides"]):
        m[i] = s.get("bg_prompt", "")
    m["cta"] = spec.get("cta_bg_prompt", "")
    return m
