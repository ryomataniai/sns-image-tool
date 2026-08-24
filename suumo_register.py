#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1 U3: SUUMO新規物件登録フォームを埋める（Playwright）。

    # ① 初回だけ：ブラウザを開いて谷合さんがログインする（パスワードは人が入れる）
    python3 suumo_register.py --login

    # ② 1室を埋める（交通入力の直前まで自動・送信はしない）
    python3 suumo_register.py --fill "06_登録データ/サンプルレジデンスA_903.json"

    # ③ モックに対するオフライン検証（SUUMOに触らない）
    python3 suumo_register.py --fill <json> --url file://.../tests/mock_suumo/index.html --headless

■設計の要点
- **送信しない。** 既定では「確認画面へ」を押さない。押すのは人（U3の受入基準は谷合さんの目視）。
- **人との受け渡しはセンチネルファイル。** 交通入力は「らくらく交通入力」を人が押す必要があるが、
  このスクリプトは対話入力（Enter待ち）を使わない：バックグラウンド実行でも動くように、
  合図は `--sentinel` のファイルが作られたことで受ける。
- **サムネイル表示を待つ。** 実機に「サムネイル表示完了前に確認画面へ進むと画像が登録されない
  場合があります」と明記されている。サムネイル＝`up_del_<slot>` の可視化なので、それを待機条件に
  使う（8/12に手作業でこの待機を怠って画像が入らなかった実績がある）。
- **埋めたら読み戻して照合する。** name属性が1文字違うと getElementsByName が空を返すだけで
  エラーにならない＝黙って空のまま送信される。埋値を読み戻して一致を確認し、違えば止める。
- **画像投入前に既存画像0枚を検証する。** コピー登録では前室の画像が残る（8/11に別物件の写真で
  登録しかけた事故がある）。0枚でなければ投入せず止める。
- ダイアログ（confirm）を待ち受ける。実機の deleteGazo はソースが読めず confirm の有無が不明。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SUUMO_URL = "https://www.fn.forrent.jp/fn/"
# ブラウザのプロファイル置き場（ログイン状態をここに保つ＝毎回ログインしない）
PROFILE_DIR = Path.home() / ".suumo_playwright_profile"

# JSONのフォームキー → 実フォームのname。
# ★実測（2026-08-13）：ほとんどが ${bukkenInputForm.X}。素の名前は郵便番号だけ。
#   依頼文§3-3が挙げていた素の名前（bukkenNm 等）は実機に存在しない。
BARE_NAMES = {"yubinNo1", "yubinNo2"}


def sel_name(key: str) -> str:
    """フォームキー → name属性。"""
    if key in BARE_NAMES:
        return key
    return "${bukkenInputForm." + key + "}"


# 画像スロット：JSONの slot → 実フォームの枠名。
# ★tsuika は ネット基本1〜3 → ネット追加1〜8 の順に詰める（実在は最大11枚なので足りる）。
#   内観写真(shitsunai)・外観パース(perth)枠は使わない（カテゴリ選択肢が別系統で、
#   使い分けの根拠が実測にないため。使わない＝意図した選択であることを残す）。
TSUIKA_SLOTS = [f"shashin{i}" for i in (1, 2, 3)] + [f"tsuikaGazo{i}" for i in range(1, 9)]
CAT_SELECT = {
    **{f"shashin{i}": "${bukkenInputForm.shashin" + str(i) + "Category}" for i in (1, 2, 3)},
    **{f"tsuikaGazo{i}": f"bukkenInputForm.tsuikaGazoInputForm[{i-1}].categoryCd"
       for i in range(1, 9)},
}


def parse_score(text: str):
    """画面テキストから名寄せスコアを取り出す。→ int または None。

    ★正規表現が永久に一致しないバグを一度出した（\\s と書いてしまいリテラルの \\s を探していた）。
      この種の壊れ方は実行するまで分からず、実機で初めて『スコアを読めない』として出る。
      関数に切り出して単体テストで固定する。
    """
    import re as _re
    m = _re.search(r"名寄せスコア\s*(\d+)\s*点", _re.sub(r"\s+", " ", text or ""))
    return int(m.group(1)) if m else None


def extract_bukken_code(html: str):
    """登録完了画面のHTMLから物件コード（12桁）を取り出す。無ければ None。
    ★表記は8/12の実測 `物件コード:(\d{12})`。タグや空白が挟まっても拾えるように緩めに当てる。"""
    import re
    m = re.search(r"物件コード\s*[:：]?\s*(?:<[^>]+>\s*)*(\d{12})", html)
    return m.group(1) if m else None


# 交通の1路線ぶんのフィールド（実測：らくらく交通入力が埋める7項目）。
# ★表示名とコードの両方が必要。コードだけだと入力エラーになる（8/11の失敗）。
#   逆に7項目とも入れれば、地図位置を触らなくても確認画面を通る（8/13に実証）。
TRANSIT_KEYS = ("pkgEnsenNmDisp", "pkgEnsenNm", "pkgEnsenCd",
                "pkgEkiNmDisp", "pkgEkiNm", "pkgEkiCd", "shoyoTime")
TRANSIT_RADIO = "kotsuShudanCd"         # 交通手段（1=徒歩 2=バス 3=車）。既定は徒歩
TRANSIT_LINES = ("", "2", "3")          # 交通1〜3
# ★etcEnsenekiFlg は**交通1〜3とは無関係**の「最寄り駅以外で利用できる沿線」チェックボックス。
#   ONにすると etcEnsenekiNm（沿線名）と etcShoyoTime（所要時間）が必須になり、
#   空だと『他交通機関チェック 最寄り駅以外で利用できる沿線名を正しく入力して下さい／
#   所要時間を入力して下さい』で確認画面に進めない。交通の複製では**触らない**。
#
# ★踏んだ原因の記録：チェックボックスの value 属性は checked と無関係に "1" を返す。
#   1208のフォームを読んだとき etcEnsenekiFlg='1' と見えたのは value であって状態ではなく、
#   それを「複製すべき値」と誤読した。手動evalでは e.value='1' としただけなので無害だったが、
#   set_field はチェックボックスと判定して set_checked(True) するので実際にONになった。
#   → checkbox/radio の状態は必ず checked で読む（value で判断しない）。


class Reg:
    def __init__(self, page, log):
        self.page = page
        self.log = log

    # ── フレーム ────────────────────────────────────────────────
    @property
    def main(self):
        """フォームがある main フレーム。★SUUMOはframesetで、topのDOMには入力欄が無い。"""
        f = self.page.frame(name="main")
        if f is None:
            raise RuntimeError("mainフレームが見つからない（ログイン切れ／画面遷移の失敗）")
        return f

    def loc(self, key):
        return self.main.locator(f'[name="{sel_name(key)}"]')

    # ── 検査 ────────────────────────────────────────────────────
    # ★実測（2026-08-13・実機で踏んだ）：削除系のidは3系統ある。
    #   up_del_<slot>      … 登録予定画像（アップしたがまだ登録していない）の削除ボタン ← これを数える
    #   up_del_div_<slot>  … その入れ物のdiv（同時に可視化される）
    #   db_del_div_<slot>  … **登録済み**画像の削除（更新画面で使う。U4のコピー登録で必要になる）
    #   [id^=up_del_] だと up_del_ と up_del_div_ の両方に当たり、枚数が**2倍**になる
    #   （9枚投入して18枚と出た）。div_ を除外して数える。
    UPDEL = '[id^="up_del_"]:not([id^="up_del_div_"])'
    DBDEL = '[id^="db_del_div_"]'

    def visible_delete_buttons(self):
        """可視な登録予定画像の削除ボタン数＝アップ済み（未登録）画像の枚数。
        サムネイル表示の完了もこれで判定できる（サムネと同時に可視化される）。"""
        return self.main.locator(f"{self.UPDEL}:visible").count()

    def visible_db_delete_buttons(self):
        """可視な**登録済み**画像の削除ボタン数（更新画面／コピー登録で残っている画像の数）。"""
        return self.main.locator(f"{self.DBDEL}:visible").count()

    def wait_form_ready(self, timeout_ms=20000):
        """フォームの描画完了を待つ。★click_menu の固定待ちだけでは、描画途中のDOMを読んで
        『空だから新規フォーム』と誤判定しうる（画像18枚事故の一因）。物件名の欄が現れ、
        削除ボタンの数が2回続けて同じになるまで待つ。"""
        self.main.locator(f'[name="{sel_name("bukkenNm")}"]').wait_for(
            state="attached", timeout=timeout_ms)
        prev = -1
        for _ in range(12):
            cur = self.visible_delete_buttons() + self.visible_db_delete_buttons()
            if cur == prev:
                return
            prev = cur
            self.page.wait_for_timeout(500)

    def assert_fresh_form(self):
        """罠1：新規フォームが前物件の内容を引き継いでいないこと。
        物件名が空・特徴項目0件・画像0枚（登録予定＋登録済みの両方）を実DOMで確認する。"""
        self.wait_form_ready()
        nm = self.loc("bukkenNm").input_value()
        tok = self.main.locator(
            'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked').count()
        img = self.visible_delete_buttons()
        dbimg = self.visible_db_delete_buttons()
        self.log(f"  フォーム初期状態: 物件名={nm!r} 特徴項目={tok}件 "
                 f"登録予定画像={img}枚 登録済み画像={dbimg}枚")
        if nm or tok or img or dbimg:
            raise RuntimeError(
                f"新規フォームが空でない（物件名={nm!r}／特徴項目{tok}件／"
                f"登録予定{img}枚／登録済み{dbimg}枚）。前物件の内容が残っている可能性があるので中止する")

    # ── 入力 ────────────────────────────────────────────────────
    def set_field(self, key, value):
        """1フィールドを埋める。要素の種類はDOMから判定する（決め打ちしない）。
        戻り値 (ok, 実際に入った値, メッセージ)。"""
        if value is None or value == "":
            return True, "", "空のため設定しない"
        name = sel_name(key)
        els = self.main.locator(f'[name="{name}"]')
        n = els.count()
        if n == 0:
            return False, None, f"name={name} の要素が無い"
        first = els.first
        tag = first.evaluate("e => e.tagName")
        typ = (first.evaluate("e => e.type || ''") or "").lower()
        # ★無効化されている要素は待たずに失敗を返す。SUUMOは他の項目に連動して
        #   select/input を無効化する（例：入居予定=指定有り を選ぶまで『旬』が無効）。
        #   ここで待つと30秒の時間切れになり、原因も分からない（実機で踏んだ）。
        if not first.is_enabled():
            return False, None, "無効化されている（連動元の項目を先に設定する必要がある）"
        # ★select / radio / checkbox は**可視**でないと操作できない（Playwrightが可視を待つ）。
        #   SUUMOは連動先を disabled ではなく **非表示** にすることがある
        #   （入居予定=指定有りを選ぶまで『旬』のselectが非表示。is_enabled()はTrueを返すので
        #    enabledの検査だけでは30秒の時間切れになった）。ここも待たずに失敗を返し、
        #   2パス目で連動元が設定済みになってから入れる。
        if tag == "SELECT" or typ in ("radio", "checkbox"):
            if not first.is_visible():
                return False, None, "非表示（連動元の項目を先に設定する必要がある）"
        if tag == "SELECT":
            first.select_option(str(value))
            got = first.input_value()
            return (got == str(value)), got, ""
        if typ == "radio":
            target = els.filter(has_not=None).nth(0)
            for i in range(n):
                e = els.nth(i)
                if e.get_attribute("value") == str(value):
                    e.check()
                    target = e
                    break
            else:
                return False, None, f"radio value={value} が無い"
            return target.is_checked(), str(value), ""
        if typ == "checkbox":
            want = bool(value) and str(value) not in ("0", "False", "false")
            if want != first.is_checked():
                first.set_checked(want)
            return (first.is_checked() == want), first.is_checked(), ""
        # ★hidden / readonly / 非表示は Playwright の fill() が書けない（可視で編集可能に
        #   なるまで待って時間切れになる）。実機の交通は etcEnsenekiFlg が hidden、
        #   沿線・駅コードも hidden のことがあるので、その場合はJS代入＋イベント発火で入れる。
        #   （手動evalでは通っていたのに set_field では通らない、という差の正体）
        # ★readonly / disabled は **属性の有無**で判定する。get_attribute は bare attribute に
        #   空文字を返すので `not ""` が True になり「編集可」と誤判定する（実機で踏んだ：
        #   交通の pkgEnsenNmDisp は readonly で、fill() が編集可になるのを待って時間切れ）。
        editable = (typ != "hidden" and first.is_visible()
                    and first.get_attribute("readonly") is None
                    and first.get_attribute("disabled") is None)
        if editable:
            first.fill(str(value))
        else:
            first.evaluate("""(e, v) => { e.value = v;
                e.dispatchEvent(new Event('input', {bubbles: true}));
                e.dispatchEvent(new Event('change', {bubbles: true})); }""", str(value))
        got = first.input_value()
        return (got == str(value)), got, ("" if editable else "hidden/readonlyへJS代入")

    def fill_form(self, form: dict):
        """JSONのformを全部埋める。_で始まるキー（原文の控え）は飛ばす。

        ★2パスで埋める。SUUMOは項目間に連動があり（入居予定=指定有りを選ぶまで『旬』が無効、
          管理費ありのチェックで金額欄が有効になる等）、1パスだと辞書順の都合で
          連動先を先に触って失敗する。1回目で失敗したものだけ2回目に再試行すれば、
          連動元が1回目で設定済みになっているので通る。順序を決め打ちしないので、
          今後別の連動が出ても同じ仕組みで吸収できる。
        """
        pending = [(k, v) for k, v in form.items() if not k.startswith("_")]
        ng = []
        for attempt in (1, 2):
            failed = []
            for key, value in pending:
                ok, got, msg = self.set_field(key, value)
                if not ok:
                    failed.append((key, value, got, msg))
            if not failed:
                return []
            if attempt == 1:
                self.log(f"  1回目で入らなかった{len(failed)}件を再試行: "
                         f"{[k for k, _v, _g, _m in failed]}")
                pending = [(k, v) for k, v, _g, _m in failed]
            else:
                ng = [f"{k}: 期待={v!r} 実際={g!r} {m}" for k, v, g, m in failed]
        return ng

    def fill_address(self, form: dict):
        """郵便番号 →「郵便番号から住所を入力」→ 丁目を選ぶ（§3-3：郵便番号入力が確実）。"""
        if not form.get("yubinNo1"):
            return ["郵便番号が無いため住所を入れられない"]
        # ★実測（2026-08-13）：<a id="yubinNoSearch" onclick="addressSearch();">郵便番号から住所を入力</a>
        #   input ではなく **Aタグ**。input[type=button][value*=...] では一致しない（実機で踏んだ）。
        btn = None
        for sel in ('#yubinNoSearch', 'a[onclick*="addressSearch"]',
                    'a:has-text("郵便番号から住所を入力")'):
            loc = self.main.locator(sel)
            if loc.count() and loc.first.is_visible():
                btn = loc.first
                break
        if btn is None:
            return ["『郵便番号から住所を入力』が見つからない（#yubinNoSearch を確認）"]
        btn.click()
        self.page.wait_for_timeout(1200)
        got = {k: self.main.locator(f'[name="${{{k}}}"]').input_value()
               for k in ("todofukenCd", "shigunkuCd", "chosonCd")
               if self.main.locator(f'[name="${{{k}}}"]').count()}
        self.log(f"  住所の自動入力: {got}")
        if not any(got.values()):
            return ["郵便番号から住所が入らなかった"]
        return []

    def check_tokucho(self, codes, allowed=None):
        """特徴項目のチェックを入れる。value＝SUUMOのコード。
        allowed＝サイト連動で増えてよいコード→理由（derived_allowed の結果）。"""
        allowed = allowed or {}
        ng = []
        for c in codes:
            e = self.main.locator(
                f'input[name="${{bukkenInputForm.categoryTokuchoCd}}"][value="{c}"]')
            if e.count() == 0:
                ng.append(f"特徴項目コード {c} のチェックボックスが無い")
                continue
            e.first.check()
            if not e.first.is_checked():
                ng.append(f"特徴項目コード {c} をチェックできなかった")
        checked = self.main.evaluate("""() => Array.from(document.querySelectorAll(
            'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked')).map(e => e.value)""")
        self.log(f"  特徴項目: {len(checked)}/{len(codes)}件チェック")
        if sorted(checked) != sorted(codes):
            extra = sorted(set(checked) - set(codes) - set(allowed))
            derived = sorted(set(checked) - set(codes) - set(extra))
            miss = sorted(set(codes) - set(checked))
            for dcode in derived:
                self.log(f"    （サイト連動 {dcode}={allowed[dcode]}）")
            if not extra and not miss:
                return ng
            # ★数だけ出すと原因が追えない。余った/足りないコードを列挙する
            #   （実機で17/16になった。サイト側が関連項目を自動チェックする可能性がある）
            ng.append(f"特徴項目が一致しない（期待{len(codes)}／実際{len(checked)}"
                      f"／余り{extra}／不足{miss}）")
        return ng

    # ── 画像 ────────────────────────────────────────────────────
    def wait_thumb(self, slot, timeout_ms=20000):
        """サムネイル表示（=up_del_<slot> の可視化）を待つ。
        ★これを待たずに確認画面へ進むと画像が登録されない（実機に明記・8/12に実際に起きた）。"""
        self.main.locator(f"#up_del_{slot}").wait_for(state="visible", timeout=timeout_ms)

    def delete_all_images(self):
        """全枠の画像を削除し、0枚になったことを検証する。
        ★コピー登録で前室の画像が残るため必要（8/11に別物件の写真で登録しかけた事故）。
        ★deleteGazo が confirm を出す可能性があるので、ダイアログを受け入れる待受を張ってから押す。"""
        self.page.on("dialog", lambda d: d.accept())
        n0 = self.visible_delete_buttons()
        self.log(f"  既存画像 {n0}枚 を削除する")
        guard = 0
        while self.visible_delete_buttons() > 0 and guard < 40:
            self.main.locator(f"{self.UPDEL}:visible").first.click()
            self.page.wait_for_timeout(400)
            guard += 1
        left = self.visible_delete_buttons()
        if left:
            raise RuntimeError(f"画像を全削除できなかった（残り{left}枚）")
        self.log("  画像0枚を確認")

    def upload_images(self, images, base_dir: Path):
        """JSONのimagesを枠へ投入し、カテゴリを設定する。
        ★投入前に0枚であることを必ず検証する（前室の画像が混ざる事故の防止）。"""
        ng = []
        before = self.visible_delete_buttons()
        if before != 0:
            raise RuntimeError(f"画像投入前に既存画像が{before}枚ある。投入せず中止する")
        madori = [i for i in images if i["slot"] == "madori"]
        gaikan = [i for i in images if i["slot"] == "gaikan"]
        tsuika = [i for i in images if i["slot"] == "tsuika"]
        if len(tsuika) > len(TSUIKA_SLOTS):
            raise RuntimeError(f"追加画像が{len(tsuika)}枚で枠{len(TSUIKA_SLOTS)}を超える")
        plan = []
        for im in madori:
            plan.append(("clientMadori", im, None))
        for im in gaikan:
            plan.append(("gaikan", im, None))
        for k, im in enumerate(tsuika):
            plan.append((TSUIKA_SLOTS[k], im, im.get("category")))
        for slot, im, cat in plan:
            p = Path(im["path"])
            if not p.is_absolute():
                p = base_dir / p
            if not p.is_file():
                ng.append(f"{im['file']}: 実体が無い（{p}）")
                continue
            self.main.locator(f"#file_up_{slot}").set_input_files(str(p))
            try:
                self.wait_thumb(slot)
            except Exception:  # noqa: BLE001
                ng.append(f"{im['file']}: サムネイルが表示されない（枠 {slot}）")
                continue
            if cat:
                name = CAT_SELECT.get(slot)
                sel = self.main.locator(f'[name="{name}"]')
                if sel.count() == 0:
                    ng.append(f"{im['file']}: カテゴリselect {name} が無い")
                    continue
                sel.first.select_option(cat)
                if sel.first.input_value() != cat:
                    ng.append(f"{im['file']}: カテゴリ {cat} を設定できなかった")
            self.log(f"    {im['file']} → {slot}" + (f" / カテゴリ{cat}" if cat else ""))
        after = self.visible_delete_buttons()
        self.log(f"  投入後の画像 {after}枚（期待 {len(plan)}枚）")
        if after != len(plan):
            ng.append(f"画像枚数が合わない（期待{len(plan)}／実際{after}）")
        return ng

    # ── 読み戻し照合 ────────────────────────────────────────────
    def readback(self, form: dict):
        """埋めた値を読み戻して照合する。
        ★name属性が違っても例外は出ず「空のまま」になる。読み戻さないと黙って空で送信される。"""
        ng = []
        for key, value in form.items():
            if key.startswith("_") or value is None or value == "":
                continue
            name = sel_name(key)
            els = self.main.locator(f'[name="{name}"]')
            if els.count() == 0:
                ng.append(f"{key}: 要素が無い")
                continue
            first = els.first
            typ = (first.evaluate("e => e.type || ''") or "").lower()
            if typ == "radio":
                checked = [els.nth(i).get_attribute("value") for i in range(els.count())
                           if els.nth(i).is_checked()]
                if str(value) not in checked:
                    ng.append(f"{key}: 期待={value} 実際={checked}")
            elif typ == "checkbox":
                want = bool(value) and str(value) not in ("0", "False", "false")
                if first.is_checked() != want:
                    ng.append(f"{key}: 期待={want} 実際={first.is_checked()}")
            else:
                got = first.input_value()
                if got != str(value):
                    ng.append(f"{key}: 期待={value!r} 実際={got!r}")
        return ng

    # ── ボタン ──────────────────────────────────────────────────
    #  ★実機のボタンは <input>/<button> ではなく **DIV＋CSSスプライト背景**：
    #      <div id="regButton2" class="spbtn btn_a_b_kakunin" title="確認画面へ">確認画面へ</div>
    #    そのため a/input/button をテキストで探すと永久に見つからない（8/12は座標で押していた）。
    #    唯一安定した手掛かりは **title属性**。id も併用する（regButton2 等）。
    #  ★このアプリの「押せるもの」は実測で3系統あり、どれもテキストでは探せない：
    #      a.menu_btn[title=…]        メニュータブ（テキスト空）
    #      div.spbtn[title=…]         画面内ボタン（確認画面へ・削除など）
    #      img.imageButton[alt=…]     確認画面の実行系（登録=#jikko / 訂正=#teisei）
    #    title と alt の両方を見ないと「登録」に到達できない（8/12は座標クリックしていた）。
    BTN_SELECTORS = (
        'div.spbtn[title="{t}"]', 'img.imageButton[alt="{t}"]',
        'div[title="{t}"]', 'img[alt="{t}"]', '[title="{t}"]', '[alt="{t}"]',
        'input[value="{t}"]', 'button:has-text("{t}")',
    )

    def find_button(self, title):
        """title（画面の文字）でボタンを探す。見つからなければ None。"""
        for pat in self.BTN_SELECTORS:
            loc = self.main.locator(pat.format(t=title))
            for i in range(loc.count()):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        return None

    def dump_buttons(self):
        """画面のボタン相当（div.spbtn / input[type=button] / button）を一覧で返す。
        ★未知の画面（確認画面・完了画面）でどれを押すかを人が決めるための材料。
          推測でクリックしないための道具なので、押す前に必ずこれを出す。"""
        return self.main.evaluate("""() => {
            const vis = (e) => { const r = e.getBoundingClientRect(),
                s = getComputedStyle(e);
              return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
            return Array.from(document.querySelectorAll(
                'div.spbtn,[class*="btn_"],input[type=button],input[type=submit],input[type=image],button,a[onclick]'))
              .filter(vis)
              .map(e => ({tag:e.tagName, id:e.id||'', cls:(e.className||'').toString().slice(0,40),
                          title:e.title||'', text:(e.textContent||'').trim().slice(0,20),
                          value:e.value||'', onclick:(e.getAttribute('onclick')||'').slice(0,60)}));
        }""")

    def fill_aza(self, chome):
        """丁目を ${azaCd} で選ぶ。★azaCd はゼロ埋め3桁（8/12実測：005=５丁目）。
        全角数字・漢数字も来るので半角数字に寄せてから詰める。取れなければ選ばない（人に返す）。"""
        if not chome:
            return []
        z = str(chome).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        kan = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        n = None
        m = __import__("re").search(r"\d+", z)
        if m:
            n = int(m.group(0))
        elif z.strip() in kan:
            n = kan[z.strip()]
        if n is None:
            return [f"丁目『{chome}』を数字にできない（azaCdは人が選ぶ）"]
        code = str(n).zfill(3)
        sel = self.main.locator('[name="${azaCd}"]')
        if sel.count() == 0:
            return ["${azaCd} のselectが無い"]
        opts = sel.first.evaluate("e => Array.from(e.options).map(o => o.value)")
        if code not in opts:
            return [f"丁目コード {code}（{chome}丁目）が選択肢に無い: {opts[:8]}"]
        sel.first.select_option(code)
        got = sel.first.input_value()
        self.log(f"  丁目: {chome}丁目 → azaCd={code}" + ("" if got == code else f" ★実際={got}"))
        return [] if got == code else [f"azaCd を {code} にできなかった（実際 {got}）"]

    # ── 照合（登録後の検証）────────────────────────────────────────
    #  ★8/12に実機で動かして成功した手順をそのまま実装している（谷合さん提供）。
    #    ・検索: ${keisaiSearchForm.bukkenCd} に12桁 → input[value=検索] の [0] を押す
    #    ・結果行: hidden input の value が物件コードと一致する行（tr）
    #    ・詳細: その行の a で innerText=='詳細'
    #    ・名寄せスコア: 更新画面ヘッダの「名寄せスコア N 点」（一覧の数値は列の意味が紛らわしい）
    def search_bukken(self, code: str):
        """物件コードで検索して更新画面（詳細）を開く。開けたら True。"""
        # ★画面表記は「更新・掲載指示」だが title は「掲載指示」（実測）。表記で探すと外れる。
        click_menu(self, "掲載指示", self.log)
        fld = self.main.locator('[name="${keisaiSearchForm.bukkenCd}"]')
        if fld.count() == 0:
            raise RuntimeError(
                "${keisaiSearchForm.bukkenCd} が無い（実測値だがフォーム名が変わった可能性）。"
                "--dump-buttons と併せて画面を読み直すこと")
        fld.first.fill(code)
        btns = self.main.locator('input[value="検索"]')
        if btns.count() == 0:
            raise RuntimeError("input[value=検索] が無い")
        btns.nth(0).click()          # ★2つあるが [0] で通る（8/12実測）
        self.page.wait_for_timeout(3000)
        # 結果行＝hidden input の value が物件コードと一致する行
        ok = self.main.evaluate("""(code) => {
            const h = Array.from(document.querySelectorAll('input[type=hidden]'))
                .find(i => i.value === code);
            if (!h) return false;
            const tr = h.closest('tr');
            if (!tr) return false;
            const a = Array.from(tr.querySelectorAll('a'))
                .find(x => (x.innerText||'').trim() === '詳細');
            if (!a) return false;
            a.click();
            return true;
        }""", code)
        if not ok:
            return False
        self.page.wait_for_timeout(3500)
        return True

    def read_registered(self):
        """更新画面から登録内容を読む。→ dict（フォーム値＋画像枚数＋名寄せスコア＋特徴項目数）。"""
        out = {}
        for key in ("bukkenNm", "heyaNo", "kai", "kaidate", "chinryo1", "chinryo2",
                    "kanrihi1", "kanrihi2", "menseki1", "menseki2", "heyaCnt",
                    "madoriTypeKbnCd", "kozoShuCd"):
            els = self.main.locator(f'[name="{sel_name(key)}"]')
            out[key] = els.first.input_value() if els.count() else None
        out["_tokucho_n"] = self.main.locator(
            'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked').count()
        # ★更新画面では画像は**登録済み**なので db_del_div_ 側を数える（実測で確認）。
        #   up_del_（登録予定）は更新画面では0枚になる。ここを間違えると常に0枚と判定する。
        out["_images"] = self.visible_db_delete_buttons()
        out["_images_pending"] = self.visible_delete_buttons()
        # ★ここは二重エスケープで一度壊した。JS側の /\s+/g と Python側の \s / \d は
        #   バックスラッシュ1本。ヒアドキュメント経由でコードを書くと \\s になりやすく、
        #   その場合「literalな \s を探す」ので永久に一致しない（実機で『スコアを読めない』になった）。
        txt = self.main.evaluate("() => document.body.innerText")
        out["_score"] = parse_score(txt)
        if out["_score"] is None:      # 読めなかったときだけ原文を残す（原因追跡用）
            out["_score_src"] = " ".join((txt or "").split())[:140]
        return out

    # ── 交通 ────────────────────────────────────────────────────
    def read_transit(self):
        """今のフォームから交通1〜3を読む。→ [{key: value}, ...]（空の行は入れない）。
        ★同一棟なら交通は同一なので、棟の1室目で人が『らくらく交通入力』した結果を
          保存して2室目以降に複製する。住所から計算するのではなく、人が検証した値の再利用。"""
        out = []
        for n in TRANSIT_LINES:
            row = {}
            for k in TRANSIT_KEYS:
                e = self.main.locator(f'[name="{sel_name(k + n)}"]')
                row[k] = e.first.input_value() if e.count() else ""
            # 交通手段はラジオ。★checked で読む（value で読むと常に最初の選択肢になる）
            rs = self.main.locator(f'[name="{sel_name(TRANSIT_RADIO + n)}"]')
            row[TRANSIT_RADIO] = next(
                (rs.nth(i).get_attribute("value") for i in range(rs.count())
                 if rs.nth(i).is_checked()), "")
            if row.get("pkgEkiCd") or row.get("pkgEkiNmDisp"):
                row["_line"] = n
                out.append(row)
        return out

    def apply_transit(self, lines):
        """保存した交通を今のフォームへ複製する。→ 未解決の一覧。"""
        ng = []
        for row in lines:
            n = row.get("_line", "")
            for k in TRANSIT_KEYS:
                v = row.get(k, "")
                if v == "":
                    continue
                ok, got, msg = self.set_field(k + n, v)
                if not ok:
                    ng.append(f"交通{n or '1'} {k}: 期待={v!r} 実際={got!r} {msg}")
            # 交通手段（徒歩/バス/車）。★モーダル経由でないと表示されないため、複製では
            #   触れないことがある。既定は徒歩(1)で、既にその値なら触る必要がない。
            #   バス(2)・車(3)を入れられない場合だけ本当の問題として報告する。
            want = row.get(TRANSIT_RADIO, "")
            if want:
                cur = self.main.locator(f'[name="{sel_name(TRANSIT_RADIO + n)}"]')
                now = next((cur.nth(i).get_attribute("value") for i in range(cur.count())
                            if cur.nth(i).is_checked()), "")
                if now != want:
                    ok, got, msg = self.set_field(TRANSIT_RADIO + n, want)
                    if not ok:
                        if want == "1":
                            self.log(f"    交通{n or '1'} 手段は既定の徒歩のまま（{msg}）")
                        else:
                            ng.append(f"交通{n or '1'} 手段: 期待={want}（徒歩以外）"
                                      f" 実際={now or '未選択'} {msg}")
        shown = " / ".join(f"{r.get('pkgEnsenNmDisp','')} {r.get('pkgEkiNmDisp','')} "
                           f"徒歩{r.get('shoyoTime','')}分" for r in lines)
        self.log(f"  交通を複製: {shown}")
        return ng

    def score(self):
        """画面の名寄せスコア表示を読む（実フォームの左上にある）。"""
        try:
            t = self.main.get_by_text("名寄せスコア").first.inner_text()
            return t.replace("\n", " ").strip()[:40]
        except Exception:  # noqa: BLE001
            return "(取得できず)"


# ── フロー ────────────────────────────────────────────────────
# naviフレームのメニュータブ。★実測（2026-08-13）：
#   <a class="menu_btn" id="menu_2" title="新規物件登録" href="MNU1R0001_f.action?id=...">
#   **テキストは空**（CSSスプライト背景）なので get_by_text は永久に一致しない。
#   このアプリはボタンもタブも title 属性だけが手掛かり。
#   ★画面のタブ表記は「更新・掲載指示」だが title は「掲載指示」。表記で書くと外れる。
MENU_TITLE = {
    "トップ": "menu_1", "新規物件登録": "menu_2", "掲載指示": "menu_3",
    "物件一括操作": "menu_4", "効果分析": "menu_6", "管理": "menu_7",
    "オーナー": "menu_9", "お役立ち": "menu_8",
}


def try_autologin(page, log) -> bool:
    """ブラウザのパスワード自動入力が効いている場合に限り、ログインボタンを押す。

    ★パスワードの値は読まない・出力しない・入力しない。**入っているかどうか（長さ>0）だけ**を
      見て、入っていればボタンを押す。自動入力が無ければ何もせず False を返して人に任せる。
    ★ログイン画面はframesetだが navi/main の名前が付いていない（実測：無名フレーム3〜4個）。
      どのフレームにフォームがあるか決め打ちできないので全フレームを走査する。
    """
    for fr in page.frames:
        try:
            pw = fr.locator('input[type=password]')
            if pw.count() == 0:
                continue
            filled = pw.first.evaluate("e => (e.value || '').length > 0")
            idf = fr.locator('input[type=text]')
            id_filled = (idf.count() > 0
                         and idf.first.evaluate("e => (e.value || '').length > 0"))
            log(f"  自動入力の状態: ID={'あり' if id_filled else 'なし'} "
                f"パスワード={'あり' if filled else 'なし'}")
            if not (filled and id_filled):
                return False
            # ★実測：ログインボタンは
            #     <input type="image" id="Image7" class="loginButton" src="btn_a_b_login.gif">
            #   alt も value も無い。input[type=image] を候補に入れていなかったため
            #   「見つからない」になっていた（このアプリの押せるもの4系統目）。
            for sel in ('input.loginButton', 'input[type=image][id=Image7]',
                        'input[type=image]', 'img[alt*="ログイン"]',
                        'input[value*="ログイン"]', 'div[title*="ログイン"]',
                        'input[type=submit]'):
                b = fr.locator(sel)
                for i in range(b.count()):
                    if b.nth(i).is_visible():
                        log(f"  ログインボタンを押す（{sel}）")
                        b.nth(i).click()
                        return True
            # ★診断出力に value を絶対に入れない（一度ここでIDとパスワードをログに出した）。
            #   タグ・id・class・alt・onclick だけを出す。alt は画像ボタンのラベルで
            #   資格情報ではないので可。value と textContent は出さない。
            found = fr.evaluate("""() => Array.from(document.querySelectorAll('img,input,div,a,button'))
                .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .filter(e => (e.type || '') !== 'password' && (e.type || '') !== 'text')
                .map(e => [e.tagName, e.type || '', e.id || '', (e.className||'').toString().slice(0,20),
                           e.alt || '', (e.getAttribute('onclick')||'').slice(0,40)].join('|'))
                .slice(0, 18)""")
            log(f"  ★ログインボタンが見つからない。候補（値は出さない）: {found}")
            return False
        except Exception as e:  # noqa: BLE001
            log(f"  （フレーム走査中: {type(e).__name__}）")
    return False


def click_menu(reg: Reg, title: str, log, wait_ms: int = 2500):
    """naviフレームのメニュータブを title で押す。"""
    navi = reg.page.frame(name="navi")
    if navi is None:
        raise RuntimeError("naviフレームが無い（ログイン切れ）")
    for sel in (f'a.menu_btn[title="{title}"]', f'a[title="{title}"]',
                f'#{MENU_TITLE.get(title, "")}' if MENU_TITLE.get(title) else None):
        if not sel:
            continue
        loc = navi.locator(sel)
        if loc.count():
            loc.first.click()
            reg.page.wait_for_timeout(wait_ms)
            return
    have = navi.evaluate("""() => Array.from(document.querySelectorAll('a'))
        .map(a => a.title || a.id || '').filter(Boolean)""")
    raise RuntimeError(f"メニュー『{title}』が見つからない。naviのa: {have}")


def open_new_form(reg: Reg, url: str, log, login_wait: int = 0, navigate: bool = True):
    """メニューの「新規物件登録」から入る。★直リンクは使わない（罠2＝復元入口になる）。"""
    if navigate:
        reg.page.goto(url, wait_until="load")
    navi = reg.page.frame(name="navi")
    if navi is None and login_wait:
        # ★ログインとフォーム操作を同一ブラウザセッションで完結させる。
        #   --login で一旦閉じる設計にすると、セッションCookieがプロファイルに残るか
        #   （＝再開できるか）が環境依存になる。人がここでパスワードを入れる。
        log(f"  ログイン画面です。この窓でログインしてください（最大{login_wait}秒待ちます）")
        log("  ★タブは増やさないこと（罠3＝2タブでセッションが壊れる）")
        t0 = time.time()
        while time.time() - t0 < login_wait:
            navi = reg.page.frame(name="navi")
            if navi is not None:
                log(f"  ログインを確認しました（{time.time()-t0:.0f}秒）")
                break
            reg.page.wait_for_timeout(2000)
    if navi is None:
        raise RuntimeError("naviフレームが無い（ログインされていない／画面遷移の失敗）")
    click_menu(reg, "新規物件登録", log)
    log(f"  main frame: {reg.main.url[-60:]}")


def _dec_eq(a1, a2, e1, e2):
    """[整数].[小数] の対を数値として比べる。
    ★SUUMOは 1.0万 を入れても更新画面では kanrihi2 が空で返る（実測）。
      文字列比較すると『期待 0 / 実際 空』で誤FAILになる。値として一致すればOKとする。"""
    def f(i, d):
        i = str(i or "0").strip() or "0"
        d = str(d or "0").strip() or "0"
        try:
            return float(f"{i}.{d}")
        except ValueError:
            return None
    return f(a1, a2) is not None and f(a1, a2) == f(e1, e2)


# ★サイトが他のフィールドから自動でチェックする特徴項目（実測）。
#   こちらから要求していないので「余り」に出るが、内容として正しいので許容する。
#   ただし**条件付き**にする：連動元が違う値なら異常として止める
#   （例：方位が西の室で「南向き」が付いたら、それは事故なので黙って通してはいけない）。
#   実測: 2701 は サンプルレジデンスA 全室（入居=即）で出た。
#         1001 は サンプルレジデンスB_1104（方位=南）で出て、909・1208（方位=西）では出なかった。
TOKUCHO_SITE_DERIVED = {
    "2701": ("即入居可（入居予定=即に連動）", "nyukyoKbnCd", ("1",)),
    "1001": ("南向き（開口部方位=南に連動）", "kaikomukiKbnCd", ("5",)),
}


def derived_allowed(form: dict) -> dict:
    """このフォームの値で、サイト連動として許容できる特徴項目コード → 理由。"""
    out = {}
    for code, (why, key, ok_vals) in TOKUCHO_SITE_DERIVED.items():
        if str(form.get(key, "")) in ok_vals:
            out[code] = why
    return out


def auto_transit(reg: Reg, log, slots=3):
    """『らくらく交通入力』のモーダルを操作して交通を入れる。→ (入った行, 未解決の一覧)。

    ■モーダルの構造（2026-08-13実測 COM1R02161.action「らくらく交通入力」）
      ・**地図の位置から候補（沿線・駅・徒歩分数）を算出して並べる画面**。
        候補1 沿線 地下鉄中央線 駅 阿波座 駅から 徒歩6分 … のように4件前後。
      ・ラジオは枠×候補のグリッド： name=koutu1/koutu2/koutu3（＝交通1/2/3の枠）、
        id=koutu_<枠>-<候補>（例 koutu_1-2 は「交通1の枠に候補2を入れる」）。
        「※同一候補は選択できません」＝1候補は1枠にしか割り当てられない。
      ・登録は <img id="registButton" alt="　　登　録　　">。
    ■なぜモーダルを使うか
      徒歩分数はSUUMOが地図から算出する。マイソクの数字を転記すると実際より近く表示され得る
      （実測：マイソク6/6/6分に対しSUUMOの算出は9/8/11分）。転記せずモーダルに計算させる。
    ■既定の割り当ては 候補1→交通1 / 候補2→交通2 / 候補3→交通3（候補の並び順）。
      選んだ結果は必ずログに出し、親フォームに実際に入った値で検証する（黙って空にしない）。
    """
    ng = []
    btn = reg.main.locator('#rakurakuKotsu')
    if btn.count() == 0:
        return [], ["#rakurakuKotsu が無い（新規登録フォームを開いてから実行する）"]
    with reg.page.context.expect_page(timeout=25000) as pi:
        btn.first.click()
    pop = pi.value
    try:
        pop.wait_for_load_state("load")
        for _ in range(30):
            if pop.evaluate("() => document.querySelectorAll('input[type=radio]').length"):
                break
            pop.wait_for_timeout(500)
        cands = pop.evaluate("""() => {
            const out = [];
            document.querySelectorAll('tr').forEach(tr => {
                const t = (tr.innerText || '').replace(/\s+/g, ' ').trim();
                const m = t.match(/候補(\d+)/);
                if (m && /沿線|徒歩/.test(t)) out.push({no: m[1], text: t.slice(0, 70)});
            });
            return out;
        }""")
        log(f"  モーダルの候補 {len(cands)}件")
        for c in cands:
            log(f"    {c['text']}")
        n_avail = pop.evaluate(
            "() => document.querySelectorAll('input[name=koutu1]').length")
        picked = []
        for slot in range(1, slots + 1):
            cand = slot            # 候補1→交通1, 候補2→交通2, 候補3→交通3
            el = pop.locator(f"#koutu_{slot}-{cand}")
            if el.count() == 0 or not el.first.is_visible():
                log(f"    交通{slot}: 候補{cand} の選択肢が無い（この枠は空のまま）")
                continue
            el.first.check()
            picked.append((slot, cand))
        if not picked:
            ng.append(f"モーダルで候補を1つも選べなかった（候補数={n_avail}）")
            pop.close()
            return [], ng
        log(f"  割り当て: " + " / ".join(f"交通{s}←候補{c}" for s, c in picked))
        rb = pop.locator('#registButton')
        if rb.count() == 0:
            ng.append("モーダルの登録ボタン（#registButton）が無い")
            pop.close()
            return [], ng
        # ★「ポップアップが閉じるのを待つ」ではなく「**親フォームに値が入るのを待つ**」。
        #   実測：閉じるのを15秒待って強制クローズしたら、rakurakuKotsuCacheFlg は 0→1 に
        #   なったのに親フォームの交通は空だった＝反映のコールバックを自分で殺していた。
        #   反映は親フォーム側で起きるので、そこを合否の条件にする。
        # ★2026-08-14実測：登録を1回押すと COM1R02161 → COM1R02162 に遷移し、
        #   **同じ選択画面が選択を保ったまま再描画されて止まる**棟がある（サンプルレジデンスA）。
        #   一発で閉じる棟（サンプルレジデンスBなど）と混在するので、回数を決め打ちにしない。
        #   「親フォームに入るまで、登録ボタンがある限り押す」（上限3回）にする。
        filled = False
        for attempt in range(3):
            if pop.is_closed():
                break
            rbs = pop.locator('#registButton')
            if rbs.count() == 0:
                log(f"  モーダル{attempt + 1}回目: 登録ボタンが無くなった（url={pop.url[-24:]}）")
                break
            if attempt:
                log(f"  モーダルが閉じない（url={pop.url[-24:]}）。登録をもう一度押す "
                    f"[{attempt + 1}回目]")
            try:
                rbs.first.click()
            except Exception as ce:  # noqa: BLE001  遷移中にクリック対象が消えることがある
                log(f"  登録クリックで例外（遷移中の可能性）: {type(ce).__name__}")
            for _ in range(24):                   # 1回の押下につき最大12秒待つ
                reg.page.wait_for_timeout(500)
                try:
                    if reg.main.locator(
                            f'[name="{sel_name("pkgEkiNmDisp")}"]').first.input_value():
                        filled = True
                        break
                except Exception:  # noqa: BLE001  親フォームが再描画中は読めないことがある
                    pass
                if pop.is_closed():
                    break
            if filled:
                break
        if not filled and not pop.is_closed():
            # 反映されない＝モーダル側に何か出ている可能性。状態を記録してから閉じる
            try:
                log(f"  モーダルの状態: url={pop.url[-40:]} title={pop.title()!r}")
                txt = pop.evaluate("() => (document.body.innerText||'').replace(/\s+/g,' ')")
                # ★220字で切ると候補一覧しか写らず、末尾のエラー文言が読めなかった。全文を出す。
                log(f"  モーダル本文（全文 {len(txt)}字）: {txt}")
                shot = Path(os.environ.get("SUUMO_SHOT_DIR", "/tmp")) / (
                    "modal_" + time.strftime("%H%M%S") + ".png")
                try:
                    pop.screenshot(path=str(shot), full_page=True)
                    log(f"  モーダルの画面: {shot}")
                except Exception:  # noqa: BLE001
                    pass
                # ★登録を押すと2ページ目（COM1R02161 → COM1R02162）に遷移していた。
                #   もう一段あるので、次に押すものを実際に見る（推測でクリックしない）。
                btns = pop.evaluate(
                    "() => { const vis = e => { const r = e.getBoundingClientRect();"
                    " return r.width > 0 && r.height > 0; };"
                    " return Array.from(document.querySelectorAll("
                    "'img,input[type=image],input[type=button],input[type=submit],"
                    "div.spbtn,a[onclick],button')).filter(vis)"
                    ".map(e => [e.tagName, e.id||'', e.alt||'', e.value||'',"
                    " (e.className||'').toString().slice(0,16),"
                    " (e.getAttribute('onclick')||'').slice(0,44)].join('|')); }")
                log("  モーダルのボタン:")
                for b in btns:
                    log(f"    {b}")
                sel = pop.evaluate(
                    "() => Array.from(document.querySelectorAll('input[type=radio]'))"
                    ".filter(e => e.checked).map(e => e.id || e.name)")
                log(f"  選択済みのラジオ: {sel}")
            except Exception as e2:  # noqa: BLE001
                log(f"  モーダル診断で例外: {type(e2).__name__}: {str(e2)[:80]}")
        if not pop.is_closed():
            pop.close()
    except Exception as e:  # noqa: BLE001
        try:
            pop.close()
        except Exception:  # noqa: BLE001
            pass
        return [], [f"モーダル操作で失敗: {type(e).__name__}: {str(e)[:120]}"]
    reg.page.wait_for_timeout(2500)
    lines = reg.read_transit()
    if not lines:
        ng.append("モーダル操作後も親フォームの交通が空（登録が反映されていない）")
    else:
        log("  親フォームの交通: " + " / ".join(
            f"{r.get('pkgEnsenNmDisp','')} {r.get('pkgEkiNmDisp','')} "
            f"徒歩{r.get('shoyoTime','')}分" for r in lines))
        for r in lines:
            if not r.get("shoyoTime"):
                ng.append(f"交通{r.get('_line') or '1'} の所要時間が空")
    return lines, ng


def submit_room(reg: Reg, log, expect_images=None):
    """『確認画面へ』→ 内容確認 →『登録』。→ (物件コード|None, 未解決の一覧)。

    ★押す前に必ず実DOMを見る：必須の元付4項目・画像枚数・確認画面かどうか・エラー表示の有無。
      推測でクリックしない（依頼文§3-10 罠4「クリック前に実レスポンスで確認する」）。
    ★『登録』は <img id="jikko" class="imageButton" alt="登録"> で、title/value/テキストは空。
      文字で探しても見つからない（8/12は座標クリックしていた）。alt が唯一の手掛かり。
    """
    ng = []
    need = ("mototsukeGyoshaNm", "mototsukeTantoNm", "mototsukeTelNo", "mototsukeKakuninDate")
    empty = [k for k in need
             if not (reg.loc(k).count() and reg.loc(k).first.input_value().strip())]
    if empty:
        # 取引態様=先物では4項目すべて必須（実機のエラー『取引態様が「先物」なのに
        # 元付業者の記入がありません』は担当者・確認日が空でも出る）
        return None, [f"元付の必須項目が空: {empty}"]
    n_img = reg.visible_delete_buttons()
    if expect_images is not None and n_img != expect_images:
        return None, [f"画像枚数が期待と違う（期待{expect_images}／実際{n_img}）"]
    btn = reg.find_button("確認画面へ")
    if btn is None:
        return None, ["『確認画面へ』が見つからない"]
    log(f"  『確認画面へ』を押す（画像{n_img}枚）")
    btn.click()
    reg.page.wait_for_timeout(4500)
    title = reg.main.title()
    if "確認" not in title:
        # ★エラーは「エラー 一覧」テーブルの行に出る（状況／区分／内容）。
        #   要素を無条件に拾うと画面の注意書きやJSの断片ばかりで原因が分からない
        #   （実際にそれでサンプルレジデンスA_810 の原因を2回取り逃した）。テーブルの行を読む。
        errs = reg.main.evaluate("""() => {
            const out = [];
            document.querySelectorAll('table').forEach(tb => {
                const head = (tb.innerText || '').replace(/\s+/g, ' ');
                if (!/エラー|状況.*区分.*内容/.test(head)) return;
                tb.querySelectorAll('tr').forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td,th'))
                        .map(c => (c.innerText || '').replace(/\s+/g, ' ').trim())
                        .filter(Boolean);
                    if (cells.length >= 2) out.push(cells.join(' | ').slice(0, 150));
                });
            });
            return Array.from(new Set(out)).slice(0, 12);
        }""")
        return None, [f"確認画面に進めなかった（画面={title}）"] + errs
    errs = reg.main.evaluate("""() => Array.from(document.querySelectorAll('*'))
        .filter(e => !e.children.length && /ありません|エラー\s*一覧/.test(e.textContent||''))
        .map(e => (e.textContent||'').trim().slice(0,80)).slice(0,8)""")
    if errs:
        return None, ["確認画面にエラー表示がある"] + errs
    score = parse_score(reg.main.evaluate("() => document.body.innerText"))
    jikko = reg.main.locator('img#jikko')
    if jikko.count() == 0 or jikko.first.get_attribute("alt") != "登録":
        alts = reg.main.evaluate("""() => Array.from(document.querySelectorAll('img.imageButton'))
            .map(e => e.id + ':' + (e.alt||''))""")
        return None, [f"『登録』ボタン（img#jikko alt=登録）が確認できない: {alts}"]
    log(f"  『登録』を押す（確認画面のスコア {score}点）")
    jikko.first.click()
    reg.page.wait_for_timeout(6000)
    code = extract_bukken_code(reg.main.evaluate("() => document.body.innerText"))
    if not code:
        return None, [f"登録完了画面から物件コードが取れない（画面={reg.main.title()}）"]
    log(f"  登録完了 物件コード={code} / 完了画面のスコア="
        f"{parse_score(reg.main.evaluate('() => document.body.innerText'))}点")
    return code, ng


def verify_room(reg: Reg, rec: dict, code: str, log):
    """登録後の照合。受入基準4-1：物件名・号室・賃料・管理費・面積・間取り・階／画像枚数／
    特徴項目数。1件でもFAILなら次に進まない（呼び出し側で止める）。
    ★名寄せスコアが低いことはFAILにしない（登録内容の不一致ではない）。警告として出す。"""
    if not reg.search_bukken(code):
        return [f"物件コード {code} の行または『詳細』リンクが見つからない"]
    got = reg.read_registered()
    f = rec["form"]
    ng = []
    for key, want in (("bukkenNm", f["bukkenNm"]), ("heyaNo", f["heyaNo"]),
                      ("kai", f["kai"]), ("kaidate", f["kaidate"]),
                      ("heyaCnt", f["heyaCnt"]), ("madoriTypeKbnCd", f["madoriTypeKbnCd"]),
                      ("kozoShuCd", f["kozoShuCd"])):
        if want in (None, ""):
            continue
        if str(got.get(key)) != str(want):
            ng.append(f"{key}: 登録値={got.get(key)!r} 期待={want!r}")
    # 賃料・管理費・面積は [整数].[小数] の対なので数値として比べる（末尾ゼロの正規化）
    for label, k1, k2 in (("賃料", "chinryo1", "chinryo2"),
                          ("管理費", "kanrihi1", "kanrihi2"),
                          ("面積", "menseki1", "menseki2")):
        if f.get(k1) in (None, ""):
            continue
        if not _dec_eq(got.get(k1), got.get(k2), f.get(k1), f.get(k2)):
            ng.append(f"{label}: 登録値={got.get(k1)}.{got.get(k2)} "
                      f"期待={f.get(k1)}.{f.get(k2)}")
    n_img = len([i for i in rec["images"]])
    if got["_images"] != n_img:
        ng.append(f"画像枚数: 登録={got['_images']} 期待={n_img}")
    # ★名寄せスコアは**照合の不一致ではない**（2026-08-14）。
    #   登録した内容がJSONと違うかどうかとは別の話で、素材（写真の種類）が足りないだけ。
    #   ここをFAILにしていたため、谷合さんが事前に了承済みの20点の室で全体が止まった。
    #   → 読めないときだけFAIL（読めない＝照合が成立していない）。低いのは警告に留める。
    if got["_score"] is None:
        ng.append("名寄せスコアを読めない（更新画面のヘッダ表記を確認）: "
                  + str(got.get("_score_src", ""))[:120])
    elif got["_score"] < 22:
        log(f"  ⚠ 名寄せスコアが{got['_score']}点（22点未満）＝掲載枠の選定で後回しの材料。"
            f"登録内容の不一致ではないので止めない")
    # 特徴項目：サイト連動で増える分（2701 即入居可）を許容する
    allowed = derived_allowed(f)
    n_min = len(rec["tokucho"])
    n_max = n_min + len(allowed)
    if not (n_min <= got["_tokucho_n"] <= n_max):
        ng.append(f"特徴項目数: 登録={got['_tokucho_n']} 期待={n_min}〜{n_max}"
                  f"（この室のサイト連動分 {list(allowed)} を許容）")
    log(f"  登録値: 名={got['bukkenNm']!r} {got['heyaNo']}号室 {got['kai']}階/{got['kaidate']}階建 "
        f"賃料{got['chinryo1']}.{got['chinryo2']}万 面積{got['menseki1']}.{got['menseki2']}㎡")
    log(f"  登録済み画像{got['_images']}枚（登録予定{got.get('_images_pending')}枚） / "
        f"名寄せスコア{got['_score']}点 / 特徴項目{got['_tokucho_n']}件")
    return ng


def serve(reg: Reg, a, log):
    """常駐モード：ブラウザを開いたまま、コマンドファイル経由で1室ずつ処理する。

    ★なぜ常駐にするか：1コマンド1室の設計だとPythonプロセスの終了でブラウザが閉じ、
      **セッションCookieがプロファイルに残らないため毎回ログインが必要になる**（実測で消えた）。
      14棟ぶん座ってもらう前提では、ログインは1回で済ませないと現実的でない。
    ★プロトコル（テキストファイル1行）：
        fill:<JSONパス>        … その室を埋める（送信はしない）
        verify:<コード>:<JSONパス> … 登録後の照合
        quit                  … 終了
      実行するとコマンドファイルは消し、結果は結果ファイルに追記する。
    """
    cmd = Path(a.cmd_file)
    res = Path(a.result_file)
    res.parent.mkdir(parents=True, exist_ok=True)

    t_start = time.time()

    def emit(text):
        # ★経過秒を必ず付ける。「即終了したのか待ってから終了したのか」がログで区別できないと
        #   原因の切り分けができない（実際にこれで詰まった）。
        line = f"[{time.time() - t_start:6.1f}s] {text}"
        log(line)
        with res.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ログイン待ち（この1回だけ）
    reg.page.goto(a.url, wait_until="load")
    if reg.page.frame(name="navi") is None and a.autologin:
        # ★ブラウザの自動入力が効いているならボタンを押すだけで入れる（値には触らない）
        if try_autologin(reg.page, emit):
            reg.page.wait_for_timeout(4000)
    if reg.page.frame(name="navi") is None:
        emit(f"[待機] ログイン画面です。この窓でログインしてください（最大{a.login_wait}秒）")
        emit("[待機] ★タブは増やさないこと（罠3＝2タブでセッションが壊れる）")
        t0 = time.time()
        n = 0
        while time.time() - t0 < a.login_wait:
            if reg.page.is_closed():
                emit("[NG] ブラウザの窓が閉じられました（再実行してください）")
                return 2
            if reg.page.frame(name="navi") is not None:
                break
            reg.page.wait_for_timeout(2000)
            n += 1
            if n % 15 == 0:
                emit(f"[待機] まだログイン画面です（{time.time()-t0:.0f}秒経過 / "
                     f"上限{a.login_wait}秒）")
    if reg.page.frame(name="navi") is None:
        emit(f"[NG] ログインを確認できませんでした（{time.time()-t_start:.0f}秒待った）")
        return 2
    emit("[OK] ログイン確認。コマンド待機に入ります（ブラウザは開いたままにします）")

    t_idle = time.time()
    while time.time() - t_idle < a.serve_timeout:
        if not cmd.exists():
            reg.page.wait_for_timeout(1500)
            continue
        line = cmd.read_text(encoding="utf-8").strip()
        cmd.unlink()
        t_idle = time.time()
        if line == "quit":
            emit("[OK] 終了します")
            break
        try:
            if line.startswith("fill:"):
                jp = Path(line[5:].strip())
                rec = json.loads(jp.read_text(encoding="utf-8"))
                emit(f"[開始] fill {rec['key']}")
                if not rec["gate"]["ok"]:
                    emit(f"[NG] ゲートで停止: {' / '.join(rec['gate']['block'])}")
                    continue
                ng = fill_one(reg, rec, jp, log, tanto=a.tanto)
                if ng:
                    emit(f"[NG] 未解決 {len(ng)}件（送信しないこと）")
                    for x in ng:
                        emit(f"      - {x}")
                else:
                    emit(f"[OK] {rec['key']} 埋め込みと照合すべて通過")
                emit(f"[情報] 画像{reg.visible_delete_buttons()}枚 / {reg.score()}")
                # ★キャッチは自由入力しない：実測でSUUMOが特徴項目から組み替えていた
                #   （入れた文字列が更新画面で『バストイレ別/エアコン/…』に置き換わっていた）。
                emit("[人] 交通入力・元付担当者名・元付確認日 → 目視 → 『確認画面へ』→『登録』")
                emit("[人] ★キャッチは入れない（SUUMOが特徴項目から組む・実測）")
            elif line.startswith("verify:"):
                _, code, jp = line.split(":", 2)
                rec = json.loads(Path(jp.strip()).read_text(encoding="utf-8"))
                emit(f"[開始] verify {rec['key']} コード={code}")
                ng = verify_room(reg, rec, code.strip(), log)
                if ng:
                    emit(f"[NG] 照合FAIL {len(ng)}件（次の室に進まないこと）")
                    for x in ng:
                        emit(f"      - {x}")
                else:
                    emit(f"[OK] {rec['key']} 照合PASS（全項目一致）")
            elif line.startswith("savetransit:"):
                # 棟の1室目で人が『らくらく交通入力』した結果を、その棟の交通として保存する
                jp = Path(line.split(":", 1)[1].strip())
                rec = json.loads(jp.read_text(encoding="utf-8"))
                lines = reg.read_transit()
                if not lines:
                    emit("[NG] 今のフォームに交通が入っていない（先に『らくらく交通入力』を）")
                else:
                    tp = transit_path(jp, rec["form"]["bukkenNm"])
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(json.dumps(lines, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                    emit(f"[OK] 交通を保存 {tp.name}: " + " / ".join(
                        f"{r.get('pkgEnsenNmDisp','')} {r.get('pkgEkiNmDisp','')} "
                        f"徒歩{r.get('shoyoTime','')}分" for r in lines))
            elif line.startswith("submit:"):
                # 『確認画面へ』→『登録』まで通す。押す前に実DOMを検証する
                jp = Path(line.split(":", 1)[1].strip())
                rec = json.loads(jp.read_text(encoding="utf-8"))
                code, sng = submit_room(reg, log, expect_images=len(rec["images"]))
                if sng:
                    emit(f"[NG] 登録しなかった（{len(sng)}件）")
                    for x in sng:
                        emit(f"      - {x}")
                else:
                    emit(f"[OK] {rec['key']} 登録完了 物件コード={code}")
                    vng = verify_room(reg, rec, code, log)
                    if vng:
                        emit(f"[NG] 照合FAIL {len(vng)}件（次の室に進まないこと）")
                        for x in vng:
                            emit(f"      - {x}")
                    else:
                        emit(f"[OK] {rec['key']} 照合PASS（全項目一致） コード={code}")
            elif line.startswith("room:"):
                # fill → submit → verify を一気に通す（交通が保存済みの棟のみ）
                jp = Path(line.split(":", 1)[1].strip())
                rec = json.loads(jp.read_text(encoding="utf-8"))
                emit(f"[開始] room {rec['key']}")
                if not rec["gate"]["ok"]:
                    emit(f"[NG] ゲートで停止: {' / '.join(rec['gate']['block'])}")
                    continue
                # ★交通が未保存の棟（＝棟の1室目）は、埋めてからモーダルを自動操作して
                #   交通を作り、その値を棟の交通として保存する。2室目以降は複製で足りる。
                tp = transit_path(jp, rec["form"]["bukkenNm"])
                first_of_building = not tp.is_file()
                fng = fill_one(reg, rec, jp, log, tanto=a.tanto)
                if first_of_building:
                    emit(f"[情報] この棟の1室目。らくらく交通入力を自動操作する")
                    lines, tng = auto_transit(reg, log)
                    if tng:
                        emit(f"[NG] 交通の自動入力に失敗（{len(tng)}件・登録しない）")
                        for x in tng:
                            emit(f"      - {x}")
                        continue
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(json.dumps(lines, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                    emit("[OK] 交通を自動入力して保存: " + " / ".join(
                        f"{r.get('pkgEnsenNmDisp','')} {r.get('pkgEkiNmDisp','')} "
                        f"徒歩{r.get('shoyoTime','')}分" for r in lines))
                if fng:
                    emit(f"[NG] 埋め込みで未解決 {len(fng)}件（登録しない）")
                    for x in fng:
                        emit(f"      - {x}")
                    continue
                code, sng = submit_room(reg, log, expect_images=len(rec["images"]))
                if sng:
                    emit(f"[NG] 登録しなかった（{len(sng)}件）")
                    for x in sng:
                        emit(f"      - {x}")
                    continue
                vng = verify_room(reg, rec, code, log)
                if vng:
                    emit(f"[NG] 照合FAIL {len(vng)}件 コード={code}")
                    for x in vng:
                        emit(f"      - {x}")
                else:
                    emit(f"[OK] {rec['key']} 完了 コード={code} 照合PASS")
            elif line.startswith("autotransit:"):
                jp = Path(line.split(":", 1)[1].strip())
                rec = json.loads(jp.read_text(encoding="utf-8"))
                emit(f"[開始] autotransit {rec['key']}")
                lines, tng = auto_transit(reg, log)
                if tng:
                    emit(f"[NG] 交通の自動入力に失敗（{len(tng)}件）")
                    for x in tng:
                        emit(f"      - {x}")
                else:
                    tp = transit_path(jp, rec["form"]["bukkenNm"])
                    tp.parent.mkdir(parents=True, exist_ok=True)
                    tp.write_text(json.dumps(lines, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                    emit(f"[OK] 交通を自動入力して保存 {tp.name}: " + " / ".join(
                        f"{r.get('pkgEnsenNmDisp','')} {r.get('pkgEkiNmDisp','')} "
                        f"徒歩{r.get('shoyoTime','')}分" for r in lines))
            elif line.startswith("modalrecon"):
                # 『らくらく交通入力』のポップアップを開いて構造を読む（読み取りのみ）。
                # ★ポップアップは新しいpageとして開くので、context側で受け取る必要がある。
                #   serveのreg.pageからは見えない＝専用のコマンドにする。
                before = set(id(pg) for pg in reg.page.context.pages)
                btn = reg.main.locator('#rakurakuKotsu')
                if btn.count() == 0:
                    emit("[NG] #rakurakuKotsu が無い（新規登録フォームを開いてから実行する）")
                else:
                    with reg.page.context.expect_page(timeout=20000) as pi:
                        btn.first.click()
                    pop = pi.value
                    pop.wait_for_load_state("load")
                    # ★JSで描画されるので load だけでは空。中身が出るまで待つ。
                    for _ in range(20):
                        n = pop.evaluate("() => document.querySelectorAll('input,select').length")
                        if n:
                            break
                        pop.wait_for_timeout(500)
                    n_el = pop.evaluate("() => document.querySelectorAll('*').length")
                    emit(f"[情報] モーダル url={pop.url[-52:]} title={pop.title()!r} "
                         f"要素数={n_el}")
                    try:
                        pop.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:  # noqa: BLE001
                        pass
                    info = pop.evaluate("""() => {
                        const vis = e => { const r = e.getBoundingClientRect();
                            return r.width > 0 && r.height > 0; };
                        const f = Array.from(document.querySelectorAll('input,select'))
                          .filter(vis)
                          .filter(e => (e.type||'') !== 'password')
                          .map(e => ({tag:e.tagName, type:e.type||'', name:(e.name||'').slice(0,46),
                                      id:e.id||'', opts: e.tagName==='SELECT'? e.options.length : null,
                                      ro: e.readOnly ? 1 : 0}));
                        const b = Array.from(document.querySelectorAll('input[type=image],input[type=button],img,div.spbtn,a[onclick]'))
                          .filter(vis)
                          .map(e => [e.tagName, e.id||'', e.alt||'', e.value||'',
                                     (e.className||'').toString().slice(0,18),
                                     (e.getAttribute('onclick')||'').slice(0,38)].join('|'));
                        return {欄: f.slice(0,26), ボタン: b.slice(0,18),
                                本文: (document.body.innerText||'').replace(/\s+/g,' ').slice(0,240),
                                frames: window.frames.length};
                    }""")
                    for k, v in info.items():
                        if isinstance(v, list):
                            emit(f"[情報] {k}:")
                            for x in v:
                                emit(f"      {x}")
                        else:
                            emit(f"[情報] {k}: {v}")
                    pop.close()
                    emit("[OK] モーダルを閉じた（何も変更していない）")
            elif line.startswith("eval:"):
                # ★診断用。常駐を止めずに現在の画面を読めるようにする
                #   （選択子を1つ直すたびに再起動＝再ログインになるのを避けるため）。
                js = line[5:]
                emit(f"[情報] eval → {reg.main.evaluate(js)!r}"[:1500])
            elif line.startswith("cleanup"):
                # 汚れたフォームを空に戻す（画像を全削除し、特徴項目のチェックを全部外す）
                reg.delete_all_images()
                reg.main.evaluate("""() => document.querySelectorAll(
                    'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked')
                    .forEach(e => e.click())""")
                n_tok = reg.main.evaluate("""() => document.querySelectorAll(
                    'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked').length""")
                emit(f"[OK] cleanup 完了（登録予定画像{reg.visible_delete_buttons()}枚／"
                     f"特徴項目{n_tok}件）")
            elif line.startswith("buttons"):
                emit("[情報] 画面のボタン一覧")
                for b in reg.dump_buttons():
                    emit(f"      {b['tag']:<6} id={b['id']:<16} title={b['title']:<14} "
                         f"text={b['text'][:16]}")
            else:
                emit(f"[NG] 不明なコマンド: {line[:40]}")
        except Exception as e:  # noqa: BLE001  ★1コマンドの失敗で常駐を落とさない
            emit(f"[NG] {type(e).__name__}: {str(e)[:200]}")
            try:
                shot = res.parent / f"error_{int(time.time())}.png"
                reg.page.screenshot(path=str(shot))
                emit(f"[情報] スクリーンショット {shot}")
            except Exception:  # noqa: BLE001
                pass
    else:
        emit("[NG] 待機時間切れ")
    return 0


def transit_path(json_path: Path, bukken_nm: str) -> Path:
    """棟ごとの交通の保存先。棟名（物件名）で1ファイル。"""
    d = json_path.resolve().parent / "_transit"
    safe = "".join(c for c in bukken_nm if c not in '/\\:*?"<>|')
    return d / f"{safe}.json"


def fill_one(reg: Reg, rec: dict, json_path: Path, log, tanto="担当者"):
    """1室を埋める（送信はしない）。未解決の一覧を返す。"""
    for w in rec["gate"]["warn"]:
        log(f"  ⚠ {w}")
    log("① 新規物件登録フォームを開く")
    open_new_form(reg, None, log, navigate=False)
    reg.assert_fresh_form()
    log("② フィールドを埋める")
    ng = reg.fill_form(rec["form"])
    log("③ 住所")
    ng += reg.fill_address(rec["form"])
    ng += reg.fill_aza(rec["form"].get("_chome"))
    log("④ 元付の担当者・確認日")
    #  ★先物では元付4項目すべて必須（実機のエラーで判明）。会社名とTELはJSON由来、
    #    担当者は固定文字列、確認日は客付版PDFのDL日（U1が form に入れている）。
    for k, v in (("mototsukeTantoNm", tanto),
                 ("mototsukeKakuninDate", rec["form"].get("mototsukeKakuninDate", ""))):
        if not v:
            ng.append(f"{k} が空（先物では必須）")
            continue
        ok, got, msg = reg.set_field(k, v)
        if not ok:
            ng.append(f"{k}: 期待={v!r} 実際={got!r} {msg}")
    log("⑤ 交通（同一棟の保存値を複製）")
    tp = transit_path(json_path, rec["form"]["bukkenNm"])
    if tp.is_file():
        ng += reg.apply_transit(json.loads(tp.read_text(encoding="utf-8")))
    else:
        log(f"  ⚠ この棟の交通が未保存（{tp.name}）。"
            "人が『らくらく交通入力』をしてから savetransit を実行すること")
    log("⑥ 特徴項目")
    ng += reg.check_tokucho(rec["tokucho"], derived_allowed(rec["form"]))
    log(f"⑦ 画像 {len(rec['images'])}枚")
    ng += reg.upload_images(rec["images"], json_path.resolve().parent)
    log("⑧ 読み戻し照合")
    ng += reg.readback(rec["form"])
    return ng


def main(argv=None):
    ap = argparse.ArgumentParser(description="SUUMO新規物件登録フォームを埋める（送信はしない）")
    ap.add_argument("--login", action="store_true",
                    help="ブラウザを開くだけ。人がログインする（パスワードは人が入れる）")
    ap.add_argument("--fill", help="埋める対象の1室JSON（suumo_fields.py の出力）")
    ap.add_argument("--url", default=SUUMO_URL, help="対象URL（モック検証時に差し替える）")
    ap.add_argument("--headless", action="store_true", help="画面を出さない（モック検証用）")
    ap.add_argument("--profile", default=str(PROFILE_DIR), help="ブラウザプロファイルの場所")
    ap.add_argument("--sentinel", default="",
                    help="このファイルが作られるまで待つ（交通入力の完了合図）")
    ap.add_argument("--sentinel-timeout", type=int, default=1800, help="合図待ちの上限秒")
    ap.add_argument("--login-wait", type=int, default=600, help="--login でのログイン待ち上限秒")
    ap.add_argument("--keep-open", type=int, default=0,
                    help="処理後にブラウザを開いたまま保つ秒数（人が交通入力・確認をする時間）")
    ap.add_argument("--autologin", action="store_true",
                    help="ブラウザの自動入力が効いている場合にログインボタンを押す"
                         "（パスワードは読まない・入力しない）")
    ap.add_argument("--tanto", default="担当者",
                    help="元付担当者名に入れる文字列（既定 担当者）")
    ap.add_argument("--serve", action="store_true",
                    help="常駐モード：ログイン1回でブラウザを開いたまま、コマンドファイルで1室ずつ処理する")
    ap.add_argument("--cmd-file", default="/tmp/suumo_cmd", help="常駐モードのコマンドファイル")
    ap.add_argument("--result-file", default="/tmp/suumo_result.log",
                    help="常駐モードの結果ファイル")
    ap.add_argument("--serve-timeout", type=int, default=14400,
                    help="常駐モードで無操作のまま待つ上限秒（既定4時間）")
    ap.add_argument("--verify", metavar="CODE",
                    help="登録後の照合：12桁の物件コードで再検索して突き合わせる（--fillのJSONが必要）")
    ap.add_argument("--dump-buttons", action="store_true",
                    help="画面のボタン一覧を出す（未知の画面でどれを押すか人が決めるため）")
    ap.add_argument("--to-confirm", action="store_true",
                    help="埋め込みが全部通ったら『確認画面へ』まで押す（★最終の『登録』は押さない）")
    ap.add_argument("--skip-fresh-check", action="store_true",
                    help="フォームが空であることの検証を飛ばす（既定はしない＝罠1の防止）")
    a = ap.parse_args(argv)
    if not a.login and not a.fill and not a.serve:
        ap.error("--login / --fill / --serve のいずれかを指定してください")
    if a.verify and not a.fill:
        ap.error("--verify には照合の基準になる --fill のJSONも必要です")

    def log(m):
        print(m, flush=True)

    from playwright.sync_api import sync_playwright

    prof = Path(a.profile).expanduser()
    prof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(prof), headless=a.headless, viewport={"width": 1400, "height": 950},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # ★ダイアログは必ず受け止める。放置すると以降の操作が全部固まる
        page.on("dialog", lambda d: (log(f"  [dialog] {d.type}: {d.message[:60]} → accept"),
                                     d.accept()))
        reg = Reg(page, log)
        rc = 0
        try:
            if a.serve:
                return serve(reg, a, log)
            if a.login:
                page.goto(a.url, wait_until="load")
                log(f"ブラウザを開きました。ログインしてください（最大{a.login_wait}秒待ちます）")
                log("★このウィンドウで操作してください。タブは増やさないこと（罠3＝2タブでセッションが壊れる）")
                t0 = time.time()
                while time.time() - t0 < a.login_wait:
                    if page.frame(name="navi") is not None:
                        log(f"ログインを確認しました（{time.time()-t0:.0f}秒）")
                        break
                    page.wait_for_timeout(2000)
                else:
                    log("★ログインを確認できませんでした")
                    rc = 2
                log(f"プロファイル: {prof}（次回以降はログイン不要）")
                if a.keep_open:
                    page.wait_for_timeout(a.keep_open * 1000)
                return rc

            rec = json.loads(Path(a.fill).read_text(encoding="utf-8"))
            key = rec["key"]
            log(f"■ {key}")
            if a.verify:
                log(f"照合のみ（物件コード {a.verify}）")
                page.goto(a.url, wait_until="load")
                if page.frame(name="navi") is None:
                    log(f"  ログイン画面です。ログインしてください（最大{a.login_wait}秒）")
                    t0 = time.time()
                    while page.frame(name="navi") is None and time.time() - t0 < a.login_wait:
                        page.wait_for_timeout(2000)
                ng = verify_room(reg, rec, a.verify, log)
                if ng:
                    log(f"\n✗ 照合FAIL {len(ng)}件（次の室に進まないこと）:")
                    for x in ng:
                        log(f"   - {x}")
                    return 1
                log("\n✅ 照合PASS（全項目一致）")
                return 0
            if not rec["gate"]["ok"]:
                log(f"✗ この室はゲートで止まっています: {' / '.join(rec['gate']['block'])}")
                return 2
            for w in rec["gate"]["warn"]:
                log(f"  ⚠ {w}")

            log("① 新規物件登録フォームを開く")
            open_new_form(reg, a.url, log, login_wait=a.login_wait)
            if not a.skip_fresh_check:
                reg.assert_fresh_form()

            log("② フィールドを埋める")
            ng = reg.fill_form(rec["form"])
            log("③ 住所（郵便番号から自動入力）")
            ng += reg.fill_address(rec["form"])
            ng += reg.fill_aza(rec["form"].get("_chome"))

            log("④ 特徴項目")
            ng += reg.check_tokucho(rec["tokucho"], derived_allowed(rec["form"]))

            log(f"⑤ 画像 {len(rec['images'])}枚（サムネイル表示を待ちながら投入）")
            ng += reg.upload_images(rec["images"], Path(a.fill).resolve().parent)

            log("⑥ 読み戻し照合")
            back = reg.readback(rec["form"])
            log(f"  名寄せスコア表示: {reg.score()}")
            if back:
                log(f"  ★読み戻しで不一致 {len(back)}件")
                for b in back:
                    log(f"     {b}")
            ng += back

            if ng:
                log(f"\n✗ 未解決 {len(ng)}件（送信しないこと）:")
                for x in ng:
                    log(f"   - {x}")
                rc = 1
            else:
                log("\n✅ 埋め込みと照合はすべて通りました")

            if a.dump_buttons:
                log("\n【画面のボタン一覧】")
                for b in reg.dump_buttons():
                    log(f"   {b['tag']:<6} id={b['id']:<16} title={b['title']:<14} "
                        f"text={b['text']:<14} cls={b['cls'][:28]}")

            if a.to_confirm and not ng:
                btn = reg.find_button("確認画面へ")
                if btn is None:
                    log("★『確認画面へ』が見つからない（div.spbtn[title] を確認すること）")
                    for b in reg.dump_buttons():
                        log(f"   {b['tag']} id={b['id']} title={b['title']} text={b['text']}")
                    rc = rc or 1
                else:
                    log("⑦ 『確認画面へ』を押す（★最終の『登録』は押しません）")
                    btn.click()
                    page.wait_for_timeout(4000)
                    log(f"  遷移先: {reg.main.url[-46:]}")
                    log("\n【確認画面のボタン一覧】★どれが『登録』かを人が確認してください")
                    for b in reg.dump_buttons():
                        log(f"   {b['tag']:<6} id={b['id']:<16} title={b['title']:<14} "
                            f"text={b['text']:<16} onclick={b['onclick'][:34]}")

            log("\n─── ここから人の作業 ───────────────────────────")
            log("1) 「らくらく交通入力」を押して交通を入力（コード直指定は入力エラーになる）")
            log("2) 元付担当者名・元付確認日・キャッチを入力")
            log("3) 画面を目視（賃料・面積・階・画像とカテゴリ）")
            log("4) 「確認画面へ」を押す ★このスクリプトは押しません")
            if a.sentinel:
                sent = Path(a.sentinel)
                log(f"\n合図待ち: {sent} が作られるまで最大{a.sentinel_timeout}秒待ちます")
                t0 = time.time()
                while not sent.exists() and time.time() - t0 < a.sentinel_timeout:
                    page.wait_for_timeout(3000)
                if sent.exists():
                    log(f"合図を受けました（{time.time()-t0:.0f}秒）")
                    log(f"  画像枚数: {reg.visible_delete_buttons()}枚")
                    log(f"  名寄せスコア表示: {reg.score()}")
                else:
                    log("★合図が来ないまま時間切れ")
                    rc = rc or 3
            elif a.keep_open:
                log(f"\nブラウザを{a.keep_open}秒開いたままにします")
                page.wait_for_timeout(a.keep_open * 1000)
        except Exception as e:  # noqa: BLE001  何が起きたかを必ず出す
            log(f"\n✗ 中断: {type(e).__name__}: {e}")
            try:
                shot = Path(a.profile).expanduser() / f"error_{int(time.time())}.png"
                page.screenshot(path=str(shot), full_page=False)
                log(f"  スクリーンショット: {shot}")
            except Exception:  # noqa: BLE001
                pass
            rc = 2
        finally:
            ctx.close()
        return rc


if __name__ == "__main__":
    sys.exit(main())
