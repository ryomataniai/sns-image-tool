#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_rooms.py の単体テスト（★合成データのみ・実名ゼロ・外部依存ゼロ・API を呼ばない）。

    python3 test_select_rooms.py

★実物件名を書かない。このリポは Public で、pre-commit の name_guard が生きている。
  ただし name_guard は万全ではない（2026-09-01 実測で棟名2件が素通り）。
  **フックに頼らず、テストに実名を書かない。**
★実データでの確認は select_rooms.py 自体をドライランで回す（CI に入れない）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core
import select_rooms as S

JST = timezone(timedelta(hours=9))
ng = 0


def t(name, got, want):
    global ng
    ok = got == want
    if not ok:
        ng += 1
    print(("  ◯ " if ok else "  ✗ ") + name + ("" if ok else "\n      got={!r}\n      want={!r}".format(got, want)))


def tt(name, cond, detail=""):
    global ng
    if not cond:
        ng += 1
    print(("  ◯ " if cond else "  ✗ ") + name + ("" if cond else "  " + detail))


# ── 合成データ（★実名ゼロ。name_guard の SAFE_WORDS に載る語で作る）
BLD = ["サンプル棟A", "サンプル棟B", "サンプル棟C", "サンプル棟D", "サンプル棟E", "サンプル棟F"]
CAND = ["サンプル棟G", "サンプル棟H", "サンプル棟I", "サンプル棟J"]


print("■ ア 棟名・部屋番号の正規化（★過去に3回踏んでいる）")
t("末尾の半角スペース＝部屋番号が空（トリムして消さない）",
  S.split_property_name("サンプル棟A "), ("サンプル棟A", ""))
t("空白区切りの最後が部屋番号", S.split_property_name("サンプル棟A 101"), ("サンプル棟A", "101"))
t("棟名に空白を含んでも最後だけが部屋番号",
  S.split_property_name("サンプル 棟A 101"), ("サンプル 棟A", "101"))
t("部屋番号なしは空文字で保持（表示は『部屋番号なし』）", S.room_label(""), "（部屋番号なし）")
t("NFD と NFC が一致する",
  S.canon_building(unicodedata.normalize("NFD", "サンプルガーデン棟A")),
  S.canon_building(unicodedata.normalize("NFC", "サンプルガーデン棟A")))
t("全角英数・全角スペースを揃える",
  S.canon_building("ＳＡＭＰＬＥ　棟Ａ"), S.canon_building("SAMPLE 棟A"))
t("★英字表記とカナ表記のブランドを同一視する",
  S.canon_building("SERENiTEサンプル"), S.canon_building("セレニテサンプル"))
t("ブランドキー（英字）", S.brand_key("SAMPLEコート棟A"), "SAMPLE")
t("ブランドキー（カナ・別名で英字へ寄せる）", S.brand_key("セレニテサンプル"), "SERENITE")
t("ブランドキー（英字は大小を揃える）", S.brand_key("SERENiTEサンプル"), "SERENITE")
t("★ブランド名を切り出せない棟名は、まるごとをキーにする（先頭N文字で切らない）",
  S.brand_key("東西南北サンプル館"), "東西南北サンプル館")
tt("★前方一致するカナのブランドキーは同系列とみなす",
   S.brand_conflict("サンプルコート", "サンプルコートレジデンス"))
tt("短すぎる前方一致は同系列にしない", not S.brand_conflict("アト", "アトリエサンプル"))
tt("無関係なブランドは別", not S.brand_conflict("SAMPLE", "DUMMY"))

print("\n■ イ マイソクのファイル名を割る")
t("建物名_部屋番号_タイムスタンプ",
  S.split_maisoku_name("サンプル棟A_101_20260901123456.pdf"),
  ("サンプル棟A", "101", date(2026, 9, 1)))
t("★建物名に '_' を含む実例（右から割る）",
  S.split_maisoku_name("サンプル_棟A_101_20260901123456.pdf"),
  ("サンプル_棟A", "101", date(2026, 9, 1)))
t("★部屋番号が空の実例",
  S.split_maisoku_name("サンプル棟A__20260901123456.pdf"),
  ("サンプル棟A", "", date(2026, 9, 1)))
t("割れないファイル名は None（黙って捨てない）", S.split_maisoku_name("こわれた.pdf"), None)
t("日付でないタイムスタンプは None", S.split_maisoku_name("サンプル棟A_101_XXXXXXXX.pdf"), None)

print("\n■ ウ facts の読み取り（取れないものを別の値で騙らない）")
t("間取タイプから型だけ取る", S.madori_type({"madori": "1K[洋室8.3]"}), "1K")
t("全角の間取タイプ", S.madori_type({"madori": "１ＬＤＫ［ＬＤＫ８．４］"}), "1LDK")
t("間取が無ければ空", S.madori_type({}), "")
t("賃料を整数にする", S.rent_yen({"rent": "74,000円"}), 74000)
t("賃料が読めなければ None（推測しない）", S.rent_yen({"rent": "応相談"}), None)
t("賃料が無ければ None", S.rent_yen({}), None)

print("\n■ エ 家具家電付き（★性質で判定。ブランド名で弾かない）")
tt("設備の記載を拾う",
   "家具家電付" in S.furnished_evidence({"equipment": "【設備】エアコン 家具家電付"}, None))
tt("備考（bukkenCatch）を拾う",
   "家電付" in S.furnished_evidence({}, {"form": {"bukkenCatch": "即入居可・家電付"}}))
tt("特徴（tokucho）を拾う",
   "家具・家電" in S.furnished_evidence({}, {"tokucho": ["家具・家電あり"]}))
t("★ブランド名だけでは弾かない",
  S.furnished_evidence({"equipment": "【設備】エアコン", "full_text": "サンプルブランド 101号室"}, None), "")

print("\n■ オ 並べ方（返信175 §4）")
def _c(b, brand, mad, rent):
    return {"building": b, "room": "101", "brand": brand, "madori": mad,
            "rent": rent, "taken": date(2026, 9, 1), "walk": 5, "tensai": "可能", "pdf": ""}

picked, relax = S.pick([_c("a", "X", "1K", 90000), _c("b", "X", "1K", 89000),
                        _c("c", "Y", "1DK", 88000), _c("d", "Z", "1LDK", 87000)], 3)
t("賃料の上位から", picked[0]["rent"], 90000)
t("★同一ブランドを連続させない（間に2本以上）",
  [p["brand"] for p in picked], ["X", "Y", "Z"])
t("★型を散らす", [p["madori"] for p in picked], ["1K", "1DK", "1LDK"])
t("緩めていない", relax, [])

picked2, relax2 = S.pick([_c("a", "X", "1K", 90000), _c("b", "X", "1K", 89000)], 2)
t("同一ブランドしか無ければ出すが、黙らない", len(picked2), 2)
tt("★緩めた理由を返す", any("同一ブランド" in m for m in relax2), str(relax2))

print("\n■ カ 投稿スロット（隔日・今日より後）")
t("直近の投稿から隔日で、今日より後の最初から",
  S.slot_dates(date(2026, 9, 2), 3, date(2026, 9, 3)),
  [date(2026, 9, 4), date(2026, 9, 6), date(2026, 9, 8)])
t("★起点が古くても隔日の刻みは崩さない（8/20 起点なら 9/5。9/4 ではない）",
  S.slot_dates(date(2026, 8, 20), 1, date(2026, 9, 3)), [date(2026, 9, 5)])
t("--start を渡せばそこから",
  S.slot_dates(date(2026, 9, 2), 2, date(2026, 9, 3), start=date(2026, 9, 10)),
  [date(2026, 9, 10), date(2026, 9, 12)])


# ══════════════════════════════════════════════════════════
# 合成ディレクトリでの通し（★PDF は中身を読まない＝ core を差し替える）
# ══════════════════════════════════════════════════════════
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=JST)
TODAY = NOW.date()


def fixture(tmp, cand_specs, posted_names, facts_by_pdf=None):
    """cand_specs: [(建物名, 部屋, 取得日)] ／ posted_names: 投稿キューの生の物件名"""
    md = os.path.join(tmp, "maisoku"); os.makedirs(md)
    rd = os.path.join(tmp, "reg"); os.makedirs(rd)
    for i, name in enumerate(posted_names):
        b, r = S.split_property_name(name)
        cand_specs = cand_specs + [(b, r, date(2026, 9, 1))]
    for b, r, d in cand_specs:
        fn = "{}_{}_{}120000.pdf".format(b, r, d.strftime("%Y%m%d"))
        open(os.path.join(md, fn), "wb").write(b"")
        json.dump({"key": "{}_{}".format(b, r),
                   "source": {"kyakuzuke": fn, "画像の転載": "記載なし"},
                   "form": {"bukkenCatch": ""}, "tokucho": []},
                  open(os.path.join(rd, "{}_{}.json".format(b, r)), "w", encoding="utf-8"),
                  ensure_ascii=False)
    pj = os.path.join(tmp, "ig-posted.json")
    json.dump({"generatedAt": NOW.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
               "count": len(posted_names), "notCovered": "（合成データ）",
               "rooms": [{"propertyName": n, "scheduledAt": "2026-09-02 21:00",
                          "status": "投稿済", "rowIndex": i + 2}
                         for i, n in enumerate(posted_names)]},
              open(pj, "w", encoding="utf-8"), ensure_ascii=False)
    nt = os.path.join(tmp, "notes.json")
    json.dump({"header_lines": ["（合成データの注記）"], "watch": []},
              open(nt, "w", encoding="utf-8"), ensure_ascii=False)
    return argparse.Namespace(
        count=1, harvest_note="（合成データ）", max_age_days=S.DEFAULT_MAX_AGE_DAYS,
        cuts=9, start="", maisoku_dir=md, reg_dir=rd, posted_json=pj, notes=nt,
        out_dir=os.path.join(tmp, "out"), write=False)


def run(a, facts):
    """core.parse_maisoku_facts を差し替えて build_report を回す。"""
    orig = core.parse_maisoku_facts
    core.parse_maisoku_facts = lambda b: dict(facts)
    try:
        return S.build_report(a, NOW)
    finally:
        core.parse_maisoku_facts = orig


FACTS_OK = {"madori": "1K[洋室8]", "rent": "78,000円",
            "access": ["地下鉄サンプル線「サンプル」駅 徒歩5分"], "full_text": "（合成）"}
POSTED6 = ["{} 10{}".format(b, i + 1) for i, b in enumerate(BLD)]     # 棟A〜F の6室

print("\n■ キ ★max-age-days は投稿予定日で判定する（選定日ではない）")
tmp = tempfile.mkdtemp()
try:
    # 取得日 8/20 → 選定日 9/3 では14日（通る）／投稿予定日 9/4 では15日（弾く）
    a = fixture(tmp, [(CAND[0], "101", date(2026, 8, 20))], POSTED6)
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("選定日基準なら14日で通るはずの室が弾かれる", "取得日が古い" in txt, txt[-1500:])
    tt("★判定日が投稿予定日だと出力に書いてある", "投稿予定日(2026-09-04)" in txt, txt[:2000])
    tt("経過日数15日と根拠が出る", "で 15日（上限 14日）" in txt, txt[-1500:])
finally:
    shutil.rmtree(tmp)

tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 8, 22))], POSTED6)
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("取得日 8/22（投稿予定日で13日）は通る", "→ 候補 1件 → 提案 1件" in txt, txt[:2500])
finally:
    shutil.rmtree(tmp)

print("\n■ ク ★facts が空の室は『facts が取れない』で弾く（返信175 §7）")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6)
    lines, _, _ = run(a, {})
    txt = "\n".join(lines)
    tt("『facts が取れない』で弾かれる", "facts が取れない" in txt and "候補 0件" in txt, txt[:2500])
    tt("★facts が取れなかった件数を必ず出す", "■ facts が取れなかった:" in txt, txt[:2500])
    tt("★全件が空なら環境の問題を疑わせる",
       "環境の問題" in txt, txt[:2500])
finally:
    shutil.rmtree(tmp)

print("\n■ ケ ★自己診断が6件未満で中断する（『候補0件』と報告しない）")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6[:5])   # 5件しか無い
    try:
        run(a, FACTS_OK)
        tt("中断する", False, "SystemExit が出なかった")
    except SystemExit as e:
        m = str(e)
        tt("中断する", True)
        tt("★『候補0件』ではないと明示する", "これは『候補0件』ではない" in m, m)
        tt("必要件数と実績が出る", "5件 / 必要 6件" in m, m)
        tt("次に見るところが出る", "ig-posted-export" in m, m)
finally:
    shutil.rmtree(tmp)

print("\n■ コ 投稿済み・同建物・転載不可")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(BLD[0], "999", date(2026, 9, 1)),          # 棟A の別室＝A/Bと同じ建物
                      (CAND[0], "101", date(2026, 9, 1))], POSTED6)
    a.count = 2
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("投稿済みの室が弾かれる", "投稿済み" in txt, txt[-2000:])
    tt("★A/B と同じ建物の別室が弾かれる", "A/B と同じ建物" in txt, txt[-2000:])
    tt("★発火しなかった条件も0件で出す", "転載『不可』の明記 ... 0件" in txt, txt[-1200:])
    tt("★発火しなかったと明記する", "★発火しなかった" in txt, txt[-1200:])
finally:
    shutil.rmtree(tmp)

print("\n■ サ CSV は標準出力と同じ中身（★節を落とさない）")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1)),
                      (CAND[1], "201", date(2026, 8, 1))], POSTED6)
    lines, csv_rows, inv = run(a, FACTS_OK)
    flat = "\n".join(" ".join(str(c) for c in row) for row in csv_rows)
    for sec in ["（合成データの注記）", "# 提案", "★手で確認するもの", "# 弾いた",
                "# 条件別ヒット数", "# fal の見積もり"]:
        tt("CSV に『{}』がある".format(sec), sec in flat, flat[:400])
    tt("CSV に提案の見出し行がある", "予定日 建物名 部屋 型 賃料" in flat, flat[:400])
    tt("在庫件数を返す", inv == 8, str(inv))
finally:
    shutil.rmtree(tmp)

print("\n■ シ 安全装置（返信175 §9）")
tt("★動画生成モジュールを import していない",
   all(m not in sys.modules for m in S._FORBIDDEN_MODULES),
   str([m for m in S._FORBIDDEN_MODULES if m in sys.modules]))
try:
    S._assert_no_generator()
    tt("構造ガードが通常時は通る", True)
except SystemExit as e:
    tt("構造ガードが通常時は通る", False, str(e))
sys.modules["reel_video"] = object()
try:
    S._assert_no_generator()
    tt("★載っていたら起動しない", False, "SystemExit が出なかった")
except SystemExit as e:
    tt("★載っていたら起動しない", "reel_video" in str(e), str(e))
finally:
    del sys.modules["reel_video"]

_PY = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "select_rooms.py")]
r = subprocess.run(_PY, capture_output=True, text=True)
tt("★--count を省くとエラーになる（既定値を置かない）",
   r.returncode != 0 and "--count" in r.stderr, r.stderr[-300:])
r = subprocess.run(_PY + ["--count", "2"], capture_output=True, text=True)
tt("★--harvest-note を省くとエラーになる",
   r.returncode != 0 and "--harvest-note" in r.stderr, r.stderr[-300:])

print("\n■ ス 投稿済み JSON（無い／古いときは中断する）")
tmp = tempfile.mkdtemp()
try:
    try:
        S.load_posted(os.path.join(tmp, "nope.json"), NOW)
        tt("無ければ中断", False)
    except SystemExit as e:
        tt("無ければ中断", "ig-posted-export" in str(e), str(e))
    p = os.path.join(tmp, "old.json")
    json.dump({"generatedAt": "2026-09-03T00:00:00Z", "rooms": []},   # = 9/3 09:00 JST
              open(p, "w", encoding="utf-8"))
    try:
        S.load_posted(p, datetime(2026, 9, 3, 22, 0, tzinfo=JST))
        tt("★直近の21:00スロットを跨いだ書き出しは中断", False)
    except SystemExit as e:
        tt("★直近の21:00スロットを跨いだ書き出しは中断", "古い" in str(e), str(e))
    d = S.load_posted(p, datetime(2026, 9, 3, 20, 0, tzinfo=JST))
    tt("スロットを跨いでいなければ通る", d.get("rooms") == [])
finally:
    shutil.rmtree(tmp)

print("\n{}".format("◯ 全PASS" if ng == 0 else "✗ {}件 FAIL".format(ng)))
sys.exit(1 if ng else 0)
