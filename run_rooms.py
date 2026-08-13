#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1: 部屋のリストを常駐モードへ順に投げて登録する実行器。

    python3 run_rooms.py --cmd /tmp/cmd --result /tmp/r.log 06_登録データ/*.json

■なぜ専用の実行器を作るか
shellのループで待機条件を書くと**部屋キーの部分一致で誤マッチする**（実際に
「_808」で待って S-FORT桜川南_808 の完了行に当たり、S-RESIDENCEドーム前千代崎_808 を
飛ばした）。部屋キーの**完全一致**で待ち、飛ばしも取りこぼしも起きないようにする。
■1室の失敗で全体を止めない（依頼文§4 受入基準4）。ただし**同じ棟で2連続失敗したら**
  その棟は打ち切る（同じ原因で無駄に叩かない）。最後にサマリを出す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


def wait_for(res: Path, key: str, timeout: int, poll: float = 5.0):
    """その部屋キーの結果行が出るまで待つ。→ ('OK'|'NG'|None, 行)。
    ★キーは完全一致で見る（部分一致は別の部屋の行に当たる）。"""
    start = len(res.read_text(encoding="utf-8").splitlines()) if res.exists() else 0
    ok_rx = re.compile(re.escape(key) + r" 完了 コード=(\d{12})")
    ng_rx = re.compile(r"\[NG\]")
    t0 = time.time()
    while time.time() - t0 < timeout:
        lines = res.read_text(encoding="utf-8").splitlines() if res.exists() else []
        block = lines[start:]
        for i, ln in enumerate(block):
            m = ok_rx.search(ln)
            if m:
                return "OK", m.group(1)
            if ng_rx.search(ln) and key.split("_")[-1] in " ".join(block[max(0, i - 3):i + 1]):
                return "NG", " / ".join(x.strip() for x in block[i:i + 6])
        time.sleep(poll)
    return None, "時間切れ"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--timeout", type=int, default=600, help="1室あたりの上限秒")
    ap.add_argument("--retry", type=int, default=1, help="失敗した室の再試行回数")
    ap.add_argument("rooms", nargs="+")
    a = ap.parse_args(argv)
    cmd, res = Path(a.cmd), Path(a.result)
    results = []
    fails_by_bldg = {}
    for jp in a.rooms:
        p = Path(jp)
        rec = json.loads(p.read_text(encoding="utf-8"))
        key, bldg = rec["key"], rec["form"]["bukkenNm"]
        if fails_by_bldg.get(bldg, 0) >= 2:
            print(f"  SKIP {key}（同じ棟で2連続失敗したため打ち切り）", flush=True)
            results.append((key, "SKIP", "棟を打ち切り"))
            continue
        status, detail = None, ""
        for attempt in range(a.retry + 1):
            if attempt:
                print(f"  再試行 {attempt}: {key}", flush=True)
            cmd.write_text(f"room:{p.resolve()}", encoding="utf-8")
            status, detail = wait_for(res, key, a.timeout)
            if status == "OK":
                break
        if status == "OK":
            print(f"  OK   {key} → {detail}", flush=True)
            fails_by_bldg[bldg] = 0
        else:
            print(f"  NG   {key}: {str(detail)[:160]}", flush=True)
            fails_by_bldg[bldg] = fails_by_bldg.get(bldg, 0) + 1
        results.append((key, status or "TIMEOUT", detail))
    ok = [r for r in results if r[1] == "OK"]
    print(f"\n■ 完了 {len(ok)}室 / 失敗 {len(results) - len(ok)}室", flush=True)
    for k, st, d in results:
        if st != "OK":
            print(f"   {st:<8}{k}: {str(d)[:120]}", flush=True)
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
