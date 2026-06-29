# -*- coding: utf-8 -*-
"""
リール動画生成エンジン (reel.py)
=================================
トピック → Geminiで「1秒フック＋テンポの良いカット割り」台本を生成 →
9:16の縦動画を、Ken Burns(ズーム)＋テキストのフェードインで書き出し。

カルーセル(静止画)とは別物として設計：
  - 冒頭1秒のフックでスワイプを止める
  - 各カットは短文＋動きでテンポを出す
  - 音源はツールでは付けない（Instagramアプリでトレンド音を後付けが効果的）

重い処理なのでローカルCLI(reel_video.py)向き。Streamlit Cloudは非推奨。
"""
from __future__ import annotations

import json
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

import carousel  # フォント・色・定数を共有

REEL_W, REEL_H = 1080, 1920   # Instagram リール 9:16
FPS = 30

NAVY = carousel.NAVY
GOLD = carousel.GOLD
WHITE = carousel.WHITE
TEAL = carousel.TEAL
SCRIM = carousel.SCRIM


def _f(size):
    return carousel._font(size)


# ----------------------------------------------------------------------
# 台本生成（フック強め・複数案）
# ----------------------------------------------------------------------
def generate_reel_script(client, topic: str, n_cuts: int = 4,
                        model: str = carousel.COPY_MODEL) -> dict:
    from google.genai import types
    from pydantic import BaseModel

    class Cut(BaseModel):
        text: str        # 1カットの短文（20文字以内・テンポ重視）
        bg_prompt: str

    class Reel(BaseModel):
        hook: str                    # 採用フック（14文字以内・スワイプを止める）
        hook_alternatives: list[str] # フック別案（人が選べるよう2〜3案）
        cuts: list[Cut]
        cta: str                     # 締めの一言（LINE誘導）
        bg_prompt_hook: str
        bg_prompt_cta: str
        caption: str                 # Instagram投稿本文(150字程度)
        hashtags: str                # ハッシュタグ10〜15個(スペース区切り・#付き)

    prompt = (
        "あなたは賃貸・不動産の教育系Instagramリール（短尺動画）の構成作家です。\n"
        f"トピック「{topic}」で、最初の1秒でスワイプを止めるリール台本を作ってください。\n\n"
        "# 最重要：フック\n"
        "- hook は14文字以内。『え、知らなかった』と指を止めさせる問い or 意外な事実。\n"
        "- hook_alternatives に、方向性の違うフック案を2〜3個（人が選べるように）。\n"
        "- 煽りすぎ・誇大・断定は避け、信頼を損なわない範囲で。\n\n"
        "# カット\n"
        f"- cuts は {n_cuts} 個。各 text は20文字以内の短い一文でテンポよく。\n"
        "- 1カット＝1メッセージ。要点を畳みかける構成。\n"
        "- cta は公式LINEへ自然に誘導する短い一言。\n\n"
        "# 背景プロンプト\n"
        "- 各 bg_prompt は『暮らしのイメージ』の写真風。特定の実在物件でなく、文字・ロゴは入れない。\n\n"
        "# 投稿テキスト\n"
        "- caption: Instagram投稿本文。冒頭で興味を引き＋要約＋保存とLINE誘導。150字程度。\n"
        "- hashtags: 関連ハッシュタグ10〜15個、スペース区切り・#付き。\n\n"
        "# 品質\n"
        "- 数字・制度は一般的な相場/通説の範囲で。宅建業者として誠実なトーン。"
    )
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=Reel,
        temperature=0.85,
    )
    resp = client.models.generate_content(model=model, contents=[prompt], config=cfg)
    parsed = getattr(resp, "parsed", None)
    if parsed is not None:
        return parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
    return json.loads(resp.text)


def bg_prompts_of(script: dict) -> dict:
    m = {"hook": script.get("bg_prompt_hook", "")}
    for i, c in enumerate(script["cuts"]):
        m[i] = c.get("bg_prompt", "")
    m["cta"] = script.get("bg_prompt_cta", "")
    return m


# ----------------------------------------------------------------------
# 描画
# ----------------------------------------------------------------------
def _bg_reel(bg_bytes, fallback=NAVY) -> Image.Image:
    base = Image.new("RGBA", (REEL_W, REEL_H), fallback + (255,))
    if not bg_bytes:
        return base
    try:
        src = Image.open(BytesIO(bg_bytes)).convert("RGBA")
        sr, dr = src.width / src.height, REEL_W / REEL_H
        if sr > dr:
            nh = REEL_H; nw = int(REEL_H * sr)
        else:
            nw = REEL_W; nh = int(REEL_W / sr)
        src = src.resize((nw, nh))
        src = src.crop(((nw - REEL_W) // 2, (nh - REEL_H) // 2,
                        (nw - REEL_W) // 2 + REEL_W, (nh - REEL_H) // 2 + REEL_H))
        base.alpha_composite(src)
    except Exception:  # noqa: BLE001
        pass
    return base


def _ken_burns(bg: Image.Image, t: float) -> Image.Image:
    """t:0→1 でゆっくりズームイン（動きを出す）。"""
    zoom = 1.0 + 0.12 * t
    nw, nh = int(REEL_W * zoom), int(REEL_H * zoom)
    big = bg.resize((nw, nh))
    l = (nw - REEL_W) // 2
    tp = (nh - REEL_H) // 2
    return big.crop((l, tp, l + REEL_W, tp + REEL_H)).convert("RGBA")


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur); cur = ch
    if cur:
        lines.append(cur)
    return lines


def _compose_frame(bg_kb, text, font, color, text_alpha, scrim_alpha=150):
    fr = bg_kb.copy()
    fr.alpha_composite(Image.new("RGBA", fr.size, SCRIM + (scrim_alpha,)))
    layer = Image.new("RGBA", fr.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lines = _wrap(d, text, font, REEL_W - 160)
    asc, desc = font.getmetrics()
    lh = asc + desc + 20
    y = (REEL_H - lh * len(lines)) // 2
    sw = max(4, font.size // 18)   # 文字サイズに応じた黒フチ
    for ln in lines:
        w = d.textlength(ln, font=font)
        d.text(((REEL_W - w) // 2, y), ln, font=font, fill=color + (text_alpha,),
               stroke_width=sw, stroke_fill=(0, 0, 0, text_alpha))
        y += lh
    fr.alpha_composite(layer)
    return fr


def render_reel_video(script: dict, bg_map: dict, out_path: str, fps: int = FPS) -> str:
    """台本＋背景 → 縦mp4。無音（音源はアプリで後付け）。"""
    import imageio.v2 as imageio

    segs = [("hook", script["hook"], _f(130), GOLD, bg_map.get("hook"), NAVY, 1.6)]
    for i, c in enumerate(script["cuts"]):
        segs.append((f"cut{i}", c["text"], _f(96), WHITE, bg_map.get(i), NAVY, 1.7))
    segs.append(("cta", script["cta"], _f(88), WHITE, bg_map.get("cta"), TEAL, 2.4))

    writer = imageio.get_writer(out_path, fps=fps, codec="libx264", quality=8,
                                macro_block_size=None, format="FFMPEG")
    try:
        for key, text, font, color, bg_bytes, fallback, dur in segs:
            bg = _bg_reel(bg_bytes, fallback)
            n = max(2, int(fps * dur))
            for fi in range(n):
                t = fi / (n - 1)
                kb = _ken_burns(bg, t)
                fade = min(1.0, (fi / fps) / 0.3)   # 最初0.3秒でテキストをフェードイン
                fr = _compose_frame(kb, text, font, color, int(255 * fade))
                writer.append_data(np.asarray(fr.convert("RGB")))
    finally:
        writer.close()
    return out_path
