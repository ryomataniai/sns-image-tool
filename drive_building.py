#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1: 棟1つを通しで登録するドライバ（常駐モードの suumo_register.py に指示を出す）。

    python3 drive_building.py --cmd /tmp/cmd --result /tmp/r.log \
        --first 06_登録データ/<1室目>.json 06_登録データ/<2室目>.json ...

■何をするか
  1. 1室目のフォームに人が『らくらく交通入力』を入れるのを**待つ**（ポーリング）
     ※交通はSUUMOに算出させる。マイソクの徒歩分数を転記すると実際より近く表示され得る
       （実測: マイソク 6/6/6分 に対しSUUMOの算出は 9/8/11分）ので転記しない。
  2. その棟の交通を保存（savetransit）
  3. 1室目を登録・照合（submit）
  4. 2室目以降を全自動で登録・照合（room）

■なぜドライバを分けるか
1室あたり 5〜6回のコマンド往復があり、手で回すと取りこぼす。棟単位で1コマンドにする。
常駐プロセスとはファイル（cmd / result）だけでやりとりするので、再起動しても壊れない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 交通1の駅名が入ったかを見るJS（1行で送る・// コメントは使わない）
CHECK_TRANSIT = ('(() => { const e = document.getElementsByName('
                 '"${bukkenInputForm.pkgEkiNmDisp}")[0]; '
                 'return e ? (e.value || "") : "(欄なし)"; })()')


def send(cmd: Path, payload: str):
    cmd.write_text(payload, encoding="utf-8")


def wait_line(res: Path, pattern: str, timeout: int, poll: float = 4.0):
    """result ログに pattern（正規表現）が現れるまで待つ。→ その行 or None。
    ★既存の行に一致しないよう、呼び出し時点の行数から先だけを見る。"""
    rx = re.compile(pattern)
    start = len(res.read_text(encoding="utf-8").splitlines()) if res.exists() else 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        lines = res.read_text(encoding="utf-8").splitlines() if res.exists() else []
        for ln in lines[start:]:
            if rx.search(ln):
                return ln
        time.sleep(poll)
    return None


def eval_js(cmd: Path, res: Path, js: str, timeout=60):
    send(cmd, "eval:" + " ".join(js.split("\n")))
    ln = wait_line(res, r"eval →", timeout)
    return ln


def main(argv=None):
    ap = argparse.ArgumentParser(description="棟1つを通しで登録する")
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--first", required=True, help="1室目のJSON（人が交通を入れる室）")
    ap.add_argument("rest", nargs="*", help="2室目以降のJSON")
    ap.add_argument("--transit-wait", type=int, default=1800,
                    help="交通入力を待つ上限秒（既定30分）")
    ap.add_argument("--skip-wait", action="store_true",
                    help="交通が既に入っている前提で待たない")
    a = ap.parse_args(argv)
    cmd, res = Path(a.cmd), Path(a.result)
    first = Path(a.first)
    rec = json.loads(first.read_text(encoding="utf-8"))
    bldg = rec["form"]["bukkenNm"]
    print(f"■ {bldg}: 1室目={rec['key']} / 残り{len(a.rest)}室", flush=True)

    # ── 1) 交通入力を待つ ────────────────────────────────────────
    if not a.skip_wait:
        print(f"① 『らくらく交通入力』を待っています（最大{a.transit_wait}秒）", flush=True)
        t0 = time.time()
        while time.time() - t0 < a.transit_wait:
            ln = eval_js(cmd, res, CHECK_TRANSIT)
            got = ""
            if ln:
                m = re.search(r"eval → '?(.*?)'?$", ln.strip())
                got = (m.group(1) if m else "").strip()
            if got and got not in ("(欄なし)", "None", ""):
                print(f"   交通を検知（最寄駅={got}） {time.time()-t0:.0f}秒", flush=True)
                break
            time.sleep(12)
        else:
            print("★交通入力が確認できないまま時間切れ。中止する", flush=True)
            return 3

    # ── 2) 棟の交通を保存 ───────────────────────────────────────
    print("② 棟の交通を保存", flush=True)
    send(cmd, f"savetransit:{first.resolve()}")
    ln = wait_line(res, r"交通を保存|交通が入っていない", 180)
    print("  ", (ln or "★応答なし").strip(), flush=True)
    if not ln or "交通が入っていない" in ln:
        return 3

    # ── 3) 1室目を登録 ─────────────────────────────────────────
    print("③ 1室目を登録", flush=True)
    send(cmd, f"submit:{first.resolve()}")
    ln = wait_line(res, r"照合PASS|照合FAIL|登録しなかった|\[NG\]", 900)
    print("  ", (ln or "★応答なし").strip(), flush=True)
    if not ln or "PASS" not in ln:
        print("★1室目で止まった。次の室に進まない（受入基準4-1）", flush=True)
        return 1

    # ── 4) 2室目以降 ───────────────────────────────────────────
    ok, ng = 1, 0
    for jp in a.rest:
        p = Path(jp)
        key = json.loads(p.read_text(encoding="utf-8"))["key"]
        print(f"④ {key}", flush=True)
        send(cmd, f"room:{p.resolve()}")
        ln = wait_line(res, r"完了 コード=|照合FAIL|登録しなかった|埋め込みで未解決|\[NG\]", 900)
        print("  ", (ln or "★応答なし").strip(), flush=True)
        if ln and "完了 コード=" in ln:
            ok += 1
        else:
            ng += 1
            print("★この室で止まった。以降を中止する（同じ原因で連続失敗させない）", flush=True)
            break
    print(f"\n■ {bldg}: 完了 {ok}室 / 失敗 {ng}室", flush=True)
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
