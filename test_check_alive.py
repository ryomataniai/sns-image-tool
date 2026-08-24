#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空室確認（--check-alive）のテスト。APIもブラウザも使わない。

実行: python3 test_check_alive.py

■測ること
  1. 収穫に無い室が★消失として出る（掲載中／未掲載を分けて出す）
  2. 収穫条件の外の室は消失にせず『判定不能』にする
  3. 棟名の表記ゆれ（タイプ接頭辞・コロンとアンダースコア）を吸収して『在り』になる
  4. 進行管理の2つのキー形式（◯◯_506 と ◯◯ 901号室）の両方を割れる
  5. 消失した室の『消える直前の更新』が記録される

★1は実運用ではまだ一度も起きていない（8/14時点で消失0室）。
  実際の消失を待つと、初めて動くのが本番になる。合成データで先に動かしておく。
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("rp", Path(__file__).with_name("realpro_dl.py"))
rp = importlib.util.module_from_spec(spec)
sys.argv = ["x"]
spec.loader.exec_module(rp)

_fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def row(name, room, state="空室", updated="1時間前", agent="株式会社テスト"):
    return {"id": f"{name}{room}", "name": name, "room": room, "addr": "大阪市西区本田1丁目",
            "access": "", "state": state, "nyukyo": "", "layout": "1K", "area": "23.0",
            "rent": "80,000", "ad": "", "agent": agent, "tel": "", "updated": updated, "raw": ""}


def form(yen, ward="西区"):
    man, rest = divmod(yen, 10000)
    return {"key": "", "form": {"chinryo1": str(man), "chinryo2": str(rest // 10).rstrip("0") or "0",
                                "_address_raw": f"大阪府大阪市{ward}本田１丁目1番1号"}}


def build(tmp):
    """合成の在庫と収穫を作る。"""
    data = tmp / "06_登録データ"
    data.mkdir()
    prog = tmp / "SUUMO進行管理.csv"
    rooms = [
        # (物件キー, 賃料, 掲載指示, 収穫に載せるか, 収穫側の棟名, 号室)
        ("在り普通_501", 80000, "未", True, "在り普通", "501"),
        ("在り接頭辞_502", 80000, "未", True, "(Aタイプ) 在り接頭辞", "502"),
        ("サンプルA(旧_サンプルB)_503", 80000, "済(8/13〜)", True,
         "サンプルA(旧:サンプルB)", "503"),
        ("在り号室形式 901号室", 80000, "未", True, "在り号室形式", "901"),
        ("消失未掲載_601", 80000, "未", False, None, None),
        ("消失掲載中_602", 80000, "済(8/13〜)", False, None, None),
        ("条件外高額_701", 127000, "未", False, None, None),
        ("条件外安価_702", 69000, "未", False, None, None),
    ]
    with prog.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["区分", "物件", "状態", "SUUMO登録",
                                           "掲載指示", "掲載候補"])
        w.writeheader()
        for key, yen, pub, *_ in rooms:
            w.writerow({"区分": "新規", "物件": key, "状態": "登録済",
                        "SUUMO登録": "済(100521000001)", "掲載指示": pub,
                        "掲載候補": "候補"})
            j = form(yen)
            j["key"] = key
            (data / f"{key}.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    harvest = [row(hn, hr) for _k, _y, _p, live, hn, hr in rooms if live]
    # 更新が古い室を1つ混ぜる（別枠に出るか）
    harvest.append(row("在り古い", "801", updated="5日前"))
    with prog.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["区分", "物件", "状態", "SUUMO登録",
                                           "掲載指示", "掲載候補"])
        w.writerow({"区分": "新規", "物件": "在り古い_801", "状態": "登録済",
                    "SUUMO登録": "済(100521000002)", "掲載指示": "済(8/13〜)",
                    "掲載候補": ""})   # ★空欄＝未判断（止まることをテストする）
    j = form(80000)
    j["key"] = "在り古い_801"
    (data / "在り古い_801.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    return prog, data, harvest


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        prog, data, harvest = build(tmp)
        out = tmp / "_空室確認.csv"
        msgs = []
        recs, _und = rp.check_alive(harvest, prog, data, out, 70000, 90000, 3,
                              msgs.append, hist_path=tmp / "_空室確認履歴.json")
        by = {r["物件"]: r for r in recs}
        log = "\n".join(msgs)

        check("収穫に無い室が★消失になる（未掲載）",
              by["消失未掲載_601"]["判定"] == "★消失", by["消失未掲載_601"]["判定"])
        check("収穫に無い室が★消失になる（掲載中）",
              by["消失掲載中_602"]["判定"] == "★消失", by["消失掲載中_602"]["判定"])
        check("掲載中の消失が『即対応』として別枠に出る",
              "掲載中なのにリアプロから消えた 1室" in log)
        check("未掲載の消失は別枠に分かれる", "未掲載で消えた 1室" in log)
        check("条件外（高額）は消失にせず判定不能",
              by["条件外高額_701"]["判定"] == "判定不能", by["条件外高額_701"]["理由"])
        check("条件外（安価）は消失にせず判定不能",
              by["条件外安価_702"]["判定"] == "判定不能", by["条件外安価_702"]["理由"])
        check("タイプ接頭辞つきの収穫と一致して『在り』",
              by["在り接頭辞_502"]["判定"] == "在り", by["在り接頭辞_502"]["判定"])
        check("コロン／アンダースコアの差を吸収して『在り』",
              by["サンプルA(旧_サンプルB)_503"]["判定"] == "在り")
        check("『◯◯ 901号室』形式のキーも割れて『在り』",
              by["在り号室形式 901号室"]["判定"] == "在り")
        # ★『在り古い_801』は掲載候補が空欄＝未判断なので、更新の別枠には出ない。
        #   未判断は確認対象から外れ、代わりに止める材料として報告される。
        check("掲載候補が空欄の室は『未判断』として止める材料になる",
              "掲載候補が未判断の室が 1室" in log and _und and _und[0]["物件"] == "在り古い_801",
              str([x["物件"] for x in _und]))
        check("未判断の室は確認対象から外れる", "在り古い_801" not in by)
        check("CSVが書き出される", out.is_file())

        # ★索引：JSONのファイル名（◯◯_501.json）と進行管理の『◯◯ 901号室』が
        #   room_key で結びつくこと。ここが切れると号室形式の行が全部
        #   『登録データが無い』に落ち、賃料帯の自動導出も壊れる（実際に壊した）。
        idx = rp.index_data(data)
        check("号室形式のキーでも登録データを引ける",
              rp.room_key(*rp.split_bukken("在り号室形式 901号室")) in idx)
        lo, hi, _why = rp.inventory_rent_range(prog, data, lambda m: None)
        check("賃料帯の自動導出が掲載候補（=全室）の実測min/maxになる",
              (lo, hi) == (69000, 127000), f"{lo}〜{hi}")

        # 2回目：履歴があるので『消える直前の更新』が記録される
        rp.check_alive(harvest, prog, data, out, 70000, 90000, 3, msgs.append,
                       hist_path=tmp / "_空室確認履歴.json")
        rec = tmp / "_消失記録.csv"
        check("消失記録が作られる", rec.is_file())
        if rec.is_file():
            rows = list(csv.DictReader(rec.open(encoding="utf-8-sig")))
            check("消失した室だけが記録される", {r["物件"] for r in rows} ==
                  {"消失未掲載_601", "消失掲載中_602"}, str({r["物件"] for r in rows}))
            check("在り→消失を追えるよう履歴の欄がある",
                  "最後に見た日" in rows[0] and "消える直前の更新" in rows[0])

    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
