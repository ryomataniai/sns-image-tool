#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fal.ai 疎通スモークテスト (fal_smoke_test.py)
=============================================
本統合の前に、fal.ai の Kling image-to-video が「キーで叩けて・動画が返る」ことだけを
最小コスト（画像1枚＝約$0.35）で確認する単体スクリプト。

使い方:
    export FAL_KEY=xxxxxxxx      # fal.ai で発行したキー
    pip install fal-client requests
    python3 fal_smoke_test.py "／path/to/部屋画像.png"
    # 画像を省略すると、サンプルの玄関画像を自動探索して使う

成功すると smoke_out.mp4 を保存し、URLと所要秒数を表示する。
ここが通れば room_tour_video.py の本番経路も通る。
"""
import os
import sys
import time
import glob

import room_tour_video as rtv


def _pick_sample() -> str:
    candidates = glob.glob(
        os.path.expanduser(
            "~/Downloads/エンクス/02_事業B_リフォーム・AI家具提案/サンプル/"
            "ニューモート_展開/*.png"))
    # LDK（奥行きがあり動きが映える）を優先
    for c in sorted(candidates):
        if "LDK" in c:
            return c
    return candidates[0] if candidates else ""


def main():
    if not os.environ.get("FAL_KEY"):
        sys.exit("✗ FAL_KEY が未設定です。 export FAL_KEY=xxxx を実行してください。")

    img_path = sys.argv[1] if len(sys.argv) > 1 else _pick_sample()
    if not img_path or not os.path.exists(img_path):
        sys.exit("✗ テスト画像が見つかりません。引数で画像パスを渡してください。")

    model = os.environ.get("SMOKE_MODEL", "kling2.6_pro")
    print(f"① 画像: {img_path}")
    print(f"② モデル: {model}（無音・5秒）")
    print("③ fal.ai に送信中…（1〜3分程度）")

    with open(img_path, "rb") as f:
        img = f.read()

    t0 = time.time()
    try:
        clip = rtv.generate_clip_fal(
            img, rtv.ROOM_PROMPTS["ldk"], duration=5, model_key=model, silent=True)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"✗ 生成に失敗: {e}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_out.mp4")
    with open(out, "wb") as f:
        f.write(clip)
    dt = time.time() - t0
    print(f"✓ 成功: {out}（{len(clip)/1e6:.1f}MB / {dt:.0f}秒）")
    print("→ 再生して破綻なくカメラが動いていれば疎通OK。本統合(app.pyタブ)へ進めます。")


if __name__ == "__main__":
    main()
