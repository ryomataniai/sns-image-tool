#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ソースを文字列置換で直すときの事前ガード。

    from patch_guard import replace_once
    s = replace_once(s, old, new, defs=1)     # old に def が1つだけあるはず

■なぜ要るか
2026-08-17 と 08-18 に、`s[s.index("def A"):s.index("def C")]` のように範囲を取って
**その間にあった別の関数ごと消す**指定を2回書いた。

  1回目: inventory_rent_range（realpro_dl.py）
  2回目: test_single_room（test_batch_suumo.py）

どちらも書き込み前に別の理由で例外が出て助かっただけで、**通っていれば関数が丸ごと消えた**。
`py_compile` では検出できない（消えても構文は正しい）。

■事前と事後の2段構え
  事前（このファイル）: 置換文字列を作った時点で `def ` の数を数える。
                        1関数を直すつもりなら 1 のはず。2つ入っていたら範囲を間違えている。
  事後（list_defs.py）: 置換後にトップレベル定義の一覧を diff して、消えた関数が無いか見る。

両方あると漏れない。事前だけだと「範囲は合っているが中身を壊した」を拾えず、
事後だけだと「気づくのが書き込んだ後」になる。
"""
from __future__ import annotations

import re
import sys

_DEF_RX = re.compile(r"^\s*(?:async\s+)?def\s+\w+|^\s*class\s+\w+", re.M)


class PatchGuard(Exception):
    """置換の範囲が意図と違う。★書き込む前に止める。"""


def count_defs(text: str) -> int:
    """テキストに含まれるトップレベル/ネストの def・class の数。"""
    return len(_DEF_RX.findall(text))


def replace_once(text: str, old: str, new: str, defs: int | None = None) -> str:
    """old を new に1回だけ置換する。→ 置換後のテキスト。

    defs を指定すると、**old に含まれる def/class の数**がそれと一致するかを先に確かめる。
    合わなければ `PatchGuard` を投げて置換しない（範囲の取り違えの検出）。
    old が0回または2回以上出てくる場合も投げる。
    """
    n = text.count(old)
    if n != 1:
        raise PatchGuard(f"old が {n}回 見つかった（1回であるべき）")
    if defs is not None:
        got = count_defs(old)
        if got != defs:
            names = _DEF_RX.findall(old)
            raise PatchGuard(
                f"old に def/class が {got}個ある（期待 {defs}個）。"
                f"範囲を取り違えている可能性が高い: {[x.strip() for x in names]}")
    return text.replace(old, new, 1)


def _self_test():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
        ok = ok and cond

    src = "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\ndef c():\n    return 3\n"
    # 1関数だけ置換 → 通る
    out = replace_once(src, "def b():\n    return 2", "def b():\n    return 22", defs=1)
    check("1関数の置換は通る", "return 22" in out and "def c" in out)
    # ★2関数をまたぐ範囲 → 止まる（これが今回の再発防止の本体）
    span = src[src.index("def b():"):src.index("def c():")] + "def c():\n    return 3\n"
    try:
        replace_once(src, span, "def c():\n    return 3\n", defs=1)
        check("★2関数をまたぐ範囲で止まる", False, "止まらなかった")
    except PatchGuard as e:
        check("★2関数をまたぐ範囲で止まる", "2個ある" in str(e), str(e)[:70])
    # old が見つからない
    try:
        replace_once(src, "def zzz():", "x", defs=1)
        check("見つからない old で止まる", False)
    except PatchGuard as e:
        check("見つからない old で止まる", "0回" in str(e))
    # old が複数回
    try:
        replace_once("xx\nxx\n", "xx", "y")
        check("複数回ヒットする old で止まる", False)
    except PatchGuard as e:
        check("複数回ヒットする old で止まる", "2回" in str(e))
    # defs=0（関数の外側を直す）
    out = replace_once("A = 1\nB = 2\n", "A = 1", "A = 9", defs=0)
    check("defs=0（定数の書き換え）は通る", out.startswith("A = 9"))
    check("class も数える", count_defs("class X:\n    def m(self): pass\n") == 2)
    print("\n" + ("✅ 全PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_self_test())
