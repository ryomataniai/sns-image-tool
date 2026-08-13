# -*- coding: utf-8 -*-
"""suumoreg-v1 U3: 登録スクリプトの安全性テスト（モック相手・SUUMOに触らない）。

実行: python3 test_suumo_register.py

■測ること（どれも「本番で踏むと実害が出る」経路）
  A. サムネイル待ちが効いていること
     ＝待ってから確認画面へ進めば画像が全部登録され、未登録が0件になる
     （8/12に手作業でこの待機を怠って画像が入らなかった実績がある）
  B. 前室の画像が残っている状態では画像を投入せず止まること
     （8/11に西長堀505の入力中にJINO新町の写真12枚が残り、別物件の写真で登録しかけた）
  C. 全削除が confirm ダイアログを越えて0枚にできること
     （実機の deleteGazo はソースが読めず confirm の有無が不明なので、出る前提で組んでいる）
  D. name属性が違えば読み戻し照合が捕まえること
     （getElementsByName は空を返すだけでエラーにならない＝黙って空で送信される）

■測っていないこと
  実SUUMOのフォーム挙動そのもの。モックは2026-08-13にreconで読み取った仕様
  （name形式・枠名・カテゴリselect名・up_del_*の可視化・サムネ遅延）を写したもので、
  読み取れなかった部分（deleteGazoの中身・確認画面から先）は再現していない。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
MOCK = "file://" + str(REPO / "tests" / "mock_suumo" / "index.html")

_fails = []


def _check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def _sample_json():
    """テスト用の1室JSON。実データがあればそれを使い、無ければ最小の合成データ。"""
    real = (REPO.parent / "SUUMO入稿_75枠_20260806" / "06_登録データ"
            / "S-RESIDENCE難波大国町Uno_903.json")
    if real.is_file():
        return json.loads(real.read_text(encoding="utf-8")), real
    return None, None


def _mk_images(n, tmp: Path):
    """ダミーJPEGをn枚作る（実データが無い環境でも失敗経路を測れるように）。"""
    from PIL import Image
    out = []
    for i in range(n):
        p = tmp / f"{i+1:02d}_dummy.jpg"
        Image.new("RGB", (60, 80), (200, 100 + i, 50)).save(p, format="JPEG")
        out.append(p)
    return out


def run(headless=True):
    from playwright.sync_api import sync_playwright
    import suumo_register as R

    rec, path = _sample_json()
    if rec is None:
        print("★実データ(06_登録データ)が無いためスキップ")
        return
    tmp = Path(tempfile.mkdtemp(prefix="suumoreg_test_"))
    prof = tmp / "prof"
    prof.mkdir()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(prof), headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        reg = R.Reg(page, lambda m: None)

        # ── A. サムネイル待ち → 確認画面で未登録0件 ─────────────────
        print("\n▶ A. サムネイル待ちが効いていること")
        R.open_new_form(reg, MOCK, lambda m: None)
        reg.assert_fresh_form()
        reg.fill_form(rec["form"])
        reg.check_tokucho(rec["tokucho"])
        ng = reg.upload_images(rec["images"], path.parent)
        _check("投入で未解決なし", not ng, str(ng[:2]))
        n_img = reg.visible_delete_buttons()
        _check("可視な削除ボタン数＝画像枚数", n_img == len(rec["images"]),
               f"{n_img}/{len(rec['images'])}")
        # ★実機は up_del_<slot> / up_del_div_<slot> / db_del_div_<slot> の3系統があり、
        #   [id^=up_del_] で数えると2倍になる（実機で9枚→18枚と出た）。div_ を除外できているか。
        raw = reg.main.locator("[id^=up_del_]:visible").count()
        _check("★[id^=up_del_] の素の数は2倍になる（モックが実機を写している証拠）",
               raw == 2 * len(rec["images"]), f"素={raw} / 正しい数={n_img}")
        _check("登録済み画像(db_del_div_)は0枚のまま", reg.visible_db_delete_buttons() == 0)
        # ★実機同様に div.spbtn[title="確認画面へ"] を title で探す（a/input/buttonには無い）
        btn = reg.find_button("確認画面へ")
        _check("『確認画面へ』をtitleで見つけられる", btn is not None)
        btn.click()
        page.wait_for_timeout(900)
        # ★確認画面は main フレーム内に遷移する（topのDOMには出ない）。
        #   実機も frameset なので、読み取り先を間違えると「取れない」ではなく「待ち続ける」。
        conf = reg.main
        conf.locator("#res_registered").wait_for(timeout=10000)
        regd = conf.locator("#res_registered").inner_text().split(",")
        drop = [x for x in conf.locator("#res_dropped").inner_text().split(",") if x]
        _check("確認画面で登録済み＝全枚数", len([x for x in regd if x]) == len(rec["images"]),
               f"{len([x for x in regd if x])}枚")
        _check("★未登録（サムネ未表示）が0件", not drop, str(drop))
        score = conf.locator("#res_score").inner_text()
        _check("確認画面の名寄せスコアがU1の見込みと一致",
               score == str(rec["score_hint"]), f"画面={score} / U1={rec['score_hint']}")
        tok = [x for x in conf.locator("#res_tokucho").inner_text().split(",") if x]
        _check("特徴項目が全部渡っている", sorted(tok) == sorted(rec["tokucho"]),
               f"{len(tok)}/{len(rec['tokucho'])}件")
        # 確認画面の「登録」も同じ方式で見つかること＋完了画面から物件コードを取れること
        rb = reg.find_button("登録")
        _check("確認画面の『登録』をtitleで見つけられる", rb is not None,
               str([b["title"] for b in reg.dump_buttons()]))
        if rb is not None:
            rb.click()
            page.wait_for_timeout(700)
            code = R.extract_bukken_code(reg.main.content())
            _check("完了画面から物件コード(12桁)を取れる", bool(code) and len(code) == 12, str(code))

        # ── B. 前室の画像が残っていたら投入しない ──────────────────
        print("\n▶ B. 前室の画像が残っていたら投入せず止まること")
        R.open_new_form(reg, MOCK, lambda m: None)
        dummies = _mk_images(1, tmp)
        reg.main.locator("#file_up_gaikan").set_input_files(str(dummies[0]))
        reg.wait_thumb("gaikan")
        _check("残存画像を1枚仕込んだ", reg.visible_delete_buttons() == 1)
        try:
            reg.upload_images(rec["images"], path.parent)
            _check("★残存画像があるのに投入してしまった", False)
        except RuntimeError as e:
            _check("残存画像を検知して中止した", "既存画像が1枚" in str(e), str(e)[:60])
        _check("罠1の検証も同じ状態を弾く",
               _raises(lambda: reg.assert_fresh_form(), "空でない"))

        # ── C. 全削除が confirm を越えて0枚にできること ───────────────
        print("\n▶ C. 全削除が confirm ダイアログを越えて0枚にできること")
        before = reg.visible_delete_buttons()
        n_dlg = len(dialogs)
        reg.delete_all_images()
        _check("0枚になった", reg.visible_delete_buttons() == 0, f"開始{before}枚")
        _check("confirmダイアログが実際に出て処理された", len(dialogs) > n_dlg,
               f"{len(dialogs)-n_dlg}件: {dialogs[-1][:24] if dialogs else ''}")

        # ── D. name属性が違えば読み戻しが捕まえること ────────────────
        print("\n▶ D. name属性が違えば読み戻し照合が捕まえること")
        R.open_new_form(reg, MOCK, lambda m: None)
        bad = dict(rec["form"])
        bad["bukkenNmm"] = "存在しないフィールド"      # 1文字違いのタイポを模す
        ng2 = reg.fill_form(bad)
        _check("存在しないnameを埋めようとしたら失敗として返る",
               any("bukkenNmm" in x for x in ng2), str(ng2[:1]))
        back = reg.readback(bad)
        _check("読み戻しでも同じフィールドを検出", any("bukkenNmm" in x for x in back),
               str(back[:1]))
        # 正しい方は一致する
        _check("正しいフィールドは読み戻しで一致",
               not any("bukkenNm:" in x for x in back), str([x for x in back if 'bukkenNm:' in x]))

        ctx.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn, needle):
    try:
        fn()
        return False
    except Exception as e:  # noqa: BLE001
        return needle in str(e)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    run(headless="--show" not in sys.argv)
    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    sys.exit(1 if _fails else 0)
