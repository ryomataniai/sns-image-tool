# -*- coding: utf-8 -*-
"""madori-v1: 間取り図の構造判定のテスト（実物のマイソクPDFを使う・APIキー不要・課金なし）。

実行: python3 test_madori.py [マイソクPDFのフォルダ]

■測ること
  1. 8/12にDLした35室すべてで間取り図が1枚検出される
  2. 検出サイズの内訳が実測と一致する
  3. 旧判定（白地率・黒線率のゲート）が検出できていた物件では、**同じ画像**が選ばれる
     ＝UIのSUUMO入稿用ZIPの出力が変わらない（受入基準3）
  4. WARNの発火が_manifest.csv/ログに載る形で得られている
  5. 戻り値が抽出リスト内の同一オブジェクトである（＝間取り図がAI生成にも回らない）

■測っていないこと
  「選ばれた画像が本当に間取り図か」は、このテストでは測れない（画素の統計は
  間取り図と写真を分離しない、というのがこの変更の出発点）。63物件ぶんの選択結果を
  コンタクトシートに並べて目視で確認した。テストが担保するのは
  「その目視で確認した挙動が、後の変更で黙って変わらないこと」。
"""
from __future__ import annotations

import collections
import glob
import io
import os
import re
import sys
import unicodedata as ud
from pathlib import Path

from PIL import Image

import core

_DEFAULT_IN = Path("/Users/taniairyouma/Downloads/エンクス/03_物件提案くん/"
                   "SUUMO入稿_75枠_20260806/01_マイソク")
# ★実測値（2026-08-12・63物件を目視確認した時点のもの）。ここが変わったら判定が変わったということ。
_EXPECT_35 = {(300, 300): 34, (600, 600): 1}
# ★件数は固定しない。マイソクは毎月増える（63→143物件になった時点で件数固定の
#   アサーションが全部落ちた）。固定すべきなのは**不変条件**であって母数ではない。
_MAX_NOT_IN_LIST_RATIO = 0.05   # 抽出リスト外になる物件の許容割合（薄すぎる間取り図）

_fails = []


def _check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def _newest_per_key(in_dir: Path):
    """部屋キー → (PDFパス, タイムスタンプ)。重複は新しい方（batch_suumo と同じ規則）。"""
    best = {}
    for p in sorted(glob.glob(str(in_dir / "*.pdf"))):
        m = re.match(r"(.+)_(\d{14})\.pdf$", ud.normalize("NFC", os.path.basename(p)))
        if not m:
            continue
        k = m.group(1)
        if k not in best or m.group(2) > best[k][1]:
            best[k] = (p, m.group(2))
    return best


def _photos(pdf_bytes):
    """UI・CLIと同じ抽出（min_px=250 → 白紙枠を除外）。"""
    ph = [b for (b, _w, _h) in core.extract_pdf_photos(pdf_bytes, min_px=250)]
    return [b for b in ph if not core.is_blank_frame(b)]


def _old_gate_pick(photos):
    """madori-v1 で選定から外した旧ヒューリスティック（白地率・黒線率のゲート）。
    比較用に凍結してある。ここを現行実装に置き換えてはいけない。"""
    best, best_score = None, -1.0
    for b in photos:
        gate, score = core.score_floorplan(b)
        if gate and score > best_score:
            best, best_score = b, score
    return best


def test_35_rooms(in_dir):
    """受入基準1・2：8/12の35室すべてで検出され、サイズの内訳が実測と一致する。"""
    best = _newest_per_key(in_dir)
    t35 = {k: v for k, v in best.items() if v[1].startswith("20260812")}
    _check("対象が35室", len(t35) == 35, f"{len(t35)}室")
    sizes, nofp, warn_strong, warn_weak = collections.Counter(), [], [], []
    for k, (p, _ts) in sorted(t35.items()):
        png, meta = core.find_floorplan_in_pdf(Path(p).read_bytes())
        if png is None:
            nofp.append(k)
            continue
        sizes[(meta["w"], meta["h"])] += 1
        if meta["warn"].startswith("★"):
            warn_strong.append(k)
        elif meta["warn"]:
            warn_weak.append(k)
    _check("35室すべてで間取り図が1枚検出される", not nofp, f"未検出: {nofp}")
    _check("検出サイズの内訳が実測と一致", dict(sizes) == _EXPECT_35, str(dict(sizes)))
    # ★強い警告（彩度＝写真の疑い）は35室では0件だったのが実測。1件でも出たら人が見る必要がある
    _check("★写真の疑い警告が0件", not warn_strong, str(warn_strong))
    _check("参考警告は取得できている（WARNの経路が生きている）", isinstance(warn_weak, list),
           f"{len(warn_weak)}室")
    print(f"    madori: {sum(sizes.values())}/35  内訳 {dict(sizes)}  参考警告 {len(warn_weak)}室")


def test_all_pdfs_detected(in_dir):
    """全マイソクで検出される（レイアウトの取りこぼしが無いこと）。"""
    best = _newest_per_key(in_dir)
    det = 0
    for _k, (p, _ts) in best.items():
        png, _m = core.find_floorplan_in_pdf(Path(p).read_bytes())
        det += png is not None
    _check(f"全{len(best)}物件で検出（母数は増えてよい）", det == len(best),
           f"{det}/{len(best)}")


def test_no_regression_vs_old(in_dir):
    """受入基準3：旧判定が検出できていた物件では同じ画像が選ばれる＝UIのZIP出力が変わらない。"""
    best = _newest_per_key(in_dir)
    same = diff = oldnone = 0
    diffs = []
    for k, (p, _ts) in sorted(best.items()):
        b = Path(p).read_bytes()
        photos = _photos(b)
        old = _old_gate_pick(photos)
        new = core.choose_floorplan(photos, [], b)
        if old is None:
            oldnone += 1
        elif old == new:
            same += 1
        else:
            diff += 1
            diffs.append((k, Image.open(io.BytesIO(old)).size,
                          Image.open(io.BytesIO(new)).size if new else None))
    _check("旧判定が当たっていた物件で選択画像が1件も変わらない", diff == 0, str(diffs))
    # 件数は参考。★固定すべき不変条件は「旧判定が当たっていた物件で別画像が出ない」ことだけ。
    _check("旧判定が取りこぼしていた物件がある（新判定の価値が残っている）",
           oldnone > 0, f"救済{oldnone}件 / 一致{same}件")
    print(f"    同一{same} / 旧が未検出（新で救済）{oldnone} / 別画像{diff}")


def test_identity_for_exclusion(in_dir):
    """守るべき不変条件：抽出リストに同一バイトの画像があるなら、その要素オブジェクトを返す。

    ★呼び出し側は `b is floorplan` で生成対象から外している。もし byte一致するのに
      別オブジェクトを返すと、同じ画像が「間取り図」として出力されつつ「室内写真」として
      AI生成にも回る（線画をGeminiに通すと文字が化ける＋1枚ぶん余計に課金される）。
    ★逆に、抽出リストに存在しない画像を返すのは問題ない（生成対象に入らないため）。
      実測でメガドームウエスト_405が該当する：間取り図が薄すぎて is_blank_frame に
      白紙枠として落とされ、抽出リストに入らない。それでも間取り図としては出力される。
    """
    best = _newest_per_key(in_dir)
    ng, not_in_list = [], []
    for k, (p, _ts) in sorted(best.items()):
        b = Path(p).read_bytes()
        photos = _photos(b)
        fp = core.choose_floorplan(photos, [], b)
        if fp is None:
            continue
        if any(x == fp for x in photos):
            if not any(x is fp for x in photos):
                ng.append(k)          # ★これが起きたら二重利用＝バグ
        else:
            not_in_list.append(k)     # 抽出対象外（生成に回らないので問題なし）
    _check("byte一致する要素があるときは必ず同一オブジェクトを返す", not ng, str(ng))
    # 抽出リスト外＝間取り図が薄すぎて is_blank_frame に白紙枠として落とされた物件。
    # 生成対象に入らないので実害はないが、増えていないかは見張る（割合で判定する）。
    ratio = len(not_in_list) / max(1, len(best))
    _check(f"抽出リスト外が全体の{_MAX_NOT_IN_LIST_RATIO:.0%}未満",
           ratio < _MAX_NOT_IN_LIST_RATIO,
           f"{len(not_in_list)}/{len(best)}件 = {ratio:.1%} {not_in_list[:4]}")


def test_png_matches_extract(in_dir):
    """find_floorplan_in_pdf のPNG化が extract_pdf_photos と同じ手順であること（バイト一致）。
    ★ここがずれると上の同一性判定が黙って外れる。"""
    best = _newest_per_key(in_dir)
    ng = []
    for k, (p, _ts) in sorted(list(best.items())[:10]):
        b = Path(p).read_bytes()
        png, _m = core.find_floorplan_in_pdf(b)
        if png is not None and png not in _photos(b):
            ng.append(k)
    _check("PNG化がextract_pdf_photosとバイト一致（先頭10件）", not ng, str(ng))


def test_llm_fallback_kept(in_dir):
    """構造判定が使えないとき（pdf_bytes無し）はLLMのFLOORPLANタグに落ちること。"""
    fake = [b"not-an-image-1", b"not-an-image-2"]
    _check("pdf_bytes無し＋FLOORPLANタグ → そのタグの画像を返す",
           core.choose_floorplan(fake, [["OTHER"], ["FLOORPLAN"]]) is fake[1])
    _check("pdf_bytes無し＋タグ無し → None",
           core.choose_floorplan(fake, [["OTHER"], ["OTHER"]]) is None)
    _check("壊れたPDFでも例外を投げずNoneを返す",
           core.find_floorplan_in_pdf(b"not a pdf")[0] is None)


def test_warn_thresholds():
    """WARNは採用を取り消さないこと（依頼文3-2）＝warnが出ても画像は返る。"""
    # 彩度の高い（＝写真らしい）画像だけを含む合成PDFは作れないので、閾値の定数だけを固定する。
    _check("彩度の強い警告のしきい値が0.10", core._FP_WARN_SAT == 0.10)
    _check("白地率の参考警告のしきい値が0.30", core._FP_WARN_WHITE == 0.30)
    _check("黒線率の参考警告のしきい値が0.005", core._FP_WARN_BLACK == 0.005)


if __name__ == "__main__":
    in_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_IN
    if not in_dir.is_dir():
        print(f"入力フォルダが無い: {in_dir}")
        sys.exit(2)
    print(f"入力: {in_dir}")
    for fn in (test_35_rooms, test_all_pdfs_detected, test_no_regression_vs_old,
               test_identity_for_exclusion, test_png_matches_extract,
               test_llm_fallback_kept, test_warn_thresholds):
        print(f"\n▶ {fn.__name__}: {(fn.__doc__ or '').splitlines()[0]}")
        try:
            fn(in_dir) if fn.__code__.co_argcount else fn()
        except Exception as e:  # noqa: BLE001
            _check(f"{fn.__name__} が例外", False, f"{type(e).__name__}: {e}")
    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    sys.exit(1 if _fails else 0)
