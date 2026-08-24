#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""realprodl-v1: リアプロ（realnetpro）で検索してマイソクPDFを落とす。

    # 常駐モード（ログインは人が1回・以後はコマンドファイルで操作する）
    python3 realpro_dl.py --serve --cmd /tmp/rp_cmd --result /tmp/rp.log

■まずは探査（依頼文§3の未確認5点）から。推測で組まない。
  1. 「客付け＋元付け物件資料(2枚)」で何ファイル落ちるか（expect_download を何回受けるか）
  2. 客付版と元付版の区別方法（ファイル名が同形式なら中身で判定する）
  3. 『印刷用PDF』と『▼』のDOM（SUUMOは div.spbtn[title] だった。a/input/button だけ見て
     「無い」と結論しない）
  4. 「web転載可能」の絞り込みがUIのどれか（内部パラメータは diversion=1 の実績あり）
  5. 「検索結果PDF出力」「mybox」で一括出力できるか

■認証
ログインは人が入れる（このスクリプトはIDもパスワードも扱わない・ログにも出さない）。
★セッションは切れる。ログイン画面へ飛ばされたら**止まって人を呼ぶ**（黙って再試行しない）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import unicodedata as ud
import sys
import time
from pathlib import Path

REALPRO_URL = "https://www.realnetpro.com/index.php"
PROFILE_DIR = Path.home() / ".realpro_pw"


def is_login_screen(page) -> bool:
    """ログイン画面か（＝セッションが切れたか）。パスワード欄の有無で見る。"""
    try:
        return page.locator('input[type=password]').count() > 0
    except Exception:  # noqa: BLE001
        return False


def dump_clickables(page, limit=40):
    """押せそうな要素を洗い出す。★a/input/button だけに絞らない
    （SUUMOでは div.spbtn[title] / img.imageButton[alt] / input[type=image] が正解だった）。
    ★value は出さない（資格情報が入っている可能性のある欄を避けるため type も見る）。"""
    return page.evaluate("""(limit) => {
        const vis = e => { const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0; };
        return Array.from(document.querySelectorAll(
            'a,button,input,img,div,span,li,label'))
          .filter(vis)
          .filter(e => {
              const t = (e.type || '').toLowerCase();
              if (t === 'password' || t === 'text' || t === 'email') return false;
              const s = (e.title || e.alt || '') + '|' +
                        (e.children.length === 0 ? (e.textContent || '') : '') + '|' +
                        (e.getAttribute('onclick') || '') + '|' + (e.className || '');
              return /PDF|pdf|資料|印刷|検索|転載|mybox|部屋ごと|建物ごと|条件|ダウンロード|出力/.test(s);
          })
          .map(e => ({tag: e.tagName, type: e.type || '', id: e.id || '',
                      cls: (e.className || '').toString().slice(0, 34),
                      title: e.title || '', alt: e.alt || '',
                      text: (e.children.length === 0 ? (e.textContent || '') : '').trim().slice(0, 26),
                      onclick: (e.getAttribute('onclick') || '').slice(0, 60),
                      href: (e.getAttribute('href') || '').slice(0, 60)}))
          .slice(0, limit);
    }""", limit)


# ── 検索と収穫 ────────────────────────────────────────────────────
# ★実測（2026-08-13）。推測していない値だけを持つ。
WARDS = {"西区": "27106", "浪速区": "27111", "北区": "27127", "中央区": "27128"}
LAYOUTS = {"ワンルーム": "1", "1K": "3", "1DK": "4", "1LDK": "6"}
# 除外する状態（§4-3）。★一覧の「状態」に出る（実測: 空室/退去予定/新築/定期借家）。
#   新築・建築中＝室内写真が存在しない。定期借家＝単身層に訴求しにくい。
EXCLUDE_STATES = ("新築", "建築中", "定期借家")

# マイソクのURL。★ドロップダウンのUI操作は不要（3項目とも単純な href だった）。
#   org 未指定＝客付版 / org=2＝元付版 / org=1＝2枚が1ファイル（使わない）。
#   URLで分ければ客付と元付の取り違えが構造的に起きない。
FACTSHEET = "https://www.realnetpro.com/common/factsheet.php?id={id}"
FACTSHEET_ORG = FACTSHEET + "&org=2"

# ★JSは js/*.js に置いて実行時に読む。Pythonに埋め込むと、ヒアドキュメント経由の
#   ファイル書き込みで \n や \t が実際の改行・タブになりJSの文字列リテラルが壊れる
#   （実際に split('\n') が壊れて SyntaxError になった）。別ファイルなら node で構文検査もできる。
JS_DIR = Path(__file__).resolve().parent / "js"


def load_js(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8")


# 棟名についた部屋タイプの接頭辞。『(Aタイプ) ◯◯』『(B\'タイプ) ◯◯』『(Jrタイプ) ◯◯』
# ★2026-08-14 実測：候補402棟のうち79棟が接頭辞つきで、同じ建物が最大4レコードに割れていた
#   （サンプルレジデンスIII が (D/H/I/Jr)タイプ の4件）。外すと 402→363棟。
_TYPE_PREFIX = re.compile("^[（(]\\s*[A-Za-z0-9]{1,3}[‘’ʼ\'′]?\\s*(?:タイプ|type)\\s*[）)]\\s*", re.I)
# 除去する記号と空白。★『サンプルレジデンスA(旧:サンプルレジデンスB)』のコロンが
#   ファイル名では _ になっていた（macOSがコロンを使えないため）。記号を落とさないと
#   同じ建物が別物として扱われ、空室確認で誤って「消失」と報告する。
_SYMBOLS = "[\\s　_\\-‐‑‒–—―−~〜・.,:;：；\'\"‘’“”()（）\\[\\]【】/／\\\\]"


def bldg_key(name: str) -> str:
    """棟名の正規化キー。**棟の数え方と空室確認で同じものを使う。**

    NFKC（全角半角統一）→ 部屋タイプ接頭辞の除去 → 記号・空白の除去 → 大文字化。
    ★NFC/NFKC/記号除去だけでは1棟も動かない（リアプロの物件名は単一DB由来で揃っている）。
      効くのは**接頭辞の除去**と、**リアプロ名とファイル名の記号差**を吸収することの2つ。
    """
    t = _TYPE_PREFIX.sub("", ud.normalize("NFKC", str(name or "")).strip())
    return re.sub(_SYMBOLS, "", t).upper()


def nfc_name(name: str) -> str:
    """棟の同一性を見るためのキー（bldg_key の別名）。"""
    return bldg_key(name)


def room_key(name: str, room: str) -> str:
    """重複判定のキー。棟キー＋号室（先頭ゼロを外す）。
    ★正規化を忘れると既存のNFDファイル名と一致せず全件「未DL」になり二重DLになる
      （01_マイソク の68件中15件がNFD）。"""
    r = re.sub(r"^0+", "", ud.normalize("NFC", str(room or "")).strip())
    return f"{bldg_key(name)}_{r}"


# ファイル名末尾のタイムスタンプ。★suumo_fields._TS_RE / batch_suumo._TS_RE と同じ規則。
#   ここに別途持つのは realpro_dl が**取得層で他モジュールに依存しない**ため
#   （suumo_fields を import すると core→PIL まで引きずる）。
#   ★規則を変えるときは3箇所とも変えること。差異が出ると「既DL」の判定がずれる。
_TS_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{14})$")


def existing_keys(dirpath: Path) -> set:
    """既存マイソクの (物件名, 号室) キー集合。ファイル名末尾のタイムスタンプを外して作る。

    ★号室の部分を `[0-9A-Za-z]+` で取らないこと（2026-08-20 に実機で踏んだ）。
      実ファイルには `…_1301号室_20260820164547` のように**日本語を含む号室**がある。
      旧実装はこれを拾えず「未DL」と判定し、**再DLが起きていた**（208件中2件）。
      → タイムスタンプだけを外し、残りを右から1回だけ分ける。号室の文字種を仮定しない。
    ★号室が空（`棟名__20260820…`）のファイルも room="" として拾う。
      拾わないと「未DL」と誤判定して再DLする。使えない室であることは
      別途 dlmany 側で弾く（C 対応）。
    """
    out = set()
    for p in dirpath.glob("*.pdf"):
        m = _TS_RE.match(ud.normalize("NFC", p.stem))
        if not m:
            continue
        stem = m.group("stem")
        name, _, room = stem.rpartition("_")
        # 区切りが無い＝棟名だけ。号室不明なので棟名をそのままキーにする
        out.add(room_key(name or stem, room))
    return out


# ── 空室確認（--check-alive）────────────────────────────────────
# ★架電の代わりにリアプロの再収穫で見る。**載っている＝元付がまだ募集している**。
#   2026-08-14 に谷合さんが決めた運用。架電は差分の数室だけになる。
_STALE_RX = re.compile(r"(\d+)\s*日前")


def split_bukken(s: str):
    """進行管理の『物件』を (棟名, 号室) に割る。★2形式が混在している（実測）：
       『◯◯_506』（59行）と『◯◯ 901号室』（90行）。片方だけ見ると全部外れる。"""
    s = ud.normalize("NFC", str(s or "")).strip()
    if s.endswith("号室"):
        s = s[:-2].strip()
        i = max(s.rfind(" "), s.rfind("\u3000"))
    else:
        i = s.rfind("_")
    return (s[:i].strip(), s[i + 1:].strip()) if i > 0 else (s, "")


def cand_state(row: dict) -> str:
    """掲載候補の3値。→ '候補' | '対象外' | '未判断'。

    ★空欄は『候補』ではなく**『未判断』**（2026-08-14 谷合さんの指示）。
      黙って候補に倒すと、判断していない室が掲載枠に入る。この道具の他の判断
      （採取条件の無い収穫で止める／判定不能を消失と混ぜない）と揃える＝
      **黙って通るより止まる**。掲載指示の前に未判断がゼロであることを確かめる。
    """
    v = (row.get("掲載候補") or "").strip()
    if v == "対象外":
        return "対象外"
    if v == "候補":
        return "候補"
    return "未判断"


def _excluded(row: dict) -> bool:
    """賃料帯の導出と空室確認の対象から外すか（候補以外はすべて外す）。"""
    return cand_state(row) != "候補"


def _is_registered(v: str) -> bool:
    v = (v or "").strip()
    return v.startswith("済") or re.fullmatch(r"\d{12}", v) is not None


def index_data(data_dir: Path):
    """06_登録データ を room_key で索引する。→ {room_key: form}。

    ★ファイル名（◯◯_501.json）と進行管理の『物件』（◯◯ 501号室 が90行）は形式が違う。
      文字列でファイルを探すと号室形式の行が全部『登録データが無い』に落ちる
      （2026-08-14 に実際にそうなり、賃料帯の自動導出が27室からしか取れなかった）。
      **キーは必ず room_key() を通す。**
    """
    idx = {}
    for jp in Path(data_dir).glob("*.json"):
        if jp.name.startswith("_"):
            continue
        try:
            j = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  壊れたJSONは無いものとして扱う
            continue
        idx[room_key(*split_bukken(j.get("key") or jp.stem))] = j.get("form", {})
    return idx


def _rent_of(f: dict):
    try:
        return round(float(f"{f['chinryo1']}.{f['chinryo2'] or 0}") * 10000)
    except Exception:  # noqa: BLE001
        return None


def _room_scope(bukken: str, idx: dict, lo: int, hi: int):
    """その室が収穫の検索条件に入るか。→ (入るか, 理由)。

    ★これが要る理由：収穫は「4区 × 1K/1DK/1LDK × 賃料lo〜hi × web転載可」で絞っている。
      条件外の室は**最初から載らない**ので、無いことを『消失』と呼ぶと誤報になる。
      実測（2026-08-14）：登録済み130室の照合で34室が『無い』と出たが、内訳は
      既存物件28室・条件外4室（69,000/102,000/105,000/127,000円）・データ無し2室で、
      **条件内で本当に消えている室はゼロ**だった。
    """
    f = idx.get(room_key(*split_bukken(bukken)))
    if f is None:
        return False, "登録データが無い（条件を確認できない）"
    yen = _rent_of(f)
    if yen is None:
        return False, "賃料を読めない"
    if not (lo <= yen <= hi):
        return False, f"賃料{yen:,}円が収穫条件({lo:,}〜{hi:,})の外"
    ward = re.search(r"大阪市(.{1,4}?区)", f.get("_address_raw", "") or "")
    w = ward.group(1) if ward else None
    if w not in WARDS:
        return False, f"区={w or '不明'} が収穫条件の外"
    return True, ""


def inventory_rent_range(progress_csv: Path, data_dir: Path, emit):
    """登録在庫の賃料の実測 min/max。→ (lo, hi, 内訳) or (None, None, ...)。

    ★人が --rent-min/--rent-max を指定しない（2026-08-14 谷合さんの指示）。
      手で入れると在庫が動いたときに合わせ忘れ、条件外の室が黙って『判定不能』に
      落ち続ける。在庫から機械的に引けば、8/26に高額室を落とした時点で範囲も自動で戻る。
    ★注意：この範囲は**収穫の検索条件そのもの**になる。在庫に飛び値があると収穫件数が増え、
      所要時間が延びる。導出した範囲と、その端を決めた室は必ずログに出す。
    """
    idx = index_data(data_dir)
    yens = {}
    for r in csv.DictReader(progress_csv.open(encoding="utf-8-sig")):
        if not _is_registered(r.get("SUUMO登録", "")) or r.get("区分") == "既存":
            continue
        # ★掲載候補だけを見る（2026-08-14 谷合さんの指示）。登録は残すが掲載しない室
        #   （高額帯でPV0.1・名寄せ22点など）を含めると、飛び値に範囲を引っ張られる。
        #   実測：5室を外すと 69,000〜127,000 → 70,000〜90,000 に収まる。
        if _excluded(r):
            continue
        f = idx.get(room_key(*split_bukken(r["物件"])))
        if f is None:
            continue
        y = _rent_of(f)
        if y is not None:
            yens[r["物件"]] = y
    if not yens:
        return None, None, "登録データから賃料を1件も引けない"
    lo_k = min(yens, key=yens.get)
    hi_k = max(yens, key=yens.get)
    emit(f"[OK] 収穫条件を在庫から自動導出: {yens[lo_k]:,}〜{yens[hi_k]:,}円"
         f"（{len(yens)}室・下限={lo_k} 上限={hi_k}）")
    return yens[lo_k], yens[hi_k], f"{len(yens)}室の実測"


def check_alive(rows, progress_csv: Path, data_dir: Path, out_csv: Path,
                lo: int, hi: int, stale_days: int, emit, hist_path: Path = None):
    """収穫データと進行管理を突き合わせ、リアプロから消えた室を出す。判断はしない。

    ★lo/hi は**その収穫が実際に採られた条件**を渡すこと。在庫から導出した希望の範囲を
      渡してはいけない。範囲外の室は収穫に載らないので、全部『消失』になる。
    """
    mg = list(csv.DictReader(progress_csv.open(encoding="utf-8-sig")))
    idx = index_data(data_dir)
    live = {}
    for r in rows:
        live[room_key(r["name"], r["room"])] = r
    reg_all = [r for r in mg if _is_registered(r.get("SUUMO登録", ""))]
    # ★掲載候補外は確認対象にしない。掲載しない室が載っているかを気にする理由がなく、
    #   収穫条件の外にあるものが毎回『判定不能』に積み上がるだけになる。
    #   ただし黙って捨てない（件数と室名をログに出す）。
    excluded = [r for r in reg_all if cand_state(r) == "対象外"]
    undecided = [r for r in reg_all if cand_state(r) == "未判断"]
    reg = [r for r in reg_all if cand_state(r) == "候補"]
    out, gone_pub, gone_un, unknown, stale, teiki = [], [], [], [], [], []
    for r in reg:
        nm, rm = split_bukken(r["物件"])
        k = room_key(nm, rm)
        hit = live.get(k)
        published = (r.get("掲載指示") or "").startswith("済")
        rec = {"物件": r["物件"], "区分": r.get("区分", ""),
               "掲載指示": r.get("掲載指示", ""), "状態(進行管理)": r.get("状態", ""),
               "リアプロ状態": hit.get("state", "") if hit else "",
               "更新": hit.get("updated", "") if hit else "",
               "元付会社": hit.get("agent", "") if hit else "", "判定": "", "理由": ""}
        if hit:
            rec["判定"] = "在り"
            d = _STALE_RX.search(hit.get("updated", "") or "")
            if d and int(d.group(1)) >= stale_days:
                stale.append(rec)
            if "定期借家" in (hit.get("state", "") or ""):
                teiki.append(rec)
        else:
            # ★『既存』は我々がリアプロから仕入れた室ではない（元からSUUMOに出ていた30室）。
            #   リアプロの検索範囲に入っている保証がないので、消失判定の対象にしない。
            if r.get("区分") == "既存":
                ok, why = False, "既存物件（我々の仕入れではない＝リアプロで追えない）"
            else:
                ok, why = _room_scope(r["物件"], idx, lo, hi)
            if ok:
                rec["判定"] = "★消失"
                (gone_pub if published else gone_un).append(rec)
            else:
                rec["判定"] = "判定不能"
                rec["理由"] = why
                unknown.append(rec)
        out.append(rec)
    # ★消失した室が「消える直前に何日古かったか」を記録して貯める（2026-08-14 指示）。
    #   stale_days=3 は根拠のない暫定値。数件たまったら実測で置き換える。
    #   前回までの各室の『更新』を履歴に持ち、消失を検知した回にその値を書き出す。
    if hist_path is not None:
        hist = {}
        if hist_path.is_file():
            try:
                hist = json.loads(hist_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001  壊れていたら作り直す（履歴は補助情報）
                hist = {}
        today = time.strftime("%Y-%m-%d")
        newly = []
        for rec in gone_pub + gone_un:
            prev = hist.get(rec["物件"])
            newly.append({"記録日": today, "物件": rec["物件"],
                          "掲載指示": rec["掲載指示"],
                          "最後に見た日": (prev or {}).get("見た日", "(履歴なし)"),
                          "消える直前の更新": (prev or {}).get("更新", "(履歴なし)")})
        if newly:
            rp = hist_path.with_name("_消失記録.csv")
            new_file = not rp.is_file()
            with rp.open("a", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(newly[0].keys()))
                if new_file:
                    w.writeheader()
                w.writerows(newly)
            emit(f"[OK] 消失{len(newly)}室の『消える直前の更新』を記録 → {rp.name}")
        for rec in out:
            if rec["判定"] == "在り":
                hist[rec["物件"]] = {"見た日": today, "更新": rec["更新"]}
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        hist_path.write_text(json.dumps(hist, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    # ★候補が0室でも落とさない（全室が未判断のときに起きる。列の見出しだけ書く）
    cols = list(out[0].keys()) if out else [
        "物件", "区分", "掲載指示", "状態(進行管理)", "リアプロ状態", "更新",
        "元付会社", "判定", "理由"]
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    if excluded:
        emit(f"[情報] 掲載候補外 {len(excluded)}室は確認対象から除外: "
             + " / ".join(f"{x['物件']}（{x.get('掲載候補_理由','')}）" for x in excluded))
    emit(f"[OK] 登録済み {len(reg_all)}室（候補 {len(reg)} / 対象外 {len(excluded)}"
         f" / 未判断 {len(undecided)}） / 収穫 {len(rows)}件 と突き合わせ")
    if undecided:
        emit(f"[NG] ★掲載候補が未判断の室が {len(undecided)}室ある。"
             "掲載指示を出す前に 進行管理.csv の『掲載候補』を 候補/対象外 で埋めること:")
        for x in undecided[:20]:
            emit(f"      {x['物件']}  掲載={x.get('掲載指示','')}")
        if len(undecided) > 20:
            emit(f"      …他{len(undecided) - 20}室")
    emit(f"      在り {sum(1 for x in out if x['判定'] == '在り')}室"
         f" / ★消失 {len(gone_pub) + len(gone_un)}室"
         f" / 判定不能 {len(unknown)}室")
    if gone_pub:
        emit(f"[NG] ★掲載中なのにリアプロから消えた {len(gone_pub)}室（即対応）:")
        for x in gone_pub:
            emit(f"      {x['物件']}  掲載={x['掲載指示']}")
    else:
        emit("      掲載中の消失: なし")
    if gone_un:
        emit(f"[情報] 未掲載で消えた {len(gone_un)}室（8/26の投入候補から外す）:")
        for x in gone_un:
            emit(f"      {x['物件']}")
    if unknown:
        emit(f"[情報] 判定不能 {len(unknown)}室（収穫条件の外＝消失ではない）:")
        cnt = collections.Counter(x["理由"] for x in unknown)
        for why, v in cnt.most_common():
            emit(f"      {v}室  {why}")
    if stale:
        emit(f"[情報] 更新が{stale_days}日前以上の {len(stale)}室"
             "（元付が落とし忘れていると成約済みが残る）:")
        for x in stale[:20]:
            emit(f"      {x['物件']}  更新={x['更新']}  掲載={x['掲載指示'][:12]}")
        if len(stale) > 20:
            emit(f"      …他{len(stale) - 20}室（CSVを見ること）")
    if teiki:
        emit(f"[情報] 定期借家 {len(teiki)}室（8/26の入替判断に使う）:")
        for x in teiki:
            emit(f"      {x['物件']}  掲載={x['掲載指示'][:14]}")
    emit(f"[OK] 一覧 → {out_csv}")
    return out, undecided


def select_rooms(todo, have, want, cap_bldg, cap_agent, seed, emit):
    """DLする室を選ぶ。棟をシャッフルして**1棟1室ずつ**取る。→ (選んだ室, 診断dict)。

    ★なぜシャッフルするか（2026-08-14 実測）：
      収穫順のまま取ると、登録在庫88室のうち1社が84%を占めた。その社は母集団1462件の
      11.4%しかなく、**偏りは市場構造ではなく取り方の副作用**。リアプロの既定並びは
      更新日時順で、まとめて更新をかける元付が上位ページに固まる
      （1〜100件目でその社が84%、201〜400件目で5%）。
    ★1棟1室だけでは効かない。363棟から収穫順の先頭45棟を取ると1社62%・上位3社96%になる
      （棟上限5の12.5%より悪い）。**シャッフルと併用して初めて効く。**
    ★シャッフルは**棟の巡回順**にかける。室の順ではない。

    ★上限は「実際に取る室の元付」で数える。棟の最頻元付ではない
      （422棟の一部は1棟に複数の元付がいるため、この定義でないとずれる）。
    ★agent が空の室は上限の対象外として通す。"不明" に束ねて上限をかけると、
      社名が空で登録されている室（梅田オフィス系）をまとめて落とす。

    ★上限は round-robin の**中**で見る。あとから絞ると want に届かなくなる
      （20室・上限2で 20/20 取り切れることは30試行で実測済み）。
    """
    import random

    if seed is None:
        seed = random.randrange(2 ** 32)
    rnd = random.Random(seed)

    # 棟ごとにまとめる。★室の並びは収穫順のまま（シャッフルするのは棟の巡回順だけ）
    groups: dict[str, list] = {}
    for r in todo:
        groups.setdefault(bldg_key(r["name"]), []).append(r)

    # ★未DL棟を先・既存棟を後、という現行の優先は残す（各グループ内でシャッフルする）
    have_bldg = {k.rsplit("_", 1)[0] for k in have}
    new_b = [b for b in groups if b not in have_bldg]
    old_b = [b for b in groups if b in have_bldg]
    rnd.shuffle(new_b)
    rnd.shuffle(old_b)
    order = new_b + old_b

    # 棟の上限は**既DL分も数える**。数えないと再実行のたびに上限ぶん積み増す
    bcnt: dict[str, int] = {}
    for k in have:
        b = k.rsplit("_", 1)[0]
        bcnt[b] = bcnt.get(b, 0) + 1

    acnt: dict[str, int] = {}
    picked, idx = [], {b: 0 for b in order}
    dropped = {"棟上限": 0, "元付上限": 0}

    # round-robin：棟を1周して各棟から1室ずつ。want に達したら止める
    while len(picked) < want:
        advanced = False
        for b in order:
            if len(picked) >= want:
                break
            if cap_bldg and bcnt.get(b, 0) >= cap_bldg:
                continue
            while idx[b] < len(groups[b]):
                r = groups[b][idx[b]]
                idx[b] += 1
                agent = (r.get("agent") or "").strip()
                # ★空の元付は上限の対象外（潰さない）
                if cap_agent and agent and acnt.get(agent, 0) >= cap_agent:
                    dropped["元付上限"] += 1
                    continue
                picked.append(r)
                bcnt[b] = bcnt.get(b, 0) + 1
                if agent:
                    acnt[agent] = acnt.get(agent, 0) + 1
                advanced = True
                break
        if not advanced:
            break            # 全棟を回っても1室も取れない＝在庫を取り切った

    for b in order:
        if cap_bldg and bcnt.get(b, 0) >= cap_bldg:
            dropped["棟上限"] += len(groups[b]) - idx[b]

    share = (max(acnt.values()) / len(picked) * 100) if picked and acnt else 0.0
    top3 = sum(sorted(acnt.values(), reverse=True)[:3])
    diag = {
        "seed": seed, "選択": len(picked), "要求": want,
        "棟数": len({bldg_key(r["name"]) for r in picked}),
        "元付社数": len(acnt),
        "最大シェア": round(share, 1),
        "上位3社": round(top3 / len(picked) * 100, 1) if picked else 0.0,
        "元付内訳": dict(sorted(acnt.items(), key=lambda x: -x[1])),
        "元付空欄": sum(1 for r in picked if not (r.get("agent") or "").strip()),
        "後回し": dropped,
    }
    emit(f"[OK] 棟シャッフル seed={seed}（--seed で再現できる）")
    emit(f"[OK] 選択 {len(picked)}/{want}室 ＝ {diag['棟数']}棟 / 元付{diag['元付社数']}社"
         f" / 最大シェア{diag['最大シェア']}% / 上位3社{diag['上位3社']}%"
         f" / 元付空欄{diag['元付空欄']}室（上限の対象外）")
    if len(picked) < want:
        emit(f"[NG] ★{want}室に届いていない（{len(picked)}室）。"
             f"上限で後回し {dropped} — 上限か母集団を見直すこと")
    emit("[OK] 元付内訳: " + json.dumps(diag["元付内訳"], ensure_ascii=False))
    return picked, diag


def warn_dup_addr(rows, emit):
    """DLした室の住所の重複を**警告だけ**する。→ 重複の組。

    ★自動統合してはいけない（2026-08-14 谷合さんの指示）。105住所に複数の棟名があり、
      その多くは実際に別建物（同じ町名の別マンション）。
    ★逆に `サンプルレジデンス NISHI-WEST` と `サンプルレジデンス西WEST` のような
      **別名の同一建物**は名前の正規化では解決できない。
    → 検出して人に投げる。母集団の事前仕分けはしない。
    """
    by: dict[str, set] = {}
    for r in rows:
        addr = re.sub(r"\s+", "", str(r.get("addr") or ""))
        if not addr:
            continue
        by.setdefault(addr, set()).add(str(r.get("name") or ""))
    dup = {k: sorted(v) for k, v in by.items() if len(v) > 1}
    if not dup:
        emit("[OK] 住所の重複なし（DLした室のなかでは）")
        return dup
    emit(f"[情報] ★同一住所に複数の棟名 {len(dup)}件。"
         f"**別建物かもしれないので自動では統合しない。人が見ること**")
    for addr, names in dup.items():
        emit(f"      {addr}: " + " / ".join(names))
    return dup


def do_search(page, lo, hi, emit) -> bool:
    """検索条件を入れて実行する。→ 成功したか。★serve と --check-alive で共用する
    （同じ条件で収穫していることが、空室確認の前提そのもののため）。"""
    page.goto("https://www.realnetpro.com/main.php?method=estate&display=room",
              wait_until="load")
    page.wait_for_timeout(2500)
    if is_login_screen(page):
        emit("[NG] ログイン画面に戻った（セッション切れ）")
        return False
    page.evaluate("() => { const b = document.querySelector('#area_b');"
                  " if (b) b.click(); }")
    page.wait_for_timeout(1500)
    res = page.evaluate(load_js("search.js"), {"wards": list(WARDS.values()),
                                               "layouts": ["3", "4", "6"],
                                               "lo": str(lo), "hi": str(hi)})
    emit(f"[OK] 条件を設定 {res}")
    page.evaluate("() => document.querySelector('div.go_search').click()")
    page.wait_for_timeout(7000)
    n = page.evaluate(
        "() => { const t = (document.body.innerText||'').replace(/\\s+/g,' ');"
        " const m = t.match(/大阪\\s*([\\d,]+)件/); return m ? m[1] : '?'; }")
    emit(f"[OK] 検索完了 ヒット {n}件 賃料{lo}〜{hi}")
    return True


def route_search_cmd(line: str):
    """serve の search 系コマンドの振り分け。→ ("name", 物件名) / ("rent", lo, hi)
    / ("bad", 理由) / None（search系ではない）。

    ★純関数にしてあるのは**分岐そのものをテストするため**。
      2026-08-20 に実機で踏んだ：dispatch が `startswith("search")` を先に見ており、
      `searchname:サンプルレジデンス本町` が賃料検索に食われた。
      lo="サンプルレジデンス本町" / hi="90000" のまま検索が通ってしまい、
      『[OK] 検索完了 ヒット 2127件 賃料サンプルレジデンス本町〜90000』が出た。
      **エラーにならず"成功"として通った**ので、ログを読むまで気づけなかった。
    ★教訓は2つ。前方一致で並べるときは**長い方を先に置く**。
      そして**賃料欄に数値でない値を入れない**（どちらか片方だけでは同じ事故が通る）。
    """
    if line.startswith("searchname:"):
        nm = line.split(":", 1)[1].strip()
        return ("bad", "searchname: 物件名が空") if not nm else ("name", nm)
    if line == "search" or line.startswith("search:"):
        parts = line.split(":")
        lo = parts[1] if len(parts) > 1 and parts[1] else "70000"
        hi = parts[2] if len(parts) > 2 and parts[2] else "90000"
        if not (str(lo).isdigit() and str(hi).isdigit()):
            return ("bad", f"search: 賃料が数値でない（lo={lo!r} hi={hi!r}）。"
                           f"物件名で探すなら searchname:<物件名>")
        return ("rent", lo, hi)
    return None


def do_search_name(page, name, emit, field=None) -> bool:
    """★物件名で検索する（賃料帯を使わない経路）。→ 成功したか。

    用途：帯（7〜9万）の外にある室を名指しでDLする。収穫には入らないので他に手段が無い。

    ★このサイトの物件名欄の name 属性は**未実測**。決め打ちで組まない。
      search_name.js が候補を集め、**1つに絞れたときだけ**入力する。
      絞れなければ候補を出して False を返す（黙って別の欄に入れない）。
      実測できたら `--name-field` で渡せば自動判定を飛ばせる。
    ★賃料・間取り・区の条件は**触らない**。名指しの検索に帯の条件が残っていると
      帯の外の室が出てこない（この経路を作った理由そのものが消える）。
    """
    page.goto("https://www.realnetpro.com/main.php?method=estate&display=room",
              wait_until="load")
    page.wait_for_timeout(2500)
    if is_login_screen(page):
        emit("[NG] ログイン画面に戻った（セッション切れ）")
        return False
    page.evaluate("() => { const b = document.querySelector('#area_b');"
                  " if (b) b.click(); }")
    page.wait_for_timeout(1500)
    res = page.evaluate(load_js("search_name.js"), {"name": name, "field": field})
    if not res.get("ok"):
        emit(f"[NG] 物件名の欄を特定できない: {res.get('why')}")
        emit("      ★推測で別の欄に入れない。下の候補から欄名を選び "
             "--name-field で指定して再実行すること")
        for c in (res.get("hits") or res.get("candidates") or [])[:20]:
            emit("      候補: " + json.dumps(c, ensure_ascii=False))
        return False
    emit(f"[OK] 物件名『{name}』を {res['used']!r} に入力（{res['by']}）")
    page.evaluate("() => document.querySelector('div.go_search').click()")
    page.wait_for_timeout(7000)
    if is_login_screen(page):
        emit("[NG] 検索後にログイン画面へ飛んだ（セッション切れ）")
        return False
    return True


def save_harvest(path: Path, rows, lo, hi, emit):
    """収穫を**採取条件つき**で保存する。

    ★条件を書かずに保存してはいけない。あとで別の賃料帯の在庫と突き合わせると、
      収穫に入っていないだけの室を『消失』と誤判定する（2026-08-14 に実際にやった）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"meta": {"rent_min": int(lo), "rent_max": int(hi),
                  "wards": sorted(WARDS), "layouts": ["1K", "1DK", "1LDK"],
                  "collected": time.strftime("%Y-%m-%d %H:%M"), "count": len(rows)},
         "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    emit(f"[OK] 収穫 {len(rows)}件（賃料{int(lo):,}〜{int(hi):,}）→ {path.name}")


def load_harvest(path: Path):
    """収穫JSONを読む。→ (rows, meta)。★旧形式（配列だけ）は meta=None で返す。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, list):
        return d, None
    return d.get("rows", []), d.get("meta")


PAGER_JS = """() => {
    const vis = e => { const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0; };
    const t = (document.body.innerText||'').replace(/\\s+/g,' ');
    const els = Array.from(document.querySelectorAll('a,span,div,li,button'))
      .filter(vis)
      .filter(e => e.children.length === 0 &&
          /^(最初|前|後|最後|\\d{1,3})$/.test((e.textContent||'').trim()))
      .map(e => ({tag:e.tagName, text:(e.textContent||'').trim(),
                  cls:(e.className||'').toString().slice(0,26), id:e.id||'',
                  href:(e.getAttribute('href')||'').slice(0,40),
                  onclick:(e.getAttribute('onclick')||'').slice(0,40)}));
    return {表示範囲: (t.match(/[\\d,]+件\\s*[\\d,]+〜[\\d,]+\\s*表示/) || [''])[0],
            要素: els.slice(0, 24)};
}"""


def pager_info(page, emit, label=""):
    """ページャの『◯件 ◯〜◯ 表示』を読む。→ dict。

    ★収穫の前後に自動で投げる（2026-08-22 F2）。
      8/20は「一覧のヒット表示1,533 vs 収穫1,466」の67件差を
      「重複掲載で確定」と読んだが**判定できていなかった**。
      8/21は 1,544 表示に対し 137ページで終端し 1,348件（差196）。
      1,544÷10＝155ページのはずが137で終わっており、**取りこぼしは実在する**。
      → 「ヒット表示」は検索条件の総数で、収穫対象の行数と定義が違う可能性がある。
        `表示範囲` を前後で記録すれば、どちらの数と比べるべきかが決まる。
    ★これは --check-alive の消失判定に効く。**1回の収穫で消失と断定しない。**
    """
    try:
        info = page.evaluate(PAGER_JS)
    except Exception as e:  # noqa: BLE001  収穫を止める理由にはしない
        emit(f"[NG] ページャを読めない{label}: {type(e).__name__}")
        return None
    emit(f"[情報] ページャ{label}: {json.dumps(info, ensure_ascii=False)[:900]}")
    return info


def do_pages(page, n, emit):
    """n ページぶん収穫して行のリストを返す。★ページャは「最初 前 1..10 後 最後」（実測）。
    「後」をテキストで押す（このサイトは onclick 属性を持たずリスナ方式）。"""
    acc, seen = [], set()
    seen_rk, dup_rk = set(), 0
    pager_info(page, emit, "（収穫前）")
    for i in range(n):
        rows = page.evaluate(load_js("harvest.js"))
        new = [r for r in rows if r["id"] not in seen]
        for r in new:
            seen.add(r["id"])
            # ★id と room_key の**両方**で数える（2026-08-22 F3）。基準は id のまま。
            #   8/20に、同じ室が17分差の2回で違う id を返した（3室）。
            #   id が揺れると同一室を二重に数えうるが、room_key に変えると
            #   bldg_key が別建物を同一視したときに**取りこぼしが黙って起きる**。
            #   id は多すぎる方向、room_key は少なすぎる方向に外れる。
            #   → 基準は変えず、**差だけを測って出す**。
            rk = room_key(r.get("name"), r.get("room"))
            if rk in seen_rk:
                dup_rk += 1
            seen_rk.add(rk)
        acc += new
        emit(f"[OK] page{i+1}: {len(rows)}件（累計{len(acc)}件）")
        if i == n - 1:
            break
        nxt = page.locator("text='後'")
        hit = next((k for k in range(nxt.count()) if nxt.nth(k).is_visible()), None)
        if hit is None:
            emit("[NG] 『後』が見つからない（最終ページの可能性）")
            break
        nxt.nth(hit).click()
        page.wait_for_timeout(5000)
        if is_login_screen(page):
            emit("[NG] ログイン画面に戻った（セッション切れ）")
            break
    pager_info(page, emit, "（収穫後）")
    emit(f"[情報] ★重複の数え方の差: id基準 {len(seen)}件 / room_key基準 {len(seen_rk)}件"
         f"（差 {len(seen) - len(seen_rk)}）。room_keyで重複した行 {dup_rk}件")
    if len(seen) != len(seen_rk):
        emit("      ★差がある＝id が揺れているか、別建物を同じ棟キーにしている。"
             "どちらかは実データを見ないと決まらない（基準は id のまま）")
    return acc


def serve(page, a, emit, log):
    """常駐モード。コマンドファイル経由で1操作ずつ行う（探査と実行の両方に使う）。

    プロトコル（1行）:
      goto:<url>            そのURLへ移動
      eval:<js>             JSを評価して結果を返す（// コメントは使わない）
      click:<selector>      その要素をクリック（可視の最初の1つ）
      dump                  押せそうな要素の一覧
      download:<selector>   クリックしてダウンロードを受け取る（複数受ける）
      quit
    """
    cmd = Path(a.cmd)
    last_search = [a.rent_min or 70000, a.rent_max or 90000]   # 収穫に書き残す採取条件
    t_idle = time.time()
    while time.time() - t_idle < a.serve_timeout:
        if not cmd.exists():
            page.wait_for_timeout(1500)
            continue
        line = cmd.read_text(encoding="utf-8").strip()
        cmd.unlink()
        t_idle = time.time()
        if line == "quit":
            emit("[OK] 終了します")
            break
        # ★どのコマンドの前でもセッション切れを確認する（黙って再試行しない）
        # ★読み取り系（dump/eval）はログイン画面でも通す。ログイン画面の構造を
        #   調べられないと「反応しない」の原因が分からない（実際に詰まった）。
        #   操作系は止める＝セッション切れのまま黙って進めない。
        if (is_login_screen(page)
                and not line.startswith(("goto:", "dump", "eval:"))):
            emit("[NG] ログイン画面に戻っています（セッション切れ）。人がログインし直すこと")
            continue
        try:
            if line.startswith("goto:"):
                page.goto(line[5:].strip(), wait_until="load")
                page.wait_for_timeout(1500)
                emit(f"[OK] goto {page.url[-60:]} title={page.title()!r}")
            elif line.startswith("eval:"):
                emit(f"[情報] eval → {page.evaluate(line[5:])!r}"[:1800])
            elif line.startswith("click:"):
                sel = line[6:].strip()
                loc = page.locator(sel)
                n = loc.count()
                hit = next((i for i in range(n) if loc.nth(i).is_visible()), None)
                if hit is None:
                    emit(f"[NG] click: 可視な要素が無い（{sel} / 該当{n}件）")
                else:
                    loc.nth(hit).click()
                    page.wait_for_timeout(2000)
                    emit(f"[OK] click {sel} → url={page.url[-50:]} title={page.title()!r}")
            # ★振り分けは route_search_cmd（純関数・テストあり）に任せる。
            #   dispatch に直書きすると分岐の順序をテストできない（8/20の事故）。
            elif route_search_cmd(line) is not None:
                cmd = route_search_cmd(line)
                if cmd[0] == "bad":
                    emit(f"[NG] {cmd[1]}")
                elif cmd[0] == "rent":
                    do_search(page, cmd[1], cmd[2], emit)
                    last_search[:] = [cmd[1], cmd[2]]
                elif do_search_name(page, cmd[1], emit, a.name_field):
                    # ★帯（7〜9万）の外の室を名指しで探す経路。賃料条件は入れない。
                    hits = page.evaluate(load_js("harvest.js"))
                    emit(f"[OK] 物件名『{cmd[1]}』で {len(hits)}件")
                    for r in hits:
                        emit(f"      id={r['id']} {r['name']}_{r['room']} "
                             f"({r.get('state')}/{r.get('layout')}/{r.get('rent')}円)"
                             f" 元付={r.get('agent')}")
                    # ★DLはしない。id を見て人が dlroom: で落とす
                    #   （名指しの検索は件数が少なく、取り違えの影響が大きいため）
                    emit("      → 落とすなら dlroom:<id>（客付版・元付版とも落ちる）")
            elif line == "pagerinfo":
                pager_info(page, emit)
            elif line.startswith("pages:"):
                acc = do_pages(page, int(line.split(":", 1)[1]), emit)
                save_harvest(Path(a.result).with_suffix(".harvest.json"), acc,
                             last_search[0], last_search[1], emit)
            elif line.startswith("dlmany:"):
                # 収穫結果から本番フォルダへDL。除外と重複はここで弾く（DLを減らす＝速い）
                want = int(line.split(":", 1)[1])
                hp = Path(a.result).with_suffix(".harvest.json")
                rows, _hm = load_harvest(hp)
                kdir = Path(a.kyakuzuke_dir)
                mdir = Path(a.motozuke_dir)
                kdir.mkdir(parents=True, exist_ok=True)
                mdir.mkdir(parents=True, exist_ok=True)
                have = existing_keys(kdir)
                todo, skipped = [], {"既DL": 0, "除外状態": 0, "号室なし": 0}
                noroom = []
                for r in rows:
                    if any(x in (r.get("state") or "") for x in EXCLUDE_STATES):
                        skipped["除外状態"] += 1
                        continue
                    # ★号室が空の室はDLしない（2026-08-20 に実機で踏んだ）。
                    #   部屋キーが `棟名_` になり、SUUMO登録で「所在階が取れない」で
                    #   block する。**画像化まで済ませてから落ちるので $0.70/室 の無駄**。
                    #   ここで弾くが、**黙って捨てない**（件数と室名を必ず出す）。
                    if not str(r.get("room") or "").strip():
                        skipped["号室なし"] += 1
                        noroom.append(str(r.get("name") or "(名称不明)"))
                        continue
                    if room_key(r["name"], r["room"]) in have:
                        skipped["既DL"] += 1
                        continue
                    todo.append(r)
                if noroom:
                    emit(f"[情報] ★号室が空で除外 {len(noroom)}室"
                         f"（登録時に『所在階が取れない』で必ず落ちるため）:")
                    for nm in noroom:
                        emit(f"      {nm}")
                emit(f"[OK] 収穫{len(rows)}件 → 候補{len(todo)}件"
                     f"（既DL{skipped['既DL']} / 除外{skipped['除外状態']}"
                     f" / 号室なし{skipped['号室なし']}）")
                # ★棟をシャッフルして1棟1室ずつ・棟上限・元付上限は select_rooms が
                #   まとめて見る。上限を「あとから絞る」形にすると want に届かない。
                todo, sel_diag = select_rooms(
                    todo, have, want,
                    a.max_per_building, a.max_per_agent, a.seed, emit)
                ok = ng = 0
                got = []
                for i, r in enumerate(todo, 1):
                    try:
                        for label, url, dest in (
                                ("客付", FACTSHEET.format(id=r["id"]), kdir),
                                ("元付", FACTSHEET_ORG.format(id=r["id"]), mdir)):
                            with page.expect_download(timeout=60000) as d:
                                page.evaluate("(u) => { window.location.href = u; }", url)
                            dl = d.value
                            dl.save_as(str(dest / dl.suggested_filename))
                        ok += 1
                        got.append(r)
                        emit(f"[OK] {i}/{len(todo)} {r['name']}_{r['room']} "
                             f"({r.get('state')}/{r.get('layout')}/{r.get('rent')}円)")
                    except Exception as e:  # noqa: BLE001  1件の失敗で止めない
                        ng += 1
                        emit(f"[NG] {i}/{len(todo)} {r['name']}_{r['room']}: "
                             f"{type(e).__name__}: {str(e)[:90]}")
                emit(f"[OK] DL完了 {ok}件 / 失敗 {ng}件")
                # ★DL完了後に住所の重複を警告する（統合はしない）
                dup = warn_dup_addr(got, emit)
                # ★事後の判定材料を残す。歩留まりの数字だけでは
                #   「シャッフルのせいで下がった」のか切り分けられない。
                #   **棟単位で全滅する棟の数**がメカニズムの直接の証拠なので、
                #   棟ごとに室を並べた形で残し、採否は谷合さんが後から書き込む。
                sel_path = Path(a.result).with_suffix(".selection.json")
                bybldg: dict[str, list] = {}
                for r in got:
                    bybldg.setdefault(bldg_key(r["name"]), []).append({
                        "room_key": room_key(r["name"], r["room"]),
                        "name": r.get("name"), "room": r.get("room"),
                        "agent": r.get("agent"), "rent": r.get("rent"),
                        "layout": r.get("layout"), "addr": r.get("addr"),
                        "採否": "",          # ★谷合さんが目視で埋める（機械は判定しない）
                    })
                sel_path.write_text(json.dumps({
                    "実行": time.strftime("%Y-%m-%d %H:%M"),
                    "診断": sel_diag, "DL成功": ok, "DL失敗": ng,
                    "棟数": len(bybldg), "室数": len(got),
                    "同一住所複数棟名": dup,
                    "棟ごと": bybldg,
                }, ensure_ascii=False, indent=1), encoding="utf-8")
                emit(f"[OK] 選定の記録 → {sel_path.name}"
                     f"（棟{len(bybldg)} / 室{len(got)}。採否欄は空。谷合さんが目視で埋める）")
            elif line == "harvest":
                rows = page.evaluate(load_js("harvest.js"))
                emit(f"[OK] harvest {len(rows)}件")
                for r in rows[:3]:
                    emit("      " + json.dumps({k: r[k] for k in
                         ("id", "name", "room", "state", "layout", "area", "rent", "ad")},
                         ensure_ascii=False))
                Path(a.result).with_suffix(".harvest.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
                emit(f"[OK] 保存 {Path(a.result).with_suffix('.harvest.json').name}")
            elif line.startswith("dlroom:"):
                # 物件IDから客付版・元付版を**別々のURL**で落とす（取り違えが起きない）
                pid = line.split(":", 1)[1].strip()
                out = Path(a.download_dir)
                out.mkdir(parents=True, exist_ok=True)
                got = []
                for label, url in (("客付", FACTSHEET.format(id=pid)),
                                   ("元付", FACTSHEET_ORG.format(id=pid))):
                    with page.expect_download(timeout=60000) as d:
                        page.evaluate("(u) => { window.location.href = u; }", url)
                    dl = d.value
                    dest = out / label / dl.suggested_filename
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dl.save_as(str(dest))
                    got.append(f"{label}:{dest.name}")
                emit(f"[OK] dlroom {pid}: " + " / ".join(got))
            elif line == "dump":
                emit("[情報] 押せそうな要素:")
                for x in dump_clickables(page):
                    emit("      " + json.dumps(x, ensure_ascii=False))
            elif line.startswith("download:"):
                sel = line[9:].strip()
                out = Path(a.download_dir)
                out.mkdir(parents=True, exist_ok=True)
                got = []
                loc = page.locator(sel)
                hit = next((i for i in range(loc.count()) if loc.nth(i).is_visible()), None)
                if hit is None:
                    emit(f"[NG] download: 可視な要素が無い（{sel}）")
                    continue
                # ★1操作で何ファイル落ちるか未確認なので、最初の1つを待ってから
                #   追加のダウンロードを一定時間受け続ける（1回決め打ちにしない）。
                with page.expect_download(timeout=40000) as d1:
                    loc.nth(hit).click()
                dl = d1.value
                p = out / dl.suggested_filename
                dl.save_as(str(p))
                got.append(p.name)
                deadline = time.time() + 20
                while time.time() < deadline:
                    try:
                        with page.expect_download(timeout=6000) as dn:
                            pass
                        dl2 = dn.value
                        p2 = out / dl2.suggested_filename
                        dl2.save_as(str(p2))
                        got.append(p2.name)
                        deadline = time.time() + 12
                    except Exception:  # noqa: BLE001  もう来ない
                        break
                emit(f"[OK] download {len(got)}件: {got}")
            else:
                emit(f"[NG] 不明なコマンド: {line[:50]}")
        except Exception as e:  # noqa: BLE001  1コマンドの失敗で常駐を落とさない
            emit(f"[NG] {type(e).__name__}: {str(e)[:220]}")
            try:
                shot = Path(a.result).parent / f"rp_error_{int(time.time())}.png"
                page.screenshot(path=str(shot))
                emit(f"[情報] スクリーンショット {shot.name}")
            except Exception:  # noqa: BLE001
                pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="リアプロで検索してマイソクPDFを落とす")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--check-alive", action="store_true",
                    help="空室確認。収穫のみ実行し（DLなし）、登録済みの室が"
                         "リアプロにまだ載っているかを突き合わせる")
    ap.add_argument("--cmd", default="/tmp/rp_cmd")
    ap.add_argument("--result", default="/tmp/rp.log")
    ap.add_argument("--download-dir", default="/tmp/rp_dl")
    base = "/Users/taniairyouma/Downloads/エンクス/03_物件提案くん/SUUMO入稿_75枠_20260806"
    ap.add_argument("--kyakuzuke-dir", default=base + "/01_マイソク")
    ap.add_argument("--motozuke-dir", default=base + "/01_マイソク_元付版")
    ap.add_argument("--profile", default=str(PROFILE_DIR))
    ap.add_argument("--url", default=REALPRO_URL)
    # ★ブラウザの自動入力が入っているときだけボタンを押す。値は読まない・入れない。
    ap.add_argument("--autologin", action="store_true",
                    help="ブラウザの自動入力が効いていればログインボタンを押す"
                         "（値は読まない。suumo_keisai.try_autologin を共用）")
    ap.add_argument("--login-wait", type=int, default=1800)
    ap.add_argument("--serve-timeout", type=int, default=14400)
    ap.add_argument("--probe-login", action="store_true",
                    help="ログイン画面のまま調査モードに入る（dump/eval のみ・値は読まない）")
    # ★元付ごとの上限。**既定2**（2026-08-20 谷合さんの判断）。
    #   8/14メモの「上限5」は45室前提の数字で、**20室では上限なしとほぼ同じ**
    #   （実測: 上限5で最大シェア19.2% / 上限なしで20.0%。5/20=25%が理論下限で効かない）。
    #   30試行の実測（20室・1416室/422棟/元付95社）:
    #       上限2 → 最大10.0% / 上位3社29.3% / 10社 / 20室とも取り切れる
    #       上限3 → 最大15.0% / 上位3社38.5% /  7社 / 20室とも取り切れる
    #   「上限3だと取り切れない」という8/14メモの懸念は再現しなかったため、締める側を採る。
    ap.add_argument("--max-per-agent", type=int, default=2,
                    help="元付1社あたりの上限室数（0で無効・既定2）。"
                         "★実際に取る室の元付で数える（棟の最頻元付ではない）")
    # ★棟の巡回順のシャッフル。既定 None＝毎回ランダム。**使ったseedは必ずログに出す**
    #   （出さないと「この偏りは引きが悪かっただけか」を後から確かめられない）。
    ap.add_argument("--seed", type=int, default=None,
                    help="棟シャッフルの seed（既定=毎回ランダム）。再現したいときに指定する")
    # ★物件名検索の欄名。未実測なので既定は None（自動判定に任せる）。
    #   自動判定が1つに絞れなかったら候補を出して止まるので、そこで実測した名前を渡す。
    ap.add_argument("--name-field", default=None,
                    help="searchname: が使う物件名欄の name/id。"
                         "省略時は自動判定（絞れなければ候補を出して止まる）")
    ap.add_argument("--max-per-building", type=int, default=0,
                    help="1棟あたりのDL上限（既DL分も数える）。0で無制限")
    ap.add_argument("--progress", default=base + "/SUUMO進行管理.csv",
                    help="--check-alive の突き合わせ元（物件・SUUMO登録・掲載指示の列を使う）")
    ap.add_argument("--data-dir", default=base + "/06_登録データ",
                    help="--check-alive が賃料と区を引く登録データ（収穫条件の内外を判定する）")
    ap.add_argument("--harvest", help="--check-alive で既存の収穫JSONを使う（再収穫しない）")
    ap.add_argument("--alive-out", default=base + "/_空室確認_{date}.csv")
    # ★--check-alive の既定は「在庫から自動導出」。手で指定しないのが正（上の関数の説明を見ること）。
    #   dlmany 側の仕入れ条件（7〜9万）とは別物。指定は調査用の逃げ道として残す。
    ap.add_argument("--rent-min", type=int, default=None)
    ap.add_argument("--rent-max", type=int, default=None)
    ap.add_argument("--pages", type=int, default=200,
                    help="--check-alive の収穫ページ数の上限（最終ページで自動停止）")
    ap.add_argument("--stale-days", type=int, default=3,
                    help="更新がこの日数以上前の室を別枠で出す（元付の落とし忘れ）")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    if not (a.serve or a.check_alive):
        ap.error("--serve か --check-alive のどちらかが要る")

    if a.check_alive:
        # ★自動導出の前に「人が明示したか」を控える。導出後に見ると区別がつかず、
        #   採取条件不明の収穫に導出値を当ててしまう（実際に一度そうなった）。
        explicit_range = a.rent_min is not None and a.rent_max is not None
        lo, hi = a.rent_min, a.rent_max
        if lo is None or hi is None:
            alo, ahi, why = inventory_rent_range(
                Path(a.progress).expanduser(), Path(a.data_dir).expanduser(),
                lambda m: print(m, flush=True))
            if alo is None:
                print(f"✗ 賃料帯を自動導出できない（{why}）。--rent-min/--rent-max を指定すること")
                return 2
            lo = alo if lo is None else lo
            hi = ahi if hi is None else hi
        a.rent_min, a.rent_max = lo, hi

    if a.check_alive and a.harvest:
        # ★オフライン経路。収穫済みJSONで突き合わせだけする（ブラウザを立てない）。
        rows, meta = load_harvest(Path(a.harvest).expanduser())
        if meta:
            # 判定は**その収穫が採られた条件**で行う（在庫から導いた範囲ではない）
            # ★警告するのは**在庫が収穫の外にはみ出しているときだけ**。
            #   収穫のほうが広いのは問題ない（全室を判定できている）。
            #   単なる不一致で毎回警告を出すと、本当に危ないときに読み飛ばされる。
            if a.rent_min < meta["rent_min"] or a.rent_max > meta["rent_max"]:
                print(f"※収穫は賃料{meta['rent_min']:,}〜{meta['rent_max']:,}で採取"
                      f"（{meta.get('collected','')}）だが、在庫の実測は"
                      f"{a.rent_min:,}〜{a.rent_max:,}。**はみ出す室は判定不能になる**"
                      "＝次回の収穫はこの範囲で回すこと", flush=True)
            else:
                print(f"※収穫は賃料{meta['rent_min']:,}〜{meta['rent_max']:,}で採取"
                      f"（{meta.get('collected','')}）。在庫の実測"
                      f"{a.rent_min:,}〜{a.rent_max:,}を覆っているので全室を判定できる",
                      flush=True)
            a.rent_min, a.rent_max = meta["rent_min"], meta["rent_max"]
        else:
            print("✗ この収穫JSONには採取条件が記録されていない（旧形式）。"
                  "どの賃料帯で採ったか分からないと消失判定ができないので、"
                  "--rent-min/--rent-max を明示すること", flush=True)
            if not explicit_range:
                return 2
        out = Path(str(a.alive_out).replace("{date}", time.strftime("%Y%m%d")))
        _recs, undecided = check_alive(
            rows, Path(a.progress).expanduser(), Path(a.data_dir).expanduser(),
            out, a.rent_min, a.rent_max, a.stale_days,
            lambda m: print(m, flush=True),
            hist_path=out.with_name("_空室確認履歴.json"))
        # ★未判断が残っていたら異常終了で返す（掲載指示の前段で止めるため）
        return 3 if undecided else 0

    res = Path(a.result)
    res.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def emit(msg):
        line = f"[{time.time() - t0:6.1f}s] {msg}"
        print(line, flush=True)
        with res.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log(msg):
        print(msg, flush=True)

    from playwright.sync_api import sync_playwright
    prof = Path(a.profile).expanduser()
    prof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(prof), headless=a.headless, accept_downloads=True,
            viewport={"width": 1500, "height": 980})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: (emit(f"[dialog] {d.type}: {d.message[:60]}"), d.accept()))
        try:
            page.goto(a.url, wait_until="load")
            page.wait_for_timeout(2000)
            if is_login_screen(page) and a.probe_login:
                emit("[情報] ログイン画面のまま調査モードに入ります（dump/eval のみ可）")
                return serve(page, a, emit, log)
            # ★ブラウザの自動入力が効いていれば押すだけ（2026-08-22 実測でリアプロも効く）。
            #   実装は suumo_keisai.try_autologin を共用する。**同じものを2つ書かない。**
            #   ★値は読まない・入れない。入っているか（長さ>0）だけを見る方針は変えない。
            if is_login_screen(page) and a.autologin:
                import suumo_keisai as _K
                if _K.try_autologin(page, lambda m: emit(f"[autologin]{m}")):
                    page.wait_for_timeout(2500)
                emit(f"[情報] autologin 後: ログイン画面={is_login_screen(page)}")
            if is_login_screen(page):
                emit(f"[待機] ログイン画面です。この窓でログインしてください（最大{a.login_wait}秒）")
                emit("[待機] ★IDとパスワードは人が入れてください（このツールは扱いません）")
                t1 = time.time()
                n = 0
                while time.time() - t1 < a.login_wait:
                    if page.is_closed():
                        emit("[NG] 窓が閉じられました")
                        return 2
                    if not is_login_screen(page):
                        break
                    page.wait_for_timeout(2000)
                    n += 1
                    if n % 15 == 0:
                        emit(f"[待機] まだログイン画面です（{time.time()-t1:.0f}秒経過）")
            if is_login_screen(page):
                emit("[NG] ログインを確認できませんでした")
                return 2
            emit(f"[OK] ログイン確認。url={page.url[-60:]} title={page.title()!r}")
            emit("[OK] コマンド待機に入ります")
            return serve(page, a, emit, log)
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
