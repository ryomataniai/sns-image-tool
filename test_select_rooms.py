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
def _fv(facts, rec=None):
    return S.furnished_evidence(facts, rec)
v, ev = _fv({"equipment": "【設備】エアコン 家具家電付"})
tt("設備の記載を拾う", v == "yes" and "家具家電付" in ev, repr((v, ev)))
v, ev = _fv({}, {"form": {"bukkenCatch": "即入居可・家電付"}})
tt("備考（bukkenCatch）を拾う", v == "yes" and "家電付" in ev, repr((v, ev)))
v, ev = _fv({}, {"tokucho": ["家具・家電あり"]})
tt("特徴（tokucho）を拾う", v == "yes" and "家具・家電" in ev, repr((v, ev)))
t("★ブランド名だけでは弾かない",
  _fv({"equipment": "【設備】エアコン", "full_text": "サンプルブランド 101号室"})[0], "")

print("\n■ エ-2 ★語はあっても『付いていない』なら弾かない（返信176・実測5/15件）")
v, ev = _fv({"equipment": "【その他】海外審査可 家具家電なし 駐輪場なし"})
t("『家具家電なし』は弾かない", v, "no")
tt("否定だと分かる根拠を返す", "否定" in ev, ev)
v, ev = _fv({"equipment": "●家具家電 レンタルサービス提携してます!担当迄ご連絡ください"})
t("『家具家電レンタル…提携』は弾かない（部屋には付いていない）", v, "no")
tt("レンタル案内だと分かる根拠を返す", "レンタル" in ev, ev)
t("★『家具家電付き』は今までどおり弾く",
  _fv({"equipment": "備考 家具家電付き ・ベッド・ソファ"})[0], "yes")

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

print("\n■ カ 投稿スロット（★火・木・土。今日より後）")
# 2026-09: 1(火) 3(木) 5(土) 8(火) 10(木) 12(土) 15(火)
t("木曜に選ぶと 土→火→木",
  S.slot_dates(3, date(2026, 9, 3))[0], [date(2026, 9, 5), date(2026, 9, 8), date(2026, 9, 10)])
t("★土→火は3日空く（隔日ではない）",
  (S.slot_dates(2, date(2026, 9, 4))[0][1] - S.slot_dates(2, date(2026, 9, 4))[0][0]).days, 3)
t("★今日が火木土でも今日は使わない（翌スロットから）",
  S.slot_dates(1, date(2026, 9, 5))[0], [date(2026, 9, 8)])
t("月曜に選ぶと 火から", S.slot_dates(1, date(2026, 9, 7))[0], [date(2026, 9, 8)])
t("--start を渡せばそこから",
  S.slot_dates(2, date(2026, 9, 3), start=date(2026, 9, 12))[0],
  [date(2026, 9, 12), date(2026, 9, 15)])
try:
    S.slot_dates(1, date(2026, 9, 3), start=date(2026, 9, 9))   # 水曜
    tt("★--start が火木土でなければエラー（黙って寄せない）", False)
except ValueError as e:
    tt("★--start が火木土でなければエラー（黙って寄せない）", "水曜" in str(e), str(e))
t("曜日は月=0 の並びで持つ", S.POST_WEEKDAYS, (1, 3, 5))
# ★返信178 §6 の想定: 9/8(火)に選ぶと 9/10 は A/B 6本目で埋まっている → 9/12・9/15
sl, sk = S.slot_dates(2, date(2026, 9, 8), reserved=[date(2026, 9, 10)])
t("★埋まっているスロットを飛ばす", sl, [date(2026, 9, 12), date(2026, 9, 15)])
t("★飛ばした日を返す（黙って飛ばさない）", sk, [date(2026, 9, 10)])
try:
    S.slot_dates(2, date(2026, 9, 8),
                 reserved=[date(2026, 9, 8) + timedelta(days=i) for i in range(400)])
    tt("★予約で埋まりきったら無限ループにせず落ちる", False)
except ValueError as e:
    tt("★予約で埋まりきったら無限ループにせず落ちる", "取れない" in str(e), str(e))


# ══════════════════════════════════════════════════════════
# 合成ディレクトリでの通し（★PDF は中身を読まない＝ core を差し替える）
# ══════════════════════════════════════════════════════════
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=JST)
TODAY = NOW.date()


def fixture(tmp, cand_specs, posted_names, ab_names=None, reserved=None):
    """cand_specs: [(建物名, 部屋, 取得日)] ／ posted_names: 投稿キューの生の物件名
    ab_names … 自己診断(a) の A/B の室（既定は AB6）。★posted_names と独立に持つ"""
    ab_names = AB6 if ab_names is None else ab_names
    md = os.path.join(tmp, "maisoku"); os.makedirs(md)
    rd = os.path.join(tmp, "reg"); os.makedirs(rd)
    for name in list(posted_names) + list(ab_names):
        b, r = S.split_property_name(name)
        if (b, r) not in [(x[0], x[1]) for x in cand_specs]:
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
    json.dump({"header_lines": ["（合成データの注記）"], "watch": [],
               "ab_rooms": list(ab_names),
               "reserved_slots": [{"date": d, "note": "（合成）"} for d in (reserved or [])]},
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
AB6 = ["{} 10{}".format(b, i + 1) for i, b in enumerate(BLD)]        # ★A/B の6室（棟A〜F）
POSTED6 = list(AB6)                                                  # 6本とも投稿済みの状態

print("\n■ キ ★max-age-days は投稿予定日で判定する（選定日ではない）")
tmp = tempfile.mkdtemp()
try:
    # ★NOW=2026-09-03(木) → 最初のスロットは 9/5(土)
    # 取得日 8/22 → 選定日 9/3 では12日（通る）／投稿予定日 9/5 では14日（通る・境界）
    # 取得日 8/20 → 選定日 9/3 では14日（通る）／投稿予定日 9/5 では16日（★弾く）
    a = fixture(tmp, [(CAND[0], "101", date(2026, 8, 20))], POSTED6)
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("選定日基準なら14日で通るはずの室が弾かれる", "取得日が古い" in txt, txt[-1500:])
    tt("★判定日が投稿予定日だと出力に書いてある", "投稿予定日(2026-09-05)" in txt, txt[:2000])
    tt("経過日数16日と根拠が出る", "で 16日（上限 14日）" in txt, txt[-1500:])
finally:
    shutil.rmtree(tmp)

tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 8, 22))], POSTED6)
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("★取得日 8/22（投稿予定日でちょうど14日）は通る＝境界",
       "→ 候補 1件 → 提案 1件" in txt, txt[:2500])
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

print("\n■ ケ ★自己診断（返信178 §1）: 件数を固定しない2本立て")
tmp = tempfile.mkdtemp()
try:
    # ★9/8 の状況 = A/B 6室のうち投稿済みは2本だけ。旧仕様（しきい値6）ではここで中断していた
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6[:2])
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("★投稿済みが2本でも通る（実験の進行に左右されない）",
       "→ 候補 1件 → 提案 1件" in txt, txt[:2200])
    tt("(a) は A/B の室数で出る", "(a) A/B の室が在庫で引ける … 6/6 件" in txt, txt[:2200])
    tt("(b) は JSON の件数で出る", "(b) 投稿済みJSONの全室が照合 … 2/2 件" in txt, txt[:2200])
    tt("★件数を固定していないと明記する", "件数は固定していない" in txt, txt[:2200])
finally:
    shutil.rmtree(tmp)

print("\n■ ケ-1b ★埋まっているスロットは出力でも空けたと分かる")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6[:2],
                reserved=["2026-09-05"])          # ★NOW=9/3(木) → 最初の候補 9/5(土) が予約済み
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("★9/5 を空けて 9/8 に回す", "2026-09-08(火)" in txt and "2026-09-05(土) は空けた" in txt,
       txt[txt.index("■ 投稿スロット"):][:400])
    tt("空けた理由が出る", "（合成）" in txt, txt[txt.index("■ 投稿スロット"):][:400])
finally:
    shutil.rmtree(tmp)

print("\n■ ケ-2 (a) A/B の室が在庫で引けなければ中断")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6[:2],
                ab_names=AB6[:5] + ["サンプル棟Z 999"])   # ★在庫に無い室を1つ混ぜる
    import os as _os
    _os.remove(_os.path.join(a.maisoku_dir, "サンプル棟Z_999_20260901120000.pdf"))
    try:
        run(a, FACTS_OK)
        tt("中断する", False, "SystemExit が出なかった")
    except SystemExit as e:
        m = str(e)
        tt("中断する", True)
        tt("★『候補0件』ではないと明示する", "これは『候補0件』ではない" in m, m)
        tt("(a) が 5/6 と出る", "(a) A/B の室が在庫で引けるか : 5/6 件" in m, m)
        tt("引けない室を名指しする", "サンプル棟Z 999" in m, m)
finally:
    shutil.rmtree(tmp)

print("\n■ ケ-3 (b) 投稿済みJSONに照合できない室があれば中断")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))],
                POSTED6[:2] + ["サンプル棟Y 888"])
    import os as _os
    _os.remove(_os.path.join(a.maisoku_dir, "サンプル棟Y_888_20260901120000.pdf"))
    try:
        run(a, FACTS_OK)
        tt("中断する", False, "SystemExit が出なかった")
    except SystemExit as e:
        m = str(e)
        tt("中断する", True)
        tt("(b) が 2/3 と出る", "(b) 投稿済みJSONの全室が照合  : 2/3 件" in m, m)
        tt("照合できない物件名を名指しする", "サンプル棟Y 888" in m, m)
        tt("先に ig-posted-export と出る", "ig-posted-export" in m, m)
finally:
    shutil.rmtree(tmp)

print("\n■ ケ-4 運用メモに ab_rooms が無ければ中断")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6[:2])
    d = json.load(open(a.notes, encoding="utf-8")); d.pop("ab_rooms")
    json.dump(d, open(a.notes, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        run(a, FACTS_OK)
        tt("中断する", False, "SystemExit が出なかった")
    except SystemExit as e:
        tt("中断する", "ab_rooms" in str(e), str(e))
finally:
    shutil.rmtree(tmp)

print("\n■ コ 投稿済み・同建物・転載不可")
tmp = tempfile.mkdtemp()
try:
    # 棟A=投稿済みの棟／棟F=まだ投稿していない A/B の棟。★どちらの別室も弾く
    a = fixture(tmp, [(BLD[0], "999", date(2026, 9, 1)),
                      (BLD[5], "998", date(2026, 9, 1)),
                      (CAND[0], "101", date(2026, 9, 1))], POSTED6[:2])
    a.count = 2
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("投稿済みの室が弾かれる", "投稿済み" in txt, txt[-2000:])
    tt("★A/B と同じ建物の別室が弾かれる", "A/B の棟（投稿済みあり）" in txt, txt[-2500:])
    tt("★まだ投稿していない A/B の棟も弾く（運用メモの ab_rooms 由来）",
       "A/B の棟（運用メモの ab_rooms）" in txt, txt[-2500:])
    tt("★発火しなかった条件も0件で出す", "転載『不可』の明記 ... 0件" in txt, txt[-1200:])
    tt("★発火しなかったと明記する", "★発火しなかった" in txt, txt[-1200:])
finally:
    shutil.rmtree(tmp)

print("\n■ ゴ 条件名と見積もり（返信176 ②④）")
t("★条件名は『転載可否を確認できない』", S.REASONS["no_reg"], "転載可否を確認できない")
tt("★カット数は幅で持つ（1本の値に丸めない）",
   S.FAL_CUTS_OBSERVED == (6, 9) and not hasattr(S, "DEFAULT_CUTS_PER_REEL"),
   repr(S.FAL_CUTS_OBSERVED))
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1)),
                      (CAND[1], "201", date(2026, 9, 1))], POSTED6)
    a.count, a.cuts = 2, 0
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("★既定は実測の幅で出す", "$0.35 × 6〜9カット × 2本 = $4.20〜$6.30" in txt, txt[-900:])
    tt("★丸めない理由が出る", "1本の値に丸めない" in txt, txt[-900:])
    a.cuts = 6
    lines, _, _ = run(a, FACTS_OK)
    txt = "\n".join(lines)
    tt("--cuts で1本の値に固定できる", "$0.35 × 6カット × 2本 = $4.20" in txt, txt[-900:])
finally:
    shutil.rmtree(tmp)

print("\n■ ゾ ★家具家電『付いていない』室は弾かず手で確認へ（返信176 ③）")
tmp = tempfile.mkdtemp()
try:
    a = fixture(tmp, [(CAND[0], "101", date(2026, 9, 1))], POSTED6)
    f = dict(FACTS_OK); f["equipment"] = "【その他】海外審査可 家具家電なし 駐輪場なし"
    lines, _, csv_rows = run(a, f)[0], None, None
    txt = "\n".join(lines)
    tt("弾かれない", "→ 候補 1件 → 提案 1件" in txt, txt[:2500])
    tt("条件は0件のまま", "家具家電付き ... 0件" in txt, txt[-1200:])
    tt("★手で確認するものに1件出る",
       "「付いていない」と読めた … 1件" in txt, txt[txt.index("★手で確認"):][:600])
    tt("★根拠が出る", "家具家電なし" in txt, txt[txt.index("★手で確認"):][:600])
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
    # ★投稿しない曜日（金・日・月）まで「古い」と言わない
    t("直近スロットは火木土の21:00だけを見る（金 9/4 12:00 → 木 9/3 21:00）",
      S._last_passed_slot(datetime(2026, 9, 4, 12, 0, tzinfo=JST)),
      datetime(2026, 9, 3, 21, 0, tzinfo=JST))
    t("月曜 9/7 12:00 → 土 9/5 21:00（日曜21時ではない）",
      S._last_passed_slot(datetime(2026, 9, 7, 12, 0, tzinfo=JST)),
      datetime(2026, 9, 5, 21, 0, tzinfo=JST))
    try:
        S.load_posted(p, datetime(2026, 9, 4, 12, 0, tzinfo=JST))   # gen=9/3 09:00 < 9/3 21:00
        tt("★木曜21時を跨いでいれば金曜でも中断させる", False, "中断しなかった")
    except SystemExit as e:
        tt("★木曜21時を跨いでいれば金曜でも中断させる", "古い" in str(e), str(e))
finally:
    shutil.rmtree(tmp)

print("\n{}".format("◯ 全PASS" if ng == 0 else "✗ {}件 FAIL".format(ng)))
sys.exit(1 if ng else 0)
