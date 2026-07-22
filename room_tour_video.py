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
    # 同梱フォント（本番aptが無い環境・ローカル検証のフォールバック。prodは上のBoldが優先）
    str(Path(__file__).parent / "fonts" / "NotoSansCJK-Regular.ttc"),
]


def _font() -> str:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    # 最後の手段: fontconfig 名（drawtext font= で解決）
    return ""


# 明朝（雑誌型カバーのマスト/コピー用）。同梱を最優先＝本番/ローカルで確実に明朝が出る。
_SERIF_CANDIDATES = [
    str(Path(__file__).parent / "fonts" / "NotoSerifJP-Regular.ttf"),   # 同梱（OFL・確実）
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",          # Cloud apt（あれば）
    "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",                       # macOSローカル
]


def _serif_font() -> str:
    """明朝フォントのパス。無ければゴシック(_font)にフォールバック（劣化するが動作は継続）。"""
    for p in _SERIF_CANDIDATES:
        if os.path.exists(p):
            return p
    return _font()


# ★v79「動く雑誌」：特大明朝Bold（見出し96-190px）＋ゴシックBold（情報バー38px/ラベル）。
#   権威サンプルと同一実体を fonts/ に配置（NotoSerifCJKjp-Bold.otf / NotoSansCJKjp-Bold.otf・SIL OFL）。
#   ★同梱の NotoSerifJP-Regular.ttf は実体 ExtraLight＝特大明朝に細すぎ→Bold実体が要る。
_SERIF_BOLD_CANDIDATES = [
    str(Path(__file__).parent / "fonts" / "NotoSerifCJKjp-Bold.otf"),   # ★v79権威（同梱）
    str(Path(__file__).parent / "fonts" / "NotoSerifJP-Bold.otf"),
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",             # Cloud apt（あれば）
]
_SANS_BOLD_CANDIDATES = [
    str(Path(__file__).parent / "fonts" / "NotoSansCJKjp-Bold.otf"),    # ★v79権威（同梱）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",              # Cloud apt
    str(Path(__file__).parent / "fonts" / "NotoSansJP-Bold.otf"),
]


def _serif_bold_font():
    """★v79 特大明朝Bold。返り値 (path, need_stroke)。Bold実体あり→(path, False)＝そのまま描く。
    無し→(Regular明朝, True)＝描画側で stroke_width の擬似Bold（ウロコが落ちるが動作継続・Bold配置で自動解消）。"""
    for p in _SERIF_BOLD_CANDIDATES:
        if os.path.exists(p):
            return p, False
    return _serif_font(), True


def _sans_bold_font():
    """★v79 ゴシックBold（情報バー/ラベル）。返り値 (path, need_stroke)。Regularだと情報バー38pxが弱い。"""
    for p in _SANS_BOLD_CANDIDATES:
        if os.path.exists(p):
            return p, False
    return _font(), True


def env_diagnostics() -> dict:
    """描画環境の自己診断（Cloudでのフォント/drawtext欠如を可視化）。設定画面で表示。
    冒頭タイトル・テロップは drawtext と日本語フォントの両方が要る。どちらか欠けると文字が出ない。
    返り値: {ffmpeg, drawtext(bool), font(path or ''), font_ok(bool)}。"""
    ff = _ffmpeg()
    drawtext = False
    try:
        h = subprocess.run([ff, "-hide_banner", "-filters"],
                           capture_output=True, text=True, timeout=30)
        drawtext = (" drawtext " in h.stdout) or ("drawtext" in h.stdout)
    except Exception:  # noqa: BLE001
        drawtext = False
    font = _font()
    serif = _serif_font()
    return {"ffmpeg": ff, "drawtext": bool(drawtext),
            "font": font, "font_ok": bool(font and os.path.exists(font)),
            "serif": serif, "serif_ok": bool(serif and os.path.exists(serif)
                                             and serif.endswith((".ttf", ".otf", "ProN.ttc"))),
            # ★キー/ボイスIDの値は返さない（存在有無のみ）＝UI/ログに秘匿情報を出さない
            "eleven_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "eleven_voice": bool(os.environ.get("ELEVENLABS_VOICE_ID"))}


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


def _adur(path: str) -> float:
    """★音声ファイルの尺（ffprobe format=duration）。★_dur は `-select_streams v:0`＝動画ストリーム選択で
    音声onlyファイル(mp3/wav)では空→例外→5.0s固定を返す（narr_actualが測れない穴）。ナレ音声はこちらで測る。
    失敗時 0.0（無音扱い＝MIN_BEATへ落ちる・偽の尺を作らない）。"""
    fp = _ffprobe()
    if not fp:
        return 0.0
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


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
# Kling（v2.1/v2.6/v3 image-to-video）は negative_prompt / cfg_scale をサポート。
# 非対応モデルを足す場合は supports_negative=False にすれば無害スキップされる。
FAL_MODELS = {
    "kling2.6_pro": {  # 既定・無音推奨
        "endpoint": "fal-ai/kling-video/v2.6/pro/image-to-video",
        "image_key": "start_image_url",
        "audio_off": {"generate_audio": False},
        "supports_negative": True,
    },
    "kling2.1_pro": {
        "endpoint": "fal-ai/kling-video/v2.1/pro/image-to-video",
        "image_key": "image_url",
        "audio_off": {},  # 2.1 はそもそも無音
        "supports_negative": True,
    },
    "kling3.0_pro": {  # 見せ場・最上（エンドポイントは v3）
        "endpoint": "fal-ai/kling-video/v3/pro/image-to-video",
        "image_key": "start_image_url",
        "audio_off": {"generate_audio": False},
        "supports_negative": True,
    },
}

# 全クリップ共通の除外プロンプト（外観のmorph抑制＋室内の破綻抑制に効く）
DEFAULT_NEGATIVE_PROMPT = (
    "distortion, morphing, warping, deforming building, changing architecture, "
    "extra or missing windows, melting walls, bending structure, people, text, watermark"
)


def generate_clip_fal(image_bytes: bytes, prompt: str, duration: int = 5,
                      model_key: str = "kling2.6_pro", silent: bool = True,
                      negative_prompt: str = "", cfg_scale: Optional[float] = None,
                      out_path: Optional[str] = None):
    """1枚の画像を image-to-video で動画化。要 FAL_KEY。
    out_path 指定時は動画を **ストリーミングでディスクへ chunk 書き込み** し out_path を返す
    （mp4全体をメモリ＝変数に載せない＝Cloud OOM対策）。未指定時は従来どおり bytes を返す。
    negative_prompt/cfg_scale は supports_negative なモデルのみ送る（非対応は無害スキップ）。"""
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
        if cfg.get("supports_negative"):           # 対応モデルのみ（非対応は400回避でスキップ）
            if negative_prompt:
                args["negative_prompt"] = negative_prompt
            if cfg_scale is not None:
                args["cfg_scale"] = cfg_scale
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
    if out_path is not None:
        # ストリーミングでディスクへ（mp4全体を変数に載せない）
        with requests.get(video_url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return out_path
    r = requests.get(video_url, timeout=180)
    r.raise_for_status()
    return r.content


def _still_clip(image_bytes: bytes, seconds: float, out_path: str) -> str:
    """画像を seconds 秒ループした静止クリップを生成（fal/Klingを通さない＝morphゼロ・課金ゼロ）。
    間取り図・3Dパース等、image-to-videoで図面が壊れるものに使う。以降は _normalize_clip で整形。"""
    ff = _ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        img = f.name
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-i", img,
                        "-t", f"{max(seconds, 1):g}", "-r", "30",
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-preset", "veryfast", out_path], check=True, timeout=120)
    finally:
        try:
            os.unlink(img)
        except Exception:  # noqa: BLE001
            pass
    return out_path


# 部屋種別ごとの既定プロンプト（ゆっくり・破綻しにくい）
ROOM_PROMPTS = {
    "entrance": "Real estate room tour. Slow smooth forward dolly through the entrance into the hallway. Furniture stays completely still. No people. Natural light, stable cinematic camera, no warping.",
    "ldk":      "Real estate room tour. Slow smooth push-in across a bright living-dining-kitchen. Furniture stays completely still. No people. Natural daylight, stable cinematic camera, no warping.",
    "bedroom":  "Real estate room tour. Slow smooth push-in toward the bed and window. Furniture stays completely still. No people. Natural daylight, stable cinematic camera, no warping.",
    "bathroom": "Real estate room tour. Slow gentle pan across a clean bathroom. Fixtures stay completely still. No people. Soft lighting, stable cinematic camera, no warping.",
    "toilet":   "Real estate room tour. Slow gentle push-in in a clean toilet room. Fixtures stay completely still. No people. Soft lighting, stable cinematic camera, no warping.",
    "generic":  "Real estate room tour. Slow smooth push-in across the room. Everything stays completely still. No people. Natural light, stable cinematic camera, no warping.",
    "exterior": "Real estate exterior. Slow cinematic forward dolly, walking toward the building facade and entrance, revealing depth and parallax. Keep the building architecture, walls, windows, number of floors and overall shape exactly as-is; do not change, add or remove any structural detail. Photorealistic, stable camera, no morphing, no warping, no deformation, no people. Natural daylight.",
}


# ★v79-4 焦点(focal)主語指向のKlingプロンプト。大原則＝モーションは必ず『そのビートの文字の主語focal』に向かう
#   （無目的な動き禁止・v11実機で確定）。room_facts_map の focal を注入。編集系(パン/ドリフト)は棄却＝主軸Kling。
#   ★v79-4c：旧negativeの構造保持資産を復元（窓の増減・階数/建築変化はKlingの定番破綻）。
_V79_NEGATIVE = ("no people, no camera shake, no warping walls, no morphing furniture, "
                 "no changing layout, no extra or missing windows, no changing architecture, "
                 "no bending structure, no text, no flickering, "
                 # ★magfit-v79c：開口部(扉/窓/シャッター)の開閉・変形と、人物出現・扉の先の別空間生成を明示禁止
                 "no opening doors, no opening windows, no doors windows or shutters opening moving or transforming, "
                 "no glass door appearing, no people appearing, no human figures, no silhouettes, "
                 "no new room or space appearing behind any door or window")

_V79_KLING_ROOM = {   # 部屋別カメラワーク（依頼文§4.5）。focal は共通の『Ending settled on {focal}』で一本化（重複回避）
    # ★magfit-v79c：玄関は『扉を開けない/扉の先を生成しない』を明示（ドア開閉→別室の幻覚を封じる）
    "entrance": ("static interior shot of the entrance and shoe cabinet with only a very slight slow micro push-in; "
                 "the door stays closed and perfectly still; do not open, move or animate the door; "
                 "do not generate or reveal any room or space behind the door; camera movement only"),
    "ldk":      "camera slowly pushes forward into the living room, with subtle parallax between furniture",
    "kitchen":  "camera slowly moves toward the kitchen window",
    "bedroom":  "camera slowly dollies in toward the window light",
    "bathroom": "very slight push-in with minimal movement",
    "washroom": "very slight push-in with minimal movement",
    "toilet":   "very slight push-in with minimal movement",
    "balcony":  "camera slowly pushes toward the balcony opening",
    # ★magfit-v79c：外観は静止〜微パン/微ズームのみ。窓/シャッター/扉の変形・ガラス扉化・人物出現を封じる
    "exterior": ("static shot of the building facade with only a very slight slow camera pan or micro zoom; "
                 "keep the architecture, walls, windows, doors, shutters and number of floors exactly as-is; "
                 "do not open move morph or transform any door window or shutter; do not add a glass door; "
                 "do not add any person or figure; the building itself stays perfectly still, camera movement only"),
    "generic":  "slow smooth push-in across the room",
}
_V79_MOTION_AMT = {"minimal": "very slight, barely-there", "normal": "slow, smooth",
                   "strong": "slow but clearly noticeable"}


def build_kling_prompt(video_type, focal="the room", motion="normal"):
    """★v79-4：焦点(focal)主語指向のKling image-to-videoプロンプトを合成。
    共通『… ending settled on {focal}. No people.』＋部屋別カメラワーク＋動き量(minimal/normal/strong)。
    ★モーションは必ず文字の主語(focal)に向かう＝無目的な動きを指示しない。狭室のminimalは呼出側(room_facts_map)で指定。"""
    focal = (str(focal or "the room")).strip() or "the room"
    room = _V79_KLING_ROOM.get(video_type, _V79_KLING_ROOM["generic"]).format(focal=focal)
    amt = _V79_MOTION_AMT.get(motion, _V79_MOTION_AMT["normal"])
    return (f"Real estate room tour. {amt.capitalize()} cinematic camera movement: {room}. "
            f"Ending settled on {focal}. Furniture and architecture stay completely still. "
            "Lighting stays constant. "   # ★v79-4c：staging照明(モテの夜間接照明等)が動画化で変わると世界観が壊れる
            "No people. Photorealistic, stable camera, no morphing, no warping.")


# ======================================================================
# ffmpeg 後処理
# ======================================================================
_CAP_FADE = "alpha='if(lt(t\\,0.4)\\,t/0.4\\,1)'"


def _cap_draw(fontref: str, text: str, size: int, x: str, y: str, taste: str,
              box_bw: Optional[int] = None, box_alpha: float = 0.45) -> str:
    """シーンテロップ1行の drawtext を作る。clean=白＋影／pop=白＋座布団box。
    x/y は drawtext の式（配置プリセットで決定）。pop の box は旧キャプションと同一（black@0.45）。"""
    if taste == "pop":
        bw = box_bw if box_bw is not None else max(12, int(size * 0.45))
        style = f"fontcolor=white:fontsize={size}:box=1:boxcolor=black@{box_alpha}:boxborderw={bw}"
    else:  # clean（既定・Simple内見準拠）：影付き・boxなし
        style = (f"fontcolor=white:fontsize={size}:"
                 f"shadowcolor=black@0.55:shadowx=2:shadowy=2")
    return f"drawtext={fontref}:text='{_esc(text)}':{style}:x={x}:y={y}:{_CAP_FADE}"


# テロップ配置プリセット（4種・safe-zone厳守：最下部200px回避／左右マージン）
# 返り値: (x式, メインy, [情感1y, 情感2y])。下中央は旧キャプション（x中央/y=h-400）と同一。
def _telop_layout(pos: str) -> tuple:
    if pos == "下左":                     # 左マージン・下部（依頼の「pop左寄せ」相当）
        return ("60", "h-400", ["h-330", "h-278"])
    if pos == "上中央":                   # 上部中央（上部タグ y=150 の下・下が家具で詰まる写真向け）
        return ("(w-text_w)/2", "260", ["330", "382"])
    if pos == "中央":                     # 画面中央（余白の効いたミニマル写真向け）
        return ("(w-text_w)/2", "(h-text_h)/2-60", ["(h-text_h)/2+10", "(h-text_h)/2+62"])
    # 下中央（既定・現行と同一）
    return ("(w-text_w)/2", "h-400", ["h-330", "h-278"])


def _normalize_clip(in_path: str, out_path: str, caption: str = "",
                    sub_lines: Optional[list] = None, top_tag: str = "", note: str = "",
                    taste: str = "clean", pos: str = "下中央", flash: str = "",
                    out_w: int = 1080, out_h: int = 1920,
                    fit_mode: str = "fill") -> str:
    """out_w×out_h（既定=縦1080x1920）に整形し、テロップ（部屋名＋情感2行）・上部タグ・注記を焼く。30fps化。

    caption   : シーンのメインライン（例 "living room 10.9J"）
    sub_lines : 情感コピー（最大2行）
    taste     : "clean"（既定・白＋影・boxなし）/ "pop"（白＋座布団box）
    pos       : 配置プリセット "下中央"（既定）/ "下左" / "上中央" / "中央"
    flash     : 冒頭極短フラッシュ文言（先頭クリップのみ・0.5秒フェードで重畳）
    fit_mode  : "fill"（既定・余白ゼロ/端が切れる）/ "contain"（全体表示・ぼかし余白）
    キー文字は safe-zone（最下部200px回避・左右マージン）内。out_w/out_h で任意比率対応。
    """
    ff = _ffmpeg()
    font = _font()
    if fit_mode == "contain":
        # 全体を収める：前景を縮小して中央配置、余白はぼかし背景（従来）
        base = (f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},boxblur=40:1,eq=brightness=-0.12[bg];"
                f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    else:
        # 埋める：拡大してフレームを覆い、はみ出しをcrop（余白ゼロ）
        base = (f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h}[base]")
    draws = []
    fontref = f"fontfile='{font}'" if font else "font='Noto Sans CJK JP'"
    if top_tag:
        draws.append(f"drawtext={fontref}:text='{_esc(top_tag)}':fontcolor=white:fontsize=40:"
                     f"box=1:boxcolor=black@0.40:boxborderw=18:x=(w-text_w)/2:y=150:"
                     f"alpha='if(lt(t\\,0.4)\\,t/0.4\\,1)'")
    x_expr, y_main, y_subs = _telop_layout(pos)   # 配置プリセット→x/y式
    if caption:                                   # メインライン（下中央は旧と同一の56/box26/y=h-400）
        draws.append(_cap_draw(fontref, caption, 56, x_expr, y_main, taste, box_bw=26))
    for k, s in enumerate((sub_lines or [])[:2]):  # 情感2行（メインの近傍・safe-zone内）
        if s and s.strip():
            draws.append(_cap_draw(fontref, s, 34, x_expr, y_subs[k], taste))
    if note:
        draws.append(f"drawtext={fontref}:text='{_esc(note)}':fontcolor=white@0.85:fontsize=26:"
                     f"box=1:boxcolor=black@0.35:boxborderw=10:x=w-text_w-40:y=h-70")
    flash_draw = None
    if flash:                                     # 冒頭極短フラッシュ（0.5秒・中央・フェードイン/アウト）
        fa = ("alpha='if(lt(t\\,0.15)\\,t/0.15\\,if(lt(t\\,0.35)\\,1\\,"
              "if(lt(t\\,0.5)\\,(0.5-t)/0.15\\,0)))'")
        flash_draw = (f"drawtext={fontref}:text='{_esc(flash)}':fontcolor=white:fontsize=76:"
                      f"shadowcolor=black@0.6:shadowx=3:shadowy=3:"
                      f"x=(w-text_w)/2:y=(h-text_h)/2:{fa}")

    # -threads 1：エンコードのフレームバッファ多重化を抑えピークRSSを下げる（Cloud 1GB制限向け）。
    #   ※ threads は圧縮の並列度のみに影響し、drawtext（テロップ/注記/SynthID経路）の
    #     描画結果は不変＝テロップ回帰なし。filter_complex(chain) は一切変更していない。
    def _encode(draw_list):
        chain = base + ";[base]" + (",".join(draw_list) + "," if draw_list else "") \
            + "fps=30,format=yuv420p,setsar=1[v]"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", in_path,
                        "-filter_complex", chain, "-map", "[v]", "-an",
                        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-preset", "veryfast", "-threads", "1", out_path],
                       check=True, timeout=300)

    try:
        _encode(draws + ([flash_draw] if flash_draw else []))
    except subprocess.CalledProcessError as e:
        # 冒頭タイトル(flash)固有の失敗はシーンを脱落させず、タイトル無しで継続（logger/stderrに可視化）。
        #   ＝「タイトルが出ない」を「シーンが丸ごと消える」に悪化させない＝嘘UIの二次被害を防ぐ。
        # ベース（テロップ/注記）まで失敗する場合はフォント/ffmpeg欠如の可能性が高く、
        #   そのまま送出して Phase B で state.json/UI に顕在化させる（握りつぶさない）。
        if flash_draw:
            _log.warning("[room_tour_video] 冒頭タイトル描画に失敗→タイトル無しでシーン継続: %s: %s",
                         type(e).__name__, e)
            _sys.stderr.write(f"[room_tour_video] 冒頭タイトル描画失敗（タイトル無しで継続）: {e}\n")
            _encode(draws)
        else:
            raise
    return out_path


# -threads 2：libx264のフレームバッファ多重化を抑えピークRSSを下げる（Cloudの少CPU/1GB制限向け）
_ENC = ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-threads", "1"]


def _reencode_piece(src: str, dst: str, vf: str) -> None:
    """1本のクリップを vf（trim等）で再エンコード。全クリップ同一パラメータに揃える
    （30fps/yuv420p/sar=1）＝後段の concat demuxer を -c copy で通すため。メモリは1本分。"""
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", src,
                    "-filter_complex", f"{vf},fps=30,format=yuv420p,setsar=1[v]",
                    "-map", "[v]", "-an", *_ENC, dst], check=True, timeout=300)


def _xfade_concat(seg_paths: list[str], out_path: str, t: float = 0.6,
                  flash_cut: bool = False) -> str:
    """メモリ安全なクロスフェード連結。
    旧方式（N本を全入力で同時デコードする xfade フィルタグラフ）は N が増えると
    ffmpeg のピークRSSが 1GB超（実測 9本=1.7GB）となり Streamlit Cloud を落とす。
    そこで各境界の t 秒だけを xfade した『極小トランジションclip』を作り、本編は
    トリムして concat demuxer（-c copy・逐次・デコードなし）で連結する。各 ffmpeg 呼び出しは
    最大2本しか開かないためピークは1本分（数十MB）で済む。見た目のクロスフェードは維持。

    flash_cut=False（既定）: transition=fade・境界0.6秒（現行どおり・回帰なし）。
    flash_cut=True         : transition=fadewhite・境界0.2秒（極短の白フラッシュ）。
      機構は同一（境界前後の短い区間のみ2本デコード）。全結合filter_complexには回帰せず、
      フレーム全量をメモリに載せないためメモリ特性は現行(fade)と同一クラス。"""
    ff = _ffmpeg()
    trans = "fadewhite" if flash_cut else "fade"
    if flash_cut:
        t = 0.2   # 白フラッシュは極短。オーバーラップも0.2に縮め、演出・尺整合・低メモリを両立
    n = len(seg_paths)
    if n == 0:
        raise ValueError("連結するセグメントがありません。")
    if n == 1:
        shutil.copy(seg_paths[0], out_path)
        return out_path
    durs = [_dur(p) for p in seg_paths]
    # クリップが t の2倍より短いとトリム区間が破綻するため、その場合はハードカットに退避
    use_xfade = min(durs) > (2 * t + 0.2)
    workdir = tempfile.mkdtemp(prefix="xf_")
    try:
        parts: list[str] = []
        if not use_xfade:
            parts = list(seg_paths)                        # フォールバック＝ハードカット
        else:
            # head = clip0[0, D0-t]
            head = os.path.join(workdir, "p_head.mp4")
            _reencode_piece(seg_paths[0], head,
                            f"[0:v]trim=start=0:end={durs[0]-t:.3f},setpts=PTS-STARTPTS")
            parts.append(head)
            for i in range(n - 1):
                # transition[i] = xfade(clip[i]の末尾t秒, clip[i+1]の先頭t秒)＝2本のみ
                tr = os.path.join(workdir, f"p_tr_{i}.mp4")
                subprocess.run(
                    [ff, "-y", "-loglevel", "error", "-i", seg_paths[i], "-i", seg_paths[i + 1],
                     "-filter_complex",
                     # xfade は入力がCFR（定フレームレート）である必要があるため各入力に fps=30 を前置
                     (f"[0:v]trim=start={durs[i]-t:.3f}:end={durs[i]:.3f},"
                      "setpts=PTS-STARTPTS,fps=30,format=yuv420p,setsar=1[a];"
                      f"[1:v]trim=start=0:end={t:.3f},"
                      "setpts=PTS-STARTPTS,fps=30,format=yuv420p,setsar=1[b];"
                      f"[a][b]xfade=transition={trans}:duration={t}:offset=0,"
                      "format=yuv420p,setsar=1[v]"),
                     "-map", "[v]", "-an", *_ENC, tr], check=True, timeout=300)
                parts.append(tr)
                if i < n - 2:                              # mid[i+1] = clip[i+1][t, D-t]
                    mid = os.path.join(workdir, f"p_mid_{i+1}.mp4")
                    _reencode_piece(seg_paths[i + 1], mid,
                                    f"[0:v]trim=start={t:.3f}:end={durs[i+1]-t:.3f},setpts=PTS-STARTPTS")
                    parts.append(mid)
            # tail = clip[N-1][t, D]
            tail = os.path.join(workdir, "p_tail.mp4")
            _reencode_piece(seg_paths[n - 1], tail,
                            f"[0:v]trim=start={t:.3f}:end={durs[n-1]:.3f},setpts=PTS-STARTPTS")
            parts.append(tail)
        # concat demuxer（-c copy・逐次・O(1)メモリ）
        listfile = os.path.join(workdir, "list.txt")
        with open(listfile, "w") as lf:
            for p in parts:
                lf.write("file '%s'\n" % p.replace("'", "'\\''"))
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", listfile, "-c", "copy", "-movflags", "+faststart", out_path],
                       check=True, timeout=600)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _trim_clip(src: str, dst: str, seconds: float) -> str:
    """クリップを先頭から seconds 秒に切り出す（尺切り出し＝速度変更でない・atempo0）。
    全クリップ同一パラメータ(30fps/yuv420p/sar=1)に揃え、後段の concat demuxer を通す。"""
    _reencode_piece(src, dst, f"[0:v]trim=start=0:end={max(0.1, seconds):.3f},setpts=PTS-STARTPTS")
    return dst


def _concat_hard(paths: list[str], out_path: str) -> str:
    """ハードカット連結（concat demuxer・-c copy・食われない）。★ビート境界に使う＝累積ズレを断つ。"""
    if len(paths) == 1:
        shutil.copy(paths[0], out_path)
        return out_path
    workdir = tempfile.mkdtemp(prefix="hc_")
    try:
        listfile = os.path.join(workdir, "list.txt")
        with open(listfile, "w") as lf:
            for p in paths:
                lf.write("file '%s'\n" % p.replace("'", "'\\''"))
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", listfile, "-c", "copy", "-movflags", "+faststart", out_path],
                       check=True, timeout=600)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _assemble_beats(beat_groups: list[list[str]], out_path: str,
                    t: float = 0.6, flash_cut: bool = False) -> str:
    """★story-v78 A0 part2：ビート単位の連結。ビート内はxfade(0.6s・多画像)／ビート境界はハードカット。
    beat_groups=[[ビート0のクリップpath...], [ビート1...], ...]（各pathは既にtrims尺にトリム済）。
    → 各ビートを xfade連結（1枚ならそのまま）→ ビート間を concat demuxer でハード連結。
    ★総尺 = Σ(各ビートの描画尺) = Σナレ秒（ビート境界は食われない）。"""
    workdir = tempfile.mkdtemp(prefix="beat_")
    try:
        beat_clips = []
        for bi, grp in enumerate(beat_groups):
            if not grp:
                continue
            if len(grp) == 1:
                beat_clips.append(grp[0])                    # 1画像ビート＝そのまま（パディングなし）
            else:
                bc = os.path.join(workdir, f"beat_{bi}.mp4")  # 多画像＝ビート内xfade
                _xfade_concat(grp, bc, t=t, flash_cut=flash_cut)
                beat_clips.append(bc)
        return _concat_hard(beat_clips, out_path)             # ★ビート間はハードカット
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ★narr-fix-b：実尺先行（measure-first）の定数。ビート最終尺 d_i = max(_MIN_BEAT, narr_actual + _TAIL_PAD)。
#   _MIN_BEAT=big_text2行+タグを読める最低尺（4.0s仮・実映像でCOO目視調整）。_TAIL_PAD=発話後の余白。
_MIN_BEAT_SEC = 4.0
_NARR_TAIL_PAD = 0.5
_FREEZE_WARN_SEC = 2.5   # フリーズ延長がこれを超えたら警告（背景静止長→スライドショー感）


def _freeze_extend(src: str, seconds: float, out_path: str) -> str:
    """★narr-fix-b：src を seconds 尺へ。src<seconds は末尾フレームを静止延長（tpad=clone・文字面/背景を保持）、
    src>=seconds は通常トリム。返り値=out_path。無音（-an）＝音声は後段で別mux。"""
    _sd = _dur(src)
    if seconds <= _sd + 0.05:
        return _trim_clip(src, out_path, seconds)
    pad = seconds - _sd
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", src,
                    "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    "-an", "-movflags", "+faststart", out_path], check=True, timeout=300)
    return out_path


def _burn_subtitles(video_path: str, events, out_path: str,
                    out_w: int = 1080, out_h: int = 1920) -> str:
    """★story-v78 B ③：時刻付き字幕を動画に焼く（clean 白＋影・下部safe-zone・1行ずつ切替）。
    events=[(line, start_sec, end_sec)]（core.subtitle_events の出力）。無音bodyに焼く→後段でBGM/ナレを重ねる。
    メモリ安全＝1パス。events空/無効なら素通し（表紙のみ・ナレ無し等）。"""
    ev = [(str(l), float(s), float(e)) for l, s, e in (events or [])
          if l and str(l).strip() and float(e) > float(s)]
    if not ev:
        shutil.copy(video_path, out_path)
        return out_path
    font = _font()
    fontref = f"fontfile='{font}'" if font else "font='Noto Sans CJK JP'"
    y = out_h - 240                                   # 下部safe-zone（最下部200px回避）・1行
    draws = [f"drawtext={fontref}:text='{_esc(line)}':fontcolor=white:fontsize=48:"
             f"shadowcolor=black@0.55:shadowx=2:shadowy=2:x=(w-text_w)/2:y={y}:"
             f"enable='between(t\\,{s:.3f}\\,{e:.3f})'" for line, s, e in ev]
    vf = "[0:v]" + ",".join(draws) + "[v]"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", video_path,
                    "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
                    "-c:a", "copy", "-r", "30", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", out_path], check=True, timeout=600)
    return out_path


def _burn_beat_overlays(video_path: str, overlays, out_path: str,
                        out_w: int = 1080, out_h: int = 1920) -> str:
    """★v79「動く雑誌」：各ビートの文字面PNG（全画面透明RGBA＝build_beat_overlay）を時間範囲で背景映像に重ねる。
    overlays=[(png_bytes, start_sec, end_sec)]。1パス（N枚を enable=between(t,s,e) で連鎖overlay）。字幕焼きの全画面版。
    PNGはビート尺のあいだ静止表示（背景=Kling動画だけが動く）。overlays空なら素通し（表紙のみ等）。メモリ安全＝1パス。"""
    ov = [(b, float(s), float(e)) for b, s, e in (overlays or [])
          if b and float(e) > float(s)]
    if not ov:
        shutil.copy(video_path, out_path)
        return out_path
    tmp = tempfile.mkdtemp(prefix="beatov_")
    try:
        inputs = ["-i", video_path]
        for k, (b, _s, _e) in enumerate(ov):
            p = os.path.join(tmp, f"ov{k}.png")
            with open(p, "wb") as f:
                f.write(b)
            inputs += ["-i", p]
        # 連鎖overlay：各PNGを実解像度へscale(保険)→時間窓enableで前段に重ねる
        chain, prev = [], "[0:v]"
        for k, (_b, s, e) in enumerate(ov):
            scaled = f"[p{k}]"
            chain.append(f"[{k + 1}:v]scale={out_w}:{out_h}{scaled}")
            nxt = f"[v{k}]"
            chain.append(f"{prev}{scaled}overlay=0:0:"
                         f"enable='between(t\\,{s:.3f}\\,{e:.3f})'{nxt}")
            prev = nxt
        fc = ";".join(chain)
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", *inputs,
                        "-filter_complex", fc, "-map", prev, "-map", "0:a?",
                        "-c:a", "copy", "-r", "30", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", out_path], check=True, timeout=900)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mux_bgm(video_path: str, bgm_wav: str, out_path: str) -> str:
    ff = _ffmpeg()
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", video_path, "-i", bgm_wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_path],
                   check=True, timeout=300)
    return out_path


# ======================================================================
# AIナレーション（narration-v68）：ElevenLabs TTS ＋ シーン同期合成 ＋ 冒頭表紙
#   設計思想：後処理の速度調整（atempo等）で辻褄を合わせない。原稿側で尺に収める。
#   ★ATEMPO/SPEED変更は本ファイルに一切書かない（受入基準の禁止事項）。
# ======================================================================
_ELEVEN_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
_ELEVEN_MODEL = "eleven_multilingual_v2"


def narration_env_ready() -> dict:
    """ElevenLabs のキー/ボイスIDが env に在るか（値は返さない＝UI/ログに秘匿情報を出さない）。"""
    return {"key": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "voice": bool(os.environ.get("ELEVENLABS_VOICE_ID"))}


def tts_elevenlabs(text: str, out_path: str, timeout: int = 60) -> str:
    """1シーンぶんのテキストを ElevenLabs で音声化して out_path(mp3) に保存。
    キー/ボイスIDは env からのみ取得し、例外やログに載せない（秘匿情報保護）。"""
    if requests is None:
        raise RuntimeError("requests が利用できません。")
    key = os.environ.get("ELEVENLABS_API_KEY")
    voice = os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        raise RuntimeError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID が未設定です。")
    try:
        resp = requests.post(
            _ELEVEN_ENDPOINT.format(voice=voice),
            headers={"xi-api-key": key, "accept": "audio/mpeg", "content-type": "application/json"},
            json={"text": text, "model_id": _ELEVEN_MODEL,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=timeout)
    except Exception as e:  # noqa: BLE001  ★キーを含みうる例外詳細は握って型のみ露出
        raise RuntimeError(f"TTSリクエスト失敗（{type(e).__name__}）") from None
    if resp.status_code != 200:
        # ★レスポンス本文にキーは出ないが、URL等の巻き込みを避け status のみ通知
        raise RuntimeError(f"TTS失敗 HTTP {resp.status_code}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


def tts_timestamps_probe(text: str, timeout: int = 60) -> dict:
    """★v78字幕同期の疎通確認用。ElevenLabs with-timestamps を叩き、生JSON(alignment等)を返す。
    本番の鍵/ボイス(HIRO)/合成設定(現行と同一)を使用。日本語で文字/単語どちらのタイムスタンプが返るか、
    要素数が入力文字列長と一致するか、句読点・数字がどう扱われるかを実レスポンスで確定するため。
    ★キーは戻り値にもログにも載せない。audio_base64 は巨大かつ解析に不要なので長さ表記へ置換。"""
    if requests is None:
        raise RuntimeError("requests が利用できません。")
    key = os.environ.get("ELEVENLABS_API_KEY")
    voice = os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        raise RuntimeError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID が未設定です。")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    try:
        resp = requests.post(
            url,
            headers={"xi-api-key": key, "accept": "application/json", "content-type": "application/json"},
            json={"text": text, "model_id": _ELEVEN_MODEL,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=timeout)
    except Exception as e:  # noqa: BLE001  ★キーを含みうる例外詳細は握って型のみ露出
        raise RuntimeError(f"疎通リクエスト失敗（{type(e).__name__}）") from None
    if resp.status_code != 200:
        raise RuntimeError(f"疎通失敗 HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise RuntimeError("疎通: JSONで返りませんでした（with-timestamps非対応の可能性）") from None
    if isinstance(data.get("audio_base64"), str):     # 音声本体は解析に不要・巨大なので長さのみ
        data["audio_base64"] = f"<omitted base64: {len(data['audio_base64'])} chars>"
    return data


def _scene_start_times(durs, t, cover_sec=0.0, flash_delay=0.0):
    """メモリ安全連結タイムライン上での各シーン可視開始時刻(秒)。
      body内: start_i = Σ(durs[:i]) − i×t （i=0→0）。
      表紙cover_sec>0: シーン0のナレは表紙の上から（=0）、i>=1 は cover_sec だけ後ろへ。
      冒頭極短フラッシュ使用時（表紙なし）: シーン0を flash_delay だけ遅らせる。"""
    starts = []
    for i in range(len(durs)):
        body = sum(durs[:i]) - i * t
        if cover_sec > 0:
            starts.append(0.0 if i == 0 else cover_sec + body)
        else:
            starts.append((flash_delay if i == 0 else 0.0) + body)
    return starts


def _cover_clip(cover_bytes: bytes, seconds: float, out_w: int, out_h: int, out_path: str) -> str:
    """表紙PNGを seconds 秒の静止クリップに（本編セグメントと同一パラメータ＝30fps/yuv420p/sar=1/同寸）。
    concat demuxer(-c copy)で本編前に安全に連結できるよう _ENC で揃える。"""
    ff = _ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(cover_bytes)
        img = f.name
    try:
        subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-i", img,
                        "-t", f"{max(seconds, 0.5):g}", "-filter_complex",
                        (f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                         f"crop={out_w}:{out_h},fps=30,format=yuv420p,setsar=1[v]"),
                        "-map", "[v]", "-an", *_ENC, out_path], check=True, timeout=120)
    finally:
        try:
            os.unlink(img)
        except Exception:  # noqa: BLE001
            pass
    return out_path


def _prepend_clip(head_path: str, body_path: str, out_path: str) -> str:
    """head_path を body_path の前に連結（concat demuxer・-c copy・同一パラメータ前提）。"""
    ff = _ffmpeg()
    workdir = tempfile.mkdtemp(prefix="pre_")
    try:
        listfile = os.path.join(workdir, "list.txt")
        with open(listfile, "w") as lf:
            for p in (head_path, body_path):
                lf.write("file '%s'\n" % p.replace("'", "'\\''"))
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", listfile, "-c", "copy", "-movflags", "+faststart", out_path],
                       check=True, timeout=300)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _mux_narration(video_path: str, audio_starts, out_path: str,
                   bgm_wav: str = None, bgm_db: float = -20.0) -> str:
    """各シーン音声を開始時刻に adelay で配置→amix して動画に付ける。★速度変更（atempo）は使わない。
    audio_starts: [(audio_path, start_sec), ...]。bgm_wav 指定時は BGM を bgm_db で下げて混ぜる。"""
    ff = _ffmpeg()
    vdur = _dur(video_path)
    inputs = ["-i", video_path]
    parts = []          # amix入力ラベル
    filt = []
    idx = 1             # 0=video
    for apath, start in audio_starts:
        inputs += ["-i", apath]
        ms = max(0, int(round(float(start) * 1000)))
        # ステレオ両ch遅延。amix前に音量素通し（正規化は amix 側で無効化）
        filt.append(f"[{idx}:a]adelay={ms}|{ms},aformat=channel_layouts=stereo[a{idx}]")
        parts.append(f"[a{idx}]")
        idx += 1
    if bgm_wav:
        inputs += ["-i", bgm_wav]
        filt.append(f"[{idx}:a]volume={bgm_db}dB,aformat=channel_layouts=stereo[bg]")
        parts.append("[bg]")
        idx += 1
    # duration=longest で全ナレを保持し、apad→atrim で音声トラックを動画尺ちょうどに揃える
    #   （first だと最初の1本の長さに切り詰められる／BGMが動画より長い場合はここでトリム）。
    filt.append("".join(parts)
                + f"amix=inputs={len(parts)}:normalize=0:duration=longest,"
                + f"apad,atrim=0:{vdur:.3f}[mix]")
    subprocess.run([ff, "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filt),
                    "-map", "0:v", "-map", "[mix]", "-c:v", "copy", "-c:a", "aac",
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
# 動画の向き → 出力寸法（_normalize_clip の out_w/out_h に渡す）
ASPECT_DIMS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


# ======================================================================
# 表紙特大（P1b-2）：タイトル大見出しの静止画（PNG）。動画には一切挿入しない。
#   ・リールのカバー画像／カルーセル1枚目に使う面。冒頭離脱を避けるため本編には入れない。
#   ・数値（徒歩分・㎡・間取り）は必ず facts 由来を渡すこと（LLM出力から数値を持ち込まない）。
# ======================================================================
COVER_DIMS = {"9:16": (1080, 1920), "4:5": (1080, 1350)}  # リールカバー / カルーセル1枚目


def _wrap_jp(text: str, max_chars: int, max_lines: int = 2) -> list:
    """日本語向けの素朴な折返し（max_chars文字ごとに改行）。空白があればそこで優先的に折る。
    max_lines を超えた分は最終行に … を付けて切り詰め（safe-zone内に収めるため）。"""
    text = (text or "").strip()
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= max_chars and (ch == " " or len(cur) >= max_chars + 4):
            lines.append(cur.strip())
            cur = "" if ch == " " else ch
        else:
            cur += ch
    if cur.strip():
        lines.append(cur.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max_chars - 1].rstrip() + "…"
    return lines


# PIL計測は build_cover が _font() から得た「ffmpeg描画と同一ファイル」を渡す前提。
# PIL・ffmpeg(libfreetype) はともに .ttc の face index 0 を使うため同一face。
# 実測：ffmpeg実描画幅 ≈ PIL getlength（誤差<0.5%・PIL側がわずかに大きい＝安全側）。
# 本番Boldでも同経路なので、_font()がBoldを返せばBold幅で計測される（同一ファイル保証）。
_FIT_SAFETY = 0.97   # 影(shadowx)・ヒンティング差を吸収する安全マージン


def _text_width(font_path: str, text: str, size: int) -> float:
    """指定フォント(=_font()結果)・サイズでの描画幅(px)。face index 0 で計測（ffmpegと一致）。
    フォント読込失敗時は 全角1em 想定の保守的上界（縮小方向＝文字切れしない側）にフォールバック。"""
    if font_path:
        try:
            from PIL import ImageFont
            return ImageFont.truetype(font_path, size, index=0).getlength(text)
        except Exception:  # noqa: BLE001
            pass
    return len(text) * size  # フォント解決不能時：全角1em想定で安全側（上界）


def _fit_size(font_path: str, lines: list, max_w: int, base: int, min_size: int = 48) -> int:
    """全行が max_w(px)×安全率 内に収まる最大サイズを base から段階縮小で求める（文字切れ防止）。"""
    limit = max_w * _FIT_SAFETY
    size = base
    while size > min_size and any(_text_width(font_path, ln, size) > limit for ln in lines if ln):
        size -= 4
    return size


def _ellipsize(font_path: str, text: str, size: int, max_w: int) -> str:
    """幅 max_w に収まらなければ末尾を削り … を必ず付ける（無言で切らない＝最後の手段）。"""
    text = (text or "").strip()
    if not text or _text_width(font_path, text, size) <= max_w * _FIT_SAFETY:
        return text
    ell = "…"
    while text and _text_width(font_path, text + ell, size) > max_w * _FIT_SAFETY:
        text = text[:-1]
    return (text + ell) if text else ell


def _cover_scrim_png(W: int, H: int, header_bottom: int, footer_top: int,
                     top_a: float = 0.55, bot_a: float = 0.62, fade: int = 140) -> str:
    """上下だけを暗くする縦グラデ暗幕(RGBA黒)のPNGを生成しパスを返す。中央は透明＝写真素のまま。
    ハードな帯を避けるため、テキスト帯の外側へ fade でなだらかに落とす。"""
    from PIL import Image
    y = np.arange(H)
    top = np.zeros(H)
    if header_bottom > 0:
        hb = min(header_bottom, H)
        top = np.where(y <= hb, top_a, top_a * np.clip(1 - (y - hb) / fade, 0, 1))
    bot = np.zeros(H)
    if footer_top < H:
        ft = max(footer_top, 0)
        bot = np.where(y >= ft, bot_a, bot_a * np.clip(1 - (ft - y) / fade, 0, 1))
    alpha = np.maximum(top, bot)
    arr = np.zeros((H, W, 4), dtype=np.uint8)
    arr[..., 3] = (alpha[:, None] * 255).astype(np.uint8)     # 黒(RGB=0)・alphaは行ごと
    fd, path = tempfile.mkstemp(suffix="_scrim.png")
    os.close(fd)
    Image.fromarray(arr, "RGBA").save(path)
    return path


# ======================================================================
# 表紙テンプレv2「雑誌型（マガジンカバー）」magcover-v69
#   承認サンプル(表紙A_VISAGE)を再現：部屋写真が主役・黒帯なし・下部クリーム覆い・明朝マスト/コピー。
#   字間はPILに無いため1文字ずつ描画で実装。
# ======================================================================
_MAG_INK = (36, 36, 34)       # #242422 本文インク
_MAG_SUB = (74, 70, 66)       # #4a4642 サブ
_MAG_GOLD = (178, 148, 88)    # #b29458 金差し色
_MAG_CREAM = (247, 244, 239)  # #f7f4ef 下部覆い


def _mag_span(font, text, ls):
    """字間ls込みの総幅(px)。"""
    if not text:
        return 0.0
    return sum(font.getlength(c) for c in text) + ls * (len(text) - 1)


def _mag_draw_spaced(draw, text, font, cx, y, ls, fill):
    """字間ls付きで中央cxに1文字ずつ描画。"""
    x = cx - _mag_span(font, text, ls) / 2
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + ls


def _mag_fit(font_path, text, ls, max_w, base, min_size):
    """字間ls込みで max_w に収まる最大サイズ（base→min_size・2刻み）。"""
    from PIL import ImageFont
    size = base
    while size > min_size:
        f = ImageFont.truetype(font_path, size)
        if _mag_span(f, text, ls) <= max_w:
            return f, size
        size -= 2
    return ImageFont.truetype(font_path, min_size), min_size


def _mag_yen(s):
    """賃料/管理費 → '¥12,345'（数字とカンマのみ抽出・空なら''）。
    ★数字以外(漢数字等)が混入したら core.money_yen が ValueError＝表紙描画を止める（沈黙破損の防止）。"""
    import core
    return core.money_yen(s)


def build_cover_magazine(image_bytes: bytes, fields: dict, aspect: str = "9:16") -> bytes:
    """雑誌型（マガジンカバー）表紙PNG。部屋写真が主役・黒帯なし・下部クリーム覆い。
    fields: masthead(誌名) / subline(エリア·No) / copy(キャッチ) /
            madori・area_sqm・station・walk・rent・fee(スペック用) / note(AI注記)。
    ★賃料には必ず管理費を併記（コードで構造保証）。★物件名・内部語は出さない（呼び出し側で除去）。"""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from io import BytesIO
    W, H = COVER_DIMS.get(aspect, COVER_DIMS["9:16"])
    serif = _serif_font()
    gothic = _font() or serif

    # 素材を fill（force_original_aspect_ratio=increase + crop 相当）
    base = Image.open(BytesIO(image_bytes)).convert("RGB")
    base = ImageOps.fit(base, (W, H), method=Image.LANCZOS)

    # 下部クリームの羽根ぼかしグラデ（黒禁止・alpha 0→228・smoothstep・y1330→1515）
    y0, y1 = int(H * 0.693), int(H * 0.789)               # 9:16→1330/1515。比率で他サイズも追従
    ramp = np.clip((np.arange(H) - y0) / max(1, (y1 - y0)), 0, 1)
    ss = ramp * ramp * (3 - 2 * ramp)                     # smoothstep
    alpha = (ss * 228).astype(np.uint8)
    ov = np.zeros((H, W, 4), dtype=np.uint8)
    ov[:, :, 0], ov[:, :, 1], ov[:, :, 2] = _MAG_CREAM
    ov[:, :, 3] = alpha[:, None]
    canvas = base.convert("RGBA")
    canvas.alpha_composite(Image.fromarray(ov, "RGBA"))
    draw = ImageDraw.Draw(canvas)

    def _region_lum(y_top, h):
        reg = np.asarray(canvas.convert("RGB"))[y_top:y_top + h, int(W * 0.1):int(W * 0.9)]
        return float(reg.mean()) if reg.size else 255.0

    def _auto_pair(y_top, h, sub_default):
        """帯の輝度で (本色, 影色 or None) を返す。明→ink/影なし、暗→白/黒影。"""
        if _region_lum(y_top, h) <= 140:
            return (255, 255, 255), (0, 0, 0)
        return sub_default, None

    # ① マスト（誌名・明朝・字間14・中央・上部・幅880に自動縮小・暗写真は白+影）
    mast = (fields.get("masthead") or "OSAKA ROOMS").strip()
    my = int(H * 0.198)                                   # ≒380
    mf, ms = _mag_fit(serif, mast, 14, 880, 76, 40)
    _mcol, _msh = _auto_pair(my, ms, _MAG_INK)
    if _msh:
        _mag_draw_spaced(draw, mast, mf, W / 2 + 2, my + 2, 14, _msh)
    _mag_draw_spaced(draw, mast, mf, W / 2, my, 14, _mcol)
    # ③ 金の細罫線（マスト直下・短く／両輝度で視認できる金）
    rule_w = min(320, _mag_span(mf, mast, 14))
    ry = my + ms + 16
    draw.rectangle([W / 2 - rule_w / 2, ry, W / 2 + rule_w / 2, ry + 2], fill=_MAG_GOLD)
    # ② サブライン（ゴシック・字間6・中央・暗写真は白）
    sub = (fields.get("subline") or "").strip()
    if sub:
        sf, _ = _mag_fit(gothic, sub, 6, 900, 32, 20)
        _scol = (255, 255, 255) if _mcol == (255, 255, 255) else _MAG_SUB
        _mag_draw_spaced(draw, sub, sf, W / 2, ry + 22, 6, _scol)

    # ④ コピー（明朝・大・中央・下部・幅1000に自動縮小 80→44）
    copy = (fields.get("copy") or "").strip()
    cy = int(H * 0.750)                                   # ≒1440
    if copy:
        cf, cs = _mag_fit(serif, copy, 2, 1000, 80, 44)
        # 暗い写真フォールバック：コピー帯の合成後平均輝度で ink / 白+影 を自動切替
        reg = np.asarray(canvas.convert("RGB"))[cy:cy + cs, int(W * 0.1):int(W * 0.9)]
        lum = float(reg.mean()) if reg.size else 255.0
        if lum <= 140:
            _mag_draw_spaced(draw, copy, cf, W / 2 + 2, cy + 2, 2, (0, 0, 0))   # 薄い影
            _mag_draw_spaced(draw, copy, cf, W / 2, cy, 2, (255, 255, 255))
        else:
            _mag_draw_spaced(draw, copy, cf, W / 2, cy, 2, _MAG_INK)
        cy_bottom = cy + cs
    else:
        cy_bottom = cy

    # ⑤ スペック2行（ゴシック・中央）★管理費を必ず併記
    madori = (fields.get("madori") or "").split("[")[0].strip()
    sqm = re.sub(r"[^\d.]", "", str(fields.get("area_sqm") or ""))
    station = (fields.get("station") or "").strip()
    walk = str(fields.get("walk") or "").strip()
    parts = []
    if madori:
        parts.append(madori)
    if sqm:
        parts.append(f"{sqm}㎡")
    if station and walk:
        parts.append(f"{station} 徒歩{walk}分")
    spec1 = "  /  ".join(parts)
    rent, fee = _mag_yen(fields.get("rent")), _mag_yen(fields.get("fee"))
    if rent and fee:
        spec2 = f"{rent}  +  管理費 {fee}  /  月"
    elif rent:
        spec2 = f"{rent}（管理費は要確認）  /  月"
    else:
        spec2 = ""
    sy = max(cy_bottom + 30, int(H * 0.826))              # ≒1585
    if spec1:
        s1f, _ = _mag_fit(gothic, spec1, 3, 980, 40, 26)
        _mag_draw_spaced(draw, spec1, s1f, W / 2, sy, 3, _MAG_INK)
        sy += int(s1f.size * 1.5)
    if spec2:
        s2f, _ = _mag_fit(gothic, spec2, 2, 980, 33, 22)
        _mag_draw_spaced(draw, spec2, s2f, W / 2, sy, 2, _MAG_SUB)

    # ⑥ AI注記（右下・小・法令必須・ハードコード既定）
    note = (fields.get("note") or "※家具・小物はAI生成のイメージ").strip()
    nf = ImageFont.truetype(gothic, 24)
    draw.text((W - nf.getlength(note) - 36, H - 56), note, font=nf, fill=_MAG_SUB)

    buf = BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def build_cover(image_bytes: bytes, fields: dict, aspect: str = "9:16",
                style: str = "default") -> bytes:
    """素材画像＋fields から『表紙特大』の静止画 PNG を生成して返す（動画には挿入しない）。

    fields:
      title        : 大見出し（特大・最大2行に自動折返し）
      subtitle     : 補足（小・タイトルの上）
      highlights   : ◎魅力ポイント（最大3・箇条／facts/PRコピー由来）
      access_band  : 駅徒歩band（例 "◯◯線「◯◯駅」徒歩◯分"／取れなければ空で省略）
      madori_area  : 間取り／面積（特大・例 "2LDK 57.64㎡"／facts由来）
      note         : 右下の注記（例 "※AI加工のイメージ"／景表法配慮）
    レイヤ（上→下）：subtitle小 → title特大 →（下詰め）highlights → access_band → madori特大。
    素材は fill（force_original_aspect_ratio=increase + crop）で覆い、可読性のため上下に暗幕を敷く。
    """
    ff = _ffmpeg()
    font = _font()
    fontref = f"fontfile='{font}'" if font else "font='Noto Sans CJK JP'"
    W, H = COVER_DIMS.get(aspect, COVER_DIMS["9:16"])

    title = (fields.get("title") or "").strip()
    subtitle = (fields.get("subtitle") or "").strip()
    highlights = [str(h).strip() for h in (fields.get("highlights") or [])
                  if h and str(h).strip()][:3]
    band = (fields.get("access_band") or "").strip()
    madori_area = (fields.get("madori_area") or "").strip()
    note = (fields.get("note") or "").strip()

    # フォントサイズ（1080幅基準）／余白（safe-zone）
    S_SUB, S_HL, S_BAND, S_NOTE = 40, 36, 40, 26
    S_TITLE_BASE, S_MADORI_BASE = 92, 88
    LH = 1.22
    M_TOP, M_BOTTOM, M_SIDE = 160, 150, 90
    MAX_W = W - 2 * M_SIDE                 # テキスト最大幅（safe-zone・文字切れ防止）
    MAX_TITLE_CHARS = 10

    # 特大2種は幅に収まるサイズへ自動フィット（PIL計測・不能時は保守推定）
    title_lines = _wrap_jp(title, MAX_TITLE_CHARS, max_lines=2)
    s_title = _fit_size(font, title_lines, MAX_W, S_TITLE_BASE) if title_lines else S_TITLE_BASE
    s_madori = _fit_size(font, [madori_area], MAX_W, S_MADORI_BASE) if madori_area else S_MADORI_BASE

    # ヘッダー（上詰め）：subtitle小 → title特大（最大2行）
    header = []
    if subtitle:
        header.append((subtitle, S_SUB))
    for ln in title_lines:
        header.append((ln, s_title))

    # フッター（下詰め・上から highlights → band → madori特大）
    footer = []
    for h in highlights:
        footer.append((h, S_HL))
    if band:
        footer.append((band, S_BAND))
    if madori_area:
        footer.append((madori_area, s_madori))

    # y座標（ヘッダーは上から、フッターは下から積む）
    y = M_TOP
    header_pos = []
    for txt, size in header:
        header_pos.append((txt, size, y))
        y += int(size * LH)
    header_bottom = y

    y = H - M_BOTTOM
    footer_pos = []
    for txt, size in reversed(footer):
        y -= int(size * LH)
        footer_pos.append((txt, size, y))
    footer_top = min([p[2] for p in footer_pos], default=H)

    # 可読性の暗幕：ハードな帯ではなく、写真に重なる縦グラデ（上/下だけ暗く・中央は素の写真）
    grad_png = _cover_scrim_png(W, H, header_bottom if header_pos else 0,
                                footer_top if footer_pos else H)

    # テキスト（最後の手段として幅に収まらなければ … で切る＝無言で切らない）
    def _dt(txt, size, yy):
        t = _ellipsize(font, txt, size, MAX_W)
        return (f"drawtext={fontref}:text='{_esc(t)}':fontcolor=white@0.94:fontsize={size}:"
                f"shadowcolor=black@0.55:shadowx=2:shadowy=3:x=(w-text_w)/2:y={yy}")

    draws = [_dt(txt, size, yy) for txt, size, yy in header_pos + footer_pos]
    if note:                                    # 注記：幅内に収め右下に1回だけ（見切れ防止）
        note_fit = _ellipsize(font, note, S_NOTE, MAX_W)
        draws.append(
            f"drawtext={fontref}:text='{_esc(note_fit)}':fontcolor=white@0.88:fontsize={S_NOTE}:"
            f"box=1:boxcolor=black@0.40:boxborderw=10:x=w-text_w-36:y=h-64")
    chain = "[base]" + (",".join(draws) if draws else "null") + "[v]"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        src = f.name
    out = src[:-4] + "_cover.png"
    try:
        # 素材を fill で完全被覆（increase+crop）→ グラデ暗幕を overlay → テキスト
        subprocess.run(
            [ff, "-y", "-loglevel", "error", "-i", src, "-i", grad_png,
             "-filter_complex",
             (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H}[bg];[bg][1:v]overlay=0:0[base];{chain}"),
             "-map", "[v]", "-frames:v", "1", out],
            check=True, timeout=120)
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        for p in (src, out, grad_png):
            try:
                os.unlink(p)
            except Exception:  # noqa: BLE001
                pass


# ======================================================================
# ★v79「動く雑誌」レイアウト（権威=OSAKA_ROOMSサンプル scripts/gen.py,ov.py,feat23.py の座標に忠実）
#   1080×1920・明朝Bold特大＋キーワードaccent＋固定マストヘッド OSAKA ROOMS＋下部情報バー（常時）。
# ======================================================================
_V79_GOLD = (232, 196, 104)
_V79_ROSE = (228, 170, 168)
_V79_SAGE = (168, 205, 172)
_V79_WHITE = (255, 255, 255)
_V79_GREY = (200, 200, 200)
_V79_NOTE = (150, 150, 150)


def _v79_serif(size):
    from PIL import ImageFont
    return ImageFont.truetype(_serif_bold_font()[0], size)


def _v79_sans_b(size):
    from PIL import ImageFont
    return ImageFont.truetype(_sans_bold_font()[0], size)


def _v79_sans_r(size):
    from PIL import ImageFont
    return ImageFont.truetype(_font() or _sans_bold_font()[0], size)


def _v79_shadow_text(canvas, xy, text, font, fill, anchor="mm", blur=8, ox=4, oy=6):
    """影付きテキスト（gen.py shadow_text 準拠）：影(0,0,0,180)を(ox,oy)ずらしてblur→本文を上に描く。
    canvas=RGBA Image。空文字は無視。"""
    from PIL import Image, ImageDraw, ImageFilter
    if not (text and str(text).strip()):
        return
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(lay).text((xy[0] + ox, xy[1] + oy), text, font=font,
                             fill=(0, 0, 0, 180), anchor=anchor)
    canvas.alpha_composite(lay.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(canvas).text(xy, text, font=font, fill=fill, anchor=anchor)


def _v79_gradient(canvas, y0, y1, a0, a1):
    """暗幕グラデ (8,8,10) を y0→y1 で α a0→a1 に線形（gen.py gradient 準拠）。canvas=RGBA。"""
    from PIL import Image
    W, H = canvas.size
    ramp = np.clip((np.arange(H) - y0) / max(1, (y1 - y0)), 0, 1)
    alpha = (a0 + (a1 - a0) * ramp).astype(np.uint8)
    ov = np.zeros((H, W, 4), dtype=np.uint8)
    ov[:, :, 0], ov[:, :, 1], ov[:, :, 2] = (8, 8, 10)
    ov[:, :, 3] = alpha[:, None]
    canvas.alpha_composite(Image.fromarray(ov, "RGBA"))


def _v79_masthead(canvas, accent=_V79_GOLD):
    """★固定マストヘッド：OSAKA ROOMS（字間・SERIF66px y205）＋ISSUE行（30px y268）＋accentライン（y308）。"""
    from PIL import ImageDraw
    W = canvas.size[0]
    _v79_shadow_text(canvas, (W // 2, 205), "O S A K A   R O O M S", _v79_serif(66), _V79_WHITE, blur=6)
    _v79_shadow_text(canvas, (W // 2, 268), "ISSUE 01  /  OSAKA・FUKUSHIMA", _v79_sans_r(30), _V79_GREY, blur=6)
    ImageDraw.Draw(canvas).rectangle([W // 2 - 170, 306, W // 2 + 170, 310], fill=accent)


def _v79_infobar(canvas, spec_line, equip_line, note_line):
    """★下部情報バー（全ビート/表紙 常時3行）：スペック＋家賃管理費併記（SANS_B38px y1755）／
    設備（SANS_R30px y1815）／AI注記＋時点注記（SANS_R26px y1872・(150,150,150)）。中央 anchor=mm。
    ★家賃には管理費を必ず併記（呼出側で構造保証＝rentguard資産）。"""
    W = canvas.size[0]
    if spec_line:                                      # ★fit-to-width：38→26縮小・下限で｜折返し2行（左右60px内）
        _sf, _slines = _v79_fit_font(spec_line, _v79_sans_b, W - 120, 38, 26)
        if len(_slines) <= 1:
            _v79_shadow_text(canvas, (W // 2, 1755), spec_line, _sf, _V79_WHITE, blur=6)
        else:                                          # 下限でも溢れ＝｜で2行（上へ詰めて設備行と干渉回避）
            for _i, _ln in enumerate(_slines):
                _v79_shadow_text(canvas, (W // 2, 1735 + _i * 44), _ln, _sf, _V79_WHITE, blur=6)
    _v79_shadow_text(canvas, (W // 2, 1815), equip_line, _v79_sans_r(30), _V79_GREY, blur=6)
    _v79_shadow_text(canvas, (W // 2, 1872), note_line, _v79_sans_r(26), _V79_NOTE, blur=6)


def _v79_fit_serif(text, base=96, min_size=64, max_w=1000):
    """特大明朝を max_w に収める（長い big_text の溢れ防止・権威は96px固定だが安全網）。"""
    from PIL import ImageDraw, Image
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for s in range(base, min_size - 1, -4):
        f = _v79_serif(s)
        if d.textlength(text, font=f) <= max_w:
            return f
    return _v79_serif(min_size)


def _v79_room_pill(canvas, label, y0=368):
    """★部屋ラベルピル（ov.py label()）：角丸ボックス(20,20,24,150)＋白枠(255,255,255,180)w2＋
    SANS_B40px WHITE。箱 y0=368→432、テキストは y0+32。"""
    from PIL import ImageDraw
    W = canvas.size[0]
    f = _v79_sans_b(40)
    d = ImageDraw.Draw(canvas)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle([W / 2 - tw / 2 - 28, y0, W / 2 + tw / 2 + 28, y0 + 64],
                        radius=8, fill=(20, 20, 24, 150), outline=(255, 255, 255, 180), width=2)
    _v79_shadow_text(canvas, (W // 2, y0 + 32), label, f, _V79_WHITE, blur=4)


def _v79_tag_pills(canvas, tags, accent=_V79_GOLD, x=110, y0=700, gap=100):
    """★追加情報タグ（角丸ダーク半透明＋左端 accent バー＋白Sans38px）。最大3・中央左余白に縦積み。
    ★静的描画（stamp-in演出＝anim2.py tag_sprite は v79-6）。映像の見どころ（中央）は塞がない。"""
    from PIL import ImageDraw
    d = ImageDraw.Draw(canvas)
    f = _v79_sans_b(38)
    for i, t in enumerate([str(x) for x in (tags or []) if x and str(x).strip()][:3]):
        y = y0 + i * gap
        tw = d.textlength(t, font=f)
        d.rounded_rectangle([x, y, x + 30 + tw + 24, y + 64], radius=10, fill=(20, 20, 24, 180))
        d.rectangle([x, y, x + 6, y + 64], fill=accent)          # 左端 accent バー
        _v79_shadow_text(canvas, (x + 30, y + 32), t, f, _V79_WHITE, anchor="lm", blur=3)


def _v79_split_accent(big_text, accent_word):
    """big_text を accent_word の直前の句読点で2行に割る。l1=白（accent前）／l2=accent色（accent_word以降）。
    accent_word が無い/含まれない→ (big_text, '')。"""
    big = (big_text or "").strip()
    aw = (accent_word or "").strip()
    if not aw or aw not in big:
        return big, ""
    idx = big.find(aw)
    cut = max(big.rfind("、", 0, idx), big.rfind("。", 0, idx), big.rfind("／", 0, idx),
              big.rfind("　", 0, idx)) + 1
    return big[:cut], big[cut:]


def _v79_wrap_width(text, font, max_w, max_lines=2):
    """★comment等を描画幅max_wで折返す（最大max_lines行・フォント縮小しない＝雑誌質感維持）。CJKは文字単位。
    各行は幅に収まる最長prefixを取り、句読点(、/。)があればそこで優先的に割る。最終行に残りを詰める（…省略しない）。"""
    from PIL import ImageDraw, Image
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    rest = str(text or "").strip()
    if not rest:
        return []
    lines = []
    for li in range(max_lines):
        if li == max_lines - 1:                       # 最終行＝残り全部（改行で対応・省略しない）
            lines.append(rest)
            rest = ""
            break
        n = 0                                          # 幅に収まる最大文字数
        while n < len(rest) and d.textlength(rest[:n + 1], font=font) <= max_w:
            n += 1
        if n >= len(rest):
            lines.append(rest)
            rest = ""
            break
        cut = max(rest.rfind("、", 0, n), rest.rfind("。", 0, n),   # n以内の最後の区切りで優先分割
                  rest.rfind("｜", 0, n), rest.rfind("／", 0, n))    # 情報バーは｜/／単位も
        cut = cut + 1 if cut > 0 else n
        lines.append(rest[:cut])
        rest = rest[cut:]
        if not rest:
            break
    return [ln for ln in lines if ln]


def _v79_fit_font(text, size_fn, max_w, base, min_size, step=4):
    """★fit-to-width（見切れ防止の本命）：text が max_w に収まる最大フォントを base→min_size で探す。
    収まれば (font, [text])＝1行。min_size でも溢れれば (min_font, 幅折返し2行)。size_fn=_v79_serif/_v79_sans_b 等。
    ★どんな入力（空/分割不能/極端）でも例外を投げず必ず有効な (font, 非空lines) を返す＝テキスト欠落を作らない。"""
    from PIL import ImageDraw, Image
    t = str(text or "")
    try:
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        for s in range(base, min_size - 1, -step):
            f = size_fn(s)
            if d.textlength(t, font=f) <= max_w:
                return f, [t]
        fmin = size_fn(min_size)
        return fmin, (_v79_wrap_width(t, fmin, max_w, max_lines=2) or [t])
    except Exception:  # noqa: BLE001  ★fit失敗でもテキストを消さない＝基準サイズで描く（多少はみ出しても描く方を優先）
        return size_fn(base), [t]


def build_beat_overlay(room_label, big_text, accent_word, comment, *, tags=None,
                       accent=_V79_GOLD, spec_line="", equip_line="", note_line="",
                       aspect="9:16") -> bytes:
    """★v79 ビート文字面（透明PNG・背景=Kling映像に重ねる）。権威=ov.py の座標。
    big_text の accent_word を色分けし2行に割る（l1=白 y1330 ／ l2=accent色 y1465）。単一行なら 1330白＋comment上げ。
    comment: 2行時 y1590 ／ 1行時 y1470（SANS_R42px GREY）。タグ（最大3・左余白）＋マストヘッド＋情報バー常時。
    返り値=PNG bytes(RGBA・透明背景)。"""
    from PIL import Image
    from io import BytesIO
    W, H = COVER_DIMS.get(aspect, COVER_DIMS["9:16"])
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _v79_gradient(canvas, 0, 460, 190, 0)         # 上グラデ（ビート面 0→460）
    _v79_gradient(canvas, 1200, H, 0, 235)        # 下グラデ
    _v79_masthead(canvas, accent)
    _v79_room_pill(canvas, room_label)
    _v79_tag_pills(canvas, tags, accent=accent)   # 追加情報タグ（最大3・左余白・静的）
    # ★big_text（金色スペック見出し）fit-to-width：l1(白)/l2(accent)を各 fit-or-wrap（96→56縮小・下限で折返し）→
    #   左右60px内に必ず収める（物件により語数が変わっても見切れゼロ）。縦は font に応じ動的に積む（権威1330/1465相当）。
    _MAXW = W - 120
    l1, l2 = _v79_split_accent(big_text, accent_word)
    _bt = []                                          # (line, font, color)
    if l1:
        _f1, _ls1 = _v79_fit_font(l1, _v79_serif, _MAXW, 96, 56)
        _bt += [(ln, _f1, _V79_WHITE) for ln in _ls1]
    if l2:
        _f2, _ls2 = _v79_fit_font(l2, _v79_serif, _MAXW, 96, 56)
        _bt += [(ln, _f2, accent) for ln in _ls2]
    # ★magfit-v79c③：白サブ文言(comment/ナレ)は描画しない（音声TTSは別・run_tour_jobでmux）。
    #   commentが消えた分、big_text ブロックを中心≈1470へ縦センタリング（縦位置が上に浮かない）。
    _lh = [int(getattr(_f, "size", 96) * 1.4) for _, _f, _ in _bt]
    _y = max(1300, 1470 - sum(_lh) // 2)
    for (_ln, _f, _col), _h in zip(_bt, _lh):
        _v79_shadow_text(canvas, (W // 2, _y), _ln, _f, _col)
        _y += _h                                      # ★.size欠落フォントでも落ちない（テキスト保全）
    if spec_line or equip_line or note_line:
        _v79_infobar(canvas, spec_line, equip_line, note_line)
    buf = BytesIO()
    canvas.save(buf, "PNG")                       # RGBA（透明背景を保持）
    return buf.getvalue()


def _v79_feature_label(canvas, feat_label, accent, y0=360):
    """★特集ラベル箱（feat23.py cover）：rectangle（角丸なし）y360-432・fill(20,20,24,170)・枠accent w3・
    SANS_B42px accent色・『特集　○○』。"""
    from PIL import ImageDraw
    W = canvas.size[0]
    text = f"特集　{feat_label}"
    f = _v79_sans_b(42)
    d = ImageDraw.Draw(canvas)
    tw = d.textlength(text, font=f)
    d.rectangle([W / 2 - tw / 2 - 36, y0, W / 2 + tw / 2 + 36, y0 + 72],
                fill=(20, 20, 24, 170), outline=accent, width=3)
    _v79_shadow_text(canvas, (W // 2, y0 + 36), text, f, accent, blur=4)


def build_cover_v79(image_bytes, *, feature_id="mote_heya", price="", price_sub="",
                    copy1="", copy2="", area_line="", hook="",
                    spec_line="", equip_line="", note_line="",
                    layout="feature", aspect="9:16") -> bytes:
    """★v79 表紙（動く雑誌カバー・権威=feat23.py(特集版)/gen.py(price_hero)）。
    ★動画冒頭カバー=この表紙特大PNGと同一ソースに統一（1源2消費・配線はv79-3）。accentは feature の色。
    layout='feature'（標準・特集版）: 特集ラベル＋copy1(y1000)＋copy2(y1140)白＋price(y1330)accent＋price_sub(y1440)。
    layout='price_hero'（数字特大版オプション）: area_line(y1000)白＋price(y1180)accent＋price_sub(y1310)＋hook(y1470)白。
    返り値=PNG bytes(RGB・写真背景で不透明)。"""
    from PIL import Image, ImageOps
    from io import BytesIO
    W, H = COVER_DIMS.get(aspect, COVER_DIMS["9:16"])
    feat = None
    try:
        import core as _core
        feat = _core.feature_of(feature_id)
    except Exception:  # noqa: BLE001
        pass
    accent = tuple(feat["accent"]) if feat else _V79_GOLD
    base = ImageOps.fit(Image.open(BytesIO(image_bytes)).convert("RGB"), (W, H), method=Image.LANCZOS)
    canvas = base.convert("RGBA")
    _v79_gradient(canvas, 0, 520, 190, 0)          # cover上グラデ（0→520）
    _v79_gradient(canvas, 1200, H, 0, 235)
    _v79_masthead(canvas, accent)
    _v79_feature_label(canvas, feat["label"] if feat else "モテ部屋", accent)
    if layout == "price_hero":
        _v79_shadow_text(canvas, (W // 2, 1000), area_line, _v79_fit_serif(area_line, 96), _V79_WHITE)
        _v79_shadow_text(canvas, (W // 2, 1180), price, _v79_serif(190), accent)
        _v79_shadow_text(canvas, (W // 2, 1310), price_sub, _v79_sans_r(40), _V79_GREY)
        _v79_shadow_text(canvas, (W // 2, 1470), hook, _v79_fit_serif(hook, 72), _V79_WHITE)
    else:                                          # feature（標準・特集版）
        _v79_shadow_text(canvas, (W // 2, 1000), copy1, _v79_fit_serif(copy1, 104), _V79_WHITE)
        _v79_shadow_text(canvas, (W // 2, 1140), copy2, _v79_fit_serif(copy2, 104), _V79_WHITE)
        _v79_shadow_text(canvas, (W // 2, 1330), price, _v79_serif(150), accent)
        _v79_shadow_text(canvas, (W // 2, 1440), price_sub, _v79_sans_r(40), _V79_GREY)
    if spec_line or equip_line or note_line:
        _v79_infobar(canvas, spec_line, equip_line, note_line)
    buf = BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _v79_assert_resolution(img_w, img_h, min_w=1080, min_h=1920, factor=1.06):
    """★v79 画質アサート（フォールバック経路=編集系パン/ドリフト用）：オーバーレイ合成前の実効解像度が
    min×factor 以上か。不足＝パンで画質破綻（サンプルv10で実確認）。返り値 (ok, need_w, need_h)。
    主軸Klingは生成でモーション＝このアサートはフォールバック素材にのみ適用。"""
    need_w, need_h = int(min_w * factor), int(min_h * factor)
    return (img_w >= need_w and img_h >= need_h), need_w, need_h


def build_tour(images: list[tuple], *, captions: Optional[list] = None,
               sub_captions: Optional[list] = None,
               top_tag: str = "", with_captions: bool = True,
               with_bgm: bool = True, also_silent: bool = True,
               model_key: str = "kling2.6_pro", duration: int = 5,
               room_types: Optional[list] = None, image_note: str = "",
               notes: Optional[list] = None, still_flags: Optional[list] = None,
               taste: str = "clean", tastes: Optional[list] = None,
               positions: Optional[list] = None, flash_text: str = "",
               focals: Optional[list] = None, motions: Optional[list] = None,   # ★v79-4 focal主語/動き量
               negative_prompt: str = _V79_NEGATIVE, cfg_scale: Optional[float] = None,
               aspect: str = "9:16", fit_mode: str = "fill", flash_cut: bool = False,
               durations: Optional[list] = None, trims: Optional[list] = None,
               beat_ids: Optional[list] = None, progress=None) -> dict:
    """
    images: [(name, image_bytes), ...] 再生順
    captions: 各クリップ下部の文言（None かつ with_captions=True なら name を使用）
    room_types: 各画像の部屋種別キー（ROOM_PROMPTS のキー）。None は 'generic'
    image_note: 全クリップ共通の右下注記（優先）。空なら notes[i] を使う。
    notes: クリップ個別の右下注記（image_note が空のときに使用。例：ステージング/リノベで文言を分ける）
        ※ image_note も notes[i] も空なら注記なし（旧render_videoの挙動を維持）。
    still_flags: True のクリップは fal/Kling を通さず静止クリップにする（間取り図・3Dパース＝
        morph防止・fal課金なし）。fit_mode は contain 固定（図面全体を表示）。None は全クリップ通常。
    aspect: 動画の向き "9:16"（既定）/ "1:1" / "16:9"
    fit_mode: 余白の扱い "fill"（既定・余白ゼロ/端が切れる）/ "contain"（全体表示・余白あり）
    durations/trims/beat_ids: ★story-v78 A0 part2（ビート割り当て）。3つ揃うと「ビートモード」。
        durations[i]=クリップiの生成尺(秒・{5,10})／trims[i]=生成後に切り出す尺（尺切り出し＝速度変更でない）／
        beat_ids[i]=クリップiが属するビートid（連続する同idが1ビート）。
        連結は _assemble_beats（ビート内xfade0.6s・ビート境界ハードカット）→ 総尺=Σ(トリム尺−ビート内xfade)=Σナレ秒。
        ★3つのいずれかが None なら現行完全回帰（global duration＋全クリップ _xfade_concat）。
    progress: callable(step:int, total:int, msg:str) 進捗コールバック（任意）
    戻り値: {'silent': path, 'bgm': path, 'outdir': dir}（生成した版のみ・mp4はファイルパスで返す）。
        ※ 呼び出し側は open(path,'rb') で download_button に渡し、不要になったら outdir を掃除する。
    """
    n = len(images)
    if n == 0:
        raise ValueError("画像がありません。")
    out_w, out_h = ASPECT_DIMS.get(aspect, (1080, 1920))
    room_types = room_types or ["generic"] * n
    if captions is None:
        captions = [name for name, _ in images] if with_captions else [""] * n
    # ★ビートモード判定：3つ揃い、かつ全て長さnのときのみ。1つでも欠ける/長さ不一致なら現行回帰。
    _beat_mode = (durations is not None and trims is not None and beat_ids is not None
                  and len(durations) == n and len(trims) == n and len(beat_ids) == n)

    workdir = tempfile.mkdtemp(prefix="tour_")
    seg_paths = []
    try:
        # ① 各画像を動画化 → ② 正規化＋キャプション
        for i, (name, img) in enumerate(images):
            _still = bool(still_flags and i < len(still_flags) and still_flags[i])
            if progress:
                progress(i, n, f"{name}: {'静止クリップ生成中' if _still else '動画生成中'}…")
            raw = os.path.join(workdir, f"raw_{i}.mp4")
            _gen_dur = int(durations[i]) if _beat_mode else duration   # ★ビートモード=per-clip生成尺
            if _still:
                # 間取り図・3Dパース等：fal/Klingを通さず静止クリップ（morphゼロ・課金ゼロ）
                _still_clip(img, _gen_dur, raw)
                _fit = "contain"                       # 全体表示（図面の端を切らない）
            else:
                rt = room_types[i] if i < len(room_types) else "generic"
                # ★v79-4：focal主語指向のKlingプロンプト。focals/motions が無ければ既定focal/normal。
                _foc = focals[i] if (focals and i < len(focals)) else None
                _mot = motions[i] if (motions and i < len(motions)) else "normal"
                prompt = build_kling_prompt(rt, _foc, _mot)
                # ストリーミングで raw へ直接書き込み（mp4を変数に載せない＝OOM対策）
                generate_clip_fal(img, prompt, duration=_gen_dur, model_key=model_key,
                                  negative_prompt=negative_prompt, cfg_scale=cfg_scale,
                                  out_path=raw)
                _fit = fit_mode
            seg = os.path.join(workdir, f"seg_{i}.mp4")
            cap = captions[i] if (with_captions and i < len(captions)) else ""
            subs = None
            if with_captions and sub_captions and i < len(sub_captions):
                raw_sub = sub_captions[i]
                parts = raw_sub.split("\n") if isinstance(raw_sub, str) else (raw_sub or [])
                subs = [s for s in parts if s and s.strip()][:2]
            flash = flash_text if (i == 0 and flash_text) else ""   # 冒頭は先頭クリップのみ
            tst = tastes[i] if (tastes and i < len(tastes)) else taste
            pos = positions[i] if (positions and i < len(positions)) else "下中央"
            # 注記：image_note（全体）優先 → クリップ個別 notes[i]。両方空なら注記なし
            _note = image_note or (notes[i] if (notes and i < len(notes) and notes[i]) else "")
            _normalize_clip(raw, seg, caption=cap, sub_lines=subs,
                            top_tag=top_tag if with_captions else "",
                            note=_note, taste=tst, pos=pos, flash=flash,
                            out_w=out_w, out_h=out_h, fit_mode=_fit)
            if _beat_mode:
                # ★生成尺（durations[i]）→ トリム尺（trims[i]）へ切り出す（尺切り出し＝速度変更でない・atempo0）
                trimmed = os.path.join(workdir, f"trim_{i}.mp4")
                _trim_clip(seg, trimmed, float(trims[i]))
                seg = trimmed
            seg_paths.append(seg)

        # ③ 連結（メモリ安全＝逐次）
        if progress:
            progress(n, n, "連結中…")
        silent = os.path.join(workdir, "tour_silent.mp4")
        if _beat_mode:
            # ★ビート境界=ハードカット（食われない）／ビート内=xfade0.6s。連続する同 beat_id を1ビートに束ねる。
            beat_groups: list[list[str]] = []
            _prev = object()
            for i, bid in enumerate(beat_ids):
                if bid != _prev:
                    beat_groups.append([])
                    _prev = bid
                beat_groups[-1].append(seg_paths[i])
            _assemble_beats(beat_groups, silent, t=0.6, flash_cut=flash_cut)
        else:
            _xfade_concat(seg_paths, silent, flash_cut=flash_cut)

        # 完成mp4は bytes ではなくファイルパスで返す（session_state/変数に mp4 を載せない）。
        # workdir は finally で消えるため、返す成果物だけ永続 outdir へ移す。
        outdir = tempfile.mkdtemp(prefix="tour_out_")
        out = {"outdir": outdir}
        if also_silent:
            dst = os.path.join(outdir, "room_tour_silent.mp4")
            shutil.move(silent, dst)
            out["silent"] = dst
            silent = dst                             # 以降のBGM合成の入力に使う
        if with_bgm:
            total = _dur(silent)
            bgm = os.path.join(workdir, "bgm.wav")
            synth_bgm(bgm, seconds=total + 2.0)
            withbgm = os.path.join(outdir, "room_tour_bgm.mp4")
            _mux_bgm(silent, bgm, withbgm)
            out["bgm"] = withbgm
        return out
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ======================================================================
# 接続断に強いジョブ実行（Phase2: fal queue submit + poll + state.json 再開）
#   ブラウザ/スクリプトが死んでも fal 側でジョブは走り続け、request_id さえ残っていれば
#   結果を再課金なしで回収できる。state.json で進捗を永続化し「続きから再開」する。
#   ※ /tmp が消える再起動後は復元不可（呼び出し側が明示）＝黙って二重課金しないための土台。
# ======================================================================
import json as _json
import hashlib as _hashlib
import time as _time
import logging as _logging
import sys as _sys
import traceback as _traceback

_log = _logging.getLogger("room_tour_video")


def _log_failure(where: str, err: Exception) -> str:
    """失敗を logger と stderr の両方に出す（Streamlit Cloud のログに必ず残す）。返り値=保存用の理由文字列。"""
    detail = f"{type(err).__name__}: {err}"
    try:
        _log.error("[room_tour_video] %s 失敗: %s", where, detail)
        print(f"[room_tour_video] {where} 失敗: {detail}", file=_sys.stderr, flush=True)
        _traceback.print_exc(file=_sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    return detail[:300]


def _download_stream(url: str, out_path: str, timeout: int = 300) -> None:
    """URLの動画をストリーミングで out_path へ chunk 書き込み（mp4を変数に載せない）。"""
    if requests is None:
        raise RuntimeError("requests が未導入です。")
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def _fal_args(image_url, prompt, duration, model_key, negative_prompt, cfg_scale, silent=True):
    cfg = FAL_MODELS.get(model_key, FAL_MODELS["kling2.6_pro"])
    args = {"prompt": prompt, cfg["image_key"]: image_url, "duration": str(duration)}
    if silent:
        args.update(cfg.get("audio_off", {}))
    if cfg.get("supports_negative"):
        if negative_prompt:
            args["negative_prompt"] = negative_prompt
        if cfg_scale is not None:
            args["cfg_scale"] = cfg_scale
    return cfg["endpoint"], args


def fal_submit_clip(image_bytes, prompt, duration=5, model_key="kling2.6_pro",
                    negative_prompt="", cfg_scale=None) -> dict:
    """1シーンを fal queue へ submit。★fal が返す request_id / status_url / response_url を dict で返す。要 FAL_KEY。
    ★status/result は この status_url/response_url を保存してそのままGETする（URLを推測で組まない＝subscribeと同理屈・
    仕様変更にも強い。※過去に path込み/path欠落を推測で組んで404/405を踏んだ＝measure-firstをURLにも適用）。
    ★後方互換: 呼び出し側は返り値["request_id"] を使う（旧は文字列を返していた）。"""
    import fal_client
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY が未設定です。")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        img_path = f.name
    try:
        image_url = fal_client.upload_file(img_path)
        endpoint, args = _fal_args(image_url, prompt, duration, model_key,
                                   negative_prompt, cfg_scale)
        handle = fal_client.submit(endpoint, arguments=args)
        return {"request_id": handle.request_id, "status_url": handle.status_url,
                "response_url": handle.response_url}
    finally:
        try:
            os.unlink(img_path)
        except Exception:  # noqa: BLE001
            pass


def _fal_root_status_url(model_key, request_id):
    """★手動 request_id 回収のフォールバック用 root形式URL（fal-ai/kling-video/requests/{id}・サブパスなし）。
    ★本命は submit返却の status_url を保存してそのまま使う（fal_poll_clip の status_url引数）。これは保存が無い時だけ。"""
    from fal_client.client import AppId, QUEUE_URL_FORMAT
    endpoint = FAL_MODELS.get(model_key, FAL_MODELS["kling2.6_pro"])["endpoint"]
    app = AppId.from_endpoint_id(endpoint)
    prefix = f"{app.namespace}/" if app.namespace else ""
    base = f"{QUEUE_URL_FORMAT}{prefix}{app.owner}/{app.alias}/requests/{request_id}"
    return base + "/status", base


def fal_poll_clip(model_key, request_id, out_path, status_url=None, response_url=None) -> str:
    """submit済みの状態確認→完了なら out_path へDLし 'done'／未完 'pending'／失敗は例外（URL/HTTP status付き）。要 FAL_KEY。
    ★status_url/response_url を渡せば それをそのままGET（=submitの返却URL・推測しない・subscribe整合・本命）。
    無ければ root形式で組む（手動 request_id 回収のフォールバックのみ）。"""
    import fal_client
    if not status_url:                       # 保存URLが無い＝手動回収フォールバック（root形式）
        status_url, response_url = _fal_root_status_url(model_key, request_id)
    handle = fal_client.SyncRequestHandle(
        request_id=request_id, response_url=response_url, status_url=status_url,
        cancel_url=(response_url or status_url) + "/cancel", client=fal_client.sync_client._client)
    try:
        status = handle.status(with_logs=False)
    except Exception as e:  # noqa: BLE001  ★URL/HTTPステータスを載せて1往復で切り分け可能に（鍵は含まない）
        _sc = getattr(getattr(e, "response", None), "status_code", None)
        raise RuntimeError(f"fal status失敗 HTTP{_sc} url={status_url} ({type(e).__name__})") from e
    if not isinstance(status, fal_client.Completed):
        return "pending"
    result = handle.get()
    url = (result or {}).get("video", {}).get("url")
    if not url:
        raise RuntimeError(f"完了したが動画URLが取得できません: {result}")
    _download_stream(url, out_path)
    return "done"


def job_id_for(images, meta) -> str:
    """入力（画像内容＋全設定）から決定的な job_id を導出。同一入力→同一id・変更→別id。"""
    h = _hashlib.md5()
    for item in images:
        img = item[1] if isinstance(item, (tuple, list)) else item
        h.update(_hashlib.md5(img).digest())
    h.update(_json.dumps(meta, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


def _job_state_path(job_dir):
    return os.path.join(job_dir, "state.json")


def read_job_state(job_dir):
    """job_dir の state.json を読む（無ければ None）。"""
    p = _job_state_path(job_dir)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save_job_state(job_dir, state):
    """state.json を原子的に保存（temp→rename）＝書込中に死んでも壊れない。"""
    p = _job_state_path(job_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, p)


def init_job(job_dir, images, scenes, glob, cover: bytes = None) -> dict:
    """新規ジョブを初期化：画像を保存し state.json を書く。scenes は各シーンのメタ dict の list。
    cover: 冒頭挿入する表紙PNG（任意）。あれば cover.png として保存（glob["cover_on"] と併用）。"""
    os.makedirs(job_dir, exist_ok=True)
    scene_list = []
    for i, (item, sc) in enumerate(zip(images, scenes)):
        img = item[1] if isinstance(item, (tuple, list)) else item
        img_name = f"img_{i}.png"
        with open(os.path.join(job_dir, img_name), "wb") as f:
            f.write(img)
        d = dict(sc)
        d.update({"i": i, "img": img_name, "raw": f"raw_{i}.mp4", "seg": f"seg_{i}.mp4",
                  "status": "pending", "request_id": None, "error": ""})
        scene_list.append(d)
    if cover:
        with open(os.path.join(job_dir, "cover.png"), "wb") as f:
            f.write(cover)
    state = {"version": 1, "phase": "generating", "glob": glob,
             "scenes": scene_list, "outputs": {}}
    _save_job_state(job_dir, state)
    return state


def job_progress(state):
    """(done, total, failed) を返す（UI表示用）。"""
    scs = state.get("scenes", [])
    done = sum(1 for s in scs if s.get("status") == "done")
    failed = sum(1 for s in scs if s.get("status") == "failed")
    return done, len(scs), failed


def run_tour_job(job_dir, progress=None, poll_interval=8, max_wait=1800) -> dict:
    """接続断に強いジョブ実行。done はスキップ／submitted は request_id で回収（★再課金なし）／
    pending・failed のみ新規 submit。全 done 後に連結。冪等（何度呼んでも続きから）。返り値=出力パス。"""
    state = read_job_state(job_dir)
    if state is None:
        raise RuntimeError("state.json がありません（ジョブ未初期化 or /tmp が消去されました）。")
    # 既に完成していれば即返す（連結の二度手間を避ける）
    if state.get("phase") == "done":
        o = state.get("outputs", {})
        if (o.get("silent") and os.path.exists(o["silent"])) or \
           (o.get("bgm") and os.path.exists(o["bgm"])):
            return {"outdir": job_dir, "silent": o.get("silent"), "bgm": o.get("bgm"),
                    "narrated": o.get("narrated"), "narrated_bgm": o.get("narrated_bgm")}
    glob = state["glob"]
    scenes = state["scenes"]
    n = len(scenes)
    out_w, out_h = glob["out_w"], glob["out_h"]

    def _jp(path):
        return os.path.join(job_dir, path)

    _unified = bool(glob.get("unified_subtitle"))   # ★B: ナレ=字幕一本化時はメイン/情感を焼かない（字幕は動画レベルで③時刻焼き）
    # ★narr-fix-b：実尺先行（measure-first）＝narration ON ＋ beatモード。segを予測trimせずフル正規化で残し、
    #   フェーズCでTTS実尺測定→d_i=max(_MIN_BEAT, narr+_TAIL_PAD)→segfitへtrim/フリーズ延長→組立（予測係数に依存しない）。
    _measure_first = bool(glob.get("narration_on")) and any(
        sc.get("beat_id") is not None for sc in scenes)

    def _make_seg(sc):   # raw → seg（テロップ・注記込み normalize）。冪等
        _normalize_clip(_jp(sc["raw"]), _jp(sc["seg"]),
                        caption="" if _unified else sc.get("caption", ""),
                        sub_lines=None if _unified else sc.get("subs"),
                        top_tag=sc.get("top_tag", ""), note=sc.get("note", ""),
                        taste=sc.get("taste", "clean"), pos=sc.get("pos", "下中央"),
                        flash=sc.get("flash", ""), out_w=out_w, out_h=out_h,
                        fit_mode=sc.get("fit", "fill"))
        # ★通常＝予測trim(sc['trim'])をsegへ適用（尺切り出し＝速度変更でない・atempo0）。
        #   ★measure-first＝trimせずフル正規化segを残す（TTS実尺測定後にsegfitへ切る＝素材を先に捨てない）。冪等。
        _tr = sc.get("trim")
        if _tr and not _measure_first:
            _tmp = _jp(f"segtrim_{sc['i']}.mp4")
            _trim_clip(_jp(sc["seg"]), _tmp, float(_tr))
            shutil.move(_tmp, _jp(sc["seg"]))

    # ── フェーズA：still はローカル生成、fal pending/failed は submit（request_id を即保存）──
    for sc in scenes:
        if sc.get("status") == "done" and os.path.exists(_jp(sc["seg"])):
            continue
        if progress:
            progress(sc["i"], n, f"{sc.get('name', '')}: 準備中…")
        img_bytes = open(_jp(sc["img"]), "rb").read()
        _gen_dur = int(sc.get("gen_dur") or glob["duration"])   # ★ビートモード=per-scene生成尺
        if sc.get("still"):
            _still_clip(img_bytes, _gen_dur, _jp(sc["raw"]))
            _make_seg(sc)
            sc["status"] = "done"
            _save_job_state(job_dir, state)
        elif sc.get("status") in ("pending", "failed"):
            rt = sc.get("room_type", "generic")
            # ★v79-4：focal主語指向のKlingプロンプト（sceneのfocal/motion＝room_facts_map由来）。無ければ既定focal/normal。
            prompt = build_kling_prompt(rt, sc.get("focal"), sc.get("motion", "normal"))
            try:
                _sub = fal_submit_clip(img_bytes, prompt, _gen_dur, glob["model_key"],
                                       glob.get("negative_prompt", ""), glob.get("cfg_scale"))
                sc["request_id"] = _sub["request_id"]
                sc["status_url"] = _sub["status_url"]     # ★fal返却URLを永続化＝pollは推測せずこれをGET
                sc["response_url"] = _sub["response_url"]
                sc["status"] = "submitted"
                sc["error"] = ""
            except Exception as e:  # noqa: BLE001  1本失敗は隔離（全体を殺さない）＝logger/state/UIへ
                sc["status"] = "failed"
                sc["error"] = _log_failure(f"submit(scene {sc['i']})", e)
            _save_job_state(job_dir, state)   # ★submit直後に永続化（死んでも回収可）

    # ── フェーズB：submitted を poll → 完了で回収DL→normalize→done ──
    waited = 0
    while any(sc.get("status") == "submitted" for sc in scenes):
        for sc in [s for s in scenes if s.get("status") == "submitted"]:
            try:
                if fal_poll_clip(glob["model_key"], sc["request_id"], _jp(sc["raw"]),
                                 sc.get("status_url"), sc.get("response_url")) == "done":
                    _make_seg(sc)
                    sc["status"] = "done"
                    _save_job_state(job_dir, state)
            except Exception as e:  # noqa: BLE001  logger/state/UIへ
                sc["status"] = "failed"
                sc["error"] = _log_failure(f"poll(scene {sc['i']})", e)
                _save_job_state(job_dir, state)
        d, _, _ = job_progress(state)
        if progress:
            progress(d, n, f"生成待ち… {d}/{n} 完了（fal処理中）")
        if not any(sc.get("status") == "submitted" for sc in scenes):
            break
        waited += poll_interval
        if waited > max_wait:
            raise RuntimeError("生成がタイムアウトしました（あとで『続きから再開』で回収できます）。")
        _time.sleep(poll_interval)

    # ── フェーズC：連結（冪等・seg があるものだけ順番に）──
    if progress:
        progress(n, n, "連結中…")
    ordered = [sc for sc in scenes if sc.get("status") == "done" and os.path.exists(_jp(sc["seg"]))]
    if not ordered:
        # 全シーン失敗：理由（シーン別・重複除去）を例外文にも載せて診断可能にする
        _errs = sorted({sc.get("error", "") for sc in scenes if sc.get("error")})
        state["phase"] = "failed"
        state["n_failed"] = sum(1 for sc in scenes if sc.get("status") == "failed")
        _save_job_state(job_dir, state)
        _detail = " / ".join(_errs) if _errs else "理由不明（各シーンにエラーが記録されていません）"
        raise RuntimeError(f"全シーン失敗: {_detail}")
    state["phase"] = "concatenating"
    _save_job_state(job_dir, state)

    # ── ★narr-fix-b：実尺先行（measure-first）＝TTS実尺を測ってから映像尺 d_i を決める（予測係数5.26に依存しない）──
    #   narration ON ＋ beatモードのみ。フル正規化seg（無trim）を d_i へ trim/フリーズ延長した segfit を作る。
    #   d_i = max(_MIN_BEAT_SEC, narr_actual + _NARR_TAIL_PAD)。overlay窓・ナレ開始・総尺は Σd_i を共有（逐次＝被り0）。
    narr_warn = []
    _beat_durations = None     # {bid: d_i}（measure-first成功時のみ非None）
    _beat_audio = {}           # {bid: apath}（TTS済みmp3・後段muxで再利用）
    _fitpath = {}              # {sc['i']: segfit path}（組立が読む・無ければsegそのもの）
    _mf_fallback = False
    if _measure_first:
        try:
            import core as _core
            _grp, _prev = [], object()
            for sc in ordered:
                _bid = sc.get("beat_id")
                if _bid != _prev:
                    _grp.append((_bid, []))
                    _prev = _bid
                _grp[-1][1].append(sc)
            _beat_durations, _mf_rows = {}, []
            for bid, scs in _grp:
                _txt = (scs[0].get("beat_narration") or "").strip()   # narration_text=comment（narr-fix-a）
                # ★narr-fix-d：TTSは narration_kana（全ひらがな読み）優先＝Geminiの文脈読みで誤読根絶。
                #   無ければ comment を normalize_reading（辞書経路）＝フォールバック。kanaも normalize_reading を通す
                #   （残存漢字は辞書で補正・仮名は素通し）。ゲートは comment 有無で（kana空でも comment があれば読む）。
                _kana = (scs[0].get("beat_narration_kana") or "").strip()
                _read = _core.normalize_reading(_kana or _txt)
                _na = 0.0
                if _txt:
                    _ap = _jp(f"narrbeat_{bid}.mp3")
                    try:
                        if not os.path.exists(_ap):
                            tts_elevenlabs(_read, _ap)   # ★キーはenvのみ・例外に載せない
                        _na = _adur(_ap)                          # ★音声尺は_adur（_durは動画v:0で音声を測れない）
                        _beat_audio[bid] = _ap
                    except Exception as e:  # noqa: BLE001  1本失敗＝そのビート無音で継続（全体は止めない）
                        narr_warn.append(_log_failure(f"tts(beat {bid})", e) + "＝このビートは無音で継続")
                        _na = 0.0
                _di = max(_MIN_BEAT_SEC, _na + _NARR_TAIL_PAD)
                _floor = (_na + _NARR_TAIL_PAD) < _MIN_BEAT_SEC
                _beat_durations[bid] = _di
                # d_i を各シーンへ配分（xfade補償 padded=d_i+0.6×境界）→ segfit（trim / 末尾フリーズ延長）
                _nn = len(scs)
                _per = (_di + 0.6 * max(0, _nn - 1)) / _nn
                _freeze = 0.0
                for sc in scs:
                    _srcd = _dur(_jp(sc["seg"]))
                    _fp = _jp(f"segfit_{sc['i']}.mp4")
                    _freeze_extend(_jp(sc["seg"]), _per, _fp)     # フル正規化segから毎回作る＝冪等
                    _fitpath[sc["i"]] = _fp
                    _freeze = max(_freeze, _per - _srcd)
                if _freeze > _FREEZE_WARN_SEC:
                    narr_warn.append(f"ビート{bid + 1}: 背景を約{_freeze:.1f}s静止延長（commentが長い→"
                                     "スライドショー感／字数短縮を・恒久策はcomment字数ガイド）")
                _mf_rows.append((bid, _di, _na, _floor, max(0.0, _freeze)))
            for bid, _di, _na, _floor, _fz in _mf_rows:   # 偽装不能な検証表（実尺配置の内訳）
                narr_warn.append(
                    f"📏 ビート{bid + 1} 実尺配置: d={_di:.1f}s ／ ナレ実尺{_na:.1f}s ／ "
                    f"フロア{'発動' if _floor else '—'} ／ 静止延長{_fz:.1f}s")
        except Exception as e:  # noqa: BLE001  ★measure-first全体の失敗＝旧経路へ・ただし隠さない（黙って被り残りは最も危険）
            _mf_fallback = True
            _measure_first = False
            _beat_durations, _beat_audio, _fitpath = None, {}, {}
            narr_warn.append("⚠️ 実尺配置に失敗し旧経路（予測尺）で生成しました（"
                             + _log_failure("measure_first", e)
                             + "）。音声被りが残る可能性があります。")
            for sc in ordered:   # フォールバック：未trimのフル正規化segを予測trimへ切る
                _tr = sc.get("trim")
                if _tr:
                    _tmp = _jp(f"segtrim_{sc['i']}.mp4")
                    _trim_clip(_jp(sc["seg"]), _tmp, float(_tr))
                    shutil.move(_tmp, _jp(sc["seg"]))

    def _beat_d(sc):
        """ビート尺：measure-first成功＝実測 d_i ／ それ以外＝予測 beat_narr_sec。overlay窓・ナレ開始が同一式を共有。"""
        bid = sc.get("beat_id")
        if _beat_durations is not None and bid in _beat_durations:
            return _beat_durations[bid]
        return float(sc.get("beat_narr_sec") or 0.0)

    def _segpath(sc):
        """組立が読むクリップ：measure-first＝segfit（d_iへ調整済）／それ以外＝seg（予測trim済 or 無trim）。"""
        return _fitpath.get(sc["i"], _jp(sc["seg"]))

    body = _jp("room_tour_silent.mp4")
    # ★story-v78ビートモード：どれか1シーンでも beat_id を持てばビートモード。
    #   ビート境界=ハードカット（食われない）／ビート内=0.6xfade（allocateのxfade=0.6と一致）。
    #   normal本線は beat_id を一切持たない＝_xfade_concat で完全回帰。
    _beat_mode = any(sc.get("beat_id") is not None for sc in ordered)
    if _beat_mode:
        beat_groups: list[list[str]] = []
        _prev = object()
        for sc in ordered:
            bid = sc.get("beat_id")
            if bid != _prev:
                beat_groups.append([])
                _prev = bid
            beat_groups[-1].append(_segpath(sc))   # ★narr-fix-b：measure-first時はd_i調整済segfit
        _assemble_beats(beat_groups, body, t=0.6, flash_cut=False)  # 内xfadeは0.6固定（allocate整合）
    else:
        _xfade_concat([_segpath(sc) for sc in ordered], body,
                      flash_cut=bool(glob.get("flash_cut", False)))

    # ── 冒頭表紙の挿入（本編前に静止クリップを連結・全出力版に反映）──
    t = 0.2 if glob.get("flash_cut") else 0.6
    cover_sec = float(glob.get("cover_sec", 0) or 0)
    cover_on = (bool(glob.get("cover_on")) and cover_sec > 0
                and os.path.exists(_jp("cover.png")))
    cover_warn = []
    if cover_on:
        try:                                     # ★フェイルセーフ：表紙は付加価値。失敗しても動画は止めない
            cov = _jp("cover_clip.mp4")
            _cover_clip(open(_jp("cover.png"), "rb").read(), cover_sec, out_w, out_h, cov)
            covered = _jp("room_tour_silent.mp4")   # 無音版そのものを表紙入りに置換
            _prepend_clip(cov, body, _jp("_tmp_covered.mp4"))
            shutil.move(_jp("_tmp_covered.mp4"), covered)
            body = covered
            cover_on = True
        except Exception as e:  # noqa: BLE001  logger/state/UIへ。表紙なしで続行
            cover_on = False
            cover_warn.append("表紙の挿入に失敗したため、表紙なしで動画を生成しました（"
                              + _log_failure("cover_prepend", e) + "）。")

    # ── ★v79「動く雑誌」：ビート文字面（big_text/accent/comment/tags）を全画面overlayで焼く ──
    #   背景=Kling動画、前景=build_beat_overlay。ナレは画面の文字を読み上げる。v78字幕焼きの代替（big_text保持時のみ発火）。
    #   ビート開始=cover_off+Σ前ビートbeat_narr_sec（＝ナレ配置と同一式）。表紙区間は文字面なし。文字面フィールドは各ビート先頭sceneに載る。
    _v79_mag = any((sc.get("big_text") or sc.get("comment")) for sc in ordered)   # ★comment のみでも発火（big_text空化耐性）
    if _v79_mag:
        try:
            _accent = tuple(glob.get("v79_accent") or _V79_GOLD)
            _aspect = glob.get("aspect", "9:16")
            _cover_off = cover_sec if cover_on else 0.0
            _ov, _acc, _prev = [], 0.0, object()
            for sc in ordered:
                bid = sc.get("beat_id")
                if bid == _prev:
                    continue                          # 同ビート2枚目以降＝先頭のみ文字面
                _prev = bid
                _nsec = _beat_d(sc)                    # ★narr-fix-b：measure-first時は実測d_i（予測beat_narr_secでなく）
                _s = _cover_off + _acc
                _acc += _nsec                          # ナレ有無に関わらず尺を積む（stillビートも前進）
                _room = sc.get("room_label") or sc.get("room") or ""
                _bigt = (sc.get("big_text") or "").strip()
                _cmt = (sc.get("comment") or "").strip()
                # ★magfit-v79b：big_text が空でも overlay を描く（マストヘッド＋タグ＋部屋pill＋commentは出す）
                #   ＝テキスト層まるごと欠落を防ぐ。真に何も無いビート（room_labelも無い＝存在しない想定）だけスキップ。
                if not (_bigt or _cmt or _room):
                    continue
                if not _bigt:                          # big_text空＝見出し欠落を隠さずログ（fact_scrub等で空化の可能性）
                    narr_warn.append(f"⚠️ ビート{bid + 1}（{_room}）: big_text（金色見出し）が空。"
                                     "マストヘッド/タグ/commentのみ描画（factで空化の可能性・要確認）。")
                try:                                   # ★per-beat隔離：1ビートの描画失敗で全ビートを落とさない
                    _png = build_beat_overlay(
                        _room, _bigt, sc.get("accent_word") or "", _cmt,
                        tags=sc.get("beat_tags") or [], accent=_accent,
                        spec_line=sc.get("spec_line") or "", equip_line=sc.get("equip_line") or "",
                        note_line=sc.get("note_line") or "", aspect=_aspect)
                    _ov.append((_png, _s, _s + _nsec))
                except Exception as e:  # noqa: BLE001  1ビート失敗は隔離＝ログのみ（他ビートは描く）
                    narr_warn.append(f"⚠️ ビート{bid + 1}（{_room}）: 文字面の描画に失敗し省略（"
                                     + _log_failure(f"build_beat_overlay(beat {bid})", e) + "）。")
            _mag = _jp("_tmp_magazine.mp4")
            _burn_beat_overlays(body, _ov, _mag, out_w, out_h)
            shutil.move(_mag, _jp("room_tour_silent.mp4"))
            body = _jp("room_tour_silent.mp4")
        except Exception as e:  # noqa: BLE001  文字面焼き込み全体の失敗はフェイルセーフ（動画は止めない）＝ログに残す
            cover_warn.append("文字面の焼き込みに失敗したため文字なしで生成しました（"
                              + _log_failure("burn_beat_overlays", e) + "）。")

    # ── ★story-v78 B ③：ナレ字幕を『字数比の1行ずつ切替』で無音bodyに焼く（with-timestamps不要のフォールバック水準）──
    #   ビート開始=cover_off+Σ前ビートdur（＝ナレ音声の配置と同一・A0の単純化）。表紙区間は字幕なし（唯一の例外）。
    #   ★v79文字面が焼かれた場合はスキップ（big_textが画面の主役＝字幕と二重にしない）。
    _sub_beats = glob.get("subtitle_beats")
    if _sub_beats and not _v79_mag:
        try:
            import core as _core
            _events = _core.subtitle_events(_sub_beats, cover_sec if cover_on else 0.0)
            _subbed = _jp("_tmp_subbed.mp4")
            _burn_subtitles(body, _events, _subbed, out_w, out_h)
            shutil.move(_subbed, _jp("room_tour_silent.mp4"))
            body = _jp("room_tour_silent.mp4")
        except Exception as e:  # noqa: BLE001  字幕はフェイルセーフ（失敗しても動画は止めない）
            cover_warn.append("字幕の焼き込みに失敗したため字幕なしで生成しました（"
                              + _log_failure("burn_subtitles", e) + "）。")

    out = {"outdir": job_dir}
    if glob.get("also_silent", True):
        out["silent"] = body
    bgm_wav = _jp("bgm.wav")
    if glob.get("with_bgm", True):
        synth_bgm(bgm_wav, seconds=_dur(body) + 2.0)
        withbgm = _jp("room_tour_bgm.mp4")
        _mux_bgm(body, bgm_wav, withbgm)
        out["bgm"] = withbgm

    # ── AIナレーション（各シーン/ビート頭に音声を配置・★速度変更しない）──
    #   ★narr_warn は上のmeasure-firstパスで初期化済み（実尺配置の📏検証表・フォールバック警告を保持）。
    if bool(glob.get("narration_on")):
        audio_starts = []
        cover_off = cover_sec if cover_on else 0.0
        _beat_mode_n = any(sc.get("beat_id") is not None for sc in ordered)
        if _beat_mode_n:
            # ★narr-fix-b：ビート開始 = cover_off + Σ前ビートの d_i（measure-first=実測 _beat_durations／fallback=予測beat_narr_sec）。
            #   measure-firstではmp3は測定時にTTS済み（_beat_audio）＝再利用。d_i≧narr_actual+TAIL_PAD＝構造的に非重複。
            #   ★_beat_d と _beat_audio を overlay窓と共有＝窓・ナレ開始・総尺が同一のΣd_iを見る（二重算入/ズレなし）。
            acc = 0.0
            _prev = object()
            for sc in ordered:
                bid = sc.get("beat_id")
                if bid == _prev:
                    continue                        # 同ビートの2枚目以降はスキップ（先頭のみ配置）
                _prev = bid
                dsec = _beat_d(sc)
                txt = (sc.get("beat_narration") or "").strip()
                apath = _beat_audio.get(bid)        # ★measure-firstで測定時にTTS済み＝再利用（二重TTS/二重課金しない）
                if txt and not apath:               # fallback（予測経路）：ここでTTS
                    apath = _jp(f"narrbeat_{bid}.mp3")
                    try:
                        if not os.path.exists(apath):
                            import core as _core
                            _kana = (sc.get("beat_narration_kana") or "").strip()   # ★narr-fix-d：読み仮名優先
                            tts_elevenlabs(_core.normalize_reading(_kana or txt), apath)  # ★キーは env のみ・例外に載せない
                    except Exception as e:  # noqa: BLE001  1本失敗は隔離＝logger/stateへ
                        narr_warn.append(_log_failure(f"tts(beat {bid})", e))
                        apath = None
                if apath and os.path.exists(apath):
                    _sec = _adur(apath)              # ★音声尺は_adur（_durは動画v:0で音声を測れない＝旧CPSログの5.0s固定バグも解消）
                    audio_starts.append((apath, cover_off + acc))
                    _over = _sec - dsec              # d_i超過（measure-first下では本来起きない・監視用）
                    if _over > 0.3:
                        narr_warn.append(f"ビート{bid + 1}: ナレ音声がビート尺を{_over:.1f}s超過（要確認）")
                acc += dsec                          # ★ナレ有無に関わらず d_i を積む（stillビートも前進）
        else:
            durs = [_dur(_jp(sc["seg"])) for sc in ordered]
            flash_title = bool(ordered and ordered[0].get("flash"))
            flash_delay = 0.3 if (flash_title and not cover_on) else 0.0
            starts = _scene_start_times(durs, t, cover_off, flash_delay)
            for k, sc in enumerate(ordered):
                txt = (sc.get("narration") or "").strip()
                if not txt:
                    continue
                apath = _jp(f"narr_{sc['i']}.mp3")
                try:
                    if not os.path.exists(apath):
                        import core as _core
                        tts_elevenlabs(_core.normalize_reading(txt), apath)  # ★B: 音声のみ正規化（字幕は生・w残す）
                except Exception as e:  # noqa: BLE001  1本失敗は隔離＝logger/stateへ
                    narr_warn.append(_log_failure(f"tts(scene {sc['i']})", e))
                    continue
                # ★実測CPS（係数_NARR_CPSは推測でなく実測で決める・factguard期の教訓）。実尺と無音を毎シーンUIへ。
                _sec = _adur(apath)              # ★音声尺は_adur（_durは動画v:0でmp3を測れず5.0s固定を返す穴）
                _chars = len(re.sub(r"\s+", "", txt))
                _cps = (_chars / _sec) if _sec > 0 else 0.0
                _silence = max(0.0, durs[k] - _sec)
                narr_warn.append(
                    f"📏 シーン{sc['i']+1} 実測: {_chars}字 ÷ 実尺{_sec:.1f}s = {_cps:.1f}字/秒"
                    f"（尺{durs[k]:.0f}s・末尾無音{_silence:.1f}s）")
                over = _sec - durs[k]
                if over > 0.3:                      # 0.3s超過は原稿短縮を促す（自動速度変更しない）
                    narr_warn.append(f"シーン{sc['i']+1}: ナレ音声が尺を{over:.1f}s超過（原稿を短縮して再生成を）")
                audio_starts.append((apath, starts[k]))
        if audio_starts:
            narrated = _jp("room_tour_narrated.mp4")
            _mux_narration(body, audio_starts, narrated)
            out["narrated"] = narrated
            if glob.get("with_bgm", True):
                if not os.path.exists(bgm_wav):
                    synth_bgm(bgm_wav, seconds=_dur(body) + 2.0)
                narrated_bgm = _jp("room_tour_narrated_bgm.mp4")
                _mux_narration(body, audio_starts, narrated_bgm, bgm_wav=bgm_wav)
                out["narrated_bgm"] = narrated_bgm

    state["phase"] = "done"
    state["outputs"] = {"silent": out.get("silent"), "bgm": out.get("bgm"),
                        "narrated": out.get("narrated"), "narrated_bgm": out.get("narrated_bgm")}
    state["narration_warnings"] = narr_warn + cover_warn   # 表紙フェイルセーフ警告も同経路でUIへ
    state["n_failed"] = sum(1 for sc in scenes if sc.get("status") == "failed")
    _save_job_state(job_dir, state)
    return out
