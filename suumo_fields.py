#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1 U1: マイソクPDF＋入稿用画像フォルダ → SUUMO登録フォームの入力値を1室1JSONで出す。

使い方:
    python3 suumo_fields.py --kyakuzuke 01_マイソク --motozuke 01_マイソク_元付版 \
        --images 05_SUUMO入稿用 --out 06_登録データ --since-ts 20260812
    python3 suumo_fields.py ... --review   # 一覧CSVだけ出して中身を目で確認する

■このユニットの位置づけ
ブラウザに触る前に、**35室ぶんの入力値を人が一覧で確認できる形にする**のが目的。
誤った値で35室登録するのが最悪の事故なので、フォーム操作より先にここを固める。
APIキー不要・ネットワーク不要・SUUMOに一切アクセスしない。

■抽出方式（実測に基づく）
core.parse_maisoku_facts() が返すのは name/address/built/madori/fee/area/rent/access/equipment の
8項目だけ（35/35充足）。SUUMOフォームが要求する 敷金・礼金・開口部方位・所在階・階建・総戸数・
建築構造・入居時期 は含まれないので、**PDFの語座標でラベルと値を対応させて**取る。
マイソクは表レイアウトで、ラベルと値がテキスト順では隣り合わないため行単位の突き合わせが必要。
実測の成功率（35室）: 号室・階 35 / 構造 35 / 階建 35 / 総戸数 35 / 入居時期 35 /
敷金 35 / 礼金 35 / 郵便番号 35 / 開口部方位 34（1室はPDF自体に記載なし＝データ欠落）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import core


def nfc(s: str) -> str:
    """macOSのNFDファイル名をNFCへ（batch_suumo と同じ理由）。"""
    return unicodedata.normalize("NFC", s)


# ── SUUMOの固定値 ──────────────────────────────────────────────────
# ★§3-3の実測値。ここは物件によらず固定で入れるもの。
BUKKEN_SHU_CD = "01"          # 物件種別＝マンション
TORIHIKI_TAIYO_CD = "4"       # 取引態様＝仲介先物（元付版が「貸主」でもmikkeは客付なので4）
# ★定期借家（teiki-fix-v1・2026-08-17）
#   以前は TEIKI_SHAKUYA_FLG = "0" の**無条件代入**で、マイソクを読む処理が無かった。
#   その結果95室すべてが「普通借家」になり、**定期借家6室が普通借家として掲載された**
#   （うち4室が掲載中）。表示が実態と違う広告が出ていた。
#   ★再発防止の本体は「**分からないときに 0 に倒さない**」こと。今回の原因がそれ。
#   ★定期借家は「物件によらず固定で入れるもの」ではないので、ここに定数を置かない。
#     値は detect_teiki() がマイソクを読んで室ごとに決め、決められなければ block する。
#     （定数を残すと「既定値として使ってよい」と読めてしまい、同じ事故に戻る）

# 実測パターン（2026-08-17・6室とも同じ書き方）
#   項目欄: 『契約期間 定期借家 2年間』
#   本文  : 『【定期借家】2年※法人契約に限り、普通借家契約への変更相談可』
#   ★本文は行折り返しで『限 り』のように空白が入ることがある。年数の取得には影響しない。
_TEIKI_WORD_RX = re.compile(r"定期借家")
# ★年は『年』を必須にする。`[年ヵヶか]` にしたら『6ヶ月』の『6ヶ』を年として拾った。
#   『2ヵ年』のような書き方も通るよう、数字と年の間の ヵヶか は任意で許す。
_TEIKI_NEN_RX = re.compile(r"定期借家\s*】?\s*(\d{1,2})\s*[ヵヶか]?年")
_TEIKI_BODY_NEN_RX = re.compile(r"【\s*定期借家\s*】\s*(\d{1,2})\s*[ヵヶか]?年")
# 「定期借家不可」「定期借家ではない」等。★否定表現は自動で 0 に倒さず人に返す
# ★間に【】を挟んだら別項目なので拾わない（2026-08-17 セルフレビューで発見）。
#   その他条件は『【定期借家】2年【ペット】不可』のように【】区切りで項目が並ぶ。
#   [^。\n]{0,10} だと**隣のペット項目の「不可」**を定期借家の否定として拾い、
#   定期借家の室が「判定不能」で block される（＝入稿が止まる）。
#   block は安全側だが、正しく読める室を人手に戻すのは実害なので閉じる。
_TEIKI_NEG_RX = re.compile(r"定期借家[^。\n【】]{0,10}(不可|ではない|では無い|無し|なし|除く)")
# 月単位の定期借家（例『定期借家 6ヶ月』）。teikiShakuyaGetsu の意味が未確認なので人に返す
_TEIKI_GETSU_RX = re.compile(r"定期借家\s*】?\s*(\d{1,2})\s*[ヶヵか]?月")

# 建築構造の表記 → kozoShuCd。★未知の表記は None にして人に返す（推測で埋めない）
# ★2026-08-13にSUUMOの実フォームから読んだ実測値。依頼文§3-3の記載には誤りがあった
#   （§3-3は鉄骨造=03としていたが、実フォームの03は「プレコン」。鉄骨は06・軽量鉄骨は07）。
#   推測で埋めると別構造で登録されるので、選択肢は実フォームから取ったものだけを持つ。
KOZO_CD = {
    "鉄筋コンクリート造": "01",   # 01=鉄筋コン
    "鉄骨鉄筋コンクリート造": "02",  # 02=鉄骨鉄筋
    "鉄骨造": "06",               # 06=鉄骨
    "軽量鉄骨造": "07",           # 07=軽量鉄骨
    "木造": "05",                 # 05=木造
}
# 開口部方位 → kaikomukiKbnCd（実測で8方位ある。§3-3は4方位しか書いていなかった）
HOUI_CD = {"北": "1", "北東": "2", "東": "3", "南東": "4",
           "南": "5", "南西": "6", "西": "7", "北西": "8"}
# 間取タイプ → madoriTypeKbnCd（実測: 01ワンルーム 02K 03DK 04SDK 05LDK 06SLDK 07LK 08SK 09SLK）
MADORI_TYPE_CD = {"R": "01", "K": "02", "DK": "03", "SDK": "04", "LDK": "05",
                  "SLDK": "06", "LK": "07", "SK": "08", "SLK": "09"}
# 敷金・礼金の単位ラジオ（実測: 1=ヶ月 2=万円・既定はヶ月）。マイソクは円表記なので万円側を使う
KINGAKU_KBN_MANEN = "2"


# ── 特徴項目（§3-8の実測対応表）────────────────────────────────────────
# マイソク設備欄の表記 → SUUMOの特徴項目コード。
# ★キーは「設備欄にこの文字列が含まれるか」で判定する。順序に意味はない。
# ★§3-8「入れてはいけないもの」（インターネット対応/高速ネット対応/管理人（巡回）/日当たり良好）は
#   ここに載せない＝載せなければ機械的に入らない。コメントで残すのは再発防止のため。
TOKUCHO = [
    ("シャワートイレ", "1603"), ("ウォシュレット", "1603"), ("温水洗浄便座", "1603"),
    ("洗濯機置場（室内）", "2129"), ("室内洗濯", "2129"),
    ("インターホン（カメラ付き）", "2414"), ("カメラ付きインターホン", "2414"),
    ("TVインターホン", "2414"),
    ("ガスコンロ（2口）", "1414"), ("ガスコンロ（２口）", "1414"), ("2口コンロ", "1414"),
    ("システムキッチン", "1401"),
    ("洗髪洗面化粧台", "1707"), ("洗面化粧台", "1707"),
    ("洗面台（独立）", "1701"), ("洗面所独立", "1701"),
    ("バス・トイレ別", "1501"), ("バス・ トイレ別", "1501"),
    ("浴室乾燥機", "1507"),
    ("エアコン", "2801"),
    ("シューズボックス", "2207"),
    ("自転車置場", "0816"), ("駐輪場", "0816"),
    ("オートバイ駐輪場", "0817"), ("バイク置場", "0817"),
    ("専用ごみ置場", "0527"), ("ごみ置き場", "0527"),
    ("24時間換気", "1801"), ("２４時間換気", "1801"),
    ("オートロック", "1201"),
    ("防犯カメラ", "1211"),
    ("エレベーター", "0501"),
    ("宅配BOX", "0517"), ("宅配ボックス", "0517"), ("宅配ＢＯＸ", "0517"),
    ("ＢＳ", "2402"), ("BS", "2402"),
    ("ケーブルTV", "2404"), ("CATV", "2404"),
    ("光ファイバー", "2410"),
    ("ネット使用料不要", "2406"), ("インターネット使用料無料", "2406"), ("ネット無料", "2406"),
    ("都市ガス", "1436"),
    ("角部屋", "1007"),
    ("バルコニー", "2001"),
    ("フローリング", "2101"),
    ("南向き", "1001"),
    ("即入居可", "2701"),
    ("ペット相談", "2705"),
    ("敷金不要", "2712"), ("敷金なし", "2712"),
    ("保証人不要", "2724"),
]
# ★SUUMOに該当項目が無い／過大主張になるため入れないもの（§3-8）。
#   ここに書いてあるのは「意図して入れていない」ことの記録。
TOKUCHO_EXCLUDED_NOTE = ("インターネット対応", "高速ネット対応", "管理人（巡回）", "日当たり良好")


# ── PDFの語座標ユーティリティ ─────────────────────────────────────────
def _word_rows(pdf_bytes, page=0, band=6.0):
    """PDF1ページの語を「同じ高さの行」にまとめる。→ [[(x0,y0,x1,y1,word), ...], ...]（x昇順）。

    ★マイソクは表レイアウトで、テキスト抽出順ではラベルと値が隣り合わない。
      行にまとめてからラベルの右を読むのが唯一確実な方法（実測でこれで35/35取れる）。
    """
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        ws = doc[page].get_text("words")
    finally:
        doc.close()
    by = collections.defaultdict(list)
    for w in ws:
        by[round((w[1] + w[3]) / 2 / band)].append(w)
    return [sorted(by[k], key=lambda v: v[0]) for k in sorted(by)]


def _right_of(rows, label):
    """ラベルを含む語の右側にある語のリスト。ラベルが無ければ None、値が無ければ []。
    ★None（ラベル自体が無い＝レイアウト変更の疑い）と []（値が空欄＝データ欠落）を区別する。
      両方を None にすると『マイソクに書いていない』と『抽出が壊れた』が見分けられなくなる。"""
    for r in rows:
        for i, w in enumerate(r):
            if label in w[4]:
                return [v[4] for v in r[i + 1:]]
    return None


def _all_words(rows):
    return [w for r in rows for w in r]


def _to_zenkaku(s: str) -> str:
    """半角英数を全角へ（番地 banchiNm は全角指定＝§3-3『例 １番６号』）。"""
    return s.translate(str.maketrans(
        "0123456789-", "０１２３４５６７８９－"))


def manen_to_yen(c1, c2):
    """('7','15') → 71500。yen_to_manen の逆。→ int or None。

    ★この変換を呼び出し側で書き直さないこと。2026-08-14に一覧CSVで
      「chinryo2 は千円の位」と読み違えて 7/15 を 85,000円 と出した（正は 71,500円）。
      小数部が1桁のときは偶然一致するので、2桁の室が出るまで気づけない
      （28室中2室だけが2桁だった）。**逆変換もここに1つだけ置く。**
    """
    if c1 in (None, ""):
        return None
    frac = str(c2 or 0)
    try:
        return round(float(f"{c1}.{frac}") * 10000)
    except ValueError:
        return None


def detect_teiki(text: str):
    """マイソク本文から定期借家を判定する。→ (flg, nen, 理由, 判定できたか)。

    flg は "1"（定期借家）/ "0"（普通借家）。**判定できないときは (None, None, 理由, False)**
    を返し、呼び出し側が block() で人に返す。

    ★これが再発防止の本体：**分からないときに "0" に倒さない。**
      以前は無条件で "0" を入れており、定期借家6室が普通借家として掲載された。
      「記載が無い＝普通借家」は妥当な既定だが、「読めなかった」を同じ扱いにしてはいけない
      （2026-08-17 の恒久ルール4「情報が無いを問題が無いと読まない」と同じ型）。
    """
    if not text:
        return None, None, "本文が空で判定できない", False
    t = text.replace("\n", " ")
    if not _TEIKI_WORD_RX.search(t):
        # 語が1度も出てこない＝普通借家。これは「記載が無い」であって「読めなかった」ではない
        return "0", None, "『定期借家』の記載なし", True
    neg = _TEIKI_NEG_RX.search(t)
    if neg:
        return (None, None,
                f"『定期借家』の否定表現がある（{neg.group(0)!r}）＝人が読むこと", False)
    getsu = _TEIKI_GETSU_RX.search(t)
    if getsu and not _TEIKI_NEN_RX.search(t):
        return None, None, (f"月単位の定期借家（{getsu.group(0)!r}）。"
                            "teikiShakuyaGetsu の意味が未確認なので人が入れること"), False
    m = _TEIKI_BODY_NEN_RX.search(t) or _TEIKI_NEN_RX.search(t)
    if not m:
        return None, None, "『定期借家』はあるが年数が読めない（既知の書式外）", False
    nen = m.group(1).lstrip("0") or "0"
    if nen == "0":
        return None, None, f"定期借家の年数が0（{m.group(0)!r}）", False
    return "1", nen, f"{m.group(0)!r} から判定", True


def yen_to_manen(s):
    """'71,500円' → ('7','15')／'70,000円' → ('7','0')／'7,000円' → ('0','7')。

    ★実フォームは「[整数] ． [小数] 万円」の**小数表記**（2026-08-13に実測）。
      chinryo1=整数部(maxlength4) / chinryo2=小数部(maxlength3)。
      依頼文§3-3の『万円単位。7.8万 → 7 / 8』は 78,000円 のケースでは同じ結果になるが、
      『万の位と千の位』と読むと 71,500円（=7.15万）が表せない。実際に7室が該当した。
    ★10円未満の端数は小数3桁（＝10円単位）で表せないので None を返して人に返す（丸めない）。
    """
    if s is None:
        return None
    d = re.sub(r"[^\d]", "", str(s))
    if not d:
        return None
    v = int(d)
    if v % 10:
        return None                       # 10円未満の端数＝この表記で表せない
    man, rest = divmod(v, 10000)
    frac = str(rest // 10).zfill(3).rstrip("0")
    return (str(man), frac or "0")


# ── 1室ぶんの抽出 ──────────────────────────────────────────────────
def motozuke_flag(motozuke_pdf, label: str):
    """元付版マイソクの『{label}[...]』を読む。→ 中身の文字列 or None（記載なし）。

    ★画像化の前に転載不可を弾くために batch_suumo からも呼ぶ。抽出規則を2箇所に
      書くと必ず片方が腐るので、ここを唯一の実装にする。
    """
    if motozuke_pdf is None:
        return None
    try:
        text = core.pdf_full_text(Path(motozuke_pdf).read_bytes())
    except Exception:  # noqa: BLE001  壊れたPDFは「読めない」＝Noneではなく例外で気づきたいが、
        return None    #   画像化の前処理を止めたくないのでNone扱いにし、呼び出し側で警告する
    m = re.search(re.escape(label) + r"\s*\[([^\]]+)\]", text)
    return m.group(1) if m else None


def extract_room(key: str, kyakuzuke_pdf: Path, motozuke_pdf, images_dir: Path):
    """1室 → dict（form / images / tokucho / gate / source）。例外は投げず block に理由を積む。"""
    out = {"key": key, "form": {}, "images": [], "tokucho": [], "tokucho_hit": [],
           "gate": {"ok": True, "block": [], "warn": []},
           "source": {"kyakuzuke": kyakuzuke_pdf.name,
                      "motozuke": motozuke_pdf.name if motozuke_pdf else None}}
    F, G = out["form"], out["gate"]

    def block(msg):
        G["block"].append(msg); G["ok"] = False

    def warn(msg):
        G["warn"].append(msg)

    pdf = kyakuzuke_pdf.read_bytes()
    facts = core.parse_maisoku_facts(pdf)
    rows = _word_rows(pdf)
    words = _all_words(rows)

    # 物件名・号室（フォルダ名／見出し）
    m = re.match(r"^(.*)_([0-9A-Za-z]+)$", key)
    F["bukkenNm"] = (facts.get("name") or (m.group(1) if m else key)).strip()
    F["heyaNo"] = m.group(2) if m else ""

    # 号室・所在階（見出しの『701（7階部分）』＝階は明記されている。号室から推測しない）
    kai = None
    for w in words:
        mm = re.match(r"^([0-9A-Za-z]+)\s*[（(]\s*(\d+)\s*階部分\s*[）)]$", w[4])
        if mm:
            if mm.group(1) != F["heyaNo"]:
                warn(f"号室がフォルダ名({F['heyaNo']})とPDF({mm.group(1)})で不一致")
            kai = mm.group(2)
            break
    F["kai"] = kai or ""
    if not kai:
        block("所在階が取れない（見出しの『N階部分』が見つからない）")

    # 建築構造の行＝構造・階建・総戸数
    kc = _right_of(rows, "建築構造")
    joined = " ".join(kc) if kc else ""
    st = re.search(r"(鉄骨鉄筋コンクリート造|鉄筋コンクリート造|軽量鉄骨造|鉄骨造|木造)", joined)
    F["kozoShuCd"] = KOZO_CD.get(st.group(1)) if st else None
    if st and not F["kozoShuCd"]:
        block(f"建築構造『{st.group(1)}』に対応するコードが未定義")
    elif not st:
        block("建築構造が取れない")
    fl = re.search(r"地上(\d+)階", joined)
    F["kaidate"] = fl.group(1) if fl else ""
    if not fl:
        block("階建が取れない")
    su = re.search(r"総戸数(\d+)戸", joined)
    F["sokosu"] = su.group(1) if su else ""
    if not su:
        warn("総戸数が取れない（任意項目として空で登録）")

    # 開口部方位（ラジオ）。★記載が無い室があるので推測せず空にして警告
    hk = _right_of(rows, "開口部方位")
    houi = None
    if hk:
        for v in hk:
            if v.strip() in HOUI_CD:
                houi = v.strip()
                break
    F["kaikomukiKbnCd"] = HOUI_CD.get(houi) if houi else None
    F["_houi_raw"] = houi
    if not houi:
        warn("開口部方位がマイソクに無い（ラジオは未選択のまま＝方位を推測しない）")

    # 現況/入居時期（ラジオ 1即 2相談 3指定有り）
    ny = _right_of(rows, "現況/入居時期")
    nyj = " ".join(ny) if ny else ""
    # 実フォーム: nyukyoKbnCd(1即/2相談/3指定有り)＋nyukyoNen/nyukyoTsuki/nyukyoShunKbnCd
    #   旬の選択肢は 0初旬(1-5) 1上旬(1-10) 2中旬(11-20) 3下旬(21-末) 4末(月末3日)。
    #   ★初旬と上旬、下旬と末は範囲が重なる。重なるものは使わず 上旬/中旬/下旬 の3択に倒す
    #     （どちらとも言える日を機械が選ぶと、実際の入居可能日と広告がずれる）。
    F["nyukyoNen"] = F["nyukyoTsuki"] = F["nyukyoShunKbnCd"] = ""
    if "即入" in nyj:
        F["nyukyoKbnCd"] = "1"
    elif "相談" in nyj:
        F["nyukyoKbnCd"] = "2"
    else:
        dm = re.search(r"(20\d{2})年\s*(\d{1,2})月(?:\s*(\d{1,2})日|\s*(上旬|中旬|下旬))?", nyj)
        if dm:
            F["nyukyoKbnCd"] = "3"
            F["nyukyoNen"], F["nyukyoTsuki"] = dm.group(1), str(int(dm.group(2)))
            if dm.group(4):
                F["nyukyoShunKbnCd"] = {"上旬": "1", "中旬": "2", "下旬": "3"}[dm.group(4)]
            elif dm.group(3):
                d_ = int(dm.group(3))
                F["nyukyoShunKbnCd"] = "1" if d_ <= 10 else ("2" if d_ <= 20 else "3")
            else:
                warn(f"入居時期の旬が取れない（原文『{nyj[:20]}』）")
        else:
            F["nyukyoKbnCd"] = None
            warn(f"入居時期を判定できない（原文『{nyj[:20]}』）")
    F["_nyukyo_raw"] = nyj[:40]

    # 賃料・管理費（万/千の2フィールド）
    for src, dst in (("rent", "chinryo"), ("fee", "kanrihi")):
        ms = yen_to_manen(facts.get(src))
        if ms:
            F[dst + "1"], F[dst + "2"] = ms
        else:
            F[dst + "1"] = F[dst + "2"] = None
            block(f"{src}『{facts.get(src)}』を万円の小数に分解できない（10円未満の端数）")
    F["_rent_raw"], F["_fee_raw"] = facts.get("rent", ""), facts.get("fee", "")

    # 敷金・礼金（値が空欄＝なし）
    sk = _right_of(rows, "敷金")
    rk = _right_of(rows, "礼金")
    F["_shikikin_raw"] = " ".join(sk) if sk else ""
    F["_reikin_raw"] = " ".join(rk) if rk else ""
    if sk is None:
        warn("敷金のラベルが無い（レイアウト変更の疑い）")
    # ★実フォームは 敷金/礼金 とも「あり」チェック＋[整数]．[小数]＋単位ラジオ(1ヶ月/2万円)。
    #   マイソクは円表記なので単位は万円(2)を選ぶ。ヶ月換算はしない（賃料との割り算で誤差が出る）。
    for raw_key, flg, f1, f2, kbn in (("_shikikin_raw", "shikikinFlg", "shikikin1", "shikikin2",
                                       "shikikinKbnCd"),
                                      ("_reikin_raw", "reikinFlg", "reikin1", "reikin2",
                                       "reikinKbnCd")):
        raw = F[raw_key].strip()
        ms = yen_to_manen(raw) if re.search(r"\d", raw) else None
        # ★金額0は「なし」として扱う。『数字があるから有』にすると
        #   「敷金が『有』なのに数字が未入力です／敷金＝0なのに敷金単位区分が設定されています」で
        #   確認画面に進めない（実測：サンプルレジデンスA_810 の敷金='0円'、
        #   サンプルレジデンスB2室の敷金='0円 用+鍵ローテーション費用+kサポ費用'）。
        #   ★後者は費用の説明文が敷金欄に混ざって取れている。0円＝なしなので実害はないが、
        #     欄の切り出しが甘い記録として残す。
        zero = ms is not None and ms[0] == "0" and ms[1] in ("0", "")
        has = ms is not None and not zero
        F[flg] = has
        if not has:
            F[f1] = F[f2] = ""
            F[kbn] = None
            if zero:
                warn(f"{raw_key}『{raw[:24]}』は0円なので『なし』として登録する")
            continue
        F[kbn] = KINGAKU_KBN_MANEN
        F[f1], F[f2] = ms

    # 専有面積（menseki1=整数部 / menseki2=小数2桁）
    ar = re.search(r"(\d+)(?:\.(\d+))?", str(facts.get("area", "")))
    if ar:
        F["menseki1"] = ar.group(1)
        F["menseki2"] = (ar.group(2) or "0").ljust(2, "0")[:2]
    else:
        F["menseki1"] = F["menseki2"] = None
        block(f"専有面積を分解できない（原文『{facts.get('area')}』）")

    # 間取り（madoriTypeKbnCd / heyaCnt / madoriYoshitsu1）
    # 実測で2形式ある: '1K[洋7 K3]' と '1K[洋:6.7畳]'
    md = str(facts.get("madori", ""))
    mt = re.match(r"^\s*(\d+)?\s*(LDK|DK|K|R)\b", md)
    if mt:
        F["heyaCnt"] = mt.group(1) or "1"
        F["madoriTypeKbnCd"] = MADORI_TYPE_CD.get(mt.group(2))
    else:
        F["heyaCnt"] = F["madoriTypeKbnCd"] = None
        block(f"間取りを解釈できない（原文『{md}』）")
    # ★表記が複数ある（実測）：『洋7』『洋:6.7畳』『洋室7.2』『洋室8』。
    #   寸法表記『6.88x4.12』は帖数ではないので取らない（換算は推測になる）。
    yo = re.search(r"洋室?\s*[:：]?\s*([\d.]+)", md)
    F["madoriYoshitsu1"] = yo.group(1) if yo else ""
    if not yo:
        warn(f"洋室畳数が取れない（原文『{md}』）")
    F["_madori_raw"] = md

    # 築年月
    bm = re.match(r"(\d{4})年\s*(\d{1,2})月", str(facts.get("built", "")))
    if bm:
        F["chikuNen"], F["chikuGetsu"] = bm.group(1), str(int(bm.group(2)))
    else:
        F["chikuNen"] = F["chikuGetsu"] = None
        block(f"築年月を分解できない（原文『{facts.get('built')}』）")

    # 住所（郵便番号入力が確実＝§3-3）＋番地（全角）
    yb = None
    for w in words:
        mm = re.match(r"^〒(\d{3})-(\d{4})$", w[4])
        if mm:
            yb = (mm.group(1), mm.group(2))
            break
    if yb:
        F["yubinNo1"], F["yubinNo2"] = yb
    else:
        F["yubinNo1"] = F["yubinNo2"] = None
        block("郵便番号が取れない（住所の自動入力ができない）")
    addr = str(facts.get("address", ""))
    F["_address_raw"] = addr
    ba = re.search(r"([0-9０-９]+番[0-9０-９]*号?|[0-9０-９]+-[0-9０-９\-]+)\s*$", addr)
    F["banchiNm"] = _to_zenkaku(ba.group(1)) if ba else ""
    if not ba:
        warn(f"番地を切り出せない（住所『{addr}』・丁目までは郵便番号入力で入る）")
    # 丁目（${azaCd} で選ぶ）。数字を渡すだけにして、コード変換は実機recon後
    ch = re.search(r"([0-9０-９一二三四五六七八九十]+)丁目", addr)
    F["_chome"] = ch.group(1) if ch else ""

    # 固定値
    # ★物件種別：木造は「アパート」。マンション固定にしていたら、SUUMOの入力チェックに
    #   「構造種別チェック｜マンションなのに木造です」で弾かれた（2026-08-14 実測・
    #   サンプルレジデンスA_0101＝木造 地上3階 総戸数6戸）。
    #   実機のセレクトは 01=マンション / 02=アパート / 11=一戸建て / 16=テラス・タウンハウス。
    #   ★木造以外は変えない。マイソク146件の内訳は RC134 / 鉄骨10 / SRC3 / 木造1 で、
    #     鉄骨造はマンションのまま登録が通っている（軽量鉄骨造は1件も無いので判断材料が無い）。
    F["bukkenShuCd"] = "02" if F.get("kozoShuCd") == KOZO_CD["木造"] else BUKKEN_SHU_CD
    F["torihikiTaiyoKbnCd"] = TORIHIKI_TAIYO_CD
    # ★定期借家：マイソクから判定する（無条件代入をやめた）
    # ★本文は客付マイソクのPDF全文。extract_room には `text` という変数は無い
    #   （最初 `text` と書いて NameError になり、ゲートに記録されて155室が block した）。
    tflg, tnen, treason, tok = detect_teiki(core.pdf_full_text(pdf))
    F["teikiShakuyaFlg"] = tflg
    F["_teiki_reason"] = treason
    # ★構造化された『契約期間』欄で裏取りする（2026-08-17 セルフレビューで追加）。
    #   detect_teiki は**全文**を見る。欄が取れないマイソクでも判定できる利点がある反面、
    #   定型文のどこかに「定期借家」の語が出るだけで "1" に倒れる（＝偽陽性）。
    #   『契約期間』ラベルはマイソク188件すべてに存在した（2026-08-17 実測）ので、
    #   欄と全文が食い違ったら**どちらが正しいか機械には決められない**＝人に返す。
    #   ★片方に寄せて自動で決めないこと。今回の事故は「勝手に決めた」ことが原因。
    kikan = _right_of(rows, "契約期間")
    F["_keiyaku_kikan_raw"] = " ".join(kikan).strip() if kikan else ""
    mismatch = False
    if kikan is None:
        warn("『契約期間』欄が無く全文だけで定期借家を判定した（レイアウト変更の疑い）")
    elif tok:
        field_says = "1" if "定期借家" in F["_keiyaku_kikan_raw"] else "0"
        mismatch = field_says != tflg
        if mismatch:
            block(f"定期借家の判定が食い違う（全文={tflg} / 『契約期間』欄="
                  f"{field_says}・原文『{F['_keiyaku_kikan_raw'][:30]}』）")
    if not tok:
        block(f"定期借家を判定できない（{treason}）")
    elif mismatch:
        # ★食い違ったら値を残さない。中途半端な値が下流に流れるほうが危ない
        F["teikiShakuyaFlg"] = None
    elif tflg == "1":
        F["teikiShakuyaNen"] = tnen
        warn(f"★定期借家 {tnen}年（{treason}）。普通借家として出さないこと")

    # ── 元付版PDF（取引態様・TEL・会社名・広告可否）──────────────────
    if motozuke_pdf is None:
        block("元付版PDFが無い（元付業者名が必須＝先物では入力エラーになる）")
    else:
        mb = motozuke_pdf.read_bytes()
        mtext = core.pdf_full_text(mb)
        mrows = _word_rows(mb)

        def flag(label):
            mm = re.search(re.escape(label) + r"\s*\[([^\]]+)\]", mtext)
            return mm.group(1) if mm else None
        # ★motozuke_flag() と同じ規則。片方だけ直すことがないよう、テストで一致を見る。

        ad, img, mad = flag("広告掲載"), flag("画像の転載"), flag("間取図転載")
        out["source"].update({"広告掲載": ad, "画像の転載": img or "記載なし",
                              "間取図転載": mad or "記載なし"})
        if ad != "許可":
            block(f"広告掲載が『{ad}』（許可でない）")
        # ★画像の転載：**明示的に[不可]のときだけ止める**（2026-08-14 谷合さんの判断）。
        #   元付版には2種類のテンプレートがあり、実測で31室中28室は
        #   『広告掲載[許可]・チラシ、雑誌等掲載広告[要確認]…』で**画像の転載の項目自体が無い**。
        #   記載なしは「不可」ではない。通す根拠は3点：
        #     ①リアプロの検索を diversion=1（web転載可能）で絞っている
        #     ②元付版に 広告掲載[許可] が明示されている
        #     ③B型テンプレートには画像転載の項目が無く、その元付が管理していないと読める
        #   ★リスクを取れるのは**登録が外部に出ない**から。枠は30/30で埋まっており
        #     8/26まで掲載できない。その間に谷合さんが二階堂さん（宅建業者）へ確認する。
        #     確認が取れなければ掲載しないだけで、登録は無駄にならない。
        #   → 8/26の掲載前に、記載なしの室について確認が取れているかを必ず見ること。
        if img == "不可":
            block("画像の転載が『不可』（Phase1の画像は元付写真が元なので使えない）")
        elif img is None:
            warn("画像の転載の記載が無い（広告掲載[許可]で通す。8/26の掲載前に要確認）")
        if mad == "不可":
            warn(f"間取図転載が『不可』＝間取り図(madori)を外す必要がある")
        tt = re.search(r"取引態様[：:]\s*(\S+)", mtext)
        F["_motozuke_torihiki_raw"] = tt.group(1) if tt else ""
        tel = re.search(r"TEL[：:]\s*([\d\-]+)", mtext)
        F["mototsukeTelNo"] = tel.group(1) if tel else ""
        if not tel:
            block("元付TELが取れない（先物では必須）")
        # 会社名の取り方は2段構え（実測でテンプレートが3種類ある）。
        #   段1: **取引態様と同じ行の左側**の語。
        #        例『レオパレスセンター大阪第５ | 取引態様：貸主』『株式会社 | みなもと管理 | 取引態様…』
        #        ★法人格を含まない社名がある（レオパレスセンター大阪第５）ので、
        #          「株式会社を含む語」を探す方式だけでは取れない。
        #   段2: 段1が空なら、取引態様と同じ高さ帯（±15）にある法人格の語。
        #        例：サムティは会社名と取引態様が別の行にある。
        #   ★「法人格を含む最初の語」は使わない。保証会社（エポスカード等）を拾う。
        anchor_y = None
        nm = ""
        for r in mrows:
            idx = next((i for i, w in enumerate(r) if "取引態様" in w[4]), None)
            if idx is None:
                continue
            anchor_y = (r[idx][1] + r[idx][3]) / 2
            # ★左側のトークンは**全部つなぐ**。実測の4パターンがこれで全部合う：
            #   『株式会社｜TAKUTO』『三菱地所…株式会社｜関西支店』
            #   『グローバルコミュニティ株式会社｜大阪支社｜大阪BP部』
            #   『レオパレスセンター大阪第５』
            #   最後の1語だけ取ると「関西支店」「大阪BP部」になる（実際にそうなった）。
            # ★同じ「行」でも y がわずかに違う別行が混ざる（行の量子化が6px幅のため）。
            #   実測：サンプルレジデンスA_601 は
            #     y=646.1 『グローバルコミュニティ株式会社｜大阪支社｜大阪BP部』
            #     y=647.8 『ごみ置場・エレベーター・…』  ← 設備リストの行
            #   が同じバンドに入り、連結して会社名が壊れた。
            #   **取引態様と同じ y のトークンだけ**に絞る（構造で切る。語の内容では切らない）。
            ay = r[idx][1]
            same = [w for w in r[:idx] if abs(w[1] - ay) <= 1.5 and w[4].strip()]
            if not same:                       # y が揃わないテンプレートは行全体に戻す
                same = [w for w in r[:idx] if w[4].strip()]
            nm = "".join(w[4].strip() for w in same)
            break
        if not nm and anchor_y is not None:
            near = sorted([w for r in mrows for w in r
                           if abs((w[1] + w[3]) / 2 - anchor_y) <= 15], key=lambda w: w[0])
            for i, w in enumerate(near):
                if re.search(r"(株式会社|有限会社|合同会社|合資会社)", w[4]):
                    nm = w[4].strip()
                    if re.fullmatch(r"(株式会社|有限会社|合同会社|合資会社)", nm) and i + 1 < len(near):
                        nm = nm + near[i + 1][4].strip()
                    break
        nm = re.split(r"お問い合わせ|仲介業者様|＜|【", nm)[0].strip()
        nm = re.sub(r"[\u3000\s]+", " ", nm)
        F["mototsukeGyoshaNm"] = nm
        if not nm:
            block("元付業者名が取れない（先物では必須＝未入力でエラーになる）")
        F["mototsukeTantoNm"] = ""      # マイソクに担当者名は無い。登録時に固定値を入れる
        # 元付確認日＝客付版マイソクのDL日（ファイル名末尾のタイムスタンプ）。
        # ★実測で書式 YYYY/MM/DD が受理された。先物では必須（未入力だと
        #   『取引態様が「先物」なのに元付業者の記入がありません』で確認画面に進めない）。
        dm = re.search(r"_(\d{4})(\d{2})(\d{2})\d{6}$", kyakuzuke_pdf.stem)
        F["mototsukeKakuninDate"] = (f"{dm.group(1)}/{dm.group(2)}/{dm.group(3)}"
                                     if dm else "")
        if not dm:
            warn("客付版PDFのファイル名からDL日が取れない（元付確認日を人が入れる）")

    # ── 特徴項目 ──────────────────────────────────────────────────
    eq = str(facts.get("equipment", ""))
    seen = []
    for token, code in TOKUCHO:
        if token in eq and code not in [c for _t, c in seen]:
            seen.append((token, code))
    # ★否定文脈（満車・厳禁・不可）の設備は入れない。core.fact_negated を再利用する
    #   （e2e-bugfix で『駐輪場満車・駐輪厳禁』をタグ化した事故と同型）。
    kept = []
    for token, code in seen:
        if core.fact_negated(token, eq) or core.fact_negated(token, facts.get("full_text", "")):
            warn(f"特徴項目『{token}』は否定文脈のため除外")
            continue
        kept.append((token, code))
    out["tokucho"] = sorted({c for _t, c in kept})
    out["tokucho_hit"] = [f"{t}={c}" for t, c in kept]
    F["bukkenCatch"] = ""   # キャッチは人が書く（自動生成は優良誤認のリスクがある）

    # ── 画像 ──────────────────────────────────────────────────────
    folder = images_dir / key
    man = folder / "_manifest.csv"
    if not man.is_file():
        block(f"_manifest.csv が無い（{folder}）")
    else:
        rows_m = list(csv.DictReader(man.open(encoding="utf-8-sig")))
        gaikan_n = 0
        for r in rows_m:
            p = folder / r["file"]
            if not p.is_file():
                # ★文字が主題の9枚は _除外/ へ退避済み＝manifestに残るが実体が無い（依頼文§1-1）
                continue
            # ★manifestの suumo_category は画像生成時点の値。storage-key-v1 より前に
            #   生成したフォルダは クローゼット が 999999 のままなので、部屋名から現行の
            #   対応表で引き直す（画像を作り直さずにカテゴリだけ正す）。差が出たら記録する。
            cat = r["suumo_category"]
            if cat != "madori":
                fixed = core.suumo_category_of_room(r.get("room", ""), cat)
                if fixed != cat:
                    warn(f"{r['file']}: カテゴリを manifest の {cat} から "
                         f"{fixed} に補正（部屋名『{r.get('room')}』）")
                    cat = fixed
            if cat == "madori":
                slot, category = "madori", None
            elif cat == "020101":
                gaikan_n += 1
                slot, category = ("gaikan", None) if gaikan_n == 1 else ("tsuika", "020101")
            else:
                slot, category = "tsuika", cat
            out["images"].append({"file": r["file"], "path": str(p.resolve()), "slot": slot,
                                  "category": category, "room": r["room"],
                                  "text_subject": r.get("text_subject", "")})
        if not out["images"]:
            block("実在する画像が0枚")
        # 枠は 間取り1+外観1+内観1+ネット基本3+追加8=14（§3-5）。実測の最大は11枚だが検査は残す
        if len(out["images"]) > 14:
            block(f"画像が{len(out['images'])}枚で枠14を超える（どれを落とすか人が決める必要がある）")
        if not any(i["slot"] == "madori" for i in out["images"]):
            warn("間取り図がない（madori＝5点カテゴリ）")

    # ★先物（torihikiTaiyoKbnCd=4）では元付4項目すべて必須（実機のエラーで判明）。
    #   会社名だけ見ていると担当者・確認日の空で確認画面に進めない。
    #   担当者は登録時に固定値を入れるのでここでは空を許容し、他3つを検査する。
    if F.get("torihikiTaiyoKbnCd") == "4":
        for k, label in (("mototsukeGyoshaNm", "元付会社名"),
                         ("mototsukeTelNo", "元付電話番号"),
                         ("mototsukeKakuninDate", "元付確認日")):
            if not F.get(k):
                block(f"{label}が空（取引態様=先物では必須）")

    # 名寄せ見込み（batch_suumo と同じ式・Phase2の照合基準22点以上の事前確認）
    out["score_hint"] = _score(out["images"])
    if out["score_hint"] < 22:
        warn(f"名寄せ見込みが{out['score_hint']}点（照合基準の22点未満）")
    return out


_CAT5 = ("madori", "020101", "040101", "040103", "040104")


def _score(images):
    """名寄せ見込み点（5点カテゴリ×5＋その他カテゴリ×1）。カテゴリ単位で数える。"""
    cats = set()
    for i in images:
        cats.add("madori" if i["slot"] == "madori"
                 else (i["category"] or "020101"))
    return sum(5 if c in _CAT5 else 1 for c in cats)


# ── 対象の解決（batch_suumo と同じ規則）──────────────────────────────
_TS_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{14})$")


def resolve(kyaku_dir: Path, moto_dir: Path, since_ts="", only=None):
    """部屋キー → (客付版PDF, 元付版PDF|None)。重複は最新タイムスタンプを採る。"""
    def newest(d):
        by = {}
        for p in sorted(d.glob("*.pdf")):
            m = _TS_RE.match(p.stem)
            if not m:
                continue
            k, ts = nfc(m.group("stem")), m.group("ts")
            if k not in by or ts > by[k][0]:
                by[k] = (ts, p)
        return by
    ky, mo = newest(kyaku_dir), newest(moto_dir) if moto_dir else {}
    keys = [k for k, v in sorted(ky.items()) if not since_ts or v[0].startswith(since_ts)]
    if only:
        on = [nfc(o) for o in only]
        keys = [k for k in keys if any(o in k for o in on)]
    return [(k, ky[k][1], mo[k][1] if k in mo else None) for k in keys]


def main(argv=None):
    ap = argparse.ArgumentParser(description="マイソク＋入稿画像 → SUUMO登録フォームの入力値")
    ap.add_argument("--kyakuzuke", required=True, help="客付版マイソクPDFのフォルダ")
    ap.add_argument("--motozuke", required=True, help="元付版マイソクPDFのフォルダ")
    ap.add_argument("--images", required=True, help="入稿用画像のフォルダ（05_SUUMO入稿用）")
    ap.add_argument("--out", required=True, help="JSON/CSVの出力先")
    ap.add_argument("--since-ts", default="", help="DLタイムスタンプ接頭辞で絞る（例 20260812）")
    ap.add_argument("--only", action="append", default=[], help="部屋キーの部分一致で絞る")
    ap.add_argument("--only-list", metavar="FILE",
                    help="改行区切りの部屋キー一覧で絞る（歩留まり判定の採用リストをそのまま渡す）"
                         "。空行と # 始まりは無視。batch_suumo と同じ形式")
    ap.add_argument("--review", action="store_true", help="一覧CSVだけ出す（JSONは書かない）")
    a = ap.parse_args(argv)

    kd, md, im = (Path(a.kyakuzuke).expanduser(), Path(a.motozuke).expanduser(),
                  Path(a.images).expanduser())
    for p, n in ((kd, "--kyakuzuke"), (md, "--motozuke"), (im, "--images")):
        if not p.is_dir():
            ap.error(f"{n} が見つかりません: {p}")
    od = Path(a.out).expanduser()
    od.mkdir(parents=True, exist_ok=True)

    only = list(a.only)
    if a.only_list:
        lp = Path(a.only_list).expanduser()
        if not lp.is_file():
            ap.error(f"--only-list が見つかりません: {lp}")
        # ★NFC正規化は必須（macOSのNFDファイル名と一致させるため）
        from_file = [nfc(x.strip()) for x in lp.read_text(encoding="utf-8").splitlines()
                     if x.strip() and not x.strip().startswith("#")]
        if not from_file:
            ap.error(f"--only-list が空です: {lp}")
        only += from_file
        print(f"--only-list: {lp.name} から {len(from_file)}件を読み込み")
    targets = resolve(kd, md, a.since_ts, only)
    # ★指定したのに1件も当たらなかったキーを出す（黙って少ない件数で走らせない）
    hit_keys = [k for k, _kp, _mp in targets]
    unmatched = [o for o in only if not any(o in k for k in hit_keys)]
    if unmatched:
        print(f"⚠ 指定したのに一致しなかったキー {len(unmatched)}件: {unmatched}")
    print(f"対象 {len(targets)}室")
    recs = []
    for key, kp, mp in targets:
        try:
            r = extract_room(key, kp, mp, im)
        except Exception as e:  # noqa: BLE001  1室の失敗で全体を止めない
            r = {"key": key, "form": {}, "images": [], "tokucho": [], "score_hint": 0,
                 "gate": {"ok": False, "block": [f"{type(e).__name__}: {e}"], "warn": []},
                 "source": {}}
        recs.append(r)
        if not a.review:
            (od / f"{key}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    # 一覧CSV（人が全室まとめて目で確認するためのもの）
    cols = ["key", "ok", "bukkenNm", "heyaNo", "kai", "kaidate", "kozoShuCd", "sokosu",
            "chinryo", "kanrihi", "menseki", "madori", "heyaCnt", "madoriYoshitsu1",
            "houi", "nyukyo", "shikikin", "reikin", "chiku", "yubin", "banchiNm",
            "mototsukeGyoshaNm", "mototsukeTelNo", "images", "score", "tokucho_n",
            "block", "warn"]
    cp = od / "_review.csv"
    with cp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            F = r["form"]
            w.writerow({
                "key": r["key"], "ok": "OK" if r["gate"]["ok"] else "NG",
                "bukkenNm": F.get("bukkenNm", ""), "heyaNo": F.get("heyaNo", ""),
                "kai": F.get("kai", ""), "kaidate": F.get("kaidate", ""),
                "kozoShuCd": F.get("kozoShuCd") or "", "sokosu": F.get("sokosu", ""),
                "chinryo": f"{F.get('chinryo1')}/{F.get('chinryo2')} ({F.get('_rent_raw','')})",
                "kanrihi": f"{F.get('kanrihi1')}/{F.get('kanrihi2')} ({F.get('_fee_raw','')})",
                "menseki": f"{F.get('menseki1')}.{F.get('menseki2')}",
                "madori": F.get("_madori_raw", ""), "heyaCnt": F.get("heyaCnt") or "",
                "madoriYoshitsu1": F.get("madoriYoshitsu1", ""),
                "houi": f"{F.get('_houi_raw') or '—'}({F.get('kaikomukiKbnCd') or '—'})",
                "nyukyo": f"{F.get('nyukyoKbnCd') or '—'}:{F.get('_nyukyo_raw','')[:12]}",
                "shikikin": ("あり" if F.get("shikikinFlg") else "なし"),
                "reikin": f"{F.get('reikin1')}/{F.get('reikin2')} ({F.get('_reikin_raw','')})",
                "chiku": f"{F.get('chikuNen')}/{F.get('chikuGetsu')}",
                "yubin": f"{F.get('yubinNo1')}-{F.get('yubinNo2')}",
                "banchiNm": F.get("banchiNm", ""),
                "mototsukeGyoshaNm": F.get("mototsukeGyoshaNm", ""),
                "mototsukeTelNo": F.get("mototsukeTelNo", ""),
                "images": len(r["images"]), "score": r.get("score_hint", 0),
                "tokucho_n": len(r["tokucho"]),
                "block": " / ".join(r["gate"]["block"]),
                "warn": " / ".join(r["gate"]["warn"]),
            })

    ok = [r for r in recs if r["gate"]["ok"]]
    ng = [r for r in recs if not r["gate"]["ok"]]
    print(f"\n登録可 {len(ok)}室 / ★登録不可 {len(ng)}室")
    for r in ng:
        print(f"  NG {r['key']}: {' / '.join(r['gate']['block'])}")
    warned = [r for r in ok if r["gate"]["warn"]]
    if warned:
        print(f"\n警告つき（登録は可・人が確認）{len(warned)}室:")
        for r in warned:
            print(f"  ⚠ {r['key']}: {' / '.join(r['gate']['warn'])}")
    print(f"\n一覧CSV: {cp}")
    if not a.review:
        print(f"JSON: {od}/<部屋キー>.json（{len(recs)}件）")
    return 0 if not ng else 1


if __name__ == "__main__":
    sys.exit(main())
