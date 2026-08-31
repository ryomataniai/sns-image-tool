#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_note_date の単体テスト（★合成データのみ・実名ゼロ・外部依存ゼロ・API を呼ばない）。

★実物件名を書かない。このリポジトリには pre-commit の name-guard があり、
  実名が入るとコミットが止まる。止まるのが正しいので、こちら側を合わせる。
★実データでの確認は check_note_date_real.py（別ファイル・CI に入れない・落とさない）。
"""
import re
import sys

sys.path.insert(0, ".")
import core

# ★実在の帳票の「形」だけを写した合成データ。物件名・住所・番地は入れない
FT_OUTPUT_DAY = "出力日:2026/08/20 16:45:48 / 次回更新予定日:2026/09/03 ※掲載情報は随時更新"
FT_MOVE_OUT = "現況/入居時期\n退去予定 / 2026年10月28日\n賃料\n74,000 円\n"
FT_VACANT = "現況/入居時期\n空室 / 2026年8月29日\n賃料\n74,000 円\n"
FT_BUILT = "築年\n2007年02月\n"
GEN = "2026-08-31"

_LBL = r"(?:情報日付|情報登録日|情報公開日|情報更新日|更新日|作成日|掲載日|公開日|募集日|登録日|出力日)"
_RULE1 = _LBL + r"[^\d]{0,6}(\d{4})[年/\-.](\d{1,2})"

ng = 0


def t(label, got, want):
    global ng
    ok = got == want
    if not ok:
        ng += 1
    print(f"  {'◯' if ok else '✗'} {label:<50} {got}" + ("" if ok else f"  ★期待 {want}"))


def rule_of(ft):
    """★どの規則で返ったかを返す。値が合っていても②③なら偶然なので、合否に使う。"""
    return "①" if re.search(_RULE1, ft) else "②か③"


print("■ ★否定した仮説（2026-08-26 否定 → 08-28 再生産 → 08-31 再否定）")
# 「次回更新予定日」に「更新日」という連続部分文字列は無い（更新 の次は「予」）。
# ★否定先読みは不要。足そうとしたら、このテストを読むこと。
t("_LBL は「次回更新予定日:2026/09/03」を拾わない",
  bool(re.search(_RULE1, "次回更新予定日:2026/09/03")), False)
t("_LBL は「出力日:2026/08/20」を拾う", bool(re.search(_RULE1, FT_OUTPUT_DAY)), True)

print("\n■ ア 出力日を規則①で拾う")
t("出力日あり → 2026年8月", core.data_note_date({"full_text": FT_OUTPUT_DAY}, GEN), "2026年8月")
t("★規則①で返っていること", rule_of(FT_OUTPUT_DAY), "①")

print("\n■ ウ 入居時期の日付を拾わない")
t("退去予定 2026年10月28日 → 生成日に落ちる",
  core.data_note_date({"full_text": FT_MOVE_OUT}, GEN), "2026年8月")
# ★8月が返るだけでは合格にしない。②で返ったら偶然の正解（0908 がその形だった）
t("空室 2026年8月29日 → ★②で返ってはいけない", rule_of(FT_VACANT), "②か③")
t("空室 2026年8月29日 → 生成日に落ちる",
  core.data_note_date({"full_text": FT_VACANT}, GEN), "2026年8月")

print("\n■ エ 未来ガード（年月比較・翌月以降を弾く／同月は通す）")
t("2026年9月 は弾く", core.data_note_date({"full_text": "情報は 2026年9月 に更新"}, GEN), "2026年8月")
t("2026年8月 は通す", core.data_note_date({"full_text": "更新 2026年8月 実施"}, GEN), "2026年8月")

print("\n■ オ 規則③の整形 ／ 回帰")
t('gen="2026-08-31" → 2026年8月（生の文字列を返さない）',
  core.data_note_date({"full_text": ""}, GEN), "2026年8月")
t("築年 2007年02月 を拾わない",
  core.data_note_date({"full_text": FT_BUILT, "built": "2007年02月"}, GEN), "2026年8月")

print("\n■ ★生成日が9月でも、出力日があれば 2026年8月")
t("gen=2026-09-20 / 出力日 8/20", core.data_note_date({"full_text": FT_OUTPUT_DAY}, "2026-09-20"), "2026年8月")

print(f"\n{'◯ 全PASS' if ng == 0 else f'✗ {ng}件 FAIL'}")
sys.exit(1 if ng else 0)
