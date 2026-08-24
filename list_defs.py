#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ファイルのトップレベル定義（def / class）を一覧する。パッチの前後で差分を見るため。

    python3 list_defs.py realpro_dl.py > /tmp/before.txt
    …パッチを当てる…
    python3 list_defs.py realpro_dl.py | diff /tmp/before.txt -

■なぜ要るか
2026-08-14 に、文字列置換のパッチで `s[index('def A'):index('def C')]` と範囲を取り、
その間にあった `inventory_rent_range` ごと消す指定になった。**書き込み前に別の例外で
止まったから助かっただけ**で、通っていたら「関数が丸ごと消えたのに構文は通る」という
気づきにくい壊れ方になっていた。py_compile では検出できない（消えても構文は正しい）。

■使い方の原則
関数の**消滅は常に事故**。増えるのは意図的なことが多い。差分に `-` が出たら必ず止まる。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def defs_of(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(f"def {n.name}")
        elif isinstance(n, ast.ClassDef):
            out.append(f"class {n.name}")
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(f"class {n.name}.{m.name}")
    return sorted(out)


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    for f in argv:
        p = Path(f)
        if len(argv) > 1:
            print(f"# {p.name}")
        for d in defs_of(p):
            print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
