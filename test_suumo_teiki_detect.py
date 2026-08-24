# -*- coding: utf-8 -*-
"""teiki-fix-v1: suumo_fields.detect_teiki() の回帰テスト。

実行: python3 test_suumo_teiki_detect.py   （pytest不要・APIキー不要・ネットワーク不要）

■なぜこのテストが必要か
以前は `TEIKI_SHAKUYA_FLG = "0"` の**無条件代入**で、マイソクを読む処理が1行も無かった。
その結果95室すべてが「普通借家」として入稿され、**定期借家6室が普通借家として掲載された**
（うち4室が2026-08-13から掲載中）。表示が実態と違う広告が出ていた状態で、
おとり広告と同じ系統の問題になる。

■このテストが守る性質は2つ
  ①「定期借家」を見落とさない（偽陰性＝掲載事故に直結する）
  ②**分からないときに "0" に倒さない**（今回の原因はこれ）。
     判定できないケースは (None, None, 理由, False) を返し、呼び出し側が block する。

■判定できたか (ok) と flg の関係
    ok=True  かつ flg="1" → 定期借家。nen に年数
    ok=True  かつ flg="0" → 普通借家（「定期借家」の記載が本文に1度も無い）
    ok=False              → **人に返す**。flg は None（"0" ではない）
"""
from __future__ import annotations

import sys

from suumo_fields import detect_teiki


FAIL: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  NG   {name}\n         got  = {got!r}\n         want = {want!r}")
        FAIL.append(name)


def flg_of(text: str):
    """(flg, nen, ok) だけ取り出す（理由は文言変更で壊れるので合否に使わない）。"""
    flg, nen, _reason, ok = detect_teiki(text)
    return (flg, nen, ok)


# ── 正常系 ────────────────────────────────────────────────────────
def test_normal() -> None:
    print("[正常系]")
    # 実測パターン①: 項目欄（6室すべてこの書き方。2026-08-17 実測）
    check("項目欄『契約期間 定期借家 2年間』",
          flg_of("契約期間 定期借家 2年間 更新料 新賃料1ヶ月"), ("1", "2", True))
    # 実測パターン②: その他条件ブロック
    check("本文『【定期借家】2年※法人契約に限り、普通借家契約への変更相談可』",
          flg_of("鍵交換代2.75万円【定期借家】2年※法人契約に限り、"
                 "普通借家契約への変更相談可 【解約予告】2ヶ月前"), ("1", "2", True))
    # ★①と②が同居する（実データは常にこれ）。年数が一致していれば当然1つに決まる
    check("項目欄と本文の両方にある",
          flg_of("契約期間 定期借家 2年間 ... 【定期借家】2年※法人契約に限り、"
                 "普通借家契約への変更相談可"), ("1", "2", True))
    # 普通借家の明記あり（188件中66件）
    check("『契約期間 普通借家 2年間』", flg_of("契約期間 普通借家 2年間"), ("0", None, True))
    # ★契約形態の明記が無い（188件中108件＝最多）。借地借家法38条により、
    #   定期借家は書面での明示が要る。明示が無い＝普通借家と読んでよい。
    check("『契約期間 2年間』（形態の明記なし）", flg_of("契約期間 2年間"), ("0", None, True))


# ── 境界値 ────────────────────────────────────────────────────────
def test_boundary() -> None:
    print("[境界値]")
    # 行折り返しで語中に空白が入る（PDF全文はこれが起きる）
    check("『定期借家 】 3 年』（折り返しの空白）",
          flg_of("【定期借家 】 3 年"), ("1", "3", True))
    check("改行が挟まる", flg_of("契約期間 定期借家\n2年間"), ("1", "2", True))
    # 年数の桁
    check("1年", flg_of("定期借家 1年間"), ("1", "1", True))
    check("10年", flg_of("定期借家 10年間"), ("1", "10", True))
    check("ゼロ埋め『02年』は 2 に正規化", flg_of("定期借家 02年間"), ("1", "2", True))
    # ★『2ヵ年』表記
    check("『定期借家 2ヵ年』", flg_of("定期借家 2ヵ年"), ("1", "2", True))
    # ★『6ヶ月』の『6ヶ』を年として拾わないこと（年は『年』を必須にしている）
    check("『定期借家 6ヶ月』は月単位＝人に返す",
          flg_of("契約期間 定期借家 6ヶ月"), (None, None, False))
    # 「普通借家契約への変更相談可」という語が本文にあっても定期借家判定は変わらない
    check("『普通借家』の語が本文にあっても定期借家が勝つ",
          flg_of("【定期借家】2年※普通借家契約への変更相談可"), ("1", "2", True))


# ── 異常系（★"0" に倒れないことを見る）──────────────────────────
def test_abnormal() -> None:
    print("[異常系]")
    check("本文が空", flg_of(""), (None, None, False))
    check("本文が None", flg_of(None), (None, None, False))
    # 語はあるが年数が読めない＝既知の書式外
    check("『定期借家』だけで年数なし",
          flg_of("契約期間 定期借家"), (None, None, False))
    check("年数が0", flg_of("定期借家 0年間"), (None, None, False))
    # 否定表現。★自動で "0" に倒さず人に返す
    for neg in ("定期借家不可", "定期借家ではない", "定期借家では無い", "定期借家なし",
                "定期借家は除く"):
        check(f"否定表現『{neg}』は人に返す", flg_of(f"備考 {neg} 2年間"),
              (None, None, False))
    # ★隣の項目の否定語を拾わないこと（2026-08-17 セルフレビューで見つけた欠陥）。
    #   その他条件は【】区切りで項目が並ぶ。【】を跨いだ「不可」は別項目のもの。
    check("『【定期借家】2年【ペット】不可』は定期借家（ペットの不可を拾わない）",
          flg_of("その他【定期借家】2年【ペット】不可"), ("1", "2", True))
    check("『【定期借家】2年【楽器】不可【短期解約】…』",
          flg_of("【定期借家】2年【楽器】不可【短期解約違約金】1ヶ月"), ("1", "2", True))
    # ただし【】を跨がない否定は今までどおり人に返す
    check("『【定期借家 不可】』は人に返す",
          flg_of("その他【定期借家 不可】ペット可"), (None, None, False))
    # ★ここが再発防止の本体。判定不能のとき flg が "0" になっていないこと
    for text in ("", "契約期間 定期借家", "定期借家 0年間", "契約期間 定期借家 6ヶ月"):
        flg, _nen, _r, ok = detect_teiki(text)
        check(f"判定不能で flg が None（{text[:14]!r}）", (flg, ok), (None, False))


# ── 実データ回帰（マイソク188件）──────────────────────────────────
def test_real_pdfs() -> None:
    """★正解は『契約期間』欄に「定期借家」が入っているか。PDF全文の判定と一致すること。

    detect_teiki は**全文**を見る（欄が取れないマイソクでも判定できるようにするため）。
    全文判定は定型文に「定期借家」が出ると偽陽性になりうるので、
    **構造化された欄を正解として突き合わせる**。
    """
    import glob
    import os
    from pathlib import Path

    print("[実データ回帰]")
    base = os.path.expanduser(
        "~/Downloads/エンクス/03_物件提案くん/SUUMO入稿_75枠_20260806/01_マイソク")
    pdfs = sorted(glob.glob(base + "/*.pdf"))
    if not pdfs:
        print(f"  skip マイソクが無いので実データ回帰は省略（{base}）")
        return

    import core
    from suumo_fields import _right_of, _word_rows

    mism, blocked, hit = [], [], 0
    for p in pdfs:
        b = Path(p).read_bytes()
        flg, nen, reason, ok = detect_teiki(core.pdf_full_text(b))
        field = " ".join(_right_of(_word_rows(b), "契約期間") or [])
        want = "1" if "定期借家" in field else "0"
        if not ok:
            blocked.append((os.path.basename(p), reason))
            continue
        if flg != want:
            mism.append((os.path.basename(p), flg, want, field))
        if flg == "1":
            hit += 1
            if nen != "2":
                mism.append((os.path.basename(p), f"nen={nen}", "nen=2", field))

    print(f"  マイソク {len(pdfs)}件 / 定期借家 {hit}件 / 不一致 {len(mism)}件 "
          f"/ 判定不能 {len(blocked)}件")
    check("『契約期間』欄と全文判定が全件一致", len(mism), 0)
    # ★判定不能が出ること自体は正しい挙動（人に返す）。ただし件数は目に見えるようにする
    for n, r in blocked:
        print(f"       判定不能: {n} — {r}")
    check("定期借家は6件（2026-08-17 実測）", hit, 6)


# ── 番地パーサー（★登録失敗の直接原因だった）──────────────────────
def test_parse_banchi() -> None:
    """★番地が空だと交通モーダルの住所検索が0件になり、登録できない。

    2026-08-22 に18室中3室が Timeout で失敗し、うち2室は28点以上だった。
    """
    from suumo_fields import parse_banchi
    print("[番地パーサー]")
    # 実データ224件の2形（218件がこのどちらか）
    check("『N-N』形", parse_banchi("大阪府大阪市西区江之子島１丁目6-1"), "6-1")
    check("『N番N号』形", parse_banchi("大阪府大阪市中央区松屋町住吉5番23号"), "5番23号")
    check("『N番N』（号なし）", parse_banchi("大阪市北区サンプル13番15"), "13番15")
    check("丁目が無い町名でも取れる", parse_banchi("大阪府大阪市中央区松屋町10-4"), "10-4")
    check("枝番が続く", parse_banchi("大阪市西区サンプル1丁目2-3-4"), "2-3-4")

    # ★今回直した1件
    check("★末尾に『 N号』が続く（森ノ宮中央のケース）",
          parse_banchi("大阪府大阪市中央区森ノ宮中央１丁目3-16 16号"), "3-16")

    print("[番地が無い住所は作らない]")
    # ★元データに番地が無いものは None。**推測で埋めない**
    for addr in ("大阪府大阪市中央区十二軒町",
                 "大阪府大阪市中央区玉造２丁目丁目",
                 "大阪府大阪市浪速区敷津東１丁目丁目"):
        check(f"『{addr[-8:]}』は None", parse_banchi(addr), None)
    check("空文字", parse_banchi(""), None)
    check("None", parse_banchi(None), None)

    print("[誤検出しないこと]")
    # ★末尾アンカーを外す案を採らなかった理由。手前の数字を拾わせない
    check("★『6-1 2-3号室』で 2-3 を拾わない（一致せず None）",
          parse_banchi("大阪市西区サンプル1丁目6-1 2-3号室"), None)
    check("★ビル名の階を番地にしない",
          parse_banchi("大阪市西区サンプルビル3階"), None)
    check("丁目だけの数字を拾わない", parse_banchi("大阪市西区サンプル1丁目"), None)


def main() -> int:
    test_parse_banchi()
    test_normal()
    test_boundary()
    test_abnormal()
    test_real_pdfs()
    print()
    if FAIL:
        print(f"NG {len(FAIL)}件: {FAIL}")
        return 1
    print("すべて ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
