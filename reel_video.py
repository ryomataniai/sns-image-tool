#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
リール動画生成 CLI (reel_video.py)
===================================
トピック → フック台本生成 → (背景AI生成) → 縦mp4書き出し。
重い処理なのでローカル実行向き。

使い方:
    export GEMINI_API_KEY=xxxxx
    python3 reel_video.py --topic "賃貸の初期費用" --out reel.mp4
    python3 reel_video.py --topic "内見でみるべき点" --no-ai-bg   # 背景単色=無料

生成後、Instagramアプリで開いてトレンド音源を付けて投稿するのが効果的。
"""
import argparse
import sys

import core
import reel


def main():
    ap = argparse.ArgumentParser(description="リール(縦動画)を生成")
    ap.add_argument("--topic", required=True, help="トピック")
    ap.add_argument("--out", default="reel.mp4", help="出力mp4")
    ap.add_argument("--cuts", type=int, default=4, help="カット数")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--no-ai-bg", action="store_true", help="背景をAI生成せず単色にする")
    args = ap.parse_args()

    try:
        client = core.get_client()
    except RuntimeError as e:
        sys.exit(f"✗ {e}")

    print(f"① フック台本を生成中… (トピック: {args.topic})")
    script = reel.generate_reel_script(client, args.topic, args.cuts)

    print("\n=== フック採用案 ===")
    print(f"  ★ {script['hook']}")
    print("=== フック別案（人が選ぶ用） ===")
    for h in script.get("hook_alternatives", []):
        print(f"  ・ {h}")
    print("\n=== カット ===")
    for i, c in enumerate(script["cuts"], 1):
        print(f"  {i}. {c['text']}")
    print(f"  CTA: {script['cta']}\n")

    if script.get("caption") or script.get("hashtags"):
        print("=== 投稿キャプション（コピペ用） ===")
        print(script.get("caption", ""))
        print(script.get("hashtags", ""))
        print()

    bg_map = {}
    if not args.no_ai_bg:
        prompts = reel.bg_prompts_of(script)
        for k, pr in prompts.items():
            if pr:
                print(f"② 背景生成: {k} …")
                data, err = core.generate_image_bytes(
                    client, pr, "gemini-2.5-flash-image", "9:16", "1K")
                if data:
                    bg_map[k] = data
                else:
                    print(f"   （背景失敗→単色にフォールバック: {err}）")

    print("③ 動画を書き出し中…（少し時間がかかります）")
    out = reel.render_reel_video(script, bg_map, args.out, fps=args.fps)
    print(f"\n✓ 完成: {out}")
    print("→ Instagramアプリで開き、トレンド音源を付けて投稿してください。")


if __name__ == "__main__":
    main()
