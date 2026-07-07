#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ローカル・フルCP（5枚→1本のツアー）検証スクリプト
==================================================
🎬タブと同じエンジン(room_tour_video.build_tour)を、ニューモートの5枚で一気に回し、
無音版・BGM版をフォルダに保存する。タブ経由の手クリックの代わりに使えて、結果を目視/自動で検証できる。

前提（ローカルMac）:
    export FFMPEG_BINARY="$HOME/.local/opt/ffmpeg-evermeet/ffmpeg"   # drawtext対応ffmpeg
    export FAL_KEY=＜falのキー＞
    python3 tour_cp_local.py
    # FONTCONFIG_FILE は未設定なら本スクリプトが自動生成する（バンドルfontsを参照）

本番(Streamlit Cloud/Debian)では FFMPEG_BINARY も FONTCONFIG_FILE も不要（apt ffmpegにdrawtext同梱）。
消費コスト目安: kling2.6_pro 無音5秒 ×5本 ≒ $1.75。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# --- evermeet ffmpeg 使用時の fontconfig を自己完結で用意（ローカルのみ） ---
if os.environ.get("FFMPEG_BINARY") and not os.environ.get("FONTCONFIG_FILE"):
    conf_dir = os.path.expanduser("~/.local/opt/ffmpeg-evermeet")
    os.makedirs(conf_dir, exist_ok=True)
    conf = os.path.join(conf_dir, "fonts.conf")
    fontdir = os.path.join(HERE, "fonts")
    cachedir = os.path.expanduser("~/.cache/fontconfig")
    if not os.path.exists(conf):
        with open(conf, "w") as f:
            f.write('<?xml version="1.0"?>\n'
                    '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                    '<fontconfig>\n'
                    f'  <dir>{fontdir}</dir>\n'
                    f'  <cachedir>{cachedir}</cachedir>\n'
                    '</fontconfig>\n')
    os.environ["FONTCONFIG_FILE"] = conf
    print(f"FONTCONFIG_FILE = {conf}")

import room_tour_video as m  # noqa: E402

if not os.environ.get("FAL_KEY"):
    sys.exit("✗ FAL_KEY が未設定です。 export FAL_KEY=xxxx を実行してください。")

BASE = os.path.expanduser(
    "~/Downloads/エンクス/02_事業B_リフォーム・AI家具提案/サンプル/ニューモート_展開")

# (ファイル名, 部屋種別, キャプション)  再生順
ORDER = [
    ("01_玄関",  "entrance", "コンクリート現しの土間玄関"),
    ("02_LDK",   "ldk",      "無垢床のフルリノベLDK 11.3帖"),
    ("03_洋室",  "bedroom",  "光が回る洋室 6帖"),
    ("04_浴室",  "bathroom", "追い焚き付き ゆとりの浴室"),
    ("05_トイレ", "toilet",   "手洗い付き 独立トイレ"),
]

images, room_types, captions = [], [], []
for fn, rt, cap in ORDER:
    p = os.path.join(BASE, fn + ".png")
    if not os.path.exists(p):
        sys.exit(f"✗ 画像が見つかりません: {p}")
    with open(p, "rb") as f:
        images.append((fn, f.read()))
    room_types.append(rt)
    captions.append(cap)

print(f"① 画像 {len(images)} 枚（{BASE}）")
print("② fal で各部屋を動画化 → 9:16整形＋キャプション → クロスフェード連結 → BGM")
print("   （5本生成で数分・約$1.75）")


def _pg(step, total, msg):
    print(f"  {step+1}/{total+1}  {msg}")


out = m.build_tour(
    images, captions=captions, room_types=room_types,
    top_tag="ニューモート204 ｜ 2LDK 57.07㎡",
    with_captions=True, with_bgm=True, also_silent=True,
    model_key="kling2.6_pro", duration=5,
    image_note="※画像はイメージです", progress=_pg)

DEST = os.path.expanduser(
    "~/Downloads/エンクス/02_事業B_リフォーム・AI家具提案/サンプル/ルームツアーCP_出力")
os.makedirs(DEST, exist_ok=True)
for k, v in out.items():
    fp = os.path.join(DEST, f"tour_cp_{k}.mp4")
    with open(fp, "wb") as f:
        f.write(v)
    print(f"✓ 保存: {fp}（{len(v)/1e6:.1f}MB）")

print("\n完了。上記フォルダの tour_cp_silent.mp4 / tour_cp_bgm.mp4 を再生して確認してください。")
