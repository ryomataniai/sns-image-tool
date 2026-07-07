# -*- coding: utf-8 -*-
"""
ルームツアー動画化エンジン (room_tour_video.py)
================================================
物件の部屋画像（実写ステージング／ルームツアー生成画像）を
「カメラがゆっくり動く短尺クリップ」に変換し、9:16に整形・キャプション・
BGMを付けてクロスフェード連結した1本のSNSリールを書き出す。

このモジュールは UI 非依存。app.py の新タブから呼び出す想定。
2026-07-06 に Cowork 上で手作業検証したffmpeg後処理を、そのまま関数化している。

依存:
    - fal-client   … 動画生成API（Kling系）。要 FAL_KEY
    - requests     … 生成物ダウンロード
    - numpy        … BGM合成（既存 requirements にあり）
    - ffmpeg(system) … packages.txt に `ffmpeg` を追加すること（drawtext/xfade/fontconfig 必須）
    - fonts-noto-cjk … packages.txt に既にあり（日本語キャプション用）

商用ライセンス注意:
    fal.ai / Kling の生成物は有料利用で商用可だが、AI生成画像＝現況相違のため
    実募集物件として出す場合は景表法・宅建業法の観点で「イメージ」明記を推奨。
    build_tour(image_note=...) で全クリップに小さな注記を焼ける。
"""
from __future__ import annotations

import os
import re
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import requests
except Exception:  # noqa: BLE001
    requests = None


# ======================================================================
# バイナリ / フォント解決
# ======================================================================
def _ffmpeg() -> str:
    """system ffmpeg を優先（drawtext/xfade/fontconfig 完備）。無ければ imageio 同梱。"""
    exe = os.environ.get("FFMPEG_BINARY")
    if exe:
        return exe
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def _ffprobe() -> Optional[str]:
    return "ffprobe" if shutil.which("ffprobe") else None


_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    str(Path(__file__).parent / "fonts" / "NotoSansJP-Bold.otf"),
    str(Path(__file__).parent / "fonts" / "NotoSansJP-Regular.otf"),
]


def _font() -> str:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    # 最後の手段: fontconfig 名（drawtext font= で解決）
    return ""


def _dur(path: str) -> float:
    """クリップ長を取得（ffprobe→失敗時 5.0）。"""
    fp = _ffprobe()
    if not fp:
        return 5.0
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return 5.0


def _esc(text: str) -> str:
    """drawtext 用エスケープ（コロン/バックスラッシュ/シングルクォート/カンマ/%）。"""
    return (text.replace("\\", r"\\").replace(":", r"\:")
                .replace("'", r"\'").replace(",", r"\,").replace("%", r"\%"))


# ======================================================================
# BGM 合成（著作権フリー・商用可のオリジナル）
# ======================================================================
def synth_bgm(out_wav: str, seconds: float = 24.0, sr: int = 44100) -> str:
    """落ち着いたアンビエントパッド（Cmaj7-Am7-Fmaj7-G）を合成して wav 保存。"""
    chords = [
        [261.63, 329.63, 392.00, 493.88],  # Cmaj7
        [220.00, 261.63, 329.63, 392.00],  # Am7
        [174.61, 220.00, 261.63, 329.63],  # Fmaj7
        [196.00, 246.94, 293.66, 392.00],  # G
    ]
    chord_dur = max(seconds, 8.0) / len(chords)

    def env(n, atk=0.9, rel=1.4):
        e = np.ones(n)
        a, r = int(atk * sr), int(rel * sr)
        e[:a] = np.linspace(0, 1, a)
        e[-r:] = np.linspace(1, 0, r)
        return e

    total = chord_dur * len(chords)
    out = np.zeros(int(total * sr))
    pos, ov = 0, int(1.2 * sr)
    for ch in chords:
        n = int(chord_dur * sr)
        t = np.arange(n) / sr
        seg = np.zeros(n)
        for i, f in enumerate(ch):
            seg += (0.6 / (i + 1)) * np.sin(2 * np.pi * f * t)
            seg += (0.15 / (i + 1)) * np.sin(2 * np.pi * f * 2 * t)
        seg += 0.10 * np.sin(2 * np.pi * ch[0] * 4 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t))
        seg *= env(n)
        if pos > 0:
            out[pos - ov:pos - ov + n] += seg
            pos = pos - ov + n
        else:
            out[:n] += seg
            pos = n
    out = out[:int(total * sr)]
    tt = np.arange(len(out)) / sr
    out *= (0.85 + 0.15 * np.sin(2 * np.pi * 0.06 * tt))
    out /= (np.max(np.abs(out)) + 1e-9)
    out *= 0.9
    data = (out * 32767).astype(np.int16)
    w = wave.open(out_wav, "w")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(np.repeat(data, 2).tobytes())
    w.close()
    # 温かみ付与＋フェード（system ffmpeg があれば）
    warm = out_wav.replace(".wav", "_warm.wav")
    fo = max(total - 2.4, 0.5)
    try:
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", out_wav,
                        "-af", f"aecho=0.8:0.9:60:0.3,lowpass=f=6500,highpass=f=90,"
                               f"dynaudnorm=f=200,afade=t=in:st=0:d=1.5,"
                               f"afade=t=out:st={fo:.1f}:d=2.4,volume=0.85",
                        warm], check=True, timeout=120)
        os.replace(warm, out_wav)
    except Exception:  # noqa: BLE001
        pass
    return out_wav


# ======================================================================
# 動画生成バックエンド（fal.ai / Kling）
# ======================================================================
# fal公式スキーマ(2026-07-06確認)に基づくモデル別設定。
#   image_key … 画像URLの引数名（モデルで異なる）
#   audio_off … 無音化に必要な追加引数（2.6/v3 は generate_audio 既定true→falseで無音&低コスト）
#   ※ i2v pro は aspect_ratio 非対応（入力画像基準）。最終9:16化はffmpeg側で実施。
FAL_MODELS = {
    "kling2.6_pro": {  # 既定・無音推奨
        "endpoint": "fal-ai/kling-video/v2.6/pro/image-to-video",
        "image_key": "start_image_url",
        "audio_off": {"generate_audio": False},
    },
    "kling2.1_pro": {
        "endpoint": "fal-ai/kling-video/v2.1/pro/image-to-video",
        "image_key": "image_url",
        "audio_off": {},  # 2.1 はそもそも無音
    },
    "kling3.0_pro": {  # 見せ場・最上（エンドポイントは v3）
        "endpoint": "fal-ai/kling-video/v3/pro/image-to-video",
        "image_key": "start_image_url",
        "audio_off": {"generate_audio": False},
    },
}


def generate_clip_fal(image_bytes: bytes, prompt: str, duration: int = 5,
                      model_key: str = "kling2.6_pro", silent: bool = True) -> bytes:
    """1枚の画像を image-to-video で動画化し mp4 バイト列を返す。要 FAL_KEY。"""
    import fal_client  # 遅延import（未導入環境でモジュール自体は読める）

    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY が未設定です（Streamlit Secrets or 環境変数）。")

    cfg = FAL_MODELS.get(model_key, FAL_MODELS["kling2.6_pro"])
    # 画像を fal ストレージへアップロード → URL 取得
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        img_path = f.name
    try:
        image_url = fal_client.upload_file(img_path)
        args = {
            "prompt": prompt,
            cfg["image_key"]: image_url,       # モデル別の画像引数名
            "duration": str(duration),          # Kling系は文字列指定
        }
        if silent:
            args.update(cfg.get("audio_off", {}))  # 無音化（対応モデルのみ）
        result = fal_client.subscribe(cfg["endpoint"], arguments=args, with_logs=False)
    finally:
        try:
            os.unlink(img_path)
        except Exception:  # noqa: BLE001
            pass

    video_url = (result or {}).get("video", {}).get("url")
    if not video_url:
        raise RuntimeError(f"動画URLが取得できませんでした: {result}")
    if requests is None:
        raise RuntimeError("requests が未導入です。")
    r = requests.get(video_url, timeout=180)
    r.raise_for_status()
    return r.content


# 部屋種別ごとの既定プロンプト（ゆっくり・破綻しにくい）
ROOM_PROMPTS = {
    "entrance": "Real estate room tour. Slow smooth forward dolly through the entrance into the hallway. Furniture stays completely still. No people. Natural light, stable cinematic camera, no warping.",
    "ldk":      "Real estate room tour. Slow smooth push-in across a bright living-dining-kitchen. Furniture stays completely still. No people. Natural daylight, stable cinematic camera, no warping.",
    "bedroom":  "Real estate room tour. Slow smooth push-in toward the bed and window. Furniture stays completely still. No people. Natural daylight, stable cinematic camera, no warping.",
    "bathroom": "Real estate room tour. Slow gentle pan across a clean bathroom. Fixtures stay completely still. No people. Soft lighting, stable cinematic camera, no warping.",
    "toilet":   "Real estate room tour. Slow gentle push-in in a clean toilet room. Fixtures stay completely still. No people. Soft lighting, stable cinematic camera, no warping.",
    "generic":  "Real estate room tour. Slow smooth push-in across the room. Everything stays completely still. No people. Natural light, stable cinematic camera, no warping.",
}


# ======================================================================
# ffmpeg 後処理
# ======================================================================
def _normalize_clip(in_path: str, out_path: str, caption: str = "",
                    top_tag: str = "", note: str = "") -> str:
    """ぼかし背景で縦1080x1920に収め、上部タグ・下部キャプション・任意注記を焼く。30fps化。"""
    ff = _ffmpeg()
    font = _font()
    base = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            "boxblur=40:1,eq=brightness=-0.12[bg];"
            "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    draws = []
    fontref = f"fontfile='{font}'" if font else "font='Noto Sans CJK JP'"
    if top_tag:
        draws.append(f"drawtext={fontref}:text='{_esc(top_tag)}':fontcolor=white:fontsize=40:"
                     f"box=1:boxcolor=black@0.40:boxborderw=18:x=(w-text_w)/2:y=150:"
                     f"alpha='if(lt(t\\,0.4)\\,t/0.4\\,1)'")
    if caption:
        draws.append(f"drawtext={fontref}:text='{_esc(caption)}':fontcolor=white:fontsize=56:"
                     f"box=1:boxcolor=black@0.45:boxborderw=26:x=(w-text_w)/2:y=h-400:"
                     f"alpha='if(lt(t\\,0.4)\\,t/0.4\\,1)'")
    if note:
        draws.append(f"drawtext={fontref}:text='{_esc(note)}':fontcolor=white@0.85:fontsize=26:"
                     f"box=1:boxcolor=black@0.35:boxborderw=10:x=w-text_w-40:y=h-70")
    chain = base + ";[base]" + (",".join(draws) + "," if draws else "") + "fps=30,format=yuv420p,setsar=1[v]"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", in_path,
                    "-filter_complex", chain, "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", out_path], check=True, timeout=300)
    return out_path


def _xfade_concat(seg_paths: list[str], out_path: str, t: float = 0.6) -> str:
    """セグメントをクロスフェード連結（可変長対応・オフセット動的計算）。"""
    ff = _ffmpeg()
    inputs = []
    for p in seg_paths:
        inputs += ["-i", p]
    if len(seg_paths) == 1:
        shutil.copy(seg_paths[0], out_path)
        return out_path
    durs = [_dur(p) for p in seg_paths]
    parts, prev, acc = [], "[0]", durs[0]
    for i in range(1, len(seg_paths)):
        offset = acc - t
        label = "[v]" if i == len(seg_paths) - 1 else f"[x{i}]"
        parts.append(f"{prev}[{i}]xfade=transition=fade:duration={t}:offset={offset:.3f}{label}")
        prev = label
        acc = acc + durs[i] - t
    filt = ";".join(parts)
    subprocess.run([ff, "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", filt, "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", "-movflags", "+faststart", out_path],
                   check=True, timeout=600)
    return out_path


def _mux_bgm(video_path: str, bgm_wav: str, out_path: str) -> str:
    ff = _ffmpeg()
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", video_path, "-i", bgm_wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_path],
                   check=True, timeout=300)
    return out_path


# ======================================================================
# マイソク解析（任意・ベストエフォート）
# ======================================================================
def parse_maisoku_specs(pdf_bytes: bytes) -> dict:
    """マイソクPDFから 物件名/間取り/面積/築年 等をベストエフォートで抽出。"""
    specs = {}
    try:
        import fitz  # pymupdf（既存 requirements）
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception:  # noqa: BLE001
        return specs
    m = re.search(r"(\d+(?:\.\d+)?)\s*㎡", text)
    if m:
        specs["area"] = m.group(1) + "㎡"
    m = re.search(r"([1-9]\s*[SLDK]{1,3}|ワンルーム|1R)", text)
    if m:
        specs["madori"] = m.group(1).replace(" ", "")
    m = re.search(r"(\d{4})\s*年\s*0?(\d{1,2})?\s*月?", text)
    if m:
        specs["built"] = m.group(1) + "年築"
    return specs


# ======================================================================
# オーケストレーション
# ======================================================================
def build_tour(images: list[tuple], *, captions: Optional[list] = None,
               top_tag: str = "", with_captions: bool = True,
               with_bgm: bool = True, also_silent: bool = True,
               model_key: str = "kling2.6_pro", duration: int = 5,
               room_types: Optional[list] = None, image_note: str = "",
               progress=None) -> dict:
    """
    images: [(name, image_bytes), ...] 再生順
    captions: 各クリップ下部の文言（None かつ with_captions=True なら name を使用）
    room_types: 各画像の部屋種別キー（ROOM_PROMPTS のキー）。None は 'generic'
    progress: callable(step:int, total:int, msg:str) 進捗コールバック（任意）
    戻り値: {'silent': bytes, 'bgm': bytes}（生成した版のみ）
    """
    n = len(images)
    if n == 0:
        raise ValueError("画像がありません。")
    room_types = room_types or ["generic"] * n
    if captions is None:
        captions = [name for name, _ in images] if with_captions else [""] * n

    workdir = tempfile.mkdtemp(prefix="tour_")
    seg_paths = []
    try:
        # ① 各画像を動画化 → ② 正規化＋キャプション
        for i, (name, img) in enumerate(images):
            if progress:
                progress(i, n, f"{name}: 動画生成中…")
            rt = room_types[i] if i < len(room_types) else "generic"
            prompt = ROOM_PROMPTS.get(rt, ROOM_PROMPTS["generic"])
            clip_bytes = generate_clip_fal(img, prompt, duration=duration, model_key=model_key)
            raw = os.path.join(workdir, f"raw_{i}.mp4")
            with open(raw, "wb") as f:
                f.write(clip_bytes)
            seg = os.path.join(workdir, f"seg_{i}.mp4")
            cap = captions[i] if (with_captions and i < len(captions)) else ""
            _normalize_clip(raw, seg, caption=cap, top_tag=top_tag if with_captions else "",
                            note=image_note)
            seg_paths.append(seg)

        # ③ クロスフェード連結
        if progress:
            progress(n, n, "連結中…")
        silent = os.path.join(workdir, "tour_silent.mp4")
        _xfade_concat(seg_paths, silent)

        out = {}
        if also_silent:
            with open(silent, "rb") as f:
                out["silent"] = f.read()
        if with_bgm:
            total = _dur(silent)
            bgm = os.path.join(workdir, "bgm.wav")
            synth_bgm(bgm, seconds=total + 2.0)
            withbgm = os.path.join(workdir, "tour_bgm.mp4")
            _mux_bgm(silent, bgm, withbgm)
            with open(withbgm, "rb") as f:
                out["bgm"] = f.read()
        return out
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
