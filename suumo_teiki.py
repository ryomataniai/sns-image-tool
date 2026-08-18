#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""teiki-fix-v1: 定期借家の是正（物件情報更新画面で2項目だけ直す）。

    python3 suumo_teiki.py --recon --codes <12桁>          # 画面と全項目を読むだけ
    python3 suumo_teiki.py --fix --codes <12桁>             # 差分検証まで（送信しない）
    python3 suumo_teiki.py --fix --codes <12桁> --confirm-write

直すのは2項目だけ:
    ${bukkenInputForm.teikiShakuyaFlg}  0 → 1
    ${bukkenInputForm.teikiShakuyaNen}  → 2
`teikiShakuyaKbnCd` と `teikiShakuyaGetsu` は**意味が未確認なので触らない**（差分では
「変わっていないこと」を確認する側に入れる）。

■★この画面の最大のリスク：**物件情報更新は物件の全項目を持つフォームで、登録すると全部が
  再送信される。**定期借家だけ変えたつもりで他の項目が壊れる／消える恐れがある。
  → 恒久ルール5のとおり、**変更前後の全項目を差分して、意図した2項目以外が
    変わっていないことを検証する。**意図外の変化があれば例外で止めて送信しない。

■★物件コードは正本（SUUMO進行管理.csv）から取る。**番号の近さから推測してはいけない。**
  2026-08-17 に、元付確認一覧のチェックボックスIDの並び（confirm…983 / …988 / …991）から
  是正したい2室のコードを隣接番号だと推測して間違えた。実際は
  …988 = 同棟の別室A、…991 = 同棟の別室B で、**どちらも掲載中の別室**。
  そのまま書いていれば掲載中2室の全項目を書き換えていた。
  ★このリポは Public なので棟名・号室は伏せてある。教訓は「IDの並びからコードを
    推測したら掲載中の別室だった」ことで、どの棟かは教訓に寄与しない。
  → **書き込み前に画面の物件名・号室を必ず突き合わせる。**正本の一致は
    「SUUMO上でそのコードがその室である」ことを保証しない（登録時に記録した値であって、
    SUUMOから読み返した値ではないため）。

■恒久ルールは suumo_keisai.py の冒頭を見ること（2段階・click()・4系統・情報が無い/問題が無い・全項目差分）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import suumo_keisai as K

# 是正する2項目
FLD_FLG = "${bukkenInputForm.teikiShakuyaFlg}"
FLD_NEN = "${bukkenInputForm.teikiShakuyaNen}"
# ★teikiShakuyaKbnCd は「期間期限」のラジオ（1=期間 / 2=期限）。2026-08-17 に判明。
#   行の文脈: 『契約期間 普通借家/定期借家  期間期限 西暦 年 ヶ月 月まで』
#   定期借家に切り替えると**サイト側のJSが既定の 1（期間）を入れる**（触っていないのに変わる）。
#   マイソクは『定期借家 2年間』＝長さでの指定なので 1（期間）が正しい。
#   ★名前に反して定期借家専用ではない：普通借家の室（実測1室・棟名はPublicリポのため伏せる）でも
#     flg=0 なのに kbnCd=1 / nen="2" だった＝契約期間の指定方法を表す汎用の項目。
#   → **意図した変更に含める**（0に戻すと期間も期限も未選択になり入力として不完全）。
FLD_KBN = "${bukkenInputForm.teikiShakuyaKbnCd}"
# ★teikiShakuyaGetsu（ヶ月）は空のまま。マイソクが「2年間」なので月数は不要。
#   先回りして 0 を入れない。必須なら確認画面でエラーが出るので、そこで分かる。
FLD_GETSU = "${bukkenInputForm.teikiShakuyaGetsu}"

# 行の「詳細」リンクを押して物件情報更新へ。★§3 の実測どおり
#   javascript:dispChangeShousai('UPD1R3104.action', ...) 形式
OPEN_DETAIL_JS = """(code) => {
  const d = window.frames['main'].document;
  const pick = (tr, suffix) => {
    const e = [...tr.querySelectorAll('input[type=hidden]')].find(
        x => (x.name || '').includes('.' + suffix + '}'));
    return e ? e.value : null;
  };
  let target = null;
  d.querySelectorAll('input[name=changeShiji]').forEach(c => {
    const tr = c.closest('tr');
    if (tr && pick(tr, 'bukkenCd') === code) target = tr;
  });
  if (!target) return {ok: false, why: 'その物件コードの行が無い'};
  const a = [...target.querySelectorAll('a')].find(
      e => (e.textContent || '').replace(/\\s+/g, '') === '詳細');
  if (!a) return {ok: false, why: '『詳細』リンクが無い',
                  links: [...target.querySelectorAll('a')].map(
                      e => (e.textContent || '').trim().slice(0, 8))};
  a.click();
  return {ok: true, href: (a.getAttribute('href') || '').slice(0, 70)};
}"""

# ★全項目のダンプ。差分の基準になるので、**値を持つものは全部**取る。
DUMP_FORM_JS = """() => {
  const d = window.frames['main'].document;
  const out = {};
  const forms = [...d.querySelectorAll('form')].map(
      f => ({name: f.name || '', id: f.id || '',
             action: (f.getAttribute('action') || ''), elements: f.elements.length}));
  d.querySelectorAll('input,select,textarea').forEach(e => {
    const nm = e.name || e.id;
    if (!nm) return;
    const t = (e.type || e.tagName).toLowerCase();
    let v;
    if (t === 'radio' || t === 'checkbox') {
      // 同名で複数あるので「checked のものの value」を集める
      if (!e.checked) { if (!(nm in out)) out[nm] = null; return; }
      v = String(e.value);
    } else if (t === 'select-one' || t === 'select') {
      v = String(e.value);
    } else {
      v = String(e.value);
    }
    if (nm in out && out[nm] !== null && out[nm] !== v) {
      out[nm] = String(out[nm]) + '|' + v;      // 同名で値が複数（想定外）を潰さず残す
    } else {
      out[nm] = v;
    }
  });
  return {title: d.title, url: d.location.href.slice(-60), forms: forms, fields: out,
          fieldCount: Object.keys(out).length,
          bukkenNm: (d.getElementsByName('${bukkenInputForm.bukkenNm}')[0] || {}).value || null,
          heyaNo: (d.getElementsByName('${bukkenInputForm.heyaNo}')[0] || {}).value || null};
}"""

# 確定ボタンの候補。★押せるものは4系統。最下部までスクロールしてから見る（恒久ルール3）
BUTTONS_JS = """() => {
  const w = window.frames['main'];
  const d = w.document;
  w.scrollTo(0, d.body.scrollHeight);
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  return [...d.querySelectorAll(
      'input[type=button],input[type=submit],input[type=image],button,img,div.spbtn,a[onclick]')]
    .filter(vis)
    .map(e => ({tag: e.tagName, type: e.type || '', id: e.id || '', name: e.name || '',
                value: (e.value || '').slice(0, 18), alt: (e.alt || '').slice(0, 18),
                title: (e.title || '').slice(0, 18),
                cls: (e.className || '').toString().slice(0, 22),
                text: (e.children && e.children.length === 0 ? (e.textContent || '') : '')
                        .trim().slice(0, 16),
                disabled: e.getAttribute('disabled'),
                onclick: (e.getAttribute('onclick') || '').slice(0, 60),
                y: Math.round(e.getBoundingClientRect().top)}))
    .filter(x => x.value || x.alt || x.title || x.text || x.id);
}"""


def open_detail(page, code, log):
    """掲載指示一覧 → コード検索 → 詳細 → 物件情報更新。→ (ok, ダンプ)。"""
    fr, ok = K.goto_keisai(page, log)
    if not ok:
        return False, {"why": "掲載指示一覧に到達できない"}
    rows, n = K.search_code(page, code, log)
    if rows is None:
        return False, {"why": "セッション切れ"}
    hit = [r for r in rows if r["code"] == code]
    log(f"  コード検索: 該当{n}件 / 行{len(rows)}")
    for r in rows:
        log("    " + K.fmt_row(r))
    if not hit:
        return False, {"why": f"その物件コードの行が無い（該当{n}件）"}
    r = page.evaluate(OPEN_DETAIL_JS, code)
    if not r.get("ok"):
        return False, r
    page.wait_for_timeout(5000)
    dump = page.evaluate(DUMP_FORM_JS)
    log(f"  詳細: title={dump['title']!r} 物件名={dump['bukkenNm']!r} "
        f"号室={dump['heyaNo']!r} 項目数={dump['fieldCount']}")
    if dump["title"] != "物件情報更新":
        return False, {"why": f"物件情報更新に遷移していない（{dump['title']!r}）"}
    return True, dump


# ── 是正（2項目だけ変える）─────────────────────────────────────
# ★ラジオは **click()** で操作する（恒久ルール2）。value 代入では onclick が走らない。
SET_TEIKI_JS = """(arg) => {
  const d = window.frames['main'].document;
  const radios = [...d.querySelectorAll('[name="' + arg.flgName + '"]')];
  if (!radios.length) return {ok: false, why: 'teikiShakuyaFlg のラジオが無い'};
  const on = radios.find(e => String(e.value) === '1');
  if (!on) return {ok: false, why: 'value=1 のラジオが無い',
                   values: radios.map(e => e.value)};
  if (on.disabled) return {ok: false, why: 'ラジオが disabled'};
  if (!on.checked) on.click();
  const nen = d.getElementsByName(arg.nenName)[0];
  if (!nen) return {ok: false, why: 'teikiShakuyaNen の欄が無い'};
  if (nen.disabled) return {ok: false, why: 'teikiShakuyaNen が disabled'};
  nen.value = arg.nen;
  nen.dispatchEvent(new Event('input', {bubbles: true}));
  nen.dispatchEvent(new Event('change', {bubbles: true}));
  nen.dispatchEvent(new Event('blur', {bubbles: true}));
  return {ok: true, flg: (radios.find(e => e.checked) || {}).value,
          nen: nen.value, nenDisabled: nen.disabled,
          radioCount: radios.length};
}"""

# 「確認画面へ」。★div.spbtn[title] 系。同じ画面に「物件削除」もあるので title 完全一致で選ぶ。
CLICK_KAKUNIN_JS = """() => {
  const d = window.frames['main'].document;
  const b = d.getElementById('regButton2');
  if (!b) return {ok: false, why: 'div#regButton2（確認画面へ）が無い'};
  if ((b.getAttribute('title') || '') !== '確認画面へ') {
    return {ok: false, why: 'regButton2 の title が『確認画面へ』でない',
            title: b.getAttribute('title')};
  }
  b.click();
  return {ok: true};
}"""

# 確認画面のダンプ。★影響範囲が書かれている可能性がある（掲載指示の確認画面はそうだった）
CONFIRM_DUMP_JS = """() => {
  const d = window.frames['main'].document;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = (d.body.innerText || '').replace(/\\s+/g, ' ');
  return {
    title: d.title, url: d.location.href.slice(-50),
    text: txt.slice(0, 700),
    errors: [...d.querySelectorAll('.error,.err,[class*=error],[class*=Error]')]
        .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90))
        .filter(Boolean).slice(0, 8),
    buttons: [...d.querySelectorAll(
        'input[type=button],input[type=submit],input[type=image],button,img,div.spbtn,a[onclick]')]
      .filter(vis)
      .map(e => ({tag: e.tagName, type: e.type || '', id: e.id || '',
                  value: (e.value || '').slice(0, 18), alt: (e.alt || '').slice(0, 18),
                  title: (e.title || '').slice(0, 18),
                  cls: (e.className || '').toString().slice(0, 22),
                  text: (e.children && e.children.length === 0 ? (e.textContent || '') : '')
                          .trim().slice(0, 16),
                  disabled: e.getAttribute('disabled'),
                  onclick: (e.getAttribute('onclick') || '').slice(0, 60)}))
      .filter(x => x.value || x.alt || x.title || x.text || x.id),
    // 定期借家の表示が確認画面に出ているか
    teikiText: (txt.match(/定期借家[^ ]{0,20}/g) || []).slice(0, 4),
    // ★名寄せスコアと物件コード・室名。確認画面にしか出ない検証材料。
    //   スコアが落ちたら画像かカテゴリが壊れたということなので、
    //   483項目の差分では拾えない副作用を検出できる（2026-08-17 谷合さんの指摘）。
    score: (() => { const m = txt.match(/名寄せスコア\\s*(\\d+)\\s*点/); 
                    return m ? parseInt(m[1], 10) : null; })(),
    bukkenCd: (() => { const m = txt.match(/物件コード\\s*[:：]\\s*(\\d{12})/);
                       return m ? m[1] : null; })(),
    heyaDisp: (() => { const m = txt.match(/(\\S+号室)/); return m ? m[1] : null; })()
  };
}"""

CLICK_REGISTER_JS = """(title) => {
  const d = window.frames['main'].document;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // ★title / alt / value の**完全一致**で選ぶ。部分一致だと「物件削除」等を拾いうる
  const cands = [...d.querySelectorAll(
      'input[type=button],input[type=submit],input[type=image],button,img,div.spbtn')]
      .filter(vis)
      .filter(e => [e.getAttribute('title'), e.alt, e.value].some(
          v => (v || '').trim() === title));
  if (!cands.length) return {ok: false, why: `『${title}』が無い`};
  if (cands.length > 1) return {ok: false, why: `『${title}』が${cands.length}個ある`,
                                ids: cands.map(e => e.id || e.className)};
  const b = cands[0];
  if (b.getAttribute('disabled')) return {ok: false, why: `『${title}』が disabled`};
  b.click();
  return {ok: true, id: b.id || '', cls: (b.className || '').toString().slice(0, 24)};
}"""


# ★teikiShakuyaKbnCd が何の項目かを読む（クリックの副作用で 0→1 になったため）。
LABEL_PROBE_JS = """(names) => {
  const d = window.frames['main'].document;
  const clean = t => (t || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
  return names.map(nm => {
    const es = [...d.querySelectorAll('[name="' + nm + '"]')];
    if (!es.length) return {name: nm, exists: false};
    const e = es[0];
    // 同じ行（tr）と、直前の見出しセルからラベルを拾う
    const tr = e.closest('tr');
    const th = tr ? tr.querySelector('th') : null;
    const td = e.closest('td');
    // ラジオなら各値の直後のテキストを拾う
    const opts = es.map(x => {
      let lbl = '';
      const l = x.id ? d.querySelector('label[for="' + x.id + '"]') : null;
      if (l) lbl = clean(l.textContent);
      else if (x.nextSibling) lbl = clean(x.nextSibling.textContent || x.nextSibling.nodeValue);
      return {value: x.value, checked: x.checked, disabled: x.disabled, label: lbl,
              onclick: (x.getAttribute('onclick') || '').slice(0, 70)};
    });
    return {name: nm, exists: true, type: e.type, count: es.length,
            th: clean(th ? th.textContent : ''),
            tdText: clean(td ? td.textContent : ''),
            trText: clean(tr ? tr.textContent : ''),
            options: opts};
  });
}"""


def show_diff(log, label, pairs, limit=40):
    log(f"  {label} {len(pairs)}件")
    for k, b, a in pairs[:limit]:
        log(f"    {k}")
        log(f"      {b!r} → {a!r}")
    if len(pairs) > limit:
        log(f"    …他{len(pairs) - limit}件")


def expected_room(code):
    """正本（SUUMO進行管理.csv）でそのコードがどの室かを引く。→ (物件, 掲載指示) or None。

    ★書き込み前に**画面の物件名・号室**と突き合わせるために使う。
      正本の一致だけでは「SUUMO上でそのコードがその室」の保証にならないので、
      これは「期待値」であって「確認」ではない。確認は画面側の値で行う。
    """
    import csv
    p = (Path(__file__).resolve().parent.parent
         / "SUUMO入稿_75枠_20260806" / "SUUMO進行管理.csv")
    if not p.is_file():
        return None
    for r in csv.DictReader(p.open(encoding="utf-8-sig")):
        if code in (r.get("SUUMO登録") or ""):
            return (r.get("物件", ""), r.get("掲載指示", ""))
    return None


# ★ページを開くたびに変わる項目（2026-08-17 実測）。編集せずに詳細画面を2回開いて確認した：
#     cmAutoCreateTime : epoch ミリ秒（'1786966484842' → '1786966499256'）
#     operate_bukken   : リクエストごとのハッシュ（CSRF/操作トークンと思われる）
#   物件データではないので差分から除く。**推測で除外していない。--probe-nonce で再確認できる。**
#   ★ここに項目を足すときは必ず --probe-nonce の実測を根拠にすること。
#     「変わって当然」を思い込みで増やすと、本当の副作用を見逃す。
NONCE_FIELDS = ("cmAutoCreateTime", "operate_bukken")


def diff_fields(before: dict, after: dict, allow: dict):
    """全項目の差分。→ (意図した変化, 意図しない変化)。

    allow は {フィールド名: 期待する新しい値}。
    ★キーの増減も「意図しない変化」に数える（項目が消えるのも壊れ方の一つ）。
    """
    intended, unexpected = [], []
    for k in sorted(set(before) | set(after)):
        b, a = before.get(k, "(欄なし)"), after.get(k, "(欄なし)")
        if b == a:
            continue
        if k in NONCE_FIELDS:      # 開くたびに変わる（実測済み）
            continue
        if k in allow and a == allow[k]:
            intended.append((k, b, a))
        else:
            unexpected.append((k, b, a))
    return intended, unexpected


def verify_fields(now: dict, nen: str, before: dict | None):
    """是正済みかを判定する純関数。→ (ng理由のリスト, 意図した変化, 意図しない変化)。

    ★before が None のときは**定期借家の3項目しか見ていない**ことを呼び出し側が明示すること。
      「4項目が正しいから全部正しい」と読ませない。他項目の副作用は未確認のまま。
    ★getsu は before があるときだけ見る。無いときに「空であるべき」と決めつけない
      （実測は普通借家の1室だけで、全室がそうだとは分かっていない）。
    """
    ng = []
    for k, want in ((FLD_FLG, "1"), (FLD_NEN, nen), (FLD_KBN, "1")):
        if now.get(k) != want:
            ng.append(f"{k} が {now.get(k)!r}（期待 {want!r}）")
    if before is None:
        return ng, [], []
    allow = {FLD_FLG: "1", FLD_NEN: nen, FLD_KBN: "1"}
    intended, unexpected = diff_fields(before, now, allow)
    if unexpected:
        ng.append(f"是正前と比べて意図しない変化が{len(unexpected)}件")
    if now.get(FLD_GETSU) != before.get(FLD_GETSU):
        ng.append(f"{FLD_GETSU} が変わった（{before.get(FLD_GETSU)!r} → {now.get(FLD_GETSU)!r}）")
    return ng, intended, unexpected


def main(argv=None):
    ap = argparse.ArgumentParser(description="定期借家の是正（2項目だけ直す）")
    ap.add_argument("--recon", action="store_true", help="画面と全項目を読むだけ")
    ap.add_argument("--probe-nonce", action="store_true",
                    help="編集せずに詳細画面を2回開いて、開くたびに変わる項目を洗い出す")
    ap.add_argument("--fix", action="store_true", help="是正する（--confirm-write が無ければ送信しない）")
    ap.add_argument("--verify", action="store_true",
                    help="是正済みかを**読むだけ**で照合する（書き込みは一切しない）。"
                         "--out-dir に <code>_before.json があれば全項目の差分も取る")
    ap.add_argument("--codes", required=True, help="物件コード12桁（カンマ区切りかファイル）")
    ap.add_argument("--nen", default="2", help="定期借家の年数（既定2）")
    ap.add_argument("--to-confirm", action="store_true",
                    help="『確認画面へ』まで押して確認画面を読み、**登録せずに止める**")
    ap.add_argument("--register-label", default="登録",
                    help="確認画面の確定ボタンのラベル（完全一致で探す）")
    ap.add_argument("--confirm-write", action="store_true",
                    help="★これが無いと登録しない（差分検証までで止まる）")
    ap.add_argument("--out-dir", default=None, help="全項目ダンプの保存先")
    ap.add_argument("--autologin", action="store_true")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--profile", default=str(K.PROFILE))
    ap.add_argument("--login-wait", type=int, default=1800)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    if not (a.recon or a.fix or a.probe_nonce or a.verify):
        ap.error("--recon / --fix / --verify / --probe-nonce のどれかが要る")
    if a.verify and (a.fix or a.confirm_write or a.to_confirm):
        ap.error("--verify は読み取り専用。--fix / --to-confirm / --confirm-write と併用しない")
    log = K.log_factory()
    codes, bad = K.read_codes(a.codes)
    if bad:
        log(f"✗ 12桁でない指定: {bad[:5]}")
        return 2
    log(f"対象 {len(codes)}件: {codes}")
    results = []
    for c in codes:
        exp = expected_room(c)
        log(f"  正本の期待値 {c}: {exp}")
        if exp is None:
            log("  ★正本に無いコード。止める（番号の推測で書き込む事故を防ぐ）")
            return 2
    out_dir = Path(a.out_dir).expanduser() if a.out_dir else None

    # ★利用時間外なら**ブラウザを開く前に**止める（ログイン失敗を積まない）
    _hrs = K.service_hours_ng()
    if _hrs:
        log(f"✗ {_hrs}")
        log("  ★ログインを試さない（失敗を繰り返すとアカウントロックの恐れがある）")
        return 2

    from playwright.sync_api import sync_playwright

    prof = Path(a.profile).expanduser()
    prof.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(prof), headless=a.headless, viewport={"width": 1500, "height": 980},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: (log(f"  [dialog] {d.type}: {d.message[:70]} → accept"),
                                     d.accept()))
        try:
            page.goto(K.URL, wait_until="load")
            page.wait_for_timeout(2500)
            if K.is_login_screen(page):
                if a.autologin and K.try_autologin(page, log):
                    pass
                elif a.login:
                    # ★人がパスワードを入れる。こちらは値を読まない・出さない。
                    log(f"ブラウザを開きました。ログインしてください（最大{a.login_wait}秒）")
                    t0 = time.time()
                    while time.time() - t0 < a.login_wait and K.is_login_screen(page):
                        page.wait_for_timeout(3000)
                else:
                    log("✗ ログイン画面です。--login を付けて人が入れること")
                    return 2
            # ★ログイン成否は**画面ではなくURL**でも見る。2026-08-18 に、パスワード欄が
            #   無い login.action に留まったまま is_login_screen が False を返し、
            #   「ログイン確認」と誤って表示した（＝情報が無いのを問題が無いと読んだ）。
            if K.is_login_screen(page) or "login.action" in page.url or K.session_dead(page):
                log(f"✗ ログインを確認できない（url={page.url[-40:]}）")
                log("  ★自動で再試行しない（アカウントはmikke名義でロックの恐れがある）")
                log("  --login を付けて人がログインすること")
                return 2
            log(f"ログイン確認。url={page.url[-46:]}")
            if a.probe_nonce:
                # ★「編集していないのに変わる項目」を実測する。推測で除外しない。
                code = codes[0]
                snaps = []
                for r in range(2):
                    ok, d1 = open_detail(page, code, log)
                    if not ok:
                        log(f"  ✗ {d1}")
                        return 1
                    snaps.append(d1["fields"])
                    log(f"  {r + 1}回目のダンプ: {len(d1['fields'])}項目")
                _i, diff = diff_fields(snaps[0], snaps[1], {})
                log(f"★編集していないのに変わった項目 {len(diff)}件")
                for k, b, aa in diff:
                    log(f"    {k}: {b!r} → {aa!r}")
                log("★読み取りのみ")
                return 0
            for i, code in enumerate(codes, 1):
                log(f"[{i}/{len(codes)}] {code}")
                ok, dump = open_detail(page, code, log)
                if not ok:
                    log(f"  ✗ {dump}")
                    return 1
                exp = expected_room(code)
                # ★画面の値と正本の期待値を突き合わせる（コード取り違えの検出）
                shown = f"{dump['bukkenNm']}_{dump['heyaNo']}"
                log(f"  画面の室: {shown}  / 正本の期待: {exp[0]}")
                f = dump["fields"]
                log(f"  定期借家: flg={f.get(FLD_FLG)!r} nen={f.get(FLD_NEN)!r} "
                    f"kbn={f.get(FLD_KBN)!r} getsu={f.get(FLD_GETSU)!r}")
                # ★--verify では書かない。verify は <code>_before.json を**読む**側で、
                #   ここで上書きすると是正後の値が「是正前」として保存され、
                #   自分自身と差分を取って必ず「変化なし＝OK」になる（＝照合が意味を失う）。
                #   2026-08-18 に --verify を足したとき、この順序で実際に踏みかけた。
                if out_dir and not a.verify:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"{code}_before.json").write_text(
                        json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
                    log(f"  全項目を保存: {code}_before.json")
                if a.verify:
                    # ★読み取りのみ。ここから先のボタンは一切押さない。
                    #   --fix --confirm-write も保存直後に同じ照合をするが、それは
                    #   **その実行の中でしか動かない**。セッションが切れた／別の日に
                    #   確かめたい／手で直した室を見たい、のいずれでも回せる口が要る。
                    f2 = dump["fields"]
                    # ★before があるときだけ全項目を突き合わせる。
                    #   無いときに「4項目が正しいから全部正しい」と読まないこと。
                    bp = out_dir / f"{code}_before.json" if out_dir else None
                    before0 = (json.loads(bp.read_text(encoding="utf-8"))["fields"]
                               if bp and bp.is_file() else None)
                    ng, it3, un3 = verify_fields(f2, a.nen, before0)
                    if before0 is not None:
                        show_diff(log, "是正前との差分（意図した変化）", it3)
                        if un3:
                            show_diff(log, "★是正前との差分（意図しない変化）", un3)
                        log(f"  全{len(f2)}項目を是正前({bp.name})と突合した")
                    else:
                        log(f"  ※ {code}_before.json が無いので全項目の差分は取れない。"
                            f"定期借家の3項目だけを見ている（他項目の副作用は**未確認**）")
                    if out_dir:
                        out_dir.mkdir(parents=True, exist_ok=True)
                        (out_dir / f"{code}_verify.json").write_text(
                            json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
                    if ng:
                        for x in ng:
                            log(f"  ✗ {x}")
                        log(f"  ✗ {code} は是正できていない")
                        results.append({"code": code, "室": shown, "判定": "NG", "理由": ng})
                    else:
                        log(f"  ◯ {code} 是正済み（flg=1 / nen={a.nen} / kbn=1 / "
                            f"getsu={f2.get(FLD_GETSU)!r}）")
                        results.append({"code": code, "室": shown, "判定": "OK",
                                        "全項目突合": bool(bp and bp.is_file())})
                    continue
                if a.recon:
                    log("  ★確定ボタンの候補（最下部までスクロールして全系統）")
                    for b in page.evaluate(BUTTONS_JS):
                        log(f"    {b['tag']}[{b['type']}] id={b['id']!r} value={b['value']!r} "
                            f"alt={b['alt']!r} title={b['title']!r} text={b['text']!r} "
                            f"cls={b['cls']!r} disabled={b['disabled']!r} y={b['y']} "
                            f"onclick={b['onclick']!r}")
                    log(f"  form: {dump['forms']}")
                    continue
                # ── ここから --fix ────────────────────────────────
                before = dump["fields"]
                if before.get(FLD_FLG) == "1":
                    log("  ◯ 既に定期借家（flg=1）。何もしない")
                    continue
                # ★期待値は3項目（kbnCd はサイトが入れる既定・2026-08-17 谷合さん承認）
                allow = {FLD_FLG: "1", FLD_NEN: a.nen, FLD_KBN: "1"}
                sv = page.evaluate(SET_TEIKI_JS,
                                   {"flgName": FLD_FLG, "nenName": FLD_NEN, "nen": a.nen})
                log(f"  2項目を変更: {sv}")
                if not sv.get("ok") or sv.get("flg") != "1" or sv.get("nen") != a.nen:
                    log(f"  ✗ 変更できない: {sv}")
                    return 1
                # ★送信直前の再ダンプ＝**自分のミス検出**（意図しない欄を触っていないか）
                mid = page.evaluate(DUMP_FORM_JS)["fields"]
                intended, unexpected = diff_fields(before, mid, allow)
                show_diff(log, "送信直前の差分（意図した変化）", intended)
                if unexpected:
                    show_diff(log, "★送信直前の差分（意図しない変化）", unexpected)
                    log("  ★その項目が何かを読む（判断材料）")
                    for x in page.evaluate(LABEL_PROBE_JS, [k for k, _b, _a in unexpected]):
                        log(f"    {x['name']}")
                        if not x.get("exists"):
                            log("      （欄が無い＝キーが消えた）")
                            continue
                        log(f"      type={x['type']} 個数={x['count']}")
                        log(f"      見出し(th)={x['th']!r}")
                        log(f"      セル(td)={x['tdText']!r}")
                        log(f"      行(tr)={x['trText']!r}")
                        for o in x["options"]:
                            log(f"        value={o['value']!r} checked={o['checked']} "
                                f"label={o['label']!r} onclick={o['onclick']!r}")
                    log("  ✗ 意図しない変化があるので送信しない（恒久ルール5）")
                    return 1
                # ★件数ではなく**最終状態**で判定する。kbnCd が既に 1 の室では
                #   変化が2件になるので、件数を固定すると通らない。
                state_ng = []
                for k, want in ((FLD_FLG, "1"), (FLD_NEN, a.nen), (FLD_KBN, "1")):
                    if mid.get(k) != want:
                        state_ng.append(f"{k} が {mid.get(k)!r}（期待 {want!r}）")
                if mid.get(FLD_GETSU) != before.get(FLD_GETSU):
                    state_ng.append(
                        f"{FLD_GETSU} が変わった（{before.get(FLD_GETSU)!r} → "
                        f"{mid.get(FLD_GETSU)!r}）。空のままにすること")
                if state_ng:
                    for x in state_ng:
                        log(f"  ✗ {x}")
                    log("  ✗ 送信しない")
                    return 1
                log(f"  ◯ 送信直前の状態は期待どおり（flg=1 / nen={a.nen} / kbn=1 / "
                    f"getsu={mid.get(FLD_GETSU)!r} は不変）。意図しない変化ゼロ")
                if not (a.to_confirm or a.confirm_write):
                    log("  ★--to-confirm / --confirm-write が無いので確認画面へ進まない")
                    continue
                ck = page.evaluate(CLICK_KAKUNIN_JS)
                log(f"  『確認画面へ』（div#regButton2）: {ck}")
                if not ck.get("ok"):
                    return 1
                page.wait_for_timeout(6000)
                cd = page.evaluate(CONFIRM_DUMP_JS)
                log(f"  確認画面: title={cd['title']!r} url={cd['url']}")
                log(f"    ★確認画面の物件コード={cd['bukkenCd']!r} 号室={cd['heyaDisp']!r} "
                    f"名寄せスコア={cd['score']!r}点")
                # ★送信直前の最終確認：確認画面のコードが対象と一致するか
                if cd["bukkenCd"] and cd["bukkenCd"] != code:
                    log(f"  ✗ ★確認画面の物件コードが対象と違う（{cd['bukkenCd']} ≠ {code}）。"
                        "別物件を書き換えるので止める")
                    return 1
                log(f"    定期借家の表示: {cd['teikiText']}")
                if cd["errors"]:
                    log(f"    ★エラー表示: {cd['errors']}")
                log(f"    本文: {cd['text'][:400]}")
                log("    ボタン:")
                for b in cd["buttons"]:
                    log(f"      {b['tag']}[{b['type']}] id={b['id']!r} value={b['value']!r} "
                        f"alt={b['alt']!r} title={b['title']!r} text={b['text']!r} "
                        f"cls={b['cls']!r} disabled={b['disabled']!r} onclick={b['onclick']!r}")
                if out_dir:
                    (out_dir / f"{code}_confirm.json").write_text(
                        json.dumps(cd, ensure_ascii=False, indent=1), encoding="utf-8")
                if not a.confirm_write:
                    log("  ★--confirm-write が無いのでここで止める（登録しない）")
                    log("  ※画面を離れれば破棄される")
                    return 0
                rg = page.evaluate(CLICK_REGISTER_JS, a.register_label)
                log(f"  『{a.register_label}』: {rg}")
                if not rg.get("ok"):
                    log("  ✗ 登録ボタンを押せない（推測でクリックしない）")
                    return 1
                page.wait_for_timeout(7000)
                after_screen = page.evaluate(CONFIRM_DUMP_JS)
                log(f"  登録後: title={after_screen['title']!r}")
                log(f"    本文: {after_screen['text'][:200]}")
                # ★保存後の実値と変更前を差分＝**サーバ側の副作用検出**
                #   （disabled で送られず値が消えた場合もここに出る）
                log("  ■ 保存後に詳細を開き直して全項目を再ダンプ")
                ok2, dump2 = open_detail(page, code, log)
                if not ok2:
                    log(f"  ✗ 保存後の照合ができない: {dump2}")
                    return 1
                after = dump2["fields"]
                intended2, unexpected2 = diff_fields(before, after, allow)
                show_diff(log, "保存後の差分（意図した変化）", intended2)
                if out_dir:
                    (out_dir / f"{code}_after.json").write_text(
                        json.dumps(dump2, ensure_ascii=False, indent=1), encoding="utf-8")
                if unexpected2:
                    show_diff(log, "★保存後の差分（意図しない変化）", unexpected2)
                    log("  ✗ ★他の項目が変わった。次の室に進まない")
                    return 1
                ng2 = []
                for k, want in ((FLD_FLG, "1"), (FLD_NEN, a.nen), (FLD_KBN, "1")):
                    if after.get(k) != want:
                        ng2.append(f"{k} が {after.get(k)!r}（期待 {want!r}）")
                if after.get(FLD_GETSU) != before.get(FLD_GETSU):
                    ng2.append(f"{FLD_GETSU} が変わった（{before.get(FLD_GETSU)!r} → "
                               f"{after.get(FLD_GETSU)!r}）")
                if ng2:
                    for x in ng2:
                        log(f"  ✗ 保存後: {x}")
                    return 1
                log(f"  ◯ {code} 是正完了。{len(before)}項目のうち変わったのは"
                    f"意図した{len(intended2)}項目だけ（getsu は不変）")
                results.append({"code": code, "室": shown, "掲載": exp[1],
                                "確認画面のスコア": cd["score"],
                                "確認画面の物件コード": cd["bukkenCd"],
                                "項目数": len(before),
                                "変わった項目": [{"項目": k, "前": b, "後": aa}
                                            for k, b, aa in intended2],
                                "意図しない変化": len(unexpected2)})
            if results and out_dir:
                out_dir.mkdir(parents=True, exist_ok=True)
                # ★照合の結果と是正の結果は別ファイルにする。混ぜると
                #   「直した記録」と「見ただけの記録」が区別できなくなる
                rp = out_dir / ("verify_results.json" if a.verify else "results.json")
                old = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else []
                rp.write_text(json.dumps(old + results, ensure_ascii=False, indent=1),
                              encoding="utf-8")
                log(f"■ {'照合' if a.verify else '是正'}結果を保存: {rp}")
            if a.verify:
                ok_n = sum(1 for r in results if r.get("判定") == "OK")
                ng_n = len(results) - ok_n
                log(f"★verify は読み取りのみ。何も変更していない")
                log(f"■ 是正済み {ok_n}件 / 未是正 {ng_n}件 （対象 {len(codes)}件）")
                for r in results:
                    if r.get("判定") == "NG":
                        log(f"   ✗ {r['code']} {r['室']}: {r['理由']}")
                if ng_n:
                    return 1
            if a.recon:
                log("★recon は読み取りのみ。何も変更していない")
            if not a.headless:
                page.wait_for_timeout(20000)
            return 0
        finally:
            if a.headless:
                ctx.close()


if __name__ == "__main__":
    sys.exit(main())
