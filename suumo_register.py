#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""suumoreg-v1 U3: SUUMO新規物件登録フォームを埋める（Playwright）。

    # ① 初回だけ：ブラウザを開いて谷合さんがログインする（パスワードは人が入れる）
    python3 suumo_register.py --login

    # ② 1室を埋める（交通入力の直前まで自動・送信はしない）
    python3 suumo_register.py --fill "06_登録データ/S-RESIDENCE難波大国町Uno_903.json"

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
    def visible_delete_buttons(self):
        """可視な up_del_* の数＝アップ済み画像の枚数。
        ★実測：各枠に up_del_<slot> が常設され、画像がある枠だけ可視になる。
          サムネイル表示の完了もこれで判定できる（サムネと同時に可視化される）。"""
        return self.main.locator("[id^=up_del_]:visible").count()

    def assert_fresh_form(self):
        """罠1：新規フォームが前物件の内容を引き継いでいないこと。
        物件名が空・特徴項目0件・画像0枚を実DOMで確認する（画面の見た目では判断しない）。"""
        nm = self.loc("bukkenNm").input_value()
        tok = self.main.locator(
            'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked').count()
        img = self.visible_delete_buttons()
        self.log(f"  フォーム初期状態: 物件名={nm!r} 特徴項目={tok}件 画像={img}枚")
        if nm or tok or img:
            raise RuntimeError(
                f"新規フォームが空でない（物件名={nm!r}／特徴項目{tok}件／画像{img}枚）。"
                "前物件の内容が残っている可能性があるので中止する")

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
        first.fill(str(value))
        got = first.input_value()
        return (got == str(value)), got, ""

    def fill_form(self, form: dict):
        """JSONのformを全部埋める。_で始まるキー（原文の控え）は飛ばす。"""
        ng = []
        for key, value in form.items():
            if key.startswith("_"):
                continue
            ok, got, msg = self.set_field(key, value)
            if not ok:
                ng.append(f"{key}: 期待={value!r} 実際={got!r} {msg}")
        return ng

    def fill_address(self, form: dict):
        """郵便番号 →「郵便番号から住所を入力」→ 丁目を選ぶ（§3-3：郵便番号入力が確実）。"""
        if not form.get("yubinNo1"):
            return ["郵便番号が無いため住所を入れられない"]
        btn = self.main.locator('input[type=button][value*="郵便番号"]')
        if btn.count() == 0:
            return ["『郵便番号から住所を入力』ボタンが見つからない"]
        btn.first.click()
        self.page.wait_for_timeout(1200)
        got = {k: self.main.locator(f'[name="${{{k}}}"]').input_value()
               for k in ("todofukenCd", "shigunkuCd", "chosonCd")
               if self.main.locator(f'[name="${{{k}}}"]').count()}
        self.log(f"  住所の自動入力: {got}")
        if not any(got.values()):
            return ["郵便番号から住所が入らなかった"]
        return []

    def check_tokucho(self, codes):
        """特徴項目のチェックを入れる。value＝SUUMOのコード。"""
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
        got = self.main.locator(
            'input[name="${bukkenInputForm.categoryTokuchoCd}"]:checked').count()
        self.log(f"  特徴項目: {got}/{len(codes)}件チェック")
        if got != len(codes):
            ng.append(f"特徴項目のチェック数が合わない（期待{len(codes)}／実際{got}）")
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
            self.main.locator("[id^=up_del_]:visible").first.click()
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

    def score(self):
        """画面の名寄せスコア表示を読む（実フォームの左上にある）。"""
        try:
            t = self.main.get_by_text("名寄せスコア").first.inner_text()
            return t.replace("\n", " ").strip()[:40]
        except Exception:  # noqa: BLE001
            return "(取得できず)"


# ── フロー ────────────────────────────────────────────────────
def open_new_form(reg: Reg, url: str, log):
    """メニューの「新規物件登録」から入る。★直リンクは使わない（罠2＝復元入口になる）。"""
    reg.page.goto(url, wait_until="load")
    navi = reg.page.frame(name="navi")
    if navi is None:
        raise RuntimeError("naviフレームが無い（ログイン画面のままの可能性）")
    link = navi.get_by_text("新規物件登録").first
    link.click()
    reg.page.wait_for_timeout(2500)
    log(f"  main frame: {reg.main.url[-60:]}")


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
    ap.add_argument("--skip-fresh-check", action="store_true",
                    help="フォームが空であることの検証を飛ばす（既定はしない＝罠1の防止）")
    a = ap.parse_args(argv)
    if not a.login and not a.fill:
        ap.error("--login か --fill のどちらかを指定してください")

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
            if not rec["gate"]["ok"]:
                log(f"✗ この室はゲートで止まっています: {' / '.join(rec['gate']['block'])}")
                return 2
            for w in rec["gate"]["warn"]:
                log(f"  ⚠ {w}")

            log("① 新規物件登録フォームを開く")
            open_new_form(reg, a.url, log)
            if not a.skip_fresh_check:
                reg.assert_fresh_form()

            log("② フィールドを埋める")
            ng = reg.fill_form(rec["form"])
            log("③ 住所（郵便番号から自動入力）")
            ng += reg.fill_address(rec["form"])
            if rec["form"].get("_chome"):
                log(f"  ⚠ 丁目は『{rec['form']['_chome']}丁目』。azaCd の対応は実機で人が選ぶ")

            log("④ 特徴項目")
            ng += reg.check_tokucho(rec["tokucho"])

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
