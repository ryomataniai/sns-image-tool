#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import-suumo-v1: SUUMO登録データ → 物件提案くんの投入用JSON（取得層）。

    python3 export_for_teiankun.py --base <SUUMO入稿_75枠_20260806> --out teiankun_import.json

■なぜ Python 側で前処理するのか
物件提案くん（TypeScript）へ直接読ませず、ここで中間JSONを作る。理由は2つ。
  ① **棟名正規化(bldg_key) / 号室正規化(room_key) / 万円→円(manen_to_yen) を
     書き直さないため。** 2026-08-14 に一覧CSVで manen_to_yen を書き直して
     2室の賃料を壊した。変換は1箇所に置く、が恒久ルール。
  ② 正規化（rentTotal / layoutCode / city / featureTags）は**提案くん側の
     normalizeProperty() に必ず通す。**ここで作るのは「表示用の文字列」だけにして、
     既存25件と正規化の基準がズレないようにする。

■出力に含めないもの
  - 画像（imageUrl は null。谷合判断 2026-08-17：スキーマが1枚しか持てないので今回は入れない）
  - 元付会社名・TEL は raw にだけ入れ、notes（顧客に見える側）へは出さない

■交通の出典が2系統ある（★食い違ったとき追えるように分ける）
  suumo-transit   : 06_登録データ/_transit/<棟>.json（SUUMOのらくらく交通入力・41棟）
  realpro-harvest : 07_分類結果/harvest_全件_20260814.json の access（残り6棟9室）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata as ud
from datetime import date
from pathlib import Path

from realpro_dl import bldg_key, room_key
from suumo_fields import KOZO_CD, TOKUCHO, manen_to_yen

# 投入対象から外す判定に使う列（正本＝SUUMO進行管理.csv）
EXCLUDE_VALUE = "対象外"

# kozoShuCd → 表記（KOZO_CD の逆引き。★表記は KOZO_CD 側を唯一の定義とする）
KOZO_BY_CD = {}
for _name, _cd in KOZO_CD.items():
    KOZO_BY_CD.setdefault(_cd, _name)

# 特徴コード → 語（TOKUCHO の逆引き。同一コードに別名が複数あるので最初の1つを代表にする）
TOKUCHO_BY_CD = {}
for _word, _cd in TOKUCHO:
    TOKUCHO_BY_CD.setdefault(_cd, _word)

# SUUMOの特徴コード → 物件提案くんの語（components/forms/presets/rent-standard.ts の
# DEFAULT_FEATURE_OPTIONS）。**2つの語彙が接するのはここ1箇所だけ**にする。
#
# ★なぜ要るか：提案くん側は notes をキーワードで拾って featureTags を作るが、
#   SUUMOの語と噛み合わないものがある。実測（2026-08-17・ADWResidence松屋町_0201）で
#   コード1701『洗面台（独立）』が提案くんの /独立洗面/ に当たらず、
#   **独立洗面台のタグが黙って落ちていた。**
#   TOKUCHO の別名（『洗面所独立』）でも当たらないので、ここで明示的に橋渡しする。
# ★提案くん側の正規表現をいじって解決しないこと。あちらは人が手入力したマイソクの
#   自由文も相手にしており、この都合で緩めると誤検出が増える。
TOKUCHO_TO_TEIANKUN = {
    "1701": "独立洗面台",   # 洗面台（独立）
    # ★1707『洗髪洗面化粧台』は入れない。「独立」と書いていないので、独立洗面台かどうかは
    #   分からない（3点ユニット内の化粧台でも同じ表記になる）。normalize.ts が
    #   バストイレ別について書いている「設備の列挙から推論しない」と同じ理由。
    "2705": "ペット可",     # ペット相談
    "1501": "バストイレ別",  # バス・トイレ別（正規表現でも当たるが明示しておく）
    "1201": "オートロック",
    "2801": "エアコン付",
}

# 定期借家の statusNote（谷合指定 2026-08-17）。
# ★proposalOk=false にはしない。定期借家でもよい顧客はいるので、
#   選択肢を消すのではなく**人が見える欄に書く**（statusNote は管理画面に出る）。
TEIKI_NOTE = "定期借家{nen}年（法人契約に限り普通借家契約への変更相談可）"

# harvest の access 表記。例『中央線「九条」徒歩6分』
_ACCESS_RX = re.compile(r"^(.*?)「(.+?)」徒歩(\d{1,3})分")
# マイソク本文の交通表記。例『長堀鶴見線「松屋町」徒歩2分』（harvest と同じ書式）
_MAISOKU_ACCESS_RX = re.compile(r"([^\s「（(]{1,20})「(.+?)」徒歩(\d{1,3})分")


def station_from_maisoku(text: str) -> str | None:
    """マイソク本文 → 『沿線 駅名駅 徒歩N分』。最短の便を採る。

    ★受入基準3が「沿線駅徒歩がマイソクPDFと一致すること」なので、この出典を選べるようにした。
      SUUMOのらくらく交通入力は自前の駅マスタで所要時間を計算し直しており、
      実測で79室中69室がマイソクと食い違う（SUUMOのほうが +1〜+6分・最頻+2分）。
      どちらも「正しい」が、**顧客に見せる値をどちらに揃えるかは人が決めること**。
    """
    best = None
    for line, eki, mins in _MAISOKU_ACCESS_RX.findall(ud.normalize("NFKC", text or "")):
        line, eki, m = line.strip(), eki.strip(), int(mins)
        if not line or not eki:
            continue
        if best is None or m < best[2]:
            best = (line, eki, m)
    return f"{best[0]} {best[1]}駅 徒歩{best[2]}分" if best else None


def _split_room(s: str):
    """進行管理CSVの『物件』列 → (棟名, 号室)。`_402` と ` 402号室` の2表記がある。"""
    s = str(s or "").strip()
    m = re.match(r"^(.*?)[ _]([0-9]{1,4})\s*号?室?$", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def load_excluded(base: Path) -> set[str]:
    """SUUMO進行管理.csv の『掲載候補=対象外』→ room_key の集合。"""
    p = base / "SUUMO進行管理.csv"
    if not p.is_file():
        raise SystemExit(f"[error] 正本が見つかりません: {p}")
    out = set()
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("掲載候補") or "").strip() == EXCLUDE_VALUE:
                out.add(room_key(*_split_room(r.get("物件"))))
    return out


def load_suumo_codes(base: Path) -> dict[str, str]:
    """_登録済み一覧_*.csv → {room_key: 物件コード}。★コードはここにしか無い。"""
    out = {}
    for p in sorted(base.glob("_登録済み一覧_*.csv")):
        with p.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                b, h = _split_room(r.get("部屋キー"))
                code = (r.get("物件コード") or "").strip()
                if code:
                    out[room_key(b, h)] = code
    return out


def load_harvest_access(base: Path) -> dict[str, str]:
    """harvest_全件_*.json → {room_key: access文字列}。_transit が無い棟の穴埋めに使う。"""
    hits = sorted(base.glob("07_分類結果/harvest_全件_*.json"))
    if not hits:
        return {}
    rows = json.loads(hits[-1].read_text(encoding="utf-8"))["rows"]
    out = {}
    for r in rows:
        k = room_key(r.get("name"), r.get("room"))
        if k not in out and r.get("access"):
            out[k] = r["access"]
    return out


def load_transit(base: Path) -> dict[str, list]:
    """_transit/<棟>.json → {bldg_key: [便,...]}。★棟単位なので室に展開する。"""
    out = {}
    for p in (base / "06_登録データ" / "_transit").glob("*.json"):
        out[bldg_key(p.stem)] = json.loads(p.read_text(encoding="utf-8"))
    return out


def station_from_transit(entries: list):
    """SUUMOの交通データ → (『沿線 駅名駅 徒歩N分』, 手段が明記されていたか)。

    ★kotsuShudanCd='1' が徒歩。**キーが欠けている棟がある**（S-RESIDENCE難波Briller の
      3便すべて。2026-08-17 実測）。欠けているものを「徒歩でない」として落とすと、
      **その棟だけ別ソース（リアプロ）に黙って乗り換わる**。実際にそれが起きて、
      SUUMO『南海本線 難波 9分』に対しリアプロ『御堂筋線 なんば 6分』と食い違った。
      → 出典を跨いで混ぜるより、**同じ棟のSUUMO登録値を使い、手段不明を警告に出す**。
      全データに徒歩以外（バス便）は1件も無い（徒歩120件・手段欠け3件）。
    ★最寄り（所要時間が最小）を採る。複数便は提案くん側に持たせる先が無い。
    """
    usable = [e for e in entries
              if str(e.get("shoyoTime") or "").isdigit()
              and e.get("pkgEnsenNmDisp") and e.get("pkgEkiNmDisp")
              # 徒歩と明記 or 手段の記載自体が無い。**別の手段が明記されていたら使わない**
              and str(e.get("kotsuShudanCd") or "") in ("1", "")]
    if not usable:
        return None, True
    e = min(usable, key=lambda x: int(x["shoyoTime"]))
    stated = str(e.get("kotsuShudanCd") or "") == "1"
    return f"{e['pkgEnsenNmDisp']} {e['pkgEkiNmDisp']}駅 徒歩{int(e['shoyoTime'])}分", stated


def station_from_access(access: str) -> str | None:
    """リアプロの access → 『沿線 駅名駅 徒歩N分』。読めなければ None（推測しない）。"""
    m = _ACCESS_RX.match(ud.normalize("NFKC", str(access or "")).strip())
    if not m:
        return None
    line, eki, mins = m.group(1).strip(), m.group(2).strip(), int(m.group(3))
    if not line or not eki:
        return None
    return f"{line} {eki}駅 徒歩{mins}分"


def _walk_min(station: str | None) -> int | None:
    """『… 徒歩N分』→ N。出典どうしの比較にだけ使う（保存はしない）。"""
    m = re.search(r"徒歩(\d{1,3})分", str(station or ""))
    return int(m.group(1)) if m else None


def calc_age(nen: str, getsu: str, today: date) -> int | None:
    """築年月 → 築年数。★月が無ければ年だけで見る（切り上げない）。"""
    if not str(nen or "").isdigit():
        return None
    y = int(nen)
    if not 1900 <= y <= today.year:
        return None
    m = int(getsu) if str(getsu or "").isdigit() and 1 <= int(getsu) <= 12 else 1
    age = today.year - y - (1 if (today.month, 1) < (m, 1) else 0)
    return max(age, 0)


def yen_str(v: int | None) -> str:
    return f"{v:,}円" if v is not None else ""


def build_room(rec: dict, transit: dict, access: dict, codes: dict,
               today: date, maisoku_text: str = "",
               station_source: str = "suumo") -> dict:
    """1室ぶんの中間JSON。**正規化はしない**（提案くん側の normalizeProperty に任せる）。"""
    F = rec["form"]
    bldg, heya = F.get("bukkenNm", ""), F.get("heyaNo", "")
    key = room_key(bldg, heya)

    rent_yen = manen_to_yen(F.get("chinryo1"), F.get("chinryo2"))
    fee_yen = manen_to_yen(F.get("kanrihi1"), F.get("kanrihi2"))

    # ★賃料は _rent_raw（円表記の原文）と必ず突き合わせる。
    #   2026-08-14 に chinryo2 を千円の位と読み違えて2室を壊した前例がある。
    warns = []
    raw_m = re.search(r"([\d,]+)\s*円", str(F.get("_rent_raw") or ""))
    if raw_m and rent_yen is not None:
        if int(raw_m.group(1).replace(",", "")) != rent_yen:
            warns.append(f"賃料が原文と不一致（原文{F['_rent_raw']} / 換算{rent_yen}円）")
    elif rent_yen is None:
        warns.append("賃料を万円から復元できない")

    # 交通。3出典すべてを raw に残したうえで、station に載せるものを station_source で選ぶ。
    su, mode_stated = station_from_transit(transit.get(bldg_key(bldg)) or [])
    hv = station_from_access(access.get(key, ""))
    mk = station_from_maisoku(maisoku_text or "")

    # 優先順。**マイソク優先でも、無ければ SUUMO→リアプロへ落ちる**（推測はしない）
    order = ([("maisoku", mk), ("suumo-transit", su), ("realpro-harvest", hv)]
             if station_source == "maisoku"
             else [("suumo-transit", su), ("realpro-harvest", hv), ("maisoku", mk)])
    st, src = None, None
    for name, val in order:
        if val:
            st, src = val, name
            break
    if st is None:
        warns.append("沿線・駅・徒歩の出典が無い（railwayLine/stationName/walkMinutes は null）")
    if src == "suumo-transit" and not mode_stated:
        warns.append(f"SUUMO交通に kotsuShudanCd が無く徒歩とみなした（『{st}』）")
    # ★出典の食い違いは「徒歩10分の内か外か」が変わるときだけ警告する。
    #   3出典は沿線の呼び方（『地下鉄長堀鶴見緑地線』/『長堀鶴見線』）も最寄り駅の
    #   採り方も違うので、単純比較すると**89室中80室で鳴って警告の意味が消える**
    #   （2026-08-17 実測）。分数の差も大半は1〜4分で結論が変わらない。
    #   マッチングが walkMinutes を使うのは「駅徒歩10分以内」の1箇所だけなので、
    #   **そこを跨ぐ食い違いに限る**。
    chosen = _walk_min(st)
    if chosen is not None:
        for other_name, other in (("SUUMO", su), ("リアプロ", hv), ("マイソク", mk)):
            o = _walk_min(other)
            if other and o is not None and (chosen <= 10) != (o <= 10):
                warns.append(
                    f"徒歩10分の内外が出典で食い違う（採用[{src}]『{st}』/ {other_name}『{other}』）")

    # notes は **SUUMOの特徴項目コードを語に戻したもの**。マイソク本文は入れない。
    #   featureTags は提案くん側が notes から allowlist 一致で導出する。
    #   ★元付会社名・TEL はここに入れない（顧客側に出る欄なので raw にだけ持つ）。
    # ★変数名に codes を使わないこと。引数の codes（SUUMO物件コードの辞書）を隠す
    tokucho_cds = list(rec.get("tokucho", []))
    features = [TOKUCHO_BY_CD[c] for c in tokucho_cds if c in TOKUCHO_BY_CD]
    # ★提案くんの語彙も併記する。SUUMOの語だけだと featureTags が黙って落ちる
    for c in tokucho_cds:
        w = TOKUCHO_TO_TEIANKUN.get(c)
        if w and w not in features:
            features.append(w)

    area = None
    if str(F.get("menseki1") or "").isdigit():
        area = f"{F['menseki1']}.{F.get('menseki2') or '0'}㎡"

    teiki = str(F.get("teikiShakuyaFlg") or "") == "1"
    status_note = None
    if teiki:
        status_note = TEIKI_NOTE.format(nen=F.get("teikiShakuyaNen") or "?")

    return {
        "importKey": key,
        # ★号室を name に入れる。既存の運用（「エグゼ難波東 405」）に合わせる。
        #   Property に号室カラムが無く、name が唯一の識別表示になるため。
        "name": f"{bldg} {re.sub(r'^0+', '', str(heya)) or heya}".strip(),
        "layout": F.get("_madori_raw") or None,
        "station": st,
        "rent": round(rent_yen / 10000, 4) if rent_yen is not None else None,
        "managementFee": yen_str(fee_yen) or None,
        "area": area,
        "age": calc_age(F.get("chikuNen"), F.get("chikuGetsu"), today),
        "address": F.get("_address_raw") or None,
        "deposit": F.get("_shikikin_raw") or None,
        "keyMoney": F.get("_reikin_raw") or None,
        "direction": F.get("_houi_raw") or None,
        "structure": KOZO_BY_CD.get(F.get("kozoShuCd")),
        "floor": (f"{F['kai']}階/{F['kaidate']}階建"
                  if F.get("kai") and F.get("kaidate") else None),
        "notes": " ".join(features) or None,
        "statusNote": status_note,
        "raw": {
            "importSource": "import-suumo-v1",
            "importKey": key,
            "suumoCode": codes.get(key),
            "buildingName": bldg,
            "roomNo": heya,
            "transitSource": src,
            # ★3出典すべてを残す。後で「どちらが正しかったか」を追えるようにする。
            #   79室中69室で SUUMO とマイソクが食い違う（SUUMOのほうが +1〜+6分）。
            "stationBySource": {
                "suumo-transit": su, "realpro-harvest": hv, "maisoku": mk,
            },
            "teikiShakuya": teiki,
            "teikiShakuyaNen": F.get("teikiShakuyaNen"),
            "teikiShakuyaSource": "maisoku:契約期間",
            "keiyakuKikanRaw": F.get("_keiyaku_kikan_raw"),
            "nyukyoRaw": F.get("_nyukyo_raw"),
            "tensai": {
                "広告掲載": rec.get("source", {}).get("広告掲載"),
                "画像の転載": rec.get("source", {}).get("画像の転載"),
                "間取図転載": rec.get("source", {}).get("間取図転載"),
            },
            # 管理側だけで使う。notes（顧客側）には出さない
            "motozuke": {
                "gyoshaNm": F.get("mototsukeGyoshaNm"),
                "telNo": F.get("mototsukeTelNo"),
                "kakuninDate": F.get("mototsukeKakuninDate"),
            },
            "rentYen": rent_yen,
            "feeYen": fee_yen,
            "rentRaw": F.get("_rent_raw"),
            "feeRaw": F.get("_fee_raw"),
            "scoreHint": rec.get("score_hint"),
            "sourcePdf": rec.get("source", {}).get("kyakuzuke"),
        },
        "_warn": warns,
        "_imageTensaiNg": rec.get("source", {}).get("画像の転載") == "不可",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="SUUMO登録データ → 物件提案くんの投入用JSON")
    ap.add_argument("--base", required=True, help="SUUMO入稿_75枠_20260806 のパス")
    ap.add_argument("--out", required=True, help="出力JSON")
    ap.add_argument("--today", default="", help="築年数の基準日 YYYY-MM-DD（既定=今日）")
    ap.add_argument("--station-source", choices=("suumo", "maisoku"), default="suumo",
                    help="station に載せる交通の出典。suumo=SUUMOらくらく交通入力（既定）/ "
                         "maisoku=マイソク本文。**79室中69室で食い違い、SUUMOのほうが +1〜+6分長い**。"
                         "どちらを選んでも3出典すべて raw に残る")
    a = ap.parse_args(argv)

    base = Path(a.base).expanduser()
    if not (base / "06_登録データ").is_dir():
        ap.error(f"--base の下に 06_登録データ がありません: {base}")
    today = date.fromisoformat(a.today) if a.today else date.today()

    excluded = load_excluded(base)
    transit = load_transit(base)
    access = load_harvest_access(base)
    codes = load_suumo_codes(base)

    # マイソク本文は交通の出典の1つ。**客付版PDF（source.kyakuzuke）だけを読む。**
    #   元付版は転載可否・TEL用で、交通の書式が違う。
    import core

    def maisoku_text(rec: dict) -> str:
        fn = (rec.get("source") or {}).get("kyakuzuke")
        if not fn:
            return ""
        p = base / "01_マイソク" / fn
        if not p.is_file():
            return ""
        try:
            return core.pdf_full_text(p.read_bytes())
        except Exception:  # noqa: BLE001 壊れたPDFで全体を止めない。交通は他の出典に落ちる
            return ""

    rooms, skipped, warned = [], [], []
    for p in sorted((base / "06_登録データ").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        F = rec.get("form") or {}
        key = room_key(F.get("bukkenNm"), F.get("heyaNo"))
        if key in excluded:
            skipped.append(rec["key"])
            continue
        room = build_room(rec, transit, access, codes, today,
                          maisoku_text(rec), a.station_source)
        if room["_warn"]:
            warned.append((rec["key"], room["_warn"]))
        rooms.append(room)

    # ★同じ importKey が2つ出たら、どちらを採るかを機械が決めてはいけない
    seen = {}
    for r in rooms:
        seen.setdefault(r["importKey"], []).append(r["name"])
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    if dup:
        print(f"[error] importKey が重複しています: {dup}", file=sys.stderr)
        return 1

    src_count = {}
    for r in rooms:
        src_count[r["raw"]["transitSource"]] = src_count.get(r["raw"]["transitSource"], 0) + 1

    out = {
        "meta": {
            "importSource": "import-suumo-v1",
            "generatedFor": str(today),
            "base": str(base),
            "count": len(rooms),
            "excludedCount": len(skipped),
            "excluded": skipped,
            "transitSource": src_count,
            "suumoCodeCount": sum(1 for r in rooms if r["raw"]["suumoCode"]),
            "teikiCount": sum(1 for r in rooms if r["raw"]["teikiShakuya"]),
            "imageTensaiNgCount": sum(1 for r in rooms if r["_imageTensaiNg"]),
        },
        "rooms": rooms,
    }
    op = Path(a.out).expanduser()
    op.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"対象 {len(rooms)}室（対象外 {len(skipped)}室を除外）→ {op}")
    print(f"  交通の出典: {src_count}")
    print(f"  SUUMO物件コードあり: {out['meta']['suumoCodeCount']}室 "
          f"/ 定期借家: {out['meta']['teikiCount']}室 "
          f"/ 画像の転載[不可]: {out['meta']['imageTensaiNgCount']}室")
    if warned:
        print(f"  ⚠ 警告 {len(warned)}室:")
        for k, w in warned:
            print(f"     {k}: {' / '.join(w)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
