# -*- coding: utf-8 -*-
"""realprodl-v1: select_rooms() / warn_dup_addr() の回帰テスト。

実行: python3 test_realpro_select.py   （pytest不要・ブラウザ不要・ネットワーク不要）

■なぜこのテストが必要か
DLの選び方は**元付の偏りを直すため**に入れた。8/14の実測では、収穫順のまま取ったせいで
登録在庫88室のうち1社が84%を占めた（母集団では11.4%）。偏りは市場構造ではなく取り方の
副作用だった。ここが壊れると**偏ったまま気づけない**（DLは通るので失敗として見えない）。

■特に守る性質
  ① 棟をシャッフルしても**室の順は変えない**（シャッフルは棟の巡回順にだけかける）
  ② 元付上限は「実際に取る室の元付」で数える。**棟の最頻元付ではない**
  ③ **agent が空の室は上限の対象外**（"不明" に束ねて落とすと梅田オフィス系が消える）
  ④ 棟上限は**既DL分も数える**（数えないと再実行のたびに上限ぶん積み増す）
  ⑤ seed を指定すれば再現できる／使った seed が診断に出る
  ⑥ want に届かないときは**黙って少ない件数で通さない**
  ⑦ 住所の重複は**警告のみ**。統合しない

★このリポは Public なので、テストデータに実在の棟名・元付会社名を書かない。
"""
from __future__ import annotations

import sys

from realpro_dl import (bldg_key, existing_keys, room_key, route_search_cmd,
                        select_rooms, warn_dup_addr)

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  NG   {name}\n         got  = {got!r}\n         want = {want!r}")
        FAIL.append(name)


def quiet(_msg) -> None:
    pass


def room(bldg: str, no: str, agent: str = "A社", addr: str = "", rent: str = "80,000"):
    return {"id": f"{bldg}-{no}", "name": bldg, "room": no, "agent": agent,
            "addr": addr or f"大阪市西区サンプル{bldg}", "rent": rent,
            "layout": "1K", "state": "空室"}


def stock(n_bldg: int, per: int, agents=None):
    """n_bldg棟 × per室。agents を渡すと棟ごとに順に割り当てる。"""
    out = []
    for b in range(n_bldg):
        ag = agents[b % len(agents)] if agents else f"社{b:02d}"
        for i in range(per):
            out.append(room(f"サンプル{b:03d}", f"{100 + i}", ag))
    return out


# ── ① 1棟1室 ────────────────────────────────────────────────────
def test_one_per_building() -> None:
    print("[1棟1室]")
    todo = stock(30, 5)
    picked, d = select_rooms(todo, set(), 20, 0, 0, 1, quiet)
    check("20室を取れる", len(picked), 20)
    bl = [bldg_key(r["name"]) for r in picked]
    check("20棟から1室ずつ（重複なし）", len(set(bl)), 20)
    check("診断の棟数も20", d["棟数"], 20)

    # 棟数 < want のときは2周目に入る（round-robin）
    picked2, _d = select_rooms(stock(5, 4), set(), 12, 0, 0, 1, quiet)
    check("棟が足りなければ2周目に入る", len(picked2), 12)
    from collections import Counter
    c = Counter(bldg_key(r["name"]) for r in picked2)
    check("  各棟から均等に（5棟×2〜3室）", sorted(c.values()), [2, 2, 2, 3, 3])


# ── ② 室の順は変えない ───────────────────────────────────────────
def test_room_order_preserved() -> None:
    print("[室の順は変えない]")
    # 1棟だけ・5室。round-robin で1室ずつ取ると収穫順のまま出るはず
    todo = [room("サンプル000", f"{100 + i}") for i in range(5)]
    picked, _d = select_rooms(todo, set(), 5, 0, 0, 7, quiet)
    check("★室の並びは収穫順のまま（シャッフルは棟の巡回順だけ）",
          [r["room"] for r in picked], ["100", "101", "102", "103", "104"])


# ── ③ 元付上限 ──────────────────────────────────────────────────
def test_agent_cap() -> None:
    print("[元付上限]")
    # 10棟すべて同じ元付 → 上限2なら2室しか取れない
    todo = stock(10, 3, agents=["甲社"])
    picked, d = select_rooms(todo, set(), 20, 0, 2, 1, quiet)
    check("同一元付は上限2で止まる", len(picked), 2)
    check("  診断の最大シェアは100%", d["最大シェア"], 100.0)

    # 元付が5社なら上限2で10室
    todo = stock(20, 2, agents=["甲社", "乙社", "丙社", "丁社", "戊社"])
    picked, d = select_rooms(todo, set(), 20, 0, 2, 1, quiet)
    check("5社×上限2 → 10室", len(picked), 10)
    from collections import Counter
    c = Counter(r["agent"] for r in picked)
    check("  どの社も2室以下", max(c.values()), 2)
    check("  元付社数5", d["元付社数"], 5)

    # ★棟の最頻元付ではなく「実際に取る室の元付」で数える
    #   1棟に2社が混在し、取るのは2室目（乙社）のケース
    mixed = [room("サンプル000", "101", "甲社"), room("サンプル000", "102", "乙社"),
             room("サンプル001", "101", "甲社"), room("サンプル001", "102", "乙社")]
    picked, _d = select_rooms(mixed, set(), 4, 0, 1, 1, quiet)
    ags = sorted(r["agent"] for r in picked)
    check("★1棟に複数の元付がいても取る室の元付で数える", ags, ["乙社", "甲社"])


# ── ④ agent が空の室 ────────────────────────────────────────────
def test_blank_agent_exempt() -> None:
    print("[元付が空の室]")
    todo = [room(f"サンプル{i:03d}", "101", "") for i in range(10)]
    picked, d = select_rooms(todo, set(), 10, 0, 2, 1, quiet)
    check("★元付が空の室は上限の対象外（10室とも取れる）", len(picked), 10)
    check("  診断に空欄件数が出る", d["元付空欄"], 10)
    check("  元付社数は0（空は社として数えない）", d["元付社数"], 0)

    # 空と実名が混在しても、実名側にだけ上限がかかる
    mix = ([room(f"空{i:03d}", "101", "") for i in range(5)]
           + [room(f"名{i:03d}", "101", "甲社") for i in range(5)])
    picked, _d = select_rooms(mix, set(), 10, 0, 2, 1, quiet)
    n_blank = sum(1 for r in picked if not r["agent"])
    n_named = sum(1 for r in picked if r["agent"])
    check("空5室は全部通る", n_blank, 5)
    check("実名は上限2で止まる", n_named, 2)


# ── ⑤ 棟上限は既DL分も数える ─────────────────────────────────────
def test_building_cap_counts_existing() -> None:
    print("[棟上限は既DL分も数える]")
    todo = stock(3, 5)
    # サンプル000 は既に2室DL済み → 上限3なら残り1室しか取れない
    have = {room_key("サンプル000", "901"), room_key("サンプル000", "902")}
    picked, _d = select_rooms(todo, have, 15, 3, 0, 1, quiet)
    from collections import Counter
    c = Counter(bldg_key(r["name"]) for r in picked)
    check("★既DL2室の棟は残り1室だけ", c[bldg_key("サンプル000")], 1)
    check("  他の棟は3室ずつ", c[bldg_key("サンプル001")], 3)
    check("  合計 1+3+3", len(picked), 7)


# ── ⑥ 未DL棟を先に取る ──────────────────────────────────────────
def test_new_buildings_first() -> None:
    print("[未DL棟を先に取る]")
    todo = stock(10, 1)
    # サンプル000〜004 は既DL棟
    have = {room_key(f"サンプル{i:03d}", "901") for i in range(5)}
    picked, _d = select_rooms(todo, have, 5, 0, 0, 3, quiet)
    news = {bldg_key(f"サンプル{i:03d}") for i in range(5, 10)}
    check("★5室とも未DL棟から", {bldg_key(r["name"]) for r in picked}, news)


# ── ⑦ seed ─────────────────────────────────────────────────────
def test_seed() -> None:
    print("[seed]")
    todo = stock(50, 1)
    a1, d1 = select_rooms(todo, set(), 20, 0, 0, 42, quiet)
    a2, d2 = select_rooms(todo, set(), 20, 0, 0, 42, quiet)
    check("同じ seed → 同じ結果", [r["id"] for r in a1], [r["id"] for r in a2])
    check("  診断に seed が出る", d1["seed"], 42)
    b1, _d = select_rooms(todo, set(), 20, 0, 0, 43, quiet)
    check("違う seed → 違う結果", [r["id"] for r in a1] != [r["id"] for r in b1], True)
    # seed=None でも診断に実際に使った値が入る（再現できないと調査できない）
    _p, d3 = select_rooms(todo, set(), 5, 0, 0, None, quiet)
    check("★seed 未指定でも使った値が診断に出る", isinstance(d3["seed"], int), True)


# ── ⑧ want に届かないとき ───────────────────────────────────────
def test_shortfall() -> None:
    print("[want に届かないとき]")
    msgs: list[str] = []
    todo = stock(3, 1)          # 3室しか無い
    picked, d = select_rooms(todo, set(), 20, 0, 0, 1, msgs.append)
    check("在庫ぶんだけ返す", len(picked), 3)
    check("★不足を明示する（黙って通さない）",
          any("届いていない" in m for m in msgs), True)
    check("  診断に要求と選択の両方が入る", (d["要求"], d["選択"]), (20, 3))


# ── ⑨ 住所の重複警告 ────────────────────────────────────────────
def test_dup_addr() -> None:
    print("[住所の重複警告]")
    msgs: list[str] = []
    rows = [room("棟A", "101", addr="大阪市西区サンプル1-2-3"),
            room("棟B", "201", addr="大阪市西区サンプル1-2-3"),   # 同一住所・別名
            room("棟C", "301", addr="大阪市北区サンプル9-9-9")]
    dup = warn_dup_addr(rows, msgs.append)
    check("同一住所を検出する", list(dup), ["大阪市西区サンプル1-2-3"])
    check("  棟名を両方出す", sorted(dup["大阪市西区サンプル1-2-3"]), ["棟A", "棟B"])
    check("★統合しない（入力は変えない）", len(rows), 3)
    check("  人に投げる文言が出る", any("人が見る" in m for m in msgs), True)

    # 表記ゆれ（空白）は吸収する
    rows2 = [room("棟A", "101", addr="大阪市西区サンプル1-2-3"),
             room("棟B", "201", addr="大阪市西区 サンプル1-2-3")]
    check("空白の違いは同一住所として拾う", len(warn_dup_addr(rows2, quiet)), 1)
    # 住所が空の室は警告の対象にしない（空同士を同一住所と呼ばない）
    rows3 = [room("棟A", "101", addr=""), room("棟B", "201", addr="")]
    check("★住所が空の室は同一住所と呼ばない", warn_dup_addr(rows3, quiet), {})


# ── ⑩ serve のコマンド分岐（★2026-08-20 に実機で踏んだ）────────────
def test_route_search_cmd() -> None:
    """★`searchname:` が `search` の前方一致に食われた事故を固定する。

    実機のログ: `[OK] 検索完了 ヒット 2127件 賃料サンプルレジデンス本町〜90000`
    **エラーにならず「成功」として通った**ので、ログを読むまで気づけなかった。
    """
    print("[serve のコマンド分岐]")
    check("★searchname: が賃料検索に食われない",
          route_search_cmd("searchname:サンプルレジデンス"), ("name", "サンプルレジデンス"))
    check("  空白は落とす", route_search_cmd("searchname:  サンプル  "), ("name", "サンプル"))
    check("  物件名が空なら bad", route_search_cmd("searchname:")[0], "bad")

    check("search 単体は既定の帯", route_search_cmd("search"), ("rent", "70000", "90000"))
    check("search:70000:90000", route_search_cmd("search:70000:90000"),
          ("rent", "70000", "90000"))
    check("search:: は既定に落ちる", route_search_cmd("search::"), ("rent", "70000", "90000"))

    # ★数値でない賃料は通さない（前方一致を直しても、ここが無いと同型の事故が通る）
    bad = route_search_cmd("search:サンプルレジデンス")
    check("★賃料が数値でなければ bad", bad[0], "bad")
    check("  理由に searchname を案内する", "searchname" in bad[1], True)

    check("search系でない行は None", route_search_cmd("pages:200"), None)
    check("  searching のような別語も None", route_search_cmd("searching"), None)
    check("  dlmany は None", route_search_cmd("dlmany:20"), None)


# ── ⑪ existing_keys（★再DLの原因）─────────────────────────────
def test_existing_keys(tmp) -> None:
    """★号室に日本語が入るファイル名を拾えず、再DLが起きていた（2026-08-20 実測）。"""
    print("[existing_keys]")
    for nm in ("サンプルレジデンス_1301号室_20260820164547.pdf",
               "サンプルコート_705_20260820164545.pdf",
               "サンプルプラザ__20260820164541.pdf",          # 号室が空
               "サンプルヴィラ_B102_20260820164542.pdf",
               "タイムスタンプなし.pdf"):
        (tmp / nm).write_bytes(b"%PDF-1.4\n")
    keys = existing_keys(tmp)
    check("★日本語を含む号室を拾う",
          room_key("サンプルレジデンス", "1301号室") in keys, True)
    check("数字だけの号室も拾う", room_key("サンプルコート", "705") in keys, True)
    check("英数字混在の号室も拾う", room_key("サンプルヴィラ", "B102") in keys, True)
    check("★号室が空でも拾う（拾わないと再DLする）",
          room_key("サンプルプラザ", "") in keys, True)
    check("タイムスタンプが無いファイルは無視", len(keys), 4)


def main() -> int:
    import tempfile
    from pathlib import Path as _P
    test_one_per_building()
    test_room_order_preserved()
    test_agent_cap()
    test_blank_agent_exempt()
    test_building_cap_counts_existing()
    test_new_buildings_first()
    test_seed()
    test_shortfall()
    test_dup_addr()
    test_route_search_cmd()
    with tempfile.TemporaryDirectory() as d:
        test_existing_keys(_P(d))
    print()
    if FAIL:
        print(f"NG {len(FAIL)}件: {FAIL}")
        return 1
    print("すべて ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
