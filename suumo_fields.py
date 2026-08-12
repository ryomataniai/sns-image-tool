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
TEIKI_SHAKUYA_FLG = "0"       # 契約＝普通借家

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
        has = bool(re.search(r"\d", raw))
        F[flg] = has
        if not has:
            F[f1] = F[f2] = ""
            F[kbn] = None
            continue
        ms = yen_to_manen(raw)
        F[kbn] = KINGAKU_KBN_MANEN
        if ms:
            F[f1], F[f2] = ms
        else:
            F[f1] = F[f2] = None
            warn(f"{raw_key}『{raw}』を万円の小数に分解できない")

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
    yo = re.search(r"洋\s*[:：]?\s*([\d.]+)", md)
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
    F["bukkenShuCd"] = BUKKEN_SHU_CD
    F["torihikiTaiyoKbnCd"] = TORIHIKI_TAIYO_CD
    F["teikiShakuyaFlg"] = TEIKI_SHAKUYA_FLG

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

        ad, img, mad = flag("広告掲載"), flag("画像の転載"), flag("間取図転載")
        out["source"].update({"広告掲載": ad, "画像の転載": img, "間取図転載": mad})
        if ad != "許可":
            block(f"広告掲載が『{ad}』（許可でない）")
        # ★依頼文のゲートは広告掲載のみだが、実測で『画像の転載[不可]』が4室あった。
        #   入稿画像は元付のマイソク写真が元なので、不可のままアップすると転載条件に反する。
        if img != "可能":
            block(f"画像の転載が『{img}』（Phase1の画像は元付写真が元なので使えない）")
        if mad not in ("可能", None):
            warn(f"間取図転載が『{mad}』＝間取り図(madori)を外す必要がある")
        tt = re.search(r"取引態様[：:]\s*(\S+)", mtext)
        F["_motozuke_torihiki_raw"] = tt.group(1) if tt else ""
        tel = re.search(r"TEL[：:]\s*([\d\-]+)", mtext)
        F["mototsukeTelNo"] = tel.group(1) if tel else ""
        if not tel:
            block("元付TELが取れない（先物では必須）")
        # 会社名＝**取引態様/TELと同じ高さ帯**にある法人格の語。
        # ★『法人格の最初の出現』では取れない（実測で全室が家賃保証会社を拾った：
        #   Quintet NAMBA→株式会社エポスカード / S-FORT桜川南→エルズサポート株式会社）。
        #   マイソクの保証会社欄は元付欄より上にあるため、先に法人格が出現する。
        #   元付会社名は必ずフッタの 取引態様・TEL・免許番号 と同じ帯にあるので、そこを起点にする
        #   （実測: サムティ 会社名y=658/取引態様y=651、みなもと y=656/651、近藤 y=656/651）。
        anchor_y = None
        for r in mrows:
            for w in r:
                if "取引態様" in w[4]:
                    anchor_y = (w[1] + w[3]) / 2
                    break
            if anchor_y is not None:
                break
        nm = ""
        if anchor_y is not None:
            near = [w for r in mrows for w in r
                    if abs((w[1] + w[3]) / 2 - anchor_y) <= 15]
            near.sort(key=lambda w: w[0])
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
        F["mototsukeTantoNm"] = ""      # マイソクに担当者名は無い。人が入れる
        F["mototsukeKakuninDate"] = ""  # 元付確認日は人が入れる（別タスク）

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
            cat = r["suumo_category"]
            if cat == "madori":
                slot, category = "madori", None
            elif cat == "020101":
                gaikan_n += 1
                slot, category = ("gaikan", None) if gaikan_n == 1 else ("tsuika", "020101")
            else:
                slot, category = "tsuika", cat
            out["images"].append({"file": r["file"], "path": str(p), "slot": slot,
                                  "category": category, "room": r["room"],
                                  "text_subject": r.get("text_subject", "")})
        if not out["images"]:
            block("実在する画像が0枚")
        # 枠は 間取り1+外観1+内観1+ネット基本3+追加8=14（§3-5）。実測の最大は11枚だが検査は残す
        if len(out["images"]) > 14:
            block(f"画像が{len(out['images'])}枚で枠14を超える（どれを落とすか人が決める必要がある）")
        if not any(i["slot"] == "madori" for i in out["images"]):
            warn("間取り図がない（madori＝5点カテゴリ）")

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
    ap.add_argument("--review", action="store_true", help="一覧CSVだけ出す（JSONは書かない）")
    a = ap.parse_args(argv)

    kd, md, im = (Path(a.kyakuzuke).expanduser(), Path(a.motozuke).expanduser(),
                  Path(a.images).expanduser())
    for p, n in ((kd, "--kyakuzuke"), (md, "--motozuke"), (im, "--images")):
        if not p.is_dir():
            ap.error(f"{n} が見つかりません: {p}")
    od = Path(a.out).expanduser()
    od.mkdir(parents=True, exist_ok=True)

    targets = resolve(kd, md, a.since_ts, a.only)
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
