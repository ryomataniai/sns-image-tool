# -*- coding: utf-8 -*-
"""teiki-fix-v1: suumo_teiki.verify_fields() の回帰テスト。

実行: python3 test_suumo_teiki_verify.py   （pytest不要・ブラウザ不要・ネットワーク不要）

■なぜこのテストが必要か
--verify は「もう直っているか」を人に代わって断言する口。**ここが甘いと、
直っていない室を『直った』と報告する**。定期借家4室が普通借家として掲載されていた
今回の事故は、まさに「確認したつもり」で起きている。

■特に守る性質
  ① 是正前(before)が無いときは**3項目しか見ていない**ことが結果に出ること
     （before があるときと無いときで、同じ「OK」を返してはいけない箇所を分ける）
  ② **自分自身と差分を取ると必ずOKになる**。これは照合ではない。
     2026-08-18 に --verify を足したとき、out_dir へ before.json を書く処理が
     読み取りより先にあり、是正後の値で before を上書きしてから自分と比べていた。
     順序を直したうえで、この「常にOK」を検出できる形をテストに残す。
  ③ getsu は before があるときだけ見る（空であるべきと決めつけない）
"""
from __future__ import annotations

import sys

from suumo_teiki import FLD_FLG, FLD_GETSU, FLD_KBN, FLD_NEN, verify_fields

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  NG   {name}\n         got  = {got!r}\n         want = {want!r}")
        FAIL.append(name)


def fields(flg="1", nen="2", kbn="1", getsu="", **extra) -> dict:
    """物件情報更新の全項目のうち、判定に効くものだけを持つ最小の辞書。"""
    d = {FLD_FLG: flg, FLD_NEN: nen, FLD_KBN: kbn, FLD_GETSU: getsu,
         # ★ダミー。このリポは Public なので実在の棟名・号室は書かない
         "${bukkenInputForm.bukkenNm}": "サンプルレジデンス",
         "${bukkenInputForm.heyaNo}": "1101",
         "${bukkenInputForm.chinryo1}": "8"}
    d.update(extra)
    return d


# ── 正常系 ────────────────────────────────────────────────────────
def test_normal() -> None:
    print("[正常系]")
    before = fields(flg="0", nen="", kbn="0")
    after = fields(flg="1", nen="2", kbn="1")
    ng, intended, unexpected = verify_fields(after, "2", before)
    check("是正済み → NGなし", ng, [])
    check("意図した変化は3項目(flg/nen/kbn)", sorted(k for k, _b, _a in intended),
          sorted([FLD_FLG, FLD_NEN, FLD_KBN]))
    check("意図しない変化なし", unexpected, [])

    # kbn が元から 1 の室（変化は2項目になる）。件数固定で判定していないこと
    before2 = fields(flg="0", nen="", kbn="1")
    ng2, it2, un2 = verify_fields(fields(), "2", before2)
    check("kbn が元から1でもOK", (ng2, un2), ([], []))
    check("その場合の意図した変化は2項目", len(it2), 2)


# ── 未是正の検出 ──────────────────────────────────────────────────
def test_not_fixed() -> None:
    print("[未是正の検出]")
    b = fields(flg="0", nen="", kbn="0")
    ng, _i, _u = verify_fields(fields(flg="0", nen="", kbn="0"), "2", b)
    check("まだ普通借家(flg=0) → NG", len(ng) >= 1, True)
    check("  flg の理由が出る", any(FLD_FLG in x for x in ng), True)

    ng2, _i, _u = verify_fields(fields(flg="1", nen="3", kbn="1"), "2", b)
    check("年数が違う(3年 vs 期待2年) → NG", any(FLD_NEN in x for x in ng2), True)

    ng3, _i, _u = verify_fields(fields(flg="1", nen="", kbn="1"), "2", b)
    check("年が空のまま保存された → NG", any(FLD_NEN in x for x in ng3), True)

    ng4, _i, _u = verify_fields(fields(flg="1", nen="2", kbn="0"), "2", b)
    check("kbn が0のまま → NG", any(FLD_KBN in x for x in ng4), True)


# ── 副作用の検出 ──────────────────────────────────────────────────
def test_side_effects() -> None:
    print("[副作用の検出]")
    before = fields(flg="0", nen="", kbn="0")
    # 意図した3項目に加えて賃料が変わっている＝全項目再送信で壊れたケース
    broken = fields(flg="1", nen="2", kbn="1", **{"${bukkenInputForm.chinryo1}": "9"})
    ng, _i, un = verify_fields(broken, "2", before)
    check("賃料が変わっていたら NG", any("意図しない変化" in x for x in ng), True)
    check("  意図しない変化として拾う", [k for k, _b, _a in un], ["${bukkenInputForm.chinryo1}"])

    # 項目が消えた（キーの欠落）も壊れ方の一つ
    lost = fields(flg="1", nen="2", kbn="1")
    del lost["${bukkenInputForm.chinryo1}"]
    ng2, _i, un2 = verify_fields(lost, "2", before)
    check("項目が消えたら NG", any("意図しない変化" in x for x in ng2), True)

    # getsu は before があるときだけ見る。変わっていたら NG
    ng3, _i, _u = verify_fields(fields(flg="1", nen="2", kbn="1", getsu="6"), "2", before)
    check("getsu が変わったら NG", any(FLD_GETSU in x for x in ng3), True)


# ── ★before が無いとき ────────────────────────────────────────────
def test_without_before() -> None:
    print("[before が無いとき]")
    ng, intended, unexpected = verify_fields(fields(), "2", None)
    check("3項目が正しければ NG なし", ng, [])
    # ★ここが肝。before が無いと差分は空になる。呼び出し側はこれを
    #   「副作用なし」と読んではいけない（「未確認」であって「無い」ではない）
    check("★差分は空になる（＝全項目は未確認）", (intended, unexpected), ([], []))
    ng2, _i, _u = verify_fields(fields(flg="0"), "2", None)
    check("before が無くても未是正は検出できる", any(FLD_FLG in x for x in ng2), True)
    # getsu は before が無いなら見ない（空であるべきと決めつけない）
    ng3, _i, _u = verify_fields(fields(getsu="6"), "2", None)
    check("before が無いとき getsu では落とさない", ng3, [])


# ── ★自分自身と比べると必ずOKになる（照合が壊れる形）────────────────
def test_self_comparison_is_meaningless() -> None:
    """★2026-08-18 に踏みかけた形をテストに残す。

    --verify が out_dir へ <code>_before.json を**書いてから読む**順序になっていると、
    是正後の値が「是正前」として保存され、自分自身と差分を取ることになる。
    差分は必ず空、意図しない変化も必ず0件で、**どんなに壊れていてもOKになる**。
    verify_fields 単体では防げない（呼び出し側の順序の問題）ので、
    「自分と比べたら常に通ってしまう」ことを明示して、順序を変えたら思い出せるようにする。
    """
    print("[自分自身との比較は照合にならない]")
    broken = fields(flg="1", nen="2", kbn="1", **{"${bukkenInputForm.chinryo1}": "9999"})
    ng, intended, unexpected = verify_fields(broken, "2", broken)
    check("★賃料が壊れていても自分と比べれば NG ゼロ", ng, [])
    check("★意図した変化もゼロ（＝何も検証していない）", (intended, unexpected), ([], []))
    # 正しい before と比べれば同じデータが NG になる
    ng2, _i, _u = verify_fields(broken, "2", fields(flg="0", nen="", kbn="0"))
    check("  正しい before と比べれば NG になる", len(ng2) >= 1, True)


def main() -> int:
    test_normal()
    test_not_fixed()
    test_side_effects()
    test_without_before()
    test_self_comparison_is_meaningless()
    print()
    if FAIL:
        print(f"NG {len(FAIL)}件: {FAIL}")
        return 1
    print("すべて ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
