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
    if flash:                                     # 冒頭極短フラッシュ（0.5秒・中央・フェードイン/アウト）
        fa = ("alpha='if(lt(t\\,0.15)\\,t/0.15\\,if(lt(t\\,0.35)\\,1\\,"
              "if(lt(t\\,0.5)\\,(0.5-t)/0.15\\,0)))'")
        draws.append(f"drawtext={fontref}:text='{_esc(flash)}':fontcolor=white:fontsize=76:"
                     f"shadowcolor=black@0.6:shadowx=3:shadowy=3:"
                     f"x=(w-text_w)/2:y=(h-text_h)/2:{fa}")
    chain = base + ";[base]" + (",".join(draws) + "," if draws else "") + "fps=30,format=yuv420p,setsar=1[v]"
    # -threads 1：エンコードのフレームバッファ多重化を抑えピークRSSを下げる（Cloud 1GB制限向け）。
    #   ※ threads は圧縮の並列度のみに影響し、drawtext（テロップ/注記/SynthID経路）の
    #     描画結果は不変＝テロップ回帰なし。filter_complex(chain) は一切変更していない。
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", in_path,
                    "-filter_complex", chain, "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", "-threads", "1", out_path],
                   check=True, timeout=300)
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


def _xfade_concat(seg_paths: list[str], out_path: str, t: float = 0.6) -> str:
    """メモリ安全なクロスフェード連結。
    旧方式（N本を全入力で同時デコードする xfade フィルタグラフ）は N が増えると
    ffmpeg のピークRSSが 1GB超（実測 9本=1.7GB）となり Streamlit Cloud を落とす。
    そこで各境界の t 秒だけを xfade した『極小トランジションclip』を作り、本編は
    トリムして concat demuxer（-c copy・逐次・デコードなし）で連結する。各 ffmpeg 呼び出しは
    最大2本しか開かないためピークは1本分（数十MB）で済む。見た目のクロスフェードは維持。"""
    ff = _ffmpeg()
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
                      f"[a][b]xfade=transition=fade:duration={t}:offset=0,"
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


def build_tour(images: list[tuple], *, captions: Optional[list] = None,
               sub_captions: Optional[list] = None,
               top_tag: str = "", with_captions: bool = True,
               with_bgm: bool = True, also_silent: bool = True,
               model_key: str = "kling2.6_pro", duration: int = 5,
               room_types: Optional[list] = None, image_note: str = "",
               notes: Optional[list] = None, still_flags: Optional[list] = None,
               taste: str = "clean", tastes: Optional[list] = None,
               positions: Optional[list] = None, flash_text: str = "",
               negative_prompt: str = DEFAULT_NEGATIVE_PROMPT, cfg_scale: Optional[float] = None,
               aspect: str = "9:16", fit_mode: str = "fill", progress=None) -> dict:
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

    workdir = tempfile.mkdtemp(prefix="tour_")
    seg_paths = []
    try:
        # ① 各画像を動画化 → ② 正規化＋キャプション
        for i, (name, img) in enumerate(images):
            _still = bool(still_flags and i < len(still_flags) and still_flags[i])
            if progress:
                progress(i, n, f"{name}: {'静止クリップ生成中' if _still else '動画生成中'}…")
            raw = os.path.join(workdir, f"raw_{i}.mp4")
            if _still:
                # 間取り図・3Dパース等：fal/Klingを通さず静止クリップ（morphゼロ・課金ゼロ）
                _still_clip(img, duration, raw)
                _fit = "contain"                       # 全体表示（図面の端を切らない）
            else:
                rt = room_types[i] if i < len(room_types) else "generic"
                prompt = ROOM_PROMPTS.get(rt, ROOM_PROMPTS["generic"])
                # ストリーミングで raw へ直接書き込み（mp4を変数に載せない＝OOM対策）
                generate_clip_fal(img, prompt, duration=duration, model_key=model_key,
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
            seg_paths.append(seg)

        # ③ クロスフェード連結（メモリ安全＝逐次）
        if progress:
            progress(n, n, "連結中…")
        silent = os.path.join(workdir, "tour_silent.mp4")
        _xfade_concat(seg_paths, silent)

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
