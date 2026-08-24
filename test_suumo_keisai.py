#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掲載指示の検証ロジックのテスト。ブラウザも通信も使わない。

実行: python3 test_suumo_keisai.py

■測ること（2026-08-17 の偽合格の再発防止）
  ★「情報が無い」を「問題が無い」と読まないこと。
    1. 読み取りが None（読めなかった）→ **照合不能**（終了コード3）。合格にしない
    2. 読み取りが 0件           → **照合不能**（終了コード3）。合格にしない
    3. 指示した室が欠けている    → 不一致（1）
    4. 落とした室が残っている    → 不一致（1）
    5. 件数が期待と違う          → 不一致（1）
    6. 全部揃っている            → 合格（0）
  ★2・3・4・5は**別々に**判定されること（1つにまとめると原因が分からない）。

■なぜテストにするか
2026-08-17 に、完了画面から読み直したため結果が空になり、
**空集合に対する「変化なし」を『◯ 対象外のレコードは1件も変わっていない』と表示した。**
そのまま合格として報告できた。ここは実データでは再現しにくい（意図的に空を作れない）ので、
関数を直接叩いて固定する。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("K", Path(__file__).with_name("suumo_keisai.py"))
K = importlib.util.module_from_spec(spec)
sys.argv = ["x"]
spec.loader.exec_module(K)

_fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def run(stub, codes, dropped=None, expect_total=None):
    """read_shijizumi を差し替えて verify を走らせる。→ (終了コード, ログ)。"""
    logs = []
    orig = K.read_shijizumi
    K.read_shijizumi = lambda page, log, page_size=200: (log("(stub)"), stub)[1]
    try:
        rc = K.verify(None, codes, dropped, expect_total, logs.append)
    finally:
        K.read_shijizumi = orig
    return rc, "\n".join(logs)


def row(nm="テスト物件 101号室", shiji="掲載", end="26/08/25 ―"):
    return {"物件": nm, "指示ネット": shiji, "掲載終了日": end}


def main():
    A, B, C = "100521000001", "100521000002", "100519000003"

    # 1. 読めなかった（None）→ 照合不能
    rc, log = run((None, None), [A])
    check("読み取りがNoneなら照合不能（3）", rc == 3, f"rc={rc}")
    # ★assertion の書き方に注意：本体は「『問題なし』ではない」と書くので
    #   「問題なし」の部分一致で判定すると、正しい実装が落ちる（最初にそうなった）。
    #   見るべきは「合格と主張していないこと」と「照合不能と言っていること」。
    check("合格と主張しない", "◯ 合格" not in log)
    check("照合不能と明示する", "照合不能" in log)

    # 2. 0件 → 照合不能（★ここが偽合格の本体）
    rc, log = run(({}, 0), [A])
    check("0件なら照合不能（3）。合格にしない", rc == 3, f"rc={rc}")
    check("0件のときに『合格』と出さない", "◯ 合格" not in log)

    # 3. 指示した室が欠けている → 不一致
    rc, log = run(({A: row()}, 1), [A, B])
    check("指示した室が欠けていれば不一致（1）", rc == 1, f"rc={rc}")
    check("欠けている室を名指しする", B in log)
    check("『指示した室が全部ある』が✗になる", "2. 指示した室が全部ある: ✗" in log)

    # 4. 落とした室が残っている → 不一致
    rc, log = run(({A: row(), C: row()}, 2), [A], dropped=[C])
    check("落とした室が残っていれば不一致（1）", rc == 1, f"rc={rc}")
    check("『落とした室が1件も残っていない』が✗になる",
          "3. 落とした室が1件も残っていない: ✗" in log)
    check("★2と3が別々に判定される（2は◯のまま）",
          "2. 指示した室が全部ある: ◯" in log)

    # 5. 件数が期待と違う → 不一致
    rc, log = run(({A: row()}, 1), [A], expect_total=45)
    check("件数が期待と違えば不一致（1）", rc == 1, f"rc={rc}")
    check("件数の判定が独立して出る", "4. 件数が期待どおり: ✗" in log)

    # 6. 全部揃っている → 合格
    rc, log = run(({A: row(), B: row()}, 2), [A, B], dropped=[C], expect_total=2)
    check("全部揃えば合格（0）", rc == 0, f"rc={rc}")
    check("4項目すべてが◯で出る", log.count("◯") >= 4, str(log.count("◯")))

    # 7. --dropped / --expect-total を省略しても、省略を「合格」に混ぜない
    rc, log = run(({A: row()}, 1), [A])
    check("省略した項目は『判定しない』と明示される",
          "落とした室: 指定なし（判定しない）" in log
          and "--expect-total 未指定なので判定しない" in log)
    check("省略時も合格になる（判定した項目は通っている）", rc == 0, f"rc={rc}")

    # 8. read_codes：12桁以外を弾く
    ok, bad = K.read_codes("100521000001, 100521000002\n#コメント\nサンプルレジデンス_0703")
    check("12桁だけを拾う", ok == [A, B], str(ok))
    check("12桁でないものを bad に落とす", bad == ["サンプルレジデンス_0703"], str(bad))
    ok2, _ = K.read_codes("100521000001,100521000001")
    check("重複コードは1件にまとめる", ok2 == [A], str(ok2))

    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
