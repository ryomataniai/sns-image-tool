#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DL1回ぶんの歩留まりを、過去回と比較できる形で出す。

    python3 yield_report.py --base ../SUUMO入稿_75枠_20260806 \
        --since-ts 20260819 --adopted ../SUUMO入稿_75枠_20260806/_採否YYYYMMDD.txt

■何を出すか（2026-08-18 に「歩留まりだけでは切り分けられない」と決めた3項目）
  1. 棟ごとの採否 ★**棟単位で全滅する棟の数がメカニズムの直接の証拠**
  2. サムティ系（S-RESIDENCE / S-FORT）の比率
  3. 棟数 / 室数の比（どれだけ散ったか）
"""
from __future__ import annotations

import argparse
import collections
import glob
import os
import re
import sys
import unicodedata as ud
from pathlib import Path

# ★★歩留まりを過去回と比較するときは 8/13 を混ぜない。
#   8/13の✗ラベルは「室内写真ゼロ」と「少なすぎる」を混ぜており、
#   8/14以降（ゼロのみ）と定義が違う。
#   比較可能なのは 8/12(100%) と 8/14(72%) の2点だけ。
#
#   ★この注意をここに書いてあるのは、READMEに書いても**比較する場面では出てこない**から。
#     2026-08-18 に、自分で「ラベルの定義が版によって違う。混ぜて評価しないこと」と
#     README に書いた4日後、同じ2つの数字を並べて比較した。
#     記録の場所を判断の場所に寄せる、が対処。
HISTORY = [
    # (ラベル, DL数, 採用数, サムティ系比率, 比較可能か, 備考)
    ("8/12", 35, 35, 0.83, True, "棟は固まっている（収穫順の先頭）"),
    ("8/13", 80, 31, 0.00, False, "★✗ラベルの定義が違う（ゼロ＋少なすぎる）。混ぜない"),
    ("8/14", 40, 29, 0.00, True, "既に散らしている（未DL棟優先＋棟上限5）"),
]

# 2026-08-18 に事前登録した予測。★事後に動かさないこと（動かすなら経緯を残す）
PREDICTION = "60〜75%"
PREDICTION_NOTE = ("当初 50〜60% としたが同日に修正。8/14が既に散らして72%なので、"
                   "8/12(固まっている・100%)→8/14 ほどの落差は残っていないため")
SAMTY_PREFIXES = ("S-RESIDENCE", "S-FORT")

# ★HISTORY は手で書く。追記を忘れると比較対象が古いまま気づけないので、
#   実行のたびに最終更新日を印字して目に入れる（強制はしない・気づければ十分）。
#   ★HISTORY に行を足したら**この日付も必ず更新する**。
HISTORY_UPDATED = "2026-08-14"


def nfc(s):
    return ud.normalize("NFC", str(s))


def batch_keys(base: Path, since_ts: str):
    """そのタイムスタンプでDLした部屋キー。"""
    out = set()
    for p in glob.glob(str(base / "01_マイソク" / "*.pdf")):
        m = re.match(r"(.+)_(\d{14})\.pdf$", nfc(os.path.basename(p)))
        if m and m.group(2).startswith(since_ts):
            out.add(m.group(1))
    return out


def judge(rate: float) -> str:
    """事前に決めた判定基準（2026-08-18）。★結果を見てから基準を動かさない。"""
    if rate >= 0.75:
        return "予測より良い。シャッフルの影響はほぼ無い"
    if rate >= 0.60:
        return f"予測（{PREDICTION}）と整合（8/14と同水準）"
    return "予測より悪い。★棟単位の全滅数を見る（下記）"


def main(argv=None):
    ap = argparse.ArgumentParser(description="DL1回ぶんの歩留まりを過去回と比較する")
    ap.add_argument("--base", required=True)
    ap.add_argument("--since-ts", required=True, help="DLのタイムスタンプ前方一致（例 20260819）")
    ap.add_argument("--adopted", required=True, help="採否◯の部屋キー一覧（改行区切り）")
    a = ap.parse_args(argv)
    base = Path(a.base).expanduser()

    dl = batch_keys(base, a.since_ts)
    if not dl:
        print(f"✗ {a.since_ts} でDLした室が1件も無い（照合不能。合格ではない）")
        return 3
    adopted = {nfc(x.strip()) for x in Path(a.adopted).read_text(encoding="utf-8").splitlines()
               if x.strip()}
    ok = dl & adopted
    rate = len(ok) / len(dl)

    print(f"■ {a.since_ts} のDL {len(dl)}室 → 採用 {len(ok)}室 = 歩留まり {rate:.0%}")
    print(f"   事前の予測: {PREDICTION}（{PREDICTION_NOTE}）")
    print(f"   判定: {judge(rate)}")
    print()

    # ── 1. 棟ごとの採否（★全滅棟がメカニズムの直接の証拠）──────────
    by = collections.defaultdict(lambda: [0, 0])       # 棟 → [DL, 採用]
    for k in dl:
        b = k.rsplit("_", 1)[0]
        by[b][0] += 1
        by[b][1] += k in adopted
    zero = [b for b, (n, y) in by.items() if y == 0]
    full = [b for b, (n, y) in by.items() if y == n]
    print(f"■ 棟ごとの採否: {len(by)}棟")
    print(f"   ★全滅した棟（採用0） {len(zero)}棟 / {len(by)}棟 = {len(zero)/len(by):.0%}")
    for b in sorted(zero):
        print(f"      全滅 {b}（{by[b][0]}室）")
    print(f"   全室採用の棟 {len(full)}棟")
    print("   ※歩留まりだけ下がって全滅棟が増えていなければ、原因はシャッフルではない")
    print()

    # ── 2. サムティ系の比率 ─────────────────────────────
    samty = [k for k in dl if k.startswith(SAMTY_PREFIXES)]
    print(f"■ サムティ系（{'/'.join(SAMTY_PREFIXES)}）: {len(samty)}室 = {len(samty)/len(dl):.0%}")
    if samty:
        sy = sum(1 for k in samty if k in adopted)
        print(f"   うち採用 {sy}室 = {sy/len(samty):.0%}")
        other = [k for k in dl if k not in samty]
        if other:
            oy = sum(1 for k in other if k in adopted)
            print(f"   サムティ系以外 {len(other)}室 → 採用 {oy}室 = {oy/len(other):.0%}")
    print()

    # ── 3. 棟数/室数の比 ────────────────────────────────
    print(f"■ 散り方: {len(by)}棟 / {len(dl)}室 = 1棟あたり {len(dl)/len(by):.2f}室")
    print()

    # ── 過去回との比較（★8/13を混ぜない）──────────────────────
    print("■ 過去回との比較")
    print(f"   {'回':<6}{'DL':>5}{'採用':>5}{'歩留まり':>9}{'サムティ系':>10}  備考")
    for label, n, y, s_, usable, note in HISTORY:
        mark = "" if usable else "  ← 比較に使わない"
        print(f"   {label:<6}{n:>5}{y:>5}{y/n:>9.0%}{s_:>10.0%}  {note}{mark}")
    print(f"   {a.since_ts[-4:]:<6}{len(dl):>5}{len(ok):>5}{rate:>9.0%}"
          f"{len(samty)/len(dl):>10.0%}  今回")
    usable = [(l, y / n) for l, n, y, _s, u, _o in HISTORY if u]
    print(f"   ★比較可能なのは {', '.join(f'{l}({r:.0%})' for l, r in usable)} の"
          f"{len(usable)}点だけ（上のコメントを読むこと）")
    # ★HISTORY の鮮度。手書きなので、追記されていないことに実行時に気づけるようにする
    import datetime
    today = datetime.date.today().isoformat()
    stale = HISTORY_UPDATED < today
    print(f"   HISTORY 最終更新: {HISTORY_UPDATED}（今日は {today}）"
          + ("  ★追記されていない可能性" if stale else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
