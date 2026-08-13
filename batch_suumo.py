#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batchsuumo-v1: マイソクPDF群 → SUUMO入稿用画像フォルダを一括生成するCLI。

使い方:
    export GEMINI_API_KEY=...
    python3 batch_suumo.py --in 01_マイソク --out 05_SUUMO入稿用 --dry-run
    python3 batch_suumo.py --in 01_マイソク --out 05_SUUMO入稿用 --only 難波大国町Uno_903
    python3 batch_suumo.py --in 01_マイソク --out 05_SUUMO入稿用 --since-ts 20260812

■設計の要点
- Streamlit を import しない（UI非依存）。判定・命名・JPEG化はすべて core の関数を通る。
  ＝UIの「SUUMO入稿用ZIP」ボタンと **同じ core.suumo_files** を使う。差はコンテナだけ
  （UI=ZIP／CLI=フォルダ）。UIだけ・CLIだけに手が入って出力が乖離する経路を作らない。
- 処理種別は既定 `高解像度化のみ` 固定。理由は下の _TREATMENT_NOTE を参照（注記の黒帯を焼かない）。
- 1室が失敗しても全体を止めない。フォルダは完成後にrenameで確定させる（＝中途半端な
  フォルダが残って --skip-existing に「完了済み」と誤認されるのを防ぐ）。
- 出力の隣にサマリCSVとログを必ず残す（1人運用＝後から自分で追えることが前提）。
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import csv
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import core

# ── SUUMO入稿の処理種別 ────────────────────────────────────────────
# 既定は「高解像度化のみ」。他の種別をCLIから通さないのは機能の出し惜しみではなく、
# 事実ガード（app._pl_stage_facts_for＝マイソク設備欄に無い設備を描かせない前置ブロック）が
# app.py／session_state 側にあり、CLIからは同じガードを掛けられないため。
# ガード無しのステージングを一括で35室ぶん作ると、記載外の設備が写った画像が
# ポータルに並ぶ（＝優良誤認）。「黙って弱いガードで作る」より「作れないと言う」を選ぶ。
_ALLOWED_TREATMENTS = ("高解像度化のみ",)
_TREATMENT_NOTE = (
    "SUUMO入稿は『高解像度化のみ』で通す。ステージング系は core.suumo_disclaimer が\n"
    "  注記を返し、画像下端に黒帯＋白文字が重ね描きされる（掲載画像としてノイズになる）。\n"
    "  『高解像度化のみ』は disc=None なので注記が焼かれない。"
)

# PDFファイル名末尾のDLタイムスタンプ（例 ..._20260812142357.pdf）
_TS_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{14})$")


def nfc(s: str) -> str:
    """日本語ファイル名をNFC（合成済み）に正規化する。

    ★macOSはファイル名を NFD（濁点・半濁点を分解した形）で返すことがある。実測で
      入力PDF 68件のうち15件がNFD（例『レジュール』『エスリード』『ドーム』）。
      正規化しないと次の2つが壊れる：
      (1) --only にターミナルから打った文字列（NFC）が1件も一致しない
      (2) 既存の出力フォルダ（実測NFC）と新規キー（NFD）が別名になり、
          --skip-existing が「未処理」と誤認して二重生成＝二重課金になる
      見た目が同一で中身が違う文字列なので、放置すると原因に辿り着けない類のバグ。
    """
    return unicodedata.normalize("NFC", s)


def api_key_from_secrets(repo_dir: Path = None):
    """`.streamlit/secrets.toml` から GEMINI_API_KEY を読む。無ければ None。

    ★core.get_api_key は環境変数しか見ない（UIは st.secrets 経由で読む）。CLIから使うたびに
      export し直すのは事故のもとなので、UIと同じ置き場所を読めるようにする。
    ★値はログにも標準出力にも出さない（あるかないかだけを扱う）。
    """
    p = (repo_dir or Path(__file__).resolve().parent) / ".streamlit" / "secrets.toml"
    if not p.is_file():
        return None
    try:
        import toml            # streamlit の依存に含まれるので追加インストール不要
        v = (toml.load(p) or {}).get("GEMINI_API_KEY")
        if v:
            return str(v)
    except Exception:  # noqa: BLE001  TOMLとして壊れていても下の行単位フォールバックで拾う
        pass
    # ★フォールバック：値が引用符で囲まれていない secrets.toml は TOML として不正で
    #   toml.load が落ちる（実際にこれで踏んだ）。CLIは動かせるようにしておく。
    #   ただし st.secrets は厳密にTOMLを読むため、**UI側は直さないと動かない**。
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^\s*GEMINI_API_KEY\s*=\s*(.+?)\s*$', ln)
            if m:
                return m.group(1).strip("\"'") or None
    except Exception:  # noqa: BLE001
        pass
    return None


# ── 対象PDFの決定 ──────────────────────────────────────────────────
def resolve_targets(in_dir: Path, since_ts: str = "", only: list = None, limit: int = 0):
    """入力フォルダ → 処理対象 [(部屋キー, PDFパス)]。

    ★重複対策：同一物件・同一号室のPDFが複数ある（再DL分）ため、**部屋キーごとに
      タイムスタンプが最新の1本だけ**を採る。古い方を混ぜると同じ部屋を2回生成して
      課金が二重になる上、どちらが入稿された版か後から判定できなくなる。
    ★since_ts：採用した最新版のタイムスタンプがこの接頭辞で始まる部屋だけに絞る
      （例 '20260812' ＝ 8/12にDLした35室）。指定なしなら全部屋。
    """
    by_key = {}
    skipped_no_ts = []
    for p in sorted(in_dir.glob("*.pdf")):
        m = _TS_RE.match(p.stem)
        if not m:
            skipped_no_ts.append(p.name)
            continue
        # ★キーはNFCに正規化する（出力フォルダ名にもこれを使う）。理由は nfc() のdocstring。
        key, ts = nfc(m.group("stem")), m.group("ts")
        cur = by_key.get(key)
        if cur is None or ts > cur[0]:
            by_key[key] = (ts, p)
    targets = [(k, v[1]) for k, v in sorted(by_key.items())
               if not since_ts or v[0].startswith(since_ts)]
    if only:
        only_n = [nfc(o) for o in only]      # 打った文字列側もNFCへ寄せる
        targets = [(k, p) for k, p in targets if any(o in k for o in only_n)]
    dropped_old = sum(1 for _ in in_dir.glob("*.pdf")) - len(by_key) - len(skipped_no_ts)
    if limit:
        targets = targets[:limit]
    return targets, {"stems": len(by_key), "dropped_old": dropped_old,
                     "no_timestamp": skipped_no_ts}


# ── 1室ぶんの素材準備（API呼び出しは分類の1回だけ）────────────────────────
def extract_sources(pdf_path: Path):
    """PDF → (写真バイト列リスト, 間取り図, 判定meta)。APIを呼ばない＝dry-runでも使える。

    ★madori-v1：間取り図はPDF内での構造（配置面積・ラスタ寸法・彩度）で決めるので、
      LLMを呼ばずに確定する。dry-run と本番で同じ結果になる（以前はローカル判定だけで
      dry-runが過度に悲観的だった）。
    """
    pdf_bytes = pdf_path.read_bytes()
    photos = [b for (b, _w, _h) in core.extract_pdf_photos(pdf_bytes, min_px=250)]
    # 中身ゼロの白い枠（マイソクの枠線）を除外＝空ファイル防止＋分類の配列ズレ防止
    photos = [b for b in photos if not core.is_blank_frame(b)]
    fp, meta = core.find_floorplan_in_pdf(pdf_bytes)
    if fp is not None:
        # 抽出リスト内の同一オブジェクトに寄せる（`b is floorplan` の除外判定のため）
        fp = next((b for b in photos if b == fp), fp)
    return photos, fp, meta


def _all_other(codes) -> bool:
    """分類結果が全画像『その他』のみか（＝分類失敗の可能性）。設備痕跡コードは除外して判定。
    ★app._pl_all_other と同じ判定（UIとCLIで分類のリトライ条件を揃える）。"""
    if not codes:
        return True
    for cl in codes:
        room = [c for c in (cl or []) if c not in core.MAISOKU_FEATURE_CODES]
        if any(c != "OTHER" for c in room):
            return False
    return True


def classify_with_retry(client, photos, log):
    """部屋種別分類。失敗（例外 or 全『その他』）なら2.5秒待って1回だけ再試行。
    落とさず必ず codes を返す（app._pl_classify_with_retry と同方針）。"""
    default = [["OTHER"] for _ in photos]
    if not photos:
        return [], None
    warn = None
    try:
        codes = core.classify_maisoku_images(client, photos)
    except Exception as e:  # noqa: BLE001  握り潰さない
        codes, warn = None, f"{type(e).__name__}: {str(e)[:120]}"
    if codes is None or _all_other(codes):
        log(f"    分類を再試行（{warn or '全画像がその他判定'}）")
        time.sleep(2.5)
        try:
            retry = core.classify_maisoku_images(client, photos)
            if _all_other(retry):
                codes, warn = retry, (warn or "全画像が『その他』判定")
            else:
                codes, warn = retry, None
        except Exception as e:  # noqa: BLE001
            codes = codes if codes is not None else default
            warn = f"{type(e).__name__}: {str(e)[:120]}"
    return (codes or default), warn


def build_items(photos, codes, floorplan, treatment: str):
    """写真＋分類コード → 生成対象アイテム。app._pl_stage_input のitem構築と同じ判定。
    返り値は core.suumo_files に渡せる形（room/treatment/disc/gen_bytes）＋並び用の pdf_index。

    除外（treatment='使わない' 相当）＝間取り図本体・地図・白紙。外観は除外しない（SUUMOの
    建物外観カテゴリ＝5点なので必要）。
    """
    items, skipped = [], []
    for i, b in enumerate(photos):
        code_list = codes[i] if i < len(codes) else ["OTHER"]
        room_codes = [c for c in code_list if c not in core.MAISOKU_FEATURE_CODES] or ["OTHER"]
        primary = room_codes[0]
        room = core.MAISOKU_CODE_TO_ROOM.get(primary, "その他")
        if primary in core.MAISOKU_EXCLUDE_CODES or core.is_blank_image(b) or b is floorplan:
            skipped.append((i, primary if primary in core.MAISOKU_EXCLUDE_CODES
                            else ("間取り図" if b is floorplan else "BLANK(local)")))
            continue
        items.append({"pdf_index": i, "room": room, "treatment": treatment,
                      "src_bytes": b, "gen_bytes": None, "disc": None})
    # 並び：標準ツアー順（core.room_tour_rank・UIの「部屋順に整列」と同じ1箇所）→ 同ランクはPDF順。
    # 既に入稿済みの10物件が 01_gaikan から始まるのと同じ並びになる。
    items.sort(key=lambda it: (core.room_tour_rank(it["room"]), it["pdf_index"]))
    return items, skipped


# ── 生成 ──────────────────────────────────────────────────────────
def generate_items(client, items, model, aspect, workers, log):
    """items を並列生成し gen_bytes を埋める。失敗した画像はスキップして続行し、
    (成功数, [失敗の説明]) を返す。★1枚の失敗で1室を落とさない。"""
    prompt = core.build_enhance_prompt()   # 高解像度化のみ＝注記なし（disc=None のまま）

    def _one(it):
        # ★例外をこの中で必ず受け止める。ex.map は反復時に例外を再送出するため、
        #   ここで漏らすと1枚の想定外エラーで1室ぶんのループが落ちる（受入基準4に反する）。
        try:
            data, err = core.generate_from_images(
                client, [(it["src_bytes"], "image/png")], prompt,
                model=model, aspect=aspect, size="2K", add_safety=False)
            if err:
                return it, None, err
            return it, core.crop_uniform_borders(data), None   # 生成の白帯レターボックス除去
        except Exception as e:  # noqa: BLE001
            return it, None, f"{type(e).__name__}: {e}"

    ok, fails = 0, []
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for it, data, err in ex.map(_one, items):
            if err:
                fails.append(f"#{it['pdf_index']} {it['room']}: {str(err)[:100]}")
                log(f"    NG #{it['pdf_index']} {it['room']}: {str(err)[:100]}")
            else:
                it["gen_bytes"] = data
                ok += 1
    return ok, fails


def _madori_size(folder: Path):
    """フォルダ内の *madori*.jpg の画素数と寸法。無ければ None。"""
    from PIL import Image
    for p in sorted(folder.glob("*madori*.jpg")):
        try:
            w, h = Image.open(p).size
            return w * h, f"{w}x{h}", p.name
        except Exception:  # noqa: BLE001
            continue
    return None


def _warn_madori_downgrade(dest: Path, tmp: Path, log):
    """上書きで間取り図の解像度が下がるなら警告する（受入基準5）。判断は人に返す＝止めない。"""
    old, new = _madori_size(dest), _madori_size(tmp)
    if old and new and new[0] < old[0]:
        log(f"    ⚠ 上書きで間取り図の解像度が下がる: 既存 {old[1]}（{old[2]}）→ 新 {new[1]}。"
            "\n      既存の方が高精細です。この室は --overwrite を外して既存を残す方が良い"
            "可能性があります（既存はPDF以外の経路で用意されたものと思われます）。")
        return True
    if old and not new:
        log(f"    ⚠ 上書きで間取り図が無くなる: 既存 {old[1]}（{old[2]}）→ 新しい出力に間取り図なし")
        return True
    return False


def write_room(out_dir: Path, key: str, items, floorplan, overwrite: bool, log,
               fp_meta=None, text_subj=None):
    """1室ぶんを書き出す。★一時フォルダに全部書いてから rename で確定させる
    （途中で落ちたフォルダが残ると、次回 --skip-existing が『完了済み』と誤認する）。"""
    dest = out_dir / key
    tmp = out_dir / f".{key}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    done = [it for it in items if it.get("gen_bytes")]
    files = core.suumo_files(done, floorplan)
    for name, data in files:
        (tmp / name).write_bytes(data)
    # ★_manifest.csv：ファイル名 → 部屋名 → SUUMOの画像カテゴリコード → PDF内の元index。
    #   目的は2つ。
    #   (1) Phase2（自動登録）の入力。画像を入れてもカテゴリを設定しないと名寄せ点が乗らない
    #       （8/12実測 11点→28点）ため、どの枠にどのコードを設定するかを機械可読で残す。
    #   (2) 入稿前の目視チェック。分類が『その他』に落ちた画像は roomNN.jpg になるが、
    #       実測でマイソクにはQRコード・地図・ロゴ枠も埋め込まれている。roomNN が何だったのかを
    #       ここで辿れるようにしておかないと、QRコードを室内写真として入稿する事故に気づけない。
    #   ★並びは core.suumo_files の契約（除外後のitem順 → 末尾に間取り図）に依存している。
    rows = []
    for i, (name, _d) in enumerate(files):
        if i < len(done):
            it = done[i]
            rows.append({"file": name, "room": it["room"],
                         "suumo_category": _ASCII_TO_CATEGORY.get(
                             re.sub(r"_\d+$", "", name.split("_", 1)[1].rsplit(".", 1)[0]),
                             "999999"),
                         "pdf_index": it["pdf_index"], "treatment": it["treatment"],
                         # ★text-subject-v1：文字が主題の画像を人が目視で外すための手がかり。
                         #   非空＝その画像の主題（AI判定）。空＝文字は主題でない（写り込みは空になる）。
                         #   ここを根拠に自動で落としたりはしない（選別は人がする）。
                         "text_subject": (text_subj or {}).get(it["pdf_index"], "")})
        else:
            # ★madori-v1：間取り図の行には判定根拠とWARNを残す。採用は取り消さない方針なので、
            #   「なぜこれを間取り図と判定したか」「怪しいかどうか」を後から辿れる形で置く。
            m = fp_meta or {}
            rows.append({"file": name, "room": "間取り図", "suumo_category": "madori",
                         "pdf_index": "", "treatment": "実物（生成AI非通過）",
                         "detect": f"{m.get('source', '?')} {m.get('w', 0)}x{m.get('h', 0)}"
                                   f" 配置{m.get('placed', 0):.0f}"
                                   f" 白{m.get('white', 0):.2f}"
                                   f" 黒{m.get('black', 0):.3f}"
                                   f" 彩{m.get('sat', 0):.3f}",
                         "warn": m.get("warn", "")})
    with (tmp / "_manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "room", "suumo_category",
                                          "pdf_index", "treatment", "text_subject",
                                          "detect", "warn"],
                           restval="")
        w.writeheader()
        w.writerows(rows)
    if dest.exists():
        if not overwrite:
            shutil.rmtree(tmp)
            raise FileExistsError(str(dest))
        # ★madori-v1 受入基準5：既存の間取り図より新しい方が小さいなら、上書きは劣化になる。
        #   実測で既存5物件（西長堀_505 / 難波大国町Tres_907 / エスリード_0201・0505 /
        #   フレンシアノイエ_308）の madori は 1099×1100 で、客付版・元付版どちらのPDFからも
        #   出ない寸法だった（＝手動DLか別経路で用意されたもの）。CLIで上書きすると300×300に落ちる。
        #   黙って下げないよう、上書き前に見つけて知らせる（--overwrite を指定した人の判断に返す）。
        _warn_madori_downgrade(dest, tmp, log)
        shutil.rmtree(dest)
    tmp.rename(dest)
    log(f"    → {dest.name}/ に{len(files)}枚（＋_manifest.csv）")
    txt = [(r["file"], r["text_subject"]) for r in rows if r.get("text_subject")]
    if txt:
        log(f"    ⚠ 文字が主題の画像 {len(txt)}件（高解像度化で日本語が化けるため入稿から外す候補）: "
            + ", ".join(f"{f}={t}" for f, t in txt))
        # ★外した場合の名寄せ点の変化を、外す判断をする場で見せる。
        #   実測でこの手の画像（給湯・浴室乾燥のリモコン）が、その室で唯一の
        #   『バス・シャワールーム』カテゴリを持っていることがある。外すと5点落ちる。
        #   点数を理由に残せという話ではなく、代替写真の追加が必要かを判断するための材料。
        all_names = [n for n, _ in files]
        keep = [n for n in all_names if n not in {f for f, _ in txt}]
        before, _b5, _b1 = score_hint(all_names)
        after, _a5, _a1 = score_hint(keep)
        if after < before:
            log(f"      → 全部外すと名寄せ見込みが {before}点 → {after}点"
                + ("（23点未満になる。代替写真が必要）" if after < 23 else ""))
    unknown = [r["file"] for r in rows if r["suumo_category"] == "999999"]
    if unknown:
        log(f"    ⚠ 部屋が特定できず『その他(999999)』になったファイル {len(unknown)}件: "
            + ", ".join(unknown)
            + "\n      → 入稿前に中身を目視すること（QRコード・地図・ロゴが混ざり得る）")
    return [n for n, _ in files]


# ★suumoreg-v1：定義は core へ移設（suumo_fields.py と共有する単一情報源）。
#   ここは既存の参照名を保つためのエイリアス。
_ASCII_TO_CATEGORY = core.SUUMO_ASCII_TO_CATEGORY
_CATEGORY_5PT = core.SUUMO_CATEGORY_5PT


def score_hint(names):
    """出力ファイル名から名寄せスコアの見込みを出す。

    ★式はSUUMOの画面に明記されている：間取り／建物外観／居室・リビング／キッチン／
      バス・シャワールーム＝1カテゴリ5点、それ以外＝1カテゴリ1点。
    ★『カテゴリ』単位で数える（枚数ではない）。room06 と room08 は別ファイルだが
      どちらも 999999 その他＝同一カテゴリなので合計1点。ここを枚数で数えると
      点数を過大に見せて「届いている」と誤判定する。
    ★これは『Phase2でカテゴリを正しく設定した場合の上限』。画像を入れてもカテゴリを
      設定しないと点は乗らない（8/12実測 11点→28点）。素材の当たり外れを入稿前に
      見るための指標で、登録後の実点数ではない。
    """
    cats = set()
    for n in names:
        base = n.split("_", 1)[1].rsplit(".", 1)[0] if "_" in n else n
        base = re.sub(r"_\d+$", "", base)            # 衝突サフィックス _2 を落とす
        cats.add(_ASCII_TO_CATEGORY.get(base, "999999"))
    hi = sorted(c for c in cats if c in _CATEGORY_5PT)
    lo = sorted(c for c in cats if c not in _CATEGORY_5PT)
    return len(hi) * 5 + len(lo), hi, lo


# ── メイン ────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="マイソクPDF群 → SUUMO入稿用画像フォルダを一括生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="処理種別: " + _TREATMENT_NOTE)
    ap.add_argument("--in", dest="in_dir", required=True, help="マイソクPDFのフォルダ")
    ap.add_argument("--out", dest="out_dir", required=True, help="出力先フォルダ")
    ap.add_argument("--dry-run", action="store_true",
                    help="何室・何枚・概算いくらかを出して終了（API未使用・課金なし）")
    ap.add_argument("--only", action="append", default=[],
                    help="部屋キーの部分一致で絞る（複数指定可）")
    ap.add_argument("--since-ts", default="",
                    help="DLタイムスタンプの接頭辞で絞る（例 20260812）")
    ap.add_argument("--limit", type=int, default=0, help="先頭N室だけ処理（動作確認用）")
    ap.add_argument("--treatment", default="高解像度化のみ",
                    help="処理種別（既定 高解像度化のみ。他は不可＝理由は --help 末尾）")
    ap.add_argument("--model", default=core.MODELS[0], choices=core.MODELS)
    ap.add_argument("--aspect", default="4:5", choices=core.ASPECT_RATIOS)
    ap.add_argument("--workers", type=int, default=4, help="1室内の並列生成数（既定4＝UIと同じ）")
    ap.add_argument("--overwrite", action="store_true",
                    help="既存の出力フォルダを作り直す（既定は既存をスキップ）")
    ap.add_argument("--no-text-check", action="store_true",
                    help="文字が主題の画像の判定（_manifest.csvのtext_subject列）を省く。"
                         "1室につきGeminiのテキスト呼び出しが1回減る")
    a = ap.parse_args(argv)

    in_dir, out_dir = Path(a.in_dir).expanduser(), Path(a.out_dir).expanduser()
    if not in_dir.is_dir():
        ap.error(f"--in が見つかりません: {in_dir}")
    if a.treatment not in _ALLOWED_TREATMENTS:
        ap.error(f"--treatment '{a.treatment}' は使えません（対応: "
                 f"{'/'.join(_ALLOWED_TREATMENTS)}）。\n理由: " + _TREATMENT_NOTE
                 + "\n  ステージング等が必要なら app.py のUIから1室ずつ行ってください"
                   "（事実ガードがUI側にあり、CLIからは同じガードを掛けられません）。")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"_batch_{stamp}.log"
    log_fh = log_path.open("w", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    targets, stat = resolve_targets(in_dir, a.since_ts, a.only, a.limit)
    log(f"入力: {in_dir}")
    log(f"出力: {out_dir}")
    log(f"部屋キー {stat['stems']}件（重複の古い版 {stat['dropped_old']}件は自動で除外）"
        + (f" / --since-ts {a.since_ts} で {len(targets)}件に絞り込み" if a.since_ts else ""))
    if stat["no_timestamp"]:
        log(f"⚠ タイムスタンプ無しで対象外にしたPDF {len(stat['no_timestamp'])}件: "
            + ", ".join(stat["no_timestamp"][:5]))
    if not targets:
        log("対象が0件です。--in / --since-ts / --only を見直してください。")
        log_fh.close()
        return 1

    # ── dry-run：APIを呼ばずに室数・枚数・概算コストを出す ──────────────
    if a.dry_run:
        rows, total, warns = [], 0, []
        for key, pdf in targets:
            try:
                photos, fp, meta = extract_sources(pdf)
            except Exception as e:  # noqa: BLE001
                log(f"  ✗ {key}: PDF読取失敗 {type(e).__name__}: {e}")
                rows.append((key, 0, "NO", "PDF読取失敗"))
                continue
            # 生成対象の見込み＝抽出写真から間取り図1枚を引いた数。
            # ★MAP/BLANK/OTHER の除外は分類（API）が必要なので dry-run では引けない。
            #   よってこれは「上限」＝実際の生成枚数はこれ以下になる（過小申告はしない）。
            n = max(0, len(photos) - (1 if fp is not None else 0))
            exists = (out_dir / key).is_dir()
            fpcol = f"{meta['w']}x{meta['h']}" if fp is not None else "なし"
            note = "既存（スキップ対象）" if exists and not a.overwrite else ""
            if meta.get("warn"):
                note = (note + " ⚠" + meta["warn"]).strip()
                warns.append((key, meta["warn"]))
            rows.append((key, n, fpcol, note))
            if not (exists and not a.overwrite):
                total += n
        usd, jpy = core.estimate_cost(total, a.model)
        n_fp = sum(1 for _k, _n, fp, _t in rows if fp != "なし" and fp != "NO")
        sizes = collections.Counter(fp for _k, _n, fp, _t in rows)
        log("")
        log(f"{'部屋キー':<44} {'生成枚数(上限)':>12} {'間取り図':>10}  備考")
        for key, n, fp, note in rows:
            log(f"{key:<44} {n:>12} {fp:>10}  {note}")
        log("")
        log(f"対象 {len(targets)}室 / 生成枚数 上限 {total}枚 / 処理={a.treatment} / model={a.model}")
        log(f"概算コスト ≈ ${usd:.2f}（約{jpy:,.0f}円・単価${core.PRICE_PER_IMAGE.get(a.model):.3f}/枚）")
        log("※分類（MAP/白紙/その他の除外）はAPIを使うためdry-runでは引いていない＝実際はこれ以下。")
        log("※動画化（fal）は呼ばないため追加課金なし。")
        # ★madori-v1：間取り図は構造判定＝LLM非依存なので、dry-runの結果が本番の結果と一致する。
        log(f"\nmadori: {n_fp}/{len(targets)}  検出サイズの内訳: "
            + " / ".join(f"{k}×{v}室" for k, v in sorted(sizes.items(), key=lambda t: -t[1])))
        if n_fp < len(targets):
            log(f"⚠ 間取り図が検出できない室 {len(targets) - n_fp}件（madori＝5点カテゴリなので"
                "欠けると名寄せ点が5点下がる）: "
                + ", ".join(k for k, _n, fp, _t in rows if fp in ("なし", "NO")))
        if warns:
            log(f"⚠ 間取り図らしくない疑いのある室 {len(warns)}件（採用はしている。中身を目視すること）:")
            for k, w in warns:
                log(f"    {k}  {w}")
        log(f"ログ: {log_path}")
        log_fh.close()
        return 0

    # ── 本番実行 ──────────────────────────────────────────────────
    try:
        # 環境変数 → .streamlit/secrets.toml（UIと同じ置き場所）の順で探す
        client = core.get_client(core.get_api_key() or api_key_from_secrets())
    except RuntimeError as e:
        log(f"✗ {e}")
        log_fh.close()
        return 2

    results = []
    t0 = time.time()
    for idx, (key, pdf) in enumerate(targets, 1):
        log(f"\n[{idx}/{len(targets)}] {key}")
        dest = out_dir / key
        if dest.is_dir() and not a.overwrite:
            log("    スキップ（出力フォルダが既に存在。作り直すなら --overwrite）")
            results.append({"key": key, "status": "SKIP", "files": 0, "score": "",
                            "cats": "", "madori": "-", "note": "既存"})
            continue
        try:
            photos, fp_struct, fp_meta = extract_sources(pdf)
        except Exception as e:  # noqa: BLE001
            log(f"    ✗ PDF読取失敗 {type(e).__name__}: {e}")
            results.append({"key": key, "status": "FAIL", "files": 0, "score": "",
                            "cats": "", "madori": "-", "note": f"PDF読取失敗 {type(e).__name__}"})
            continue
        if not photos:
            log("    ✗ 使える写真が0枚（min_px=250で抽出できず）")
            results.append({"key": key, "status": "FAIL", "files": 0, "score": "",
                            "cats": "", "madori": "-", "note": "写真0枚"})
            continue
        codes, cwarn = classify_with_retry(client, photos, log)
        if cwarn:
            log(f"    ⚠ 分類が不十分: {cwarn}（部屋名がその他に寄る＝ファイル名がroomNNになる）")
        # ★text-subject-v1：文字が主題の画像（給湯リモコン・注意書き・QRコード等）に印を付ける。
        #   「高解像度化のみ」でも画像内の日本語は別の文字に化けるため（実測）、入稿から外す候補を
        #   人が目視で選べるようにする。落とす判断はしない＝ここでは印を付けるだけ。
        #   ローカルの画素統計では測れないことを確認済み（core.detect_text_subject のdocstring）。
        text_subj = {}
        if not a.no_text_check:
            try:
                subs = core.detect_text_subject(client, photos)
                text_subj = {i: s for i, s in enumerate(subs) if s}
            except Exception as e:  # noqa: BLE001  握り潰さない・失敗しても本処理は続ける
                log(f"    ⚠ 文字主題の判定に失敗: {type(e).__name__}: {str(e)[:100]}"
                    "（_manifest.csv の text_subject 列が空になる）")
        # ★madori-v1：間取り図は構造判定で確定している（extract_sources で算出済み・LLM非依存）。
        #   構造判定で候補0件のときだけ、LLMのFLOORPLANタグにフォールバックする。
        floorplan = fp_struct
        if floorplan is None:
            floorplan = core.choose_floorplan(photos, codes)
            if floorplan is not None:
                fp_meta = dict(fp_meta, source="llm_tag")
        items, skipped = build_items(photos, codes, floorplan, a.treatment)
        log(f"    抽出{len(photos)}枚 → 生成{len(items)}枚"
            f"（除外{len(skipped)}枚: {', '.join(f'#{i}{c}' for i, c in skipped) or '-'}）"
            f" / 間取り図="
            + ("なし" if floorplan is None
               else f"{fp_meta['w']}x{fp_meta['h']}（{fp_meta.get('source', '?')}）"))
        if floorplan is None:
            log("    ⚠ 間取り図が検出できていない（madori＝5点カテゴリが欠ける）")
        elif fp_meta.get("warn"):
            log(f"    ⚠ 間取り図 {fp_meta['warn']}")
        ok, fails = generate_items(client, items, a.model, a.aspect, a.workers, log)
        if ok == 0:
            log("    ✗ 生成できた画像が0枚。この室はフォルダを作らない")
            results.append({"key": key, "status": "FAIL", "files": 0, "score": "",
                            "cats": "", "madori": "なし" if floorplan is None else "あり",
                            "note": f"生成全失敗({len(fails)}枚)"})
            continue
        try:
            names = write_room(out_dir, key, items, floorplan, a.overwrite, log,
                               fp_meta, text_subj)
        except FileExistsError as e:  # noqa: PERF203
            log(f"    ✗ 出力先が既に存在: {e}")
            results.append({"key": key, "status": "FAIL", "files": 0, "score": "",
                            "cats": "", "madori": "-", "note": "出力先が既存"})
            continue
        sc, cat5, others = score_hint(names)
        note = "" if not fails else f"生成失敗{len(fails)}枚"
        if cwarn:
            note = (note + " / 分類不十分").strip(" /")
        if floorplan is None:
            note = (note + " / 間取り図なし").strip(" /")
        elif fp_meta.get("warn"):
            note = (note + " / 間取り図WARN:" + fp_meta["warn"]).strip(" /")
        log(f"    名寄せ見込み {sc}点（5点カテゴリ {len(cat5)}={','.join(cat5) or '-'} / "
            f"1点 {len(others)}）※Phase2でカテゴリ設定した場合の上限")
        if sc < 23:
            log(f"    ⚠ 23点未満（{sc}点）。Phase2の照合基準に届かない見込み")
        results.append({"key": key, "status": "OK" if not fails else "PARTIAL",
                        "files": len(names), "score": sc,
                        "cats": ",".join(cat5),
                        "madori": ("なし" if floorplan is None else
                                   f"{fp_meta['w']}x{fp_meta['h']}"
                                   f"({fp_meta.get('source', '?')})"),
                        "note": note})

    # ── サマリ ────────────────────────────────────────────────────
    csv_path = out_dir / f"_batch_summary_{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "status", "files", "score", "cats",
                                          "madori", "note"])
        w.writeheader()
        w.writerows(results)
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_part = sum(1 for r in results if r["status"] == "PARTIAL")
    n_skip = sum(1 for r in results if r["status"] == "SKIP")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_img = sum(int(r["files"] or 0) for r in results)
    usd, jpy = core.estimate_cost(n_img, a.model)
    log("\n" + "=" * 72)
    log(f"完了 {time.time() - t0:.0f}秒  OK {n_ok} / 一部失敗 {n_part} / スキップ {n_skip}"
        f" / 失敗 {n_fail}  （計 {len(results)}室・{n_img}ファイル）")
    log(f"生成コスト目安 ≈ ${usd:.2f}（約{jpy:,.0f}円・出力ファイル数ベース／間取り図含む）")
    for r in results:
        if r["status"] in ("FAIL", "PARTIAL"):
            log(f"  {r['status']:<8}{r['key']}  {r['note']}")
    low = [r for r in results if r["status"] in ("OK", "PARTIAL")
           and isinstance(r["score"], int) and r["score"] < 23]
    if low:
        log(f"⚠ 名寄せ見込みが23点未満の室 {len(low)}件（入稿前に素材を確認）: "
            + ", ".join(r["key"] for r in low))
    log(f"サマリ: {csv_path}")
    log(f"ログ:   {log_path}")
    log_fh.close()
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
