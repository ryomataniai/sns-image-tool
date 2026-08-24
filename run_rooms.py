#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1: 部屋のリストを常駐モードへ順に投げて登録する実行器。

    python3 run_rooms.py --cmd /tmp/cmd --result /tmp/r.log 06_登録データ/*.json

■なぜ専用の実行器を作るか
shellのループで待機条件を書くと**部屋キーの部分一致で誤マッチする**（実際に
「_808」で待って S-FORTサンプル北_808 の完了行に当たり、S-RESIDENCEサンプル南_808 を
飛ばした）。部屋キーの**完全一致**で待ち、飛ばしも取りこぼしも起きないようにする。
■止め方は失敗の種類で分ける（2026-08-14 谷合さんの指示）。
  ・**照合FAIL＝全体を即停止**。登録は済んでいるのに内容が違う＝状態が悪い。
    そのまま次を登録すると、同じ原因の誤登録を増やすことになる。
  ・**未登録で終わった失敗（確認画面のエラー／埋め込み未解決／交通の自動入力失敗）は
    その室だけスキップして続行**。SUUMOには何も入っていないので、続けても状態は悪化しない。
    番地欠落・洋室畳数なしの室がここで落ちる想定。
■同じ棟で2連続スキップしたらその棟は打ち切る（同じ原因で無駄に叩かない）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


# ★登録済みで内容が違う＝全体を止める種類の失敗。ここだけは続行しない。
HALT_RX = re.compile(r"照合FAIL")


def wait_for(res: Path, key: str, timeout: int, start: int, poll: float = 4.0):
    """その部屋の結果行が出るまで待つ。→ ('OK'|'HALT'|'SKIP'|None, 詳細)。

    ★キーは完全一致で見る（部分一致は別の部屋の行に当たる。実際に「_808」で待って
      S-FORTサンプル北_808 の完了行に当たり、S-RESIDENCEサンプル南_808 を飛ばした）。
    ★start は**コマンドを書く前**に数えた行数を渡すこと。書いてから数えると、
      その間に出た行を読み飛ばして時間切れになる。
    """
    ok_rx = re.compile(re.escape(key) + r" 完了 コード=(\d{12})")
    t0 = time.time()
    while time.time() - t0 < timeout:
        lines = res.read_text(encoding="utf-8").splitlines() if res.exists() else []
        block = lines[start:]
        for i, ln in enumerate(block):
            m = ok_rx.search(ln)
            if m:
                return "OK", m.group(1)
            if "[NG]" in ln:
                # この室のコマンドを出したあとの行なので、[NG] はこの室のもの。
                detail = " / ".join(x.strip() for x in block[i:i + 6])
                return ("HALT" if HALT_RX.search(ln) else "SKIP"), detail
        time.sleep(poll)
    return None, "時間切れ"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--timeout", type=int, default=600, help="1室あたりの上限秒")
    ap.add_argument("--retry", type=int, default=1, help="失敗した室の再試行回数")
    # ★部屋キーに空白が入る（「サンプル レジデンスA_0801.json」）。
    #   shellの展開に頼るとクォート事故になるので、改行区切りのファイルで渡せるようにする。
    ap.add_argument("--rooms-file", help="JSONパスを改行区切りで並べたファイル")
    ap.add_argument("rooms", nargs="*")
    a = ap.parse_args(argv)
    if a.rooms_file:
        a.rooms = [ln for ln in Path(a.rooms_file).read_text(encoding="utf-8").splitlines()
                   if ln.strip()] + list(a.rooms)
    if not a.rooms:
        ap.error("部屋が1つも指定されていない")
    cmd, res = Path(a.cmd), Path(a.result)
    results = []
    skips_by_bldg = {}
    halted = None
    for jp in a.rooms:
        p = Path(jp)
        rec = json.loads(p.read_text(encoding="utf-8"))
        key, bldg = rec["key"], rec["form"]["bukkenNm"]
        if halted:
            results.append((key, "未実行", f"照合FAILで停止（{halted}）"))
            continue
        if skips_by_bldg.get(bldg, 0) >= 2:
            print(f"  SKIP {key}（同じ棟で2連続スキップのため打ち切り）", flush=True)
            results.append((key, "SKIP", "棟を打ち切り"))
            continue
        status, detail = None, ""
        for attempt in range(a.retry + 1):
            if attempt:
                print(f"  再試行 {attempt}: {key}", flush=True)
            start = len(res.read_text(encoding="utf-8").splitlines()) if res.exists() else 0
            cmd.write_text(f"room:{p.resolve()}", encoding="utf-8")
            status, detail = wait_for(res, key, a.timeout, start)
            # ★再試行するのは時間切れのときだけ。確認画面のエラーは決定的なので、
            #   同じJSONで叩き直しても同じ結果になる（＝無駄にSUUMOを叩く）。
            if status is not None:
                break
        if status == "OK":
            print(f"  OK   {key} → コード={detail}", flush=True)
            skips_by_bldg[bldg] = 0
        elif status == "HALT":
            print(f"  ★照合FAIL {key}: {str(detail)[:200]}", flush=True)
            print("  ★登録済みで内容が違う。以降を全部止める。", flush=True)
            halted = key
        else:
            print(f"  SKIP {key}: {str(detail)[:160]}", flush=True)
            skips_by_bldg[bldg] = skips_by_bldg.get(bldg, 0) + 1
        results.append((key, status or "TIMEOUT", detail))
    ok = [r for r in results if r[1] == "OK"]
    print(f"\n■ 完了 {len(ok)}室 / 未完了 {len(results) - len(ok)}室", flush=True)
    for k, st, d in results:
        if st != "OK":
            print(f"   {st:<8}{k}: {str(d)[:160]}", flush=True)
    if halted:
        print(f"★照合FAILで停止した: {halted}", flush=True)
        return 2
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
