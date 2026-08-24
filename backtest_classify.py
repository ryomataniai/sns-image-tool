#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classify-only の結果に採否の判定規則を当てて、目視ラベルとの一致を測る。

    python3 backtest_classify.py --classify _classify_*.json --base ../SUUMO入稿_75枠_20260806

■測ること
  「✗（室内写真ゼロ）を✗と判定できるか」。
  ★谷合さんの指示：**再現率より適合率**。◯を✗と誤判定するのは在庫が減るだけだが、
    ✗を◯と誤判定すると画像化$0.35と登録時間が無駄になる。
    したがって見るべき数字は「◯と予測した室のうち本当に◯だった割合」（適合率）と
    「✗を✗と拾えた割合」（✗の再現率）。全体の正解率は見ない（母数が偏っていて意味がない）。

■測っていないこと
  △（居室欠け）の判定。1室しかラベルが無く、評価に耐えない。集計から外す。

■規則をコードに埋め込まない理由
  規則を1つ決め打ちにすると、外れたときに「規則が悪いのか分類が悪いのか」が分からない。
  複数の規則を同じデータに当てて並べる。どれも実用にならなければ「ならない」と出す。
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import unicodedata as ud
from pathlib import Path

# 室内が写っているとみなすコード。EXTERIOR/MAP/BLANK/FLOORPLAN は室内ではない。
# ★OTHER は「室内だが判別不能」という定義だが、実際は郵便受け・インターホン・
#   販促アイコンの受け皿になりうる。室内の証拠として数えてよいかは規則ごとに変える。
INDOOR = {"LIVING", "BEDROOM", "KITCHEN", "BATH", "WASH", "TOILET",
          "WASHER_PAN", "ENTRANCE", "HALLWAY", "STORAGE", "BALCONY"}
# SUUMOの名寄せで1カテゴリ5点になる部屋（間取り図は別扱い）
FIVE_PT = {"BEDROOM", "LIVING", "KITCHEN", "BATH"}
# 生活空間そのもの。玄関・廊下・収納・バルコニーは室内だが「部屋の写真」ではない
CORE_ROOM = {"LIVING", "BEDROOM", "KITCHEN", "BATH", "WASH", "TOILET"}


def nfc(s):
    return ud.normalize("NFC", str(s))


def load_labels(base: Path):
    """3セットの正解ラベルを読む。→ {部屋キー: '◯'|'✗'|'△'}。"""
    lab = {}

    def keys_of(ts):
        out = set()
        for p in glob.glob(str(base / "01_マイソク" / "*.pdf")):
            m = re.match(r"(.+)_(\d{14})\.pdf$", nfc(os.path.basename(p)))
            if m and m.group(2).startswith(ts):
                out.add(m.group(1))
        return out

    # 8/12：35室すべて◯（歩留まり100%）
    for k in keys_of("20260812"):
        lab[k] = "◯"
    # 8/13：80室のうち _採用31室.txt にあるものが◯、残りが✗
    adopted = {nfc(l.strip()) for l in (base / "_採用31室.txt").read_text(
        encoding="utf-8").splitlines() if l.strip()}
    for k in keys_of("20260813"):
        lab[k] = "◯" if k in adopted else "✗"
    # 8/14：CSVの採否列（◯ / ✗室内ゼロ / △居室欠け）
    p14 = base / "_採否判定_20260814.csv"
    if p14.is_file():
        for r in csv.DictReader(p14.open(encoding="utf-8-sig")):
            v = (r.get("採否") or "").strip()
            lab[nfc(r["部屋キー"])] = "◯" if v.startswith("◯") else (
                "△" if v.startswith("△") else "✗")
    return lab


# ── 判定規則。(名前, 説明, 関数) 関数は codes（各画像のコード配列）→ True(=◯) ──
def _flat(codes):
    return [set(c) for c in codes]


RULES = [
    ("R1 室内1枚以上", "室内コードが1枚でもあれば◯（OTHERは数えない）",
     lambda cs: any(s & INDOOR for s in _flat(cs))),
    ("R2 室内2枚以上", "室内コードの写真が2枚以上",
     lambda cs: sum(1 for s in _flat(cs) if s & INDOOR) >= 2),
    ("R3 室内3枚以上", "室内コードの写真が3枚以上",
     lambda cs: sum(1 for s in _flat(cs) if s & INDOOR) >= 3),
    ("R4 5点カテゴリ1つ", "居室/リビング/キッチン/浴室のどれかがある",
     lambda cs: any(s & FIVE_PT for s in _flat(cs))),
    ("R5 5点カテゴリ2種", "居室/リビング/キッチン/浴室のうち2種類以上",
     lambda cs: len({c for s in _flat(cs) for c in s if c in FIVE_PT}) >= 2),
    ("R6 5点カテゴリ3種", "居室/リビング/キッチン/浴室のうち3種類以上",
     lambda cs: len({c for s in _flat(cs) for c in s if c in FIVE_PT}) >= 3),
    ("R7 生活空間2種", "居室/リビング/キッチン/浴室/洗面/トイレのうち2種類以上",
     lambda cs: len({c for s in _flat(cs) for c in s if c in CORE_ROOM}) >= 2),
    ("R8 居室あり", "BEDROOM または LIVING がある",
     lambda cs: any(s & {"BEDROOM", "LIVING"} for s in _flat(cs))),
    # ★R9/R10 は 8/13 の外れ4室を見てから足した規則＝**そのセットに後付けで合わせている**。
    #   8/13の✗は「室内写真ゼロ」ではなく「室内写真が少なすぎる」を含む広い判断だった
    #   （サンプルレジデンスA_903 は実物に居室とDKが2枚ある）。枚数の下限はそこから来ている。
    #   後付けなので、次の新しいセットで測り直すまで性能を信じないこと。
    ("R9 5点1つ+室内4枚", "5点カテゴリがあり、かつ室内写真が4枚以上（後付け）",
     lambda cs: any(s & FIVE_PT for s in _flat(cs))
     and sum(1 for s in _flat(cs) if s & INDOOR) >= 4),
    ("R10 5点2種+室内4枚", "5点カテゴリ2種以上、かつ室内写真4枚以上（後付け）",
     lambda cs: len({c for s in _flat(cs) for c in s if c in FIVE_PT}) >= 2
     and sum(1 for s in _flat(cs) if s & INDOOR) >= 4),
]


_ROOM_SUFFIX = re.compile(r"(_\d{3,4}|\s*\d{3,4}\s*号室)$")


def _bldg(key: str) -> str:
    """部屋キー → 棟名（末尾の号室を落とす）。→ 例 'ナントカ_903' なら 'ナントカ'。"""  # name-guard: ok（架空の例）
    return _ROOM_SUFFIX.sub("", key).strip()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--classify", nargs="+", required=True,
                    help="classify-only が出したJSON（複数可）")
    ap.add_argument("--base", required=True, help="SUUMO入稿_75枠_20260806")
    ap.add_argument("--detail", action="store_true", help="誤判定の室を全部出す")
    ap.add_argument("--hard", help="最難ケースとして内訳を見る棟名（既定: ◯✗が割れる棟を自動選択）")
    a = ap.parse_args(argv)

    base = Path(a.base).expanduser()
    lab = load_labels(base)
    data = {}
    for pat in a.classify:
        for f in sorted(glob.glob(pat)):
            for r in json.loads(Path(f).read_text(encoding="utf-8")):
                if r.get("codes"):
                    data[nfc(r["key"])] = r
    print(f"ラベル {len(lab)}室 / 分類済み {len(data)}室")
    miss = [k for k in lab if k not in data]
    if miss:
        print(f"⚠ 分類結果が無い {len(miss)}室（評価から外す）: {miss[:4]}")

    ev = [(k, lab[k], data[k]) for k in sorted(data) if k in lab and lab[k] != "△"]
    n_o = sum(1 for _k, l, _r in ev if l == "◯")
    n_x = len(ev) - n_o
    print(f"評価対象 {len(ev)}室（◯{n_o} / ✗{n_x}）※△は除外\n")

    print(f"{'規則':<22}{'✗再現率':>9}{'◯適合率':>10}{'見逃し':>7}{'取りこぼし':>9}  説明")
    print("-" * 96)
    best = []
    for name, desc, fn in RULES:
        pred = {k: fn(r["codes"]) for k, _l, r in ev}
        fp = [k for k, l, _r in ev if l == "✗" and pred[k]]      # ✗なのに◯＝課金の無駄
        fn_ = [k for k, l, _r in ev if l == "◯" and not pred[k]]  # ◯なのに✗＝在庫減
        tp_o = n_o - len(fn_)
        rec_x = (n_x - len(fp)) / n_x if n_x else 0
        prec_o = tp_o / (tp_o + len(fp)) if (tp_o + len(fp)) else 0
        print(f"{name:<22}{rec_x:>8.0%}{prec_o:>10.0%}{len(fp):>7}{len(fn_):>9}  {desc}")
        best.append((name, rec_x, prec_o, fp, fn_))

    print("\n★見逃し（✗なのに◯と判定＝$0.35が無駄になる室）")
    for name, _rx, _po, fp, _fn in best:
        if fp:
            print(f"  {name}: {len(fp)}室  " + ", ".join(fp[:6])
                  + (" …" if len(fp) > 6 else ""))
        else:
            print(f"  {name}: なし")

    # ★同一棟で正解が◯✗に割れる棟＝最難ケースを見る。
    #   以前は棟名を直書きしていたが、リポが Public なので実物件名はソースに置かない
    #   （2026-08-24）。**割れている棟を数えて選ぶ**ので、セットが変わっても追随する。
    #   棟名の直書きをやめた副産物として、次のセットで別の棟が割れたら自動でそちらを見る。
    groups: dict[str, set] = {}
    for _k, _l, _r in ev:
        groups.setdefault(_bldg(_k), set()).add(_l)
    split = sorted(b for b, ls in groups.items() if len(ls) > 1)
    target = a.hard or (split[0] if split else None)
    hard = [k for k in data if target and _bldg(k) == target] if target else []
    if hard:
        print(f"\n★{target}（同一棟で◯✗が割れる。割れている棟は全{len(split)}件）")
        for k in sorted(hard):
            flat = sorted({c for cs in data[k]["codes"] for c in cs})
            row = "  ".join(f"{n.split()[0]}={'◯' if f(data[k]['codes']) else '✗'}"
                            for n, _d, f in RULES)
            print(f"  {k:<34}正解={lab.get(k,'?')}  {row}")
            print(f"      {data[k]['n_photos']}枚: {' '.join(flat)}")

    if a.detail:
        print("\n■ 全室の内訳")
        for k, l, r in ev:
            flat = sorted({c for cs in r["codes"] for c in cs})
            print(f"  {l}  {k:<38}{r['n_photos']:>3}枚  {' '.join(flat)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
