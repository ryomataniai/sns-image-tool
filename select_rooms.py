#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投稿する部屋をマイソクの山から選ぶ（★候補を出すだけ。動画は作らない）。

    python3 select_rooms.py --count 2 --harvest-note "SUUMO 大阪市内 7〜9万 2026-08-06〜08-31 取得"
    python3 select_rooms.py --count 2 --harvest-note "..." --write     # CSV を書く

■ このコマンドがすること / しないこと（返信175 §1）
    する … 候補を選ぶ／弾いた理由を1件ずつ出す／並べて日付を割り当てる／fal の見積もり額を表示する
    しない … 動画を生成する／fal・Gemini を呼ぶ／Blob へ上げる／投稿キューに入れる／Sheets を読む

  ★「うっかり走る」経路を設計で断つ。動画生成モジュールを import しないことを
    起動時に **実測して**確かめる（_assert_no_generator）。意志ではなく構造に置く。

■ 入力（返信175 §2）
    マイソク    ../SUUMO入稿_75枠_20260806/01_マイソク/*.pdf
    登録データ  ../SUUMO入稿_75枠_20260806/06_登録データ/*.json   ← 転載可否・設備・備考
    投稿済み    ~/line-hearing-bot/data/ig-posted.json           ← ★Sheets は読まない
    運用メモ    ~/Library/Application Support/sns-studio/select_notes.json
                ★実物件名を含むので**リポジトリの外**に置く。このリポは Public。

■ 出力（返信175 §8）
    CSV 1枚 ＋ 標準出力のサマリ。★物件名・部屋番号を含むので**リポジトリの外**へ書く。

■ ★このファイルに実物件名を書かないこと
    ブランド名（英字表記とカナ表記の対応表）だけは書く。棟名ではないので name_guard の
    対象外（yield_report.py の SAMTY_PREFIXES と同じ扱い）。
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import unicodedata
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402  ★parse_maisoku_facts / _pr_shortest_direct_walk だけ使う

JST = timezone(timedelta(hours=9))

# ── 既定パス ────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MAISOKU = os.path.join(_HERE, "../SUUMO入稿_75枠_20260806/01_マイソク")
DEFAULT_REG = os.path.join(_HERE, "../SUUMO入稿_75枠_20260806/06_登録データ")
DEFAULT_POSTED = os.path.expanduser("~/line-hearing-bot/data/ig-posted.json")
DEFAULT_STATE_DIR = os.path.expanduser("~/Library/Application Support/sns-studio")
DEFAULT_NOTES = os.path.join(DEFAULT_STATE_DIR, "select_notes.json")
DEFAULT_OUT_DIR = os.path.join(DEFAULT_STATE_DIR, "select")

# ── 定数（根拠つき）────────────────────────────────────────
# ★元データ（RealNetPro）は14日ごとに更新される。マイソク13本すべてで
#   「出力日 → 次回更新予定日」がちょうど14日だった（2026-08-31 実測）。
#   ★成約確率との関係は未測定。この14日は暫定値。
DEFAULT_MAX_AGE_DAYS = 14

# ★投稿は隔日 21:00（A/B の運用）。
POST_HOUR = 21
POST_STEP_DAYS = 2

# ★自己診断のしきい値（返信175 §5）。
#   旧仕様は15件（A/B6＋過去9本）。過去9本は投稿キューに無く、うち2本は物件名すら
#   特定できないため6件へ下げた。2026-09-11 の遡り投入後は13件へ引き上げる。
SELFCHECK_MIN_MATCHED_POSTED = 6

# ★fal の単価（返信175 §8）。表示するだけ。呼ばない。
FAL_USD_PER_CUT = 0.35
# ★1本あたりのカット数の既定。実績 $3.15/本 ÷ $0.35 = 9カット。
DEFAULT_CUTS_PER_REEL = 9

# ★対象の型。これ以外は弾く。
TARGET_MADORI = ("1K", "1DK", "1LDK")

# ★家具家電付きの判定語（性質で判定する。ブランド名で弾かない）。
FURNISHED_PAT = re.compile(r"家具家電|家具・家電|家具、家電|家具/家電|家具付|家電付|"
                           r"家具・家電付|ファニチャー付|furnished", re.IGNORECASE)

# ★同じブランドが英字表記とカナ表記の両方で在庫に入る場合だけ足す。
#   在庫の実測（2026-09-03: 英字8件 / カナ7件）で1件だけ判明している。
#   ★ここに書くのはブランド名だけ。棟名（ブランド＋地名）は書かない。
_BRAND_ALIASES = {
    "セレニテ": "SERENITE",
}

# ★import してはいけないモジュール（§9）。起動時に実測する。
_FORBIDDEN_MODULES = ("room_tour_video", "reel_video", "reel", "make_reel",
                      "carousel", "fal_client", "google.genai", "google.generativeai")

# 弾く条件（★表示順＝優先順。1室が複数に該当しうるので、件数は条件ごとに独立に数える）
REASONS = OrderedDict([
    ("posted", "投稿済み"),
    ("ab_building", "A/B と同じ建物"),
    ("no_facts", "facts が取れない"),
    ("madori", "型が対象外（1K/1DK/1LDK 以外）"),
    ("age", "取得日が古い"),
    ("tensai_ng", "転載『不可』の明記"),
    ("furnished", "家具家電付き"),
    ("no_reg", "登録データが無い（転載可否を確認できない）"),
])


# ══════════════════════════════════════════════════════════
# 構造ガード
# ══════════════════════════════════════════════════════════
def _assert_no_generator() -> None:
    """★動画生成モジュールが載っていないことを実測する（返信175 §9）。
    「import しないよう気をつける」では止まらない。載っていたら起動しない。"""
    bad = [m for m in _FORBIDDEN_MODULES if m in sys.modules]
    if bad:
        raise SystemExit("✗ 選定コマンドに動画生成モジュールが載っている: "
                         + ", ".join(bad) + "\n  ★選定と生成は別コマンド。import 経路を切ること。")


# ══════════════════════════════════════════════════════════
# 正規化（★ここは過去に3回踏んでいる。返信175 §3）
# ══════════════════════════════════════════════════════════
def norm_key(s: str) -> str:
    """比較用の正規化。NFC/NFD・全角/半角・大小・連続空白を揃える。
    ★前後の空白は落とす。『部屋番号が空』は空白ではなく **空文字** で表す（下記 split_property_name）。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def brand_key(building: str) -> str:
    """棟名からブランド名を取り出す。英字の先頭連続 or カナの先頭連続。
    ★近似である（『エスリード』と『エスリードレジデンス』は別キーになる）。
      だから CSV に『ブランド』列を出して、人が見て気づけるようにしてある。"""
    s = unicodedata.normalize("NFKC", building or "").strip()
    for kana, latin in _BRAND_ALIASES.items():   # ★別名は最優先（カナ連続の貪欲一致より先）
        if s.startswith(kana):
            return latin
    m = re.match(r"[A-Za-z][A-Za-z0-9\-'\.]*", s)
    if m:
        return m.group(0).upper()
    m = re.match(r"[ァ-ヴー]+", s)
    if m:
        return m.group(0)
    # ★漢字始まりなど、ブランド名を切り出せないものは棟名まるごとをキーにする。
    #   先頭N文字で切ると、別の棟どうしを同じブランドに見せてしまう（切るほうが危ない）。
    return s or "(不明)"


# ★ブランドキーが前方一致するときは同系列とみなす最短長。
#   棟名が全部カナだと貪欲一致でブランドキーが伸びる（『エスリード』と
#   『エスリードレジデンス』が別キーになる）。連続を避ける判定は**寄せる側**に倒す。
_BRAND_PREFIX_MIN = 3


def brand_conflict(a: str, b: str) -> bool:
    """同じブランドが連続していないかの判定。前方一致も同系列として扱う。"""
    if not a or not b:
        return False
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return len(lo) >= _BRAND_PREFIX_MIN and hi.startswith(lo)


def canon_building(building: str) -> str:
    """棟名の比較キー。カナ表記のブランドを英字表記へ寄せてから正規化する
    （★『セレニテ本町…』と『SERENiTE本町…』を同一視する）。"""
    s = unicodedata.normalize("NFKC", building or "").strip()
    for kana, latin in _BRAND_ALIASES.items():
        if s.startswith(kana):
            s = latin + s[len(kana):]
            break
    return norm_key(s)


def split_property_name(raw: str):
    """投稿キューの『物件名』（生の値）を (建物名, 部屋番号) に割る。
    区切りは**最後の空白**。★末尾の半角スペースは『部屋番号が空』の意味なので、
      トリムして消さず 部屋番号='' として保持する（依頼_18 で判明）。"""
    s = unicodedata.normalize("NFKC", raw or "").replace("　", " ")
    if " " not in s:
        return s.strip(), ""
    bld, room = s.rsplit(" ", 1)
    return bld.strip(), room.strip()


def split_maisoku_name(basename: str):
    """マイソクのファイル名を (建物名, 部屋番号, 取得日) に割る。
        建物名_部屋番号_YYYYMMDDhhmmss.pdf
    ★建物名に '_' を含む実例があるので**右から**割る。
    ★部屋番号が空の実例（建物名__タイムスタンプ.pdf）がある。空文字のまま保持する。
    割れなければ None（黙って捨てない・呼び出し側が件数を出す）。"""
    stem = basename[:-4] if basename.lower().endswith(".pdf") else basename
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    bld, room, ts = parts
    m = re.match(r"(\d{8})", ts)
    if not m:
        return None
    try:
        taken = datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None
    return bld, room, taken


def room_label(room: str) -> str:
    return room if room else "（部屋番号なし）"


# ══════════════════════════════════════════════════════════
# 入力の読み込み
# ══════════════════════════════════════════════════════════
def load_notes(path: str) -> dict:
    """運用メモ（★実物件名を含むのでリポ外）。無ければ中断する。
    ★『毎回出す注記』が黙って消えるのが一番まずい。無いなら止める。"""
    if not os.path.exists(path):
        raise SystemExit(
            "✗ 運用メモが無い: " + path + "\n"
            "  ★出力の先頭に毎回出す注記（返信175 §5）が入っている。無いまま走らせない。\n"
            "  次の形で作る（★リポジトリの外に置くこと）:\n"
            '  {"header_lines": ["...", "..."],\n'
            '   "watch": [{"match": "建物名 部屋番号", "note": "手で確認する理由"}]}')
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except ValueError as e:
        raise SystemExit("✗ 運用メモが JSON として読めない: {} ({})".format(path, e))
    lines = d.get("header_lines") or []
    if not lines:
        raise SystemExit("✗ 運用メモに header_lines が無い: " + path)
    return d


def load_posted(path: str, now: datetime) -> dict:
    """投稿済み JSON を読む。無い／古いときは中断する（返信175 §2）。
    ★古い＝『書き出したあとに投稿スロット(21:00)を跨いだ』。跨いでいれば昨夜の投稿を含まない。"""
    if not os.path.exists(path):
        raise SystemExit("✗ 投稿済み JSON が無い: " + path
                         + "\n  ★先に ig-posted-export を回してください:\n"
                           "    cd ~/line-hearing-bot && set -a; . ./.env.ig.local; set +a\n"
                           "    npx tsx scripts/ig-posted-export.ts")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    gen_raw = str(d.get("generatedAt") or "")
    gen = _parse_iso_utc(gen_raw)
    if gen is None:
        raise SystemExit("✗ 投稿済み JSON の generatedAt を読めない: " + repr(gen_raw))
    last_slot = _last_passed_slot(now)
    if gen < last_slot:
        raise SystemExit(
            "✗ 投稿済み JSON が古い（書き出し {} < 直近の投稿スロット {}）\n"
            "  ★このスロットの投稿が JSON に入っていない可能性がある＝二重投稿の元。\n"
            "  先に ig-posted-export を回してください:\n"
            "    cd ~/line-hearing-bot && set -a; . ./.env.ig.local; set +a\n"
            "    npx tsx scripts/ig-posted-export.ts".format(
                gen.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                last_slot.astimezone(JST).strftime("%Y-%m-%d %H:%M")))
    d["_generatedAtJst"] = gen.astimezone(JST)
    return d


def _parse_iso_utc(s: str):
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _last_passed_slot(now: datetime) -> datetime:
    """直近に過ぎた 21:00(JST)。"""
    n = now.astimezone(JST)
    slot = n.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
    if slot > n:
        slot -= timedelta(days=1)
    return slot


def load_registrations(reg_dir: str):
    """登録データ JSON を読み、(客付マイソクのファイル名 → rec) と (正規化 key → rec) を返す。"""
    by_pdf, by_key, bad = {}, {}, []
    for p in sorted(glob.glob(os.path.join(reg_dir, "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
        except (ValueError, OSError) as e:
            bad.append((os.path.basename(p), str(e)))
            continue
        pdfname = (rec.get("source") or {}).get("kyakuzuke")
        if pdfname:
            by_pdf[pdfname] = rec
        k = rec.get("key")
        if k:
            kb, _, kr = str(k).rpartition("_")     # key = 建物名_部屋番号
            by_key[(canon_building(kb or k), norm_key(kr))] = rec
    return by_pdf, by_key, bad


def load_maisoku(maisoku_dir: str):
    """マイソクを読み、部屋ごとに1件へ畳む（同じ部屋が複数バッチにあれば新しい取得日を採る）。
    返り値: (rooms, unparsable, dropped_older)"""
    rooms, unparsable = {}, []
    dropped = 0
    for p in sorted(glob.glob(os.path.join(maisoku_dir, "*.pdf"))):
        base = os.path.basename(p)
        parsed = split_maisoku_name(base)
        if parsed is None:
            unparsable.append(base)
            continue
        bld, room, taken = parsed
        k = (canon_building(bld), norm_key(room))
        prev = rooms.get(k)
        if prev is None or taken > prev["taken"]:
            if prev is not None:
                dropped += 1
            rooms[k] = {"key": k, "pdf": p, "pdf_name": base,
                        "building": bld, "room": room, "taken": taken}
        else:
            dropped += 1
    return rooms, unparsable, dropped


# ══════════════════════════════════════════════════════════
# 判定
# ══════════════════════════════════════════════════════════
def madori_type(facts: dict) -> str:
    """facts の間取タイプから型だけを取り出す（'1K[洋室8.3]' → '1K'）。"""
    raw = str((facts or {}).get("madori") or "")
    return re.split(r"[\[\(（]", unicodedata.normalize("NFKC", raw))[0].strip().upper()


def rent_yen(facts: dict):
    """賃料を整数（円）にする。読めなければ None（推測しない）。"""
    raw = unicodedata.normalize("NFKC", str((facts or {}).get("rent") or ""))
    m = re.search(r"([\d,]+)\s*円", raw)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def furnished_evidence(facts: dict, rec) -> str:
    """家具家電付きの根拠文字列。無ければ ''。
    ★設備・備考の文字列で判定する。ブランド名では弾かない。
    ★否定文脈（『家具家電なし』等）でも拾う＝安全側。根拠を出すので人が見て戻せる。"""
    hay = []
    if rec:
        form = rec.get("form") or {}
        hay.append(str(form.get("bukkenCatch") or ""))
        hay += [str(x) for x in (rec.get("tokucho") or [])]
    hay.append(str((facts or {}).get("equipment") or ""))
    hay.append(str((facts or {}).get("full_text") or ""))
    for h in hay:
        m = FURNISHED_PAT.search(h)
        if m:
            s, e = max(0, m.start() - 20), min(len(h), m.end() + 20)
            return re.sub(r"\s+", " ", h[s:e]).strip()
    return ""


def slot_dates(anchor: date, count: int, today: date, start: date = None):
    """投稿予定日を返す。既定は『直近の投稿日から隔日』で、今日より後の最初のスロットから。"""
    if start is not None:
        first = start
    else:
        first = anchor
        while first <= today:
            first += timedelta(days=POST_STEP_DAYS)
    return [first + timedelta(days=POST_STEP_DAYS * i) for i in range(count)]


def pick(cands, count: int):
    """並べ方（返信175 §4）: 賃料の上位から／同一ブランドを連続させない（間に2本以上）／型を散らす。
    ★制約を緩めたときは黙らず理由を返す。返り値 (picked, relaxations)"""
    pool = sorted(cands, key=lambda r: (-(r["rent"] or 0), r["building"], r["room"]))
    picked, relax = [], []
    while len(picked) < count and pool:
        recent_brands = {r["brand"] for r in picked[-2:]}     # 間に2本以上 ⇒ 直近2本と同ブランド不可
        last_type = picked[-1]["madori"] if picked else None
        def _free(r):
            return not any(brand_conflict(r["brand"], b) for b in recent_brands)
        chosen = next((r for r in pool if _free(r) and r["madori"] != last_type), None)
        if chosen is None:
            chosen = next((r for r in pool if _free(r)), None)
            if chosen is not None:
                relax.append("{}本目: 型を散らせなかった（同じ {} が続く）".format(
                    len(picked) + 1, chosen["madori"]))
        if chosen is None:
            chosen = pool[0]
            relax.append("{}本目: 同一ブランドの連続を避けられなかった（{}）".format(
                len(picked) + 1, chosen["brand"]))
        pool.remove(chosen)
        picked.append(chosen)
    return picked, relax


# ══════════════════════════════════════════════════════════
# 本体
# ══════════════════════════════════════════════════════════
def build_report(a, now: datetime):
    """レポート行（文字列のリスト）と CSV 行を返す。ファイルは書かない。"""
    _assert_no_generator()
    today = now.astimezone(JST).date()
    L = []          # 標準出力・CSV に共通で出す行
    def say(s=""):
        L.append(s)

    notes = load_notes(a.notes)
    posted = load_posted(a.posted_json, now)
    by_pdf, by_key, bad_reg = load_registrations(a.reg_dir)
    rooms, unparsable, dropped_dup = load_maisoku(a.maisoku_dir)

    # ── 投稿済みの索引（★生の値を正規化するのはこちら側の仕事）
    posted_rooms = posted.get("rooms") or []
    posted_keys, posted_buildings = set(), set()
    for r in posted_rooms:
        bld, room = split_property_name(r.get("propertyName", ""))
        posted_keys.add((canon_building(bld), norm_key(room)))
        posted_buildings.add(canon_building(bld))

    # ── ★自己診断（返信175 §5）: 候補を出す前に、照合が効いていることを確かめる
    inv_buildings = {k[0] for k in rooms}
    matched = [r for r in posted_rooms
               if canon_building(split_property_name(r.get("propertyName", ""))[0]) in inv_buildings]
    if len(matched) < SELFCHECK_MIN_MATCHED_POSTED:
        unmatched = [r.get("propertyName", "") for r in posted_rooms
                     if canon_building(split_property_name(r.get("propertyName", ""))[0])
                     not in inv_buildings]
        raise SystemExit(
            "✗ 自己診断で中断（★これは『候補0件』ではない。照合が期待どおり効いていない）\n"
            "  投稿済み JSON の件数 : {}件（generatedAt {}）\n"
            "  うち建物名が在庫と一致: {}件 / 必要 {}件\n"
            "  一致しなかった物件名  : {}\n"
            "  マイソク在庫          : {}件 / {}棟\n"
            "  ★見るところ: JSON が古くないか（先に ig-posted-export）／棟名の正規化が効いているか。"
            .format(len(posted_rooms),
                    posted["_generatedAtJst"].strftime("%Y-%m-%d %H:%M"),
                    len(matched), SELFCHECK_MIN_MATCHED_POSTED,
                    unmatched if unmatched else "（なし）",
                    len(rooms), len(inv_buildings)))

    # ── スロット（★max-age は投稿予定日で判定する。選定日ではない）
    anchor = today
    sched = [_parse_sched(r.get("scheduledAt")) for r in posted_rooms]
    sched = [d for d in sched if d]
    if sched:
        anchor = max(sched)
    start = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else None
    slots = slot_dates(anchor, a.count, today, start)
    judge_date = slots[-1]       # ★最終スロットで判定する（どのスロットに入っても古くない）

    # ── 各室を判定
    cands, rejects = [], []
    fired = Counter()
    facts_empty = 0
    read_errors = []
    for k in sorted(rooms):
        r = rooms[k]
        rec = by_pdf.get(r["pdf_name"]) or by_key.get(k)   # ★索引は同じ正規化キーで引く
        try:
            with open(r["pdf"], "rb") as f:
                facts = core.parse_maisoku_facts(f.read())
        except OSError as e:
            facts = {}
            read_errors.append("{} ({})".format(r["pdf_name"], e))
        if not facts:
            facts_empty += 1

        hits = []   # (条件キー, 根拠の文字列)
        if k in posted_keys:
            hits.append(("posted", "投稿キュー 状態=投稿済"))
        if k[0] in posted_buildings and k not in posted_keys:
            hits.append(("ab_building", "同じ建物で投稿済みあり"))
        if not facts:
            hits.append(("no_facts", "parse_maisoku_facts が空を返した"))
        mad = madori_type(facts)
        if facts and mad not in TARGET_MADORI:
            hits.append(("madori", "間取タイプ『{}』".format(facts.get("madori") or "(取れない)")))
        age = (judge_date - r["taken"]).days
        if age > a.max_age_days:
            hits.append(("age", "取得日 {} → 投稿予定 {} で {}日（上限 {}日）".format(
                r["taken"], judge_date, age, a.max_age_days)))
        if rec is None:
            hits.append(("no_reg", "06_登録データ に対応する JSON が無い"))
        else:
            tensai = (rec.get("source") or {}).get("画像の転載")
            if tensai == "不可":
                hits.append(("tensai_ng", "画像の転載『不可』"))
        ev = furnished_evidence(facts, rec)
        if ev:
            hits.append(("furnished", ev))

        for key, _ in hits:
            fired[key] += 1

        row = {
            "building": r["building"], "room": r["room"], "brand": brand_key(r["building"]),
            "madori": mad, "rent": rent_yen(facts), "taken": r["taken"],
            "walk": core._pr_shortest_direct_walk(facts.get("access")) if facts else None,
            "tensai": ((rec.get("source") or {}).get("画像の転載") if rec else "★登録データなし"),
            "pdf": r["pdf_name"],
        }
        if hits:
            first = next(kk for kk in REASONS if kk in {h[0] for h in hits})
            ev_first = next(v for kk, v in hits if kk == first)
            row["reason"] = REASONS[first]
            row["evidence"] = ev_first
            rejects.append(row)
        else:
            cands.append(row)

    picked, relax = pick(cands, a.count)

    # ══════ 出力の組み立て（返信175 §8）════════════════════
    for line in notes["header_lines"]:
        say(line)
    say()
    say("■ 収穫条件: " + a.harvest_note)
    say("■ 投稿済みJSON: {}（generatedAt {} / count {} / rooms {}）".format(
        a.posted_json, posted["_generatedAtJst"].strftime("%Y-%m-%d %H:%M"),
        posted.get("count"), len(posted_rooms)))
    if posted.get("notCovered"):
        say("■ 照合対象外: " + str(posted["notCovered"]))
    say("■ 自己診断: 投稿済み {}件のうち建物名が在庫と一致 {}件（必要 {}件）… OK".format(
        len(posted_rooms), len(matched), SELFCHECK_MIN_MATCHED_POSTED))

    prev = _read_state(a.out_dir)
    say("■ 在庫: 前回 {} → 今回 {}件（{}）".format(
        "{}件".format(prev.get("count")) if prev else "—（記録なし・初回）",
        len(rooms),
        "初回" if not prev else "{:+d}".format(len(rooms) - int(prev.get("count") or 0))))
    by_date = Counter(str(r["taken"]) for r in rooms.values())
    say("   取得日別: " + " / ".join("{} {}件".format(d, n) for d, n in sorted(by_date.items())))
    if dropped_dup:
        say("   ★同じ部屋の重複 {}件は新しい取得日を採用した".format(dropped_dup))
    if unparsable:
        say("   ⚠️ ファイル名を割れなかった {}件: {}".format(len(unparsable), unparsable))
    if read_errors:
        say("   ⚠️ マイソクを読めなかった {}件: {}".format(len(read_errors), read_errors))
    if bad_reg:
        say("   ⚠️ 登録データを読めなかった {}件: {}".format(len(bad_reg), [b[0] for b in bad_reg]))
    say("■ max-age-days: {}{}／根拠＝元データの14日更新サイクル／★成約確率は未測定・暫定".format(
        a.max_age_days, "（既定）" if a.max_age_days == DEFAULT_MAX_AGE_DAYS else "（★既定 14 から変更）"))
    say("   ★判定は選定日({})ではなく投稿予定日({})で行う".format(today, judge_date))
    say("■ facts が取れなかった: {}件 / 走査 {}件{}".format(
        facts_empty, len(rooms),
        "   ★★全件が空＝在庫ではなく環境の問題（PyMuPDF/fitz が無い等）を疑う"
        if facts_empty and facts_empty == len(rooms) else ""))
    say("■ 走査 {}件 → 候補 {}件 → 提案 {}件".format(len(rooms), len(cands), len(picked)))
    say("■ 投稿スロット: 直近の投稿 {} を起点に隔日 {}:00 … {}".format(
        anchor, POST_HOUR, ", ".join(str(d) for d in slots)))

    say()
    head_end = len(L)          # ★ここまでが見出し。文字列一致で切らない
    say("# 提案")
    prop_rows = [["予定日", "建物名", "部屋", "型", "賃料", "徒歩",
                  "取得日", "投稿時点の経過日数", "転載", "ブランド"]]
    for d, r in zip(slots, picked):
        prop_rows.append([
            "{} {}:00".format(d, POST_HOUR), r["building"], room_label(r["room"]), r["madori"],
            "{:,}円".format(r["rent"]) if r["rent"] is not None else "（取れない）",
            "徒歩{}分".format(r["walk"]) if r["walk"] is not None else "（取れない）",
            str(r["taken"]), (d - r["taken"]).days, r["tensai"], r["brand"]])
    for row in prop_rows:
        say("  " + ", ".join(str(x) for x in row))
    mid_start = len(L)          # ★提案表のあと〜弾いた表の手前（警告・手で確認するもの）
    if len(picked) < a.count:
        say("  ⚠️ 要求 {}件に対し {}件しか出せなかった（候補 {}件）".format(a.count, len(picked), len(cands)))
    for m in relax:
        say("  ⚠️ 制約を緩めた … " + m)

    # ★手で確認する物件（運用メモの watch リスト。自動では外さない）
    #   ★0件でも必ず出す。しかも「在庫に無い」と「在庫にはあるが候補に残らなかった」を分ける。
    #   空欄だけ出すと、照合が壊れているのか本当に居ないのかが区別できない。
    say()
    say("# ★手で確認するもの（自動では外さない・0件でも出す）")
    if not (notes.get("watch") or []):
        say("  （運用メモに watch の登録なし）")
    for w in (notes.get("watch") or []):
        wb, wr = split_property_name(w.get("match", ""))
        wbk, wrk = canon_building(wb), norm_key(wr)
        in_inv = [rooms[k] for k in rooms if k[0] == wbk]
        hits = [r for r in picked + cands if canon_building(r["building"]) == wbk]
        say("  {} … 在庫 {}件 / 候補・提案 {}件 … {}".format(
            w.get("match", ""), len(in_inv), len(hits), w.get("note", "")))
        if not in_inv:
            say("      ⚠️ 在庫に1件も無い。棟名の表記が変わった可能性がある（照合が効いていない疑い）")
        for r in hits:
            same_room = bool(wr) and norm_key(r["room"]) == wrk
            say("      {} {} {}".format("★部屋まで一致" if same_room else "建物のみ一致",
                                        r["building"], room_label(r["room"])))

    mid_end = len(L)
    say()
    say("# 弾いた {}件（★1件ずつ理由と根拠）".format(len(rejects)))
    rej_rows = [["建物名", "部屋", "弾いた条件", "根拠の文字列"]]
    for r in sorted(rejects, key=lambda x: (list(REASONS.values()).index(x["reason"]),
                                            x["building"], x["room"])):
        rej_rows.append([r["building"], room_label(r["room"]), r["reason"], r["evidence"]])
    for row in rej_rows:
        say("  " + ", ".join(str(x) for x in row))

    say()
    say("# 条件別ヒット数（★0件でも必ず出す。1室が複数条件に該当しうるので合計は一致しない）")
    for key, label in REASONS.items():
        say("  {} ... {}件{}".format(label, fired.get(key, 0),
                                     "   ★発火しなかった" if not fired.get(key) else ""))

    est = FAL_USD_PER_CUT * a.cuts * len(picked)
    say()
    say("# fal の見積もり（★表示するだけ。呼ばない）")
    say("  ${:.2f} × {}カット × {}本 = ${:.2f}".format(FAL_USD_PER_CUT, a.cuts, len(picked), est))
    say("  ★カット数の既定 {} は実績 $3.15/本 ÷ ${:.2f} から。--cuts で変えられる".format(
        DEFAULT_CUTS_PER_REEL, FAL_USD_PER_CUT))

    # ★CSV は標準出力と同じ中身にする（★『手で確認するもの』を落とさない）
    csv_rows = [[x] for x in L[:head_end]] + [["# 提案"]] + prop_rows \
        + [[x] for x in L[mid_start:mid_end]] \
        + [[""], ["# 弾いた {}件".format(len(rejects))]] + rej_rows \
        + [[""], ["# 条件別ヒット数"]] \
        + [[label, fired.get(key, 0)] for key, label in REASONS.items()] \
        + [[""], ["# fal の見積もり（表示のみ）",
                  "${:.2f} × {} × {} = ${:.2f}".format(FAL_USD_PER_CUT, a.cuts, len(picked), est)]]
    return L, csv_rows, len(rooms)


def _parse_sched(s):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _read_state(out_dir: str):
    p = os.path.join(out_dir, "_last_inventory.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="投稿する部屋をマイソクから選ぶ（★候補提示まで。動画は作らない）")
    ap.add_argument("--count", type=int, required=True,
                    help="★必須。既定値は置かない（『全部』が事故になる）。週1回2〜3本が単位")
    ap.add_argument("--harvest-note", required=True,
                    help="★必須。収穫条件をそのまま出力の先頭とログに残す")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help="★投稿予定日で判定する。既定14＝元データの14日更新サイクル")
    ap.add_argument("--cuts", type=int, default=DEFAULT_CUTS_PER_REEL, help="fal 見積もり用のカット数")
    ap.add_argument("--start", default="", help="最初の投稿予定日 YYYY-MM-DD（既定は直近投稿から隔日）")
    ap.add_argument("--maisoku-dir", default=DEFAULT_MAISOKU)
    ap.add_argument("--reg-dir", default=DEFAULT_REG)
    ap.add_argument("--posted-json", default=DEFAULT_POSTED)
    ap.add_argument("--notes", default=DEFAULT_NOTES, help="★運用メモ（リポ外）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="★リポジトリの外。物件名を含むため")
    ap.add_argument("--write", action="store_true",
                    help="★これを付けない限り何も書かない（既定はドライラン）")
    a = ap.parse_args()
    if a.count < 1:
        return _die("--count は1以上")
    if a.cuts < 1:
        return _die("--cuts は1以上")
    if a.start:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", a.start):
            return _die("--start は YYYY-MM-DD")
        if datetime.strptime(a.start, "%Y-%m-%d").date() <= datetime.now(JST).date():
            return _die("--start は明日以降（過去日に投稿予定は組まない）")

    now = datetime.now(timezone.utc)
    lines, csv_rows, inv = build_report(a, now)
    print("\n".join(lines))

    if not a.write:
        print("\n★ドライラン。何も書いていない（書くなら --write）")
        return 0
    os.makedirs(a.out_dir, exist_ok=True)
    stamp = now.astimezone(JST).strftime("%Y%m%d_%H%M")
    out = os.path.join(a.out_dir, "select_{}.csv".format(stamp))
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(csv_rows)
    with open(os.path.join(a.out_dir, "_last_inventory.json"), "w", encoding="utf-8") as f:
        json.dump({"count": inv, "at": now.astimezone(JST).isoformat(),
                   "harvestNote": a.harvest_note}, f, ensure_ascii=False, indent=2)
    print("\n★書いた: " + out)
    return 0


def _die(msg: str) -> int:
    print("✗ " + msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
