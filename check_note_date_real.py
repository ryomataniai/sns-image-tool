#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_note_date の実データ検査（★テストではない。CI に入れない・落とさない）。

    python3 check_note_date_real.py [--out <パス>]

★単体テスト（test_note_date.py）と役割を分ける。
  単体テスト … 合成データのみ・実名ゼロ・外部依存ゼロ・CI で毎回・落ちる
  この検査   … 01_マイソク/ を全数走査・実名を含む・手で走らせる・★落とさない

★出力にマイソクの物件名が入るので、**リポジトリの外**へ書く（既定）。
  リポジトリ内に置くと pre-commit の name-guard に掛かる。掛かるのが正しいので、
  フックを緩めずこちらの置き場所を変える。

★見るのは「①以外で返った件数」。①以外が出たら、**その帳票に「出力日」が無い**＝
  新しい形式が来た合図。②③に落ちると生成日を名乗るので、古い情報を新しく見せる。
"""
import argparse
import glob
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

M = "../SUUMO入稿_75枠_20260806/01_マイソク/*.pdf"
_LBL = (r"(?:情報日付|情報登録日|情報公開日|情報更新日|更新日|作成日|掲載日|公開日"
        r"|募集日|登録日|出力日)")
_RULE1 = _LBL + r"[^\d]{0,6}(\d{4})[年/\-.](\d{1,2})"


def main() -> int:
    ap = argparse.ArgumentParser(description="data_note_date の実データ検査（落とさない）")
    ap.add_argument("--out", default=os.path.expanduser(
        "~/Library/Application Support/sns-studio/note_date_check.tsv"),
        help="★リポジトリの外へ書く（既定）。物件名を含むため")
    ap.add_argument("--gen", default="", help="生成日。既定は JST 当日")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), M)))
    if not files:
        print(f"■ マイソクが見つかりません（{M}）。★検査をスキップします（落としません）")
        return 0
    gen = a.gen or core.jst_date_str()
    rows, tally = [], Counter()
    for f in files:
        fa = core.parse_maisoku_facts(open(f, "rb").read()) or {}
        ft = str(fa.get("full_text", ""))
        rule = "①" if re.search(_RULE1, ft) else "②か③"
        got = core.data_note_date(fa, gen)
        tally[rule] += 1
        rows.append((os.path.basename(f), rule, got))

    print(f"■ 走査 {len(files)}件（生成日 {gen}）")
    for k in ("①", "②か③"):
        print(f"   {k:<6} {tally[k]:>4}件")
    other = [r for r in rows if r[1] != "①"]
    if other:
        print(f"\n★①以外が {len(other)}件。**その帳票に「出力日」が無い**＝新しい形式の合図。")
        print("   ②③に落ちると生成日を名乗る＝古い情報を新しく見せる。")
        for n, _r, g in other[:10]:
            print(f"   - {n[:52]}  → {g}")
        if len(other) > 10:
            print(f"   （他 {len(other) - 10}件。全件は {a.out}）")
    else:
        print("\n◯ 全件が規則①（出力日）で返っています")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write("file\trule\tnote_date\n")
        for r in rows:
            fp.write("\t".join(r) + "\n")
    print(f"\n■ 全件: {a.out}  （★リポジトリの外）")
    return 0        # ★落とさない


if __name__ == "__main__":
    sys.exit(main())
