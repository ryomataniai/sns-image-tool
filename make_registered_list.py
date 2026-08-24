#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登録済み一覧CSVを作る（8/26の掲載枠選定で賃料帯を見るための表）。

    python3 make_registered_list.py --data 06_登録データ --codes codes.txt \
        --out ../_登録済み一覧_20260814.csv

codes.txt は「部屋キー<TAB or |>物件コード」の改行区切り。

■なぜスクリプトにするか
一覧の生成をその場のワンライナーで書いたら、賃料の万円→円の変換を書き直して
2室ぶん壊した（7/15 を 85,000円と出した。正は 71,500円）。**変換は
suumo_fields.manen_to_yen 1箇所だけ**を使い、さらに _review.csv に入っている
PDF由来の円表記と突き合わせて、合わないなら書き出さずに落とす。
■自己照合が要る理由
この表は「掲載する室を賃料帯で選ぶ」ために使う。壊れた値は
「優先帯から外れた」という誤判断になって、正しい室を落とす。
表が黙って壊れることが一番まずい。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import suumo_fields

COLS = ["部屋キー", "物件コード", "物件名", "号室", "賃料円", "管理費円",
        "専有面積", "所在階", "名寄せスコア", "元付会社", "掲載"]


def read_codes(p: Path):
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = re.split(r"[\t|,]", ln, 1)
        if len(parts) != 2:
            raise SystemExit(f"codes の行が読めない: {ln!r}")
        out[parts[0].strip()] = parts[1].strip()
    return out


def pdf_yen(review: dict, key: str, col: str):
    """_review.csv の『7/15 (71,500円)』からPDF由来の円を取る。→ int or None。"""
    m = re.search(r"\((\d[\d,]*)円\)", (review.get(key) or {}).get(col, ""))
    return int(m.group(1).replace(",", "")) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="06_登録データ")
    ap.add_argument("--codes", required=True, help="部屋キー|物件コード の一覧")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scores", help="部屋キー|名寄せスコア の一覧（無ければ空欄）")
    a = ap.parse_args(argv)

    data = Path(a.data).expanduser()
    codes = read_codes(Path(a.codes).expanduser())
    scores = read_codes(Path(a.scores).expanduser()) if a.scores else {}
    rv = data / "_review.csv"
    review = {}
    if rv.is_file():
        review = {r["key"]: r for r in csv.DictReader(rv.open(encoding="utf-8-sig"))}
    else:
        print(f"⚠ {rv} が無い。PDF由来の値と突き合わせられない", file=sys.stderr)

    rows, ng = [], []
    for key, code in sorted(codes.items()):
        jp = data / f"{key}.json"
        if not jp.is_file():
            ng.append(f"{key}: JSONが無い（{jp}）")
            continue
        f = json.loads(jp.read_text(encoding="utf-8"))["form"]
        chin = suumo_fields.manen_to_yen(f.get("chinryo1"), f.get("chinryo2"))
        kanri = suumo_fields.manen_to_yen(f.get("kanrihi1"), f.get("kanrihi2"))
        # ★PDF由来の値と突き合わせる。合わないなら書かずに落とす。
        for label, got, col in (("賃料", chin, "chinryo"), ("管理費", kanri, "kanrihi")):
            want = pdf_yen(review, key, col)
            if want is not None and got != want:
                ng.append(f"{key}: {label} 算出={got} PDF由来={want}")
        if not re.fullmatch(r"\d{12}", code):
            ng.append(f"{key}: 物件コードが12桁でない（{code!r}）")
        rows.append({
            "部屋キー": key, "物件コード": code, "物件名": f.get("bukkenNm", ""),
            "号室": f.get("heyaNo", ""),
            "賃料円": f"{chin:,}" if chin is not None else "",
            "管理費円": f"{kanri:,}" if kanri is not None else "",
            "専有面積": f"{f.get('menseki1','')}.{f.get('menseki2','')}",
            "所在階": f.get("kai", ""), "名寄せスコア": scores.get(key, ""),
            "元付会社": f.get("mototsukeGyoshaNm", ""), "掲載": "未",
        })

    dup = [c for c in codes.values() if list(codes.values()).count(c) > 1]
    if dup:
        ng.append(f"物件コードが重複している: {sorted(set(dup))}")
    if ng:
        print(f"★{len(ng)}件の不一致。CSVは書き出さない:", file=sys.stderr)
        for x in ng:
            print(f"   {x}", file=sys.stderr)
        return 1

    out = Path(a.out).expanduser()
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    yens = [int(r["賃料円"].replace(",", "")) for r in rows if r["賃料円"]]
    print(f"{out}\n  {len(rows)}室 / 賃料 {min(yens):,}〜{max(yens):,}円 "
          f"（PDF由来の値と全件一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
