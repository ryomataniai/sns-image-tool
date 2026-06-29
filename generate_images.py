#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNS画像量産ツール (CLI版 v1.1)
================================
Gemini API (Nano Banana) でプロンプト一覧(CSV)から画像を一括生成する。
生成ロジックは core.py に集約（Web版 app.py と共通）。

使い方:
    export GEMINI_API_KEY=xxxxx
    python3 generate_images.py --prompts prompts_sample.csv --out output --dry-run
    python3 generate_images.py --prompts prompts_sample.csv --out output
"""

import argparse
import sys
import time
from pathlib import Path

import core


def main():
    ap = argparse.ArgumentParser(description="Gemini で SNS用画像を一括生成（CLI版）")
    ap.add_argument("--prompts", default="prompts_sample.csv", help="プロンプトCSV")
    ap.add_argument("--out", default="output", help="出力フォルダ")
    ap.add_argument("--model", default=core.DEFAULT_MODEL, help="モデル名")
    ap.add_argument("--aspect", default="4:5",
                    help="比率 例:4:5(縦) 1:1(正方) 9:16(リール) 16:9(横)")
    ap.add_argument("--size", default="1K", help="解像度 512/1K/2K/4K")
    ap.add_argument("--count", type=int, default=1, help="1プロンプトあたり枚数")
    ap.add_argument("--max", type=int, default=50, help="総枚数の安全上限")
    ap.add_argument("--dry-run", action="store_true", help="生成せず見積もりだけ")
    args = ap.parse_args()

    try:
        rows = core.load_prompts(args.prompts)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(f"✗ {e}")
    plan = core.build_plan(rows, args.count)

    if len(plan) > args.max:
        sys.exit(f"✗ 総枚数 {len(plan)} 枚が上限 {args.max} 枚を超過。"
                 f"--max を上げるか件数を減らしてください。")

    usd, jpy = core.estimate_cost(len(plan), args.model)
    print("=" * 56)
    print(f" 生成予定 : {len(plan)} 枚")
    print(f" モデル   : {args.model}")
    print(f" 比率/解像: {args.aspect} / {args.size}")
    print(f" 推定コスト: ≈ ${usd:.2f}  (約 {jpy:.0f} 円・参考値)")
    print(f" 出力先   : {args.out}/")
    print("=" * 56)

    if args.dry_run:
        for i, (pid, pr) in enumerate(plan, 1):
            print(f"  [{i:>3}] {pid}  | {pr[:46]}")
        print("\n(dry-run: 実生成はしていません)")
        return

    try:
        client = core.get_client()
    except RuntimeError as e:
        sys.exit(f"✗ {e}")

    Path(args.out).mkdir(parents=True, exist_ok=True)
    ok = 0
    t0 = time.time()
    for i, (pid, pr) in enumerate(plan, 1):
        out_path = Path(args.out) / f"{pid}.png"
        print(f"[{i}/{len(plan)}] {pid} ...", flush=True)
        data, err = core.generate_image_bytes(
            client, pr, args.model, args.aspect, args.size
        )
        if data:
            out_path.write_bytes(data)
            ok += 1
            print(f"  ✓ {out_path}")
        else:
            print(f"  ✗ 失敗: {err}")

    dt = time.time() - t0
    print("\n" + "=" * 56)
    print(f" 完了: {ok}/{len(plan)} 枚成功  ({dt:.0f}秒)")
    print(f" 保存先: {Path(args.out).resolve()}/")
    print("=" * 56)


if __name__ == "__main__":
    main()
