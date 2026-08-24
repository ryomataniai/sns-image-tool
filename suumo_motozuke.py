#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motozuke-v1: SUUMO（forrent）の元付確認日を更新する。

★★2026-08-17 に打ち切り決定。**この自動化はやる意味がない。**
  元付確認日を更新しても**掲載終了日と残日数は動かない**ことを実測で確定させた
  （詳細画面のフォーム実値 2026/08/10 → 2026/08/17 に変わったが、掲載終了日は 26/08/25 のまま）。
  掲載終了日 26/08/25 は**現在の契約期間の終わり**で、確認日とは無関係だった。
  8/6に75枠を申込済みで8/26から新契約。**延ばすものではなく、8/26に掲載指示を出し直すもの。**
  → その役は suumo_keisai.py。--from-check-alive は実装していない（作る意味がない）。
  ★このファイルを残すのは、上の恒久ルールと画面構造の実測が次に効くから。

    python3 suumo_motozuke.py --recon                     # 画面構造を読むだけ（何も変えない）
    python3 suumo_motozuke.py --dry-run
    python3 suumo_motozuke.py --update --codes <12桁> --confirm-write
    python3 suumo_motozuke.py --update --from-check-alive <_空室確認_*.csv> --confirm-write

■★単独で使う道具にしない（依頼文§0）。
  「元付確認日を更新する」＝「元付に確認した」という宣言。その確認の実体は
  realpro_dl.py --check-alive のリアプロ照合（載っている＝元付がまだ募集している）。
  --from-check-alive で「在り」の室だけを対象にする。**消失・判定不能は更新しない**＝
  放置すればそのまま掲載が切れる（危険側でなく安全側に倒れる）。

■ログイン・フレーム・セッション判定は suumo_keisai から流用する。
  同じものを2つ書くと片方が腐る（2026-08-14 に棟名キーの突き合わせで5回踏んだ）。
■★このアプリ（fn.forrent.jp）の恒久ルール — 2026-08-17 に2度踏んだので必ず守る
  1) **書き込みは必ず2段階。**「◯◯する／一括更新実行」→ **確認画面** → もう一度「一括更新実行」。
     **1段目で完了したと判定しない。**
       掲載指示: 『指示する』→ 確認 → `#shijiButton` → title=「掲載指示完了」
       元付更新: `img#exec0` → title=「元付更新確認」→ `#update1` → title=「元付更新完了」
     1段目だけで止めると**エラーも出ずに何も保存されない**（元付更新で3回空振りした）。
  2) **チェックボックスは必ず `click()` で操作する。`checked = true` の代入では onclick が走らない。**
     元付確認一覧の onclick は
       toggleInput(code, checked)   → 日付欄の disabled を外し UNYOU_DATE を自動投入
       toggleDisable('seiyaku…')    → 成約チェックを無効化（相互排他）
       toggleExecBtnDisable(...)    → 一括更新実行ボタンの有効化
     を全部やっている。代入で立てると **disabled な input は送信されない**ので黙って空振りする。
     2026-08-13 の『らくらく交通入力』モーダル（dispatchEvent が必要だった）と同型。
  3) 押せるものは4系統ある：input[type=button] / input[type=image] /
     img[alt]（imageButton） / div.spbtn[title]。**a/input/button だけ見て「無い」と結論しない。**
     確定ボタンは**最下部**にあることが多い（`img#exec0` は y=4729）。
  4) **「情報が無い」を「問題が無い」と読まない。**
     照合対象がゼロ件なら「合格」ではなく**「照合不能」**。
     **3値（正常 / 異常 / 判定不能）に分け、判定不能を正常に倒さない。**
     2026-08-17 に同じ型を3回出した：
       ・収穫条件の外の室を「★消失」とした（母集団に無いだけ）
       ・「掲載終了日が動かない＝前提が誤り」と結論しかけた（そもそも保存されていなかった）
       ・空集合の「変化なし」を合格として表示した（読み取りが空だっただけ）
  5) **全項目を持つフォームで一部だけ変えるときは、変更前後の全項目を差分して、
     意図した項目以外が変わっていないことを検証する。**
     物件情報更新画面は物件の全項目を持ち、「登録」で**全部が再送信される**。
     2026-08-17 に同型を1回踏んだ（toggleInput が元付会社名・担当・電話も編集可能にしていた）。

"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import suumo_keisai as K

TAB_MOTOZUKE = "a.dsSameActionReload-Tab2"     # 元付確認一覧（innerTextは空・クラス名だけが手がかり）
TAB_KEISAI = K.TAB_KEISAI                      # 掲載終了日はこちらの一覧にある
MENU_KOSHIN = K.MENU_KOSHIN


# ── 元付確認一覧の読み取り ────────────────────────────────────────
# ★行の特定は hidden の bukkenCd。`1004` 始まりの既存物件があるので桁の決め打ちをしない。
#   配列インデックスと表示順は一致しない前提で扱う（掲載指示一覧では実際にずれていた）。
ROWS_JS = """() => {
  const d = window.frames['main'].document;
  const pick = (tr, suffix) => {
    const e = [...tr.querySelectorAll('input[type=hidden],input[type=text]')].find(
        x => (x.name || '').includes('.' + suffix + '}'));
    return e ? {name: e.name, value: e.value, type: e.type,
                maxlength: e.getAttribute('maxlength') || '',
                readonly: e.readOnly, onchange: (e.getAttribute('onchange')||'').slice(0,60)}
             : null;
  };
  // 元付確認日の入力欄を持つ行を対象にする（無い行はヘッダや区切り）
  const inputs = [...d.querySelectorAll('input[name*=mototsukeKakuninDate]')];
  return inputs.map((inp, i) => {
    const tr = inp.closest('tr');
    if (!tr) return {i: i, err: 'trが無い'};
    const cells = [...tr.querySelectorAll('td')].map(
        c => (c.innerText || '').replace(/\\s+/g, ' ').trim());
    const cd = pick(tr, 'bukkenCd');
    return {
      i: i,
      code: cd ? cd.value : null,
      codeName: cd ? cd.name : null,
      dateField: {name: inp.name, value: inp.value,
                  maxlength: inp.getAttribute('maxlength') || '',
                  readonly: inp.readOnly,
                  onchange: (inp.getAttribute('onchange') || '').slice(0, 70),
                  onclick: (inp.getAttribute('onclick') || '').slice(0, 70)},
      cellCount: cells.length,
      cells: cells,
      hiddens: [...tr.querySelectorAll('input[type=hidden]')].map(
          e => (e.name || '') + '=' + String(e.value).slice(0, 20)).slice(0, 10),
      images: [...tr.querySelectorAll('img,a')].map(
          e => (e.alt || e.title || (e.getAttribute('onclick')||'')).slice(0, 40))
          .filter(Boolean).slice(0, 6)
    };
  });
}"""

HEAD_JS = """() => {
  const d = window.frames['main'].document;
  const inp = d.querySelector('input[name*=mototsukeKakuninDate]');
  const tbl = inp ? inp.closest('table') : null;
  if (!tbl) return null;
  return [...tbl.querySelectorAll('tr')].slice(0, 3).map(
      tr => [...tr.querySelectorAll('th,td')].map(
          c => (c.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 18)));
}"""

# ★§3-1「更新を確定するボタンが見つかっていない」。下部にある可能性があるので
#   **ページ最下部までスクロールしてから**、押せそうなもの全系統を洗い出す。
#   このアプリの「押せるもの」は実測4系統：input[type=button] / input[type=image] /
#   img.imageButton[alt] / div.spbtn[title]。a/input/button だけ見て無いと結論しない。
BUTTONS_JS = """() => {
  const w = window.frames['main'];
  const d = w.document;
  w.scrollTo(0, d.body.scrollHeight);
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const all = [...d.querySelectorAll(
      'input[type=button],input[type=submit],input[type=image],button,' +
      'img,div.spbtn,a[onclick],div[title],span[onclick]')].filter(vis);
  return {
    scrollHeight: d.body.scrollHeight,
    items: all.map(e => ({
      tag: e.tagName, type: e.type || '', id: e.id || '', name: e.name || '',
      value: (e.value || '').slice(0, 20), alt: (e.alt || '').slice(0, 20),
      title: (e.title || '').slice(0, 20),
      cls: (e.className || '').toString().slice(0, 24),
      text: (e.children && e.children.length === 0 ? (e.textContent || '') : '').trim().slice(0, 18),
      onclick: (e.getAttribute('onclick') || '').slice(0, 70),
      y: Math.round(e.getBoundingClientRect().top)
    })).filter(x => x.value || x.alt || x.title || x.text || x.onclick || x.id)
  };
}"""

# ★§3-3「一括で同じ日付を入れる機能があるか」。文字列で探すのではなく
#   select / 入力欄 / それらしい語を全部出して判断する。
IKKATSU_JS = """() => {
  const d = window.frames['main'].document;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = (d.body.innerText || '').replace(/\\s+/g, ' ');
  return {
    hasIkkatsuWord: /チェックした物件|一括|まとめて/.test(txt),
    words: (txt.match(/[^ ]{0,12}(チェックした物件|一括|まとめて)[^ ]{0,20}/g) || []).slice(0, 6),
    selects: [...d.querySelectorAll('select')].filter(vis).map(e => ({
      name: (e.name || '').slice(0, 46), id: e.id || '',
      value: e.value,
      opts: [...e.options].map(o => o.value + '=' + (o.text || '').trim()).slice(0, 8)})),
    checkboxes: [...d.querySelectorAll('input[type=checkbox]')].filter(vis).map(e => ({
      name: (e.name || '').slice(0, 46), id: e.id || '', checked: e.checked})),
    textInputsOutsideRows: [...d.querySelectorAll('input[type=text]')].filter(vis)
      .filter(e => !/mototsukeKakuninDate/.test(e.name || ''))
      .map(e => ({name: (e.name || '').slice(0, 50), id: e.id || '',
                  value: String(e.value).slice(0, 14)})).slice(0, 14)
  };
}"""


# ★2回の送信（チェックのみ／日付＋チェック）がどちらも保存されなかった。
#   書き込みを増やす前に**どのフォームに属しているか**を読む。
#   exec0 は onclick="ImageButton.onceSubmit(mainForm, null, this)" ＝ mainForm だけを送る。
#   日付欄やチェックが別フォームにあると、押しても値が送られない。
FORMS_JS = """(code) => {
  const d = window.frames['main'].document;
  const nameOf = e => (e && e.form) ? (e.form.name || e.form.id || '(無名)') : '(フォーム外)';
  const di = d.getElementById('dateinput' + code);
  const cb = d.getElementById('confirm' + code);
  const ex = d.getElementById('exec0');
  return {
    forms: [...d.querySelectorAll('form')].map(f => ({
      name: f.name || '', id: f.id || '', action: (f.getAttribute('action') || '').slice(0, 50),
      method: f.method, elements: f.elements.length})),
    dateField: {form: nameOf(di), disabled: di ? di.disabled : null,
                name: di ? di.name : null},
    checkbox: {form: nameOf(cb), disabled: cb ? cb.disabled : null,
               name: cb ? cb.name : null},
    exec0: {exists: !!ex, form: nameOf(ex),
            onclick: ex ? (ex.getAttribute('onclick') || '') : null},
    hasMainForm: !!d.forms['mainForm'],
    mainFormElements: d.forms['mainForm'] ? d.forms['mainForm'].elements.length : null,
    // mainForm に日付欄が含まれているか（名前で探す）
    dateInMainForm: (() => {
      const f = d.forms['mainForm'];
      if (!f) return null;
      return [...f.elements].filter(e => /mototsukeKakuninDate/.test(e.name || '')).length;
    })(),
    confirmInMainForm: (() => {
      const f = d.forms['mainForm'];
      if (!f) return null;
      return [...f.elements].filter(e => e.name === 'confirmBukkenCds').length;
    })(),
    url: d.location.href.slice(-70)
  };
}"""


# ★日付欄は disabled。代入で checked を立てるとハンドラが走らず有効化されない。
#   **本物のクリック**で何が起きるかを読む（サーバには送らない）。
CLICK_PROBE_JS = """(code) => {
  const d = window.frames['main'].document;
  const cb = d.getElementById('confirm' + code);
  const di = d.getElementById('dateinput' + code);
  if (!cb || !di) return {ok: false, why: '要素が無い'};
  const before = {checked: cb.checked, disabled: di.disabled, value: di.value};
  const attrs = {onclick: cb.getAttribute('onclick') || '',
                 onchange: cb.getAttribute('onchange') || ''};
  cb.click();                       // ★代入ではなく本物のクリック
  const after = {checked: cb.checked, disabled: di.disabled, value: di.value};
  // 元に戻す（画面状態を汚さない）
  cb.click();
  const restored = {checked: cb.checked, disabled: di.disabled, value: di.value};
  return {ok: true, attrs: attrs, before: before, after: after, restored: restored};
}"""


# ★関数の実体を読む。推測でクリックしない（依頼文§8-1）。
JS_SOURCE_JS = """(code) => {
  const w = window.frames['main'];
  const d = w.document;
  const src = f => { try { return String(w[f]).replace(/\\s+/g, ' ').slice(0, 700); }
                     catch (e) { return '(読めない: ' + e.message + ')'; } };
  const cb = d.getElementById('confirm' + code);
  if (cb && !cb.checked) cb.click();
  const ex = d.getElementById('exec0');
  const after = ex ? {disabledAttr: ex.getAttribute('disabled'), disabledProp: ex.disabled,
                      src: (ex.getAttribute('src') || '').split('/').pop(),
                      cls: (ex.className || '').toString()} : null;
  const out = {
    toggleExecBtnDisable: src('toggleExecBtnDisable'),
    toggleInput: src('toggleInput'),
    onceSubmit: (() => { try { return String(w.ImageButton.onceSubmit)
        .replace(/\\s+/g, ' ').slice(0, 700); } catch (e) { return '(読めない)'; } })(),
    bukkenCdList: (() => { try { return JSON.stringify(w.bukkenCdList).slice(0, 200); }
                          catch (e) { return '(読めない)'; } })(),
    execAfterCheck: after,
    // exec 系のIDが他にもあるか
    execIds: [...d.querySelectorAll('[id^=exec]')].map(
        e => ({id: e.id, tag: e.tagName, disabled: e.getAttribute('disabled'),
               alt: e.alt || ''}))
  };
  if (cb && cb.checked) cb.click();     // 元に戻す
  return out;
}"""


# ★toggleInput は date だけでなく gyosha / tanto / tel も有効化する（実体を読んで判明）。
#   空のまま送ると元付会社名・担当・電話を消しうる。**送信前に必ず中身を見る。**
SIDE_EFFECT_JS = """(code) => {
  const d = window.frames['main'].document;
  const cb = d.getElementById('confirm' + code);
  const names = ['gyosha', 'tanto', 'tel', 'date'];
  const read = () => names.map(n => {
    const e = d.getElementById(n + 'input' + code);
    return {field: n, exists: !!e, disabled: e ? e.disabled : null,
            value: e ? String(e.value) : null, name: e ? (e.name || '') : null};
  });
  const before = read();
  const label = (() => { const e = d.getElementById('labelBase' + code);
                         return e ? (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80)
                                  : null; })();
  if (cb && !cb.checked) cb.click();
  const after = read();
  if (cb && cb.checked) cb.click();
  return {labelBase: label, before: before, after: after};
}"""


def goto_tab(page, tab_sel, want_title, log):
    """情報更新一覧 → 指定タブ。→ (mainフレーム, 到達したか)。"""
    fr = K.main_frame(page)
    if fr is None or fr.locator(tab_sel).count() == 0:
        log(f"  情報更新一覧へ移動（{MENU_KOSHIN}）")
        if not page.evaluate(K.MENU_PROBE, MENU_KOSHIN):
            log("  ★メニューのリンクが見つからない")
            return fr, False
        page.wait_for_timeout(3000)
        fr = K.main_frame(page)
    if fr is None:
        return None, False
    n = fr.locator(tab_sel).count()
    if n == 0:
        log(f"  ★タブ {tab_sel} が無い。title={fr.title()!r}")
        return fr, False
    fr.locator(tab_sel).first.click()
    page.wait_for_timeout(3000)
    fr = K.main_frame(page)
    ttl = fr.title() if fr else "?"
    log(f"  {tab_sel} を押した → title={ttl!r}（タブ候補{n}個）")
    return fr, ttl == want_title


def keisai_end_dates(page, log):
    """掲載指示一覧から {物件コード: 掲載終了日} を読む。★§3-4の前後比較に使う。

    ★元付確認一覧には掲載終了日の列が無い（依頼文§2-2の列一覧にも無い）。
      §3-4の「30件すべて 26/08/25」はこちらの画面の値。
    """
    fr, ok = goto_tab(page, TAB_KEISAI, "掲載指示一覧", log)
    if not ok:
        log("  ★掲載指示一覧に行けないので掲載終了日を読めない")
        return {}
    rows = page.evaluate(K.CELLS_JS)
    out = {}
    for r in rows:
        if not r["code"]:
            continue
        c = r["cells"]
        out[r["code"]] = {
            "掲載終了日": c[K.COL["掲載終了日"]] if len(c) > K.COL["掲載終了日"] else "",
            "指示ネット": c[K.COL["指示ネット"]] if len(c) > K.COL["指示ネット"] else "",
            "物件": c[K.COL["物件"]] if len(c) > K.COL["物件"] else "",
        }
    log(f"  掲載指示一覧から {len(out)}件の掲載終了日を読んだ")
    return out


# ── 「現在掲載指示済」でフィルタして掲載終了日を読む ──────────────────
# ★掲載終了日は**掲載指示一覧＋現在掲載指示済フィルタ**にしか無い（2026-08-17 確定）。
#   フィルタ前の一覧と比べると母集団が違い、「26室が存在しない」という誤った観察になる
#   （実際にそうなった。フィルタ後は元付確認一覧の30室と重なり30/30）。
FILTER_JS = """() => {
  const d = window.frames['main'].document;
  const a = [...d.querySelectorAll('a')].find(
      e => (e.innerText || '').replace(/\s+/g, '') === '現在掲載指示済');
  if (!a) return {ok: false, why: '『現在掲載指示済』のリンクが無い'};
  a.click();
  return {ok: true};
}"""


def end_dates_filtered(page, log):
    """掲載指示一覧 →「現在掲載指示済」→ {コード: 掲載終了日}。

    ★実装は suumo_keisai.read_shijizumi に移した（2026-08-17）。
      こちらは打ち切りのファイルなので、生きているコードを1つだけ持つ。
    """
    got, _n = K.read_shijizumi(page, log)
    return {c: v["掲載終了日"] for c, v in (got or {}).items()}


def motozuke_rows(page, log):
    """元付確認一覧 → {コード: {物件, 残日数, 前回, 確認日欄}}。"""
    fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
    if not ok:
        return {}
    out = {}
    for r in page.evaluate(ROWS_JS):
        if not r.get("code"):
            continue
        c = r.get("cells", [])
        out[r["code"]] = {
            "物件": c[3] if len(c) > 3 else "",
            "残日数": c[14] if len(c) > 14 else "",
            "前回": c[15] if len(c) > 15 else "",
            "確認日欄": r["dateField"]["value"],
        }
    log(f"  元付確認一覧: {len(out)}件")
    return out


def snapshot(page, code, log):
    """§3-4の前後比較に使う4点を取る。→ dict。"""
    mz = motozuke_rows(page, log)
    ed = end_dates_filtered(page, log)
    row = mz.get(code, {})
    return {"残日数": row.get("残日数"), "前回元付確認日": row.get("前回"),
            "確認日欄": row.get("確認日欄"), "掲載終了日": ed.get(code),
            "物件": row.get("物件"), "_元付件数": len(mz), "_掲載件数": len(ed)}


# ── 確認済みチェック → 一括更新実行 ──────────────────────────────
# ★行ごとに2つのチェックボックスが並んでいる（2026-08-17 実測）:
#     name='confirmBukkenCds'  id='confirm<物件コード>'   ← 確認済み（こちらを使う）
#     name='seiyakuBukkenCds'  id='seiyaku<物件コード>'   ← ★成約（触ると掲載が落ちる）
#   接頭辞しか違わないので、**id の完全一致**で特定する。
CHECK_CONFIRM_JS = """(codes) => {
  const d = window.frames['main'].document;
  const want = new Set(codes.map(c => 'confirm' + c));
  const on = [], off = [];
  d.querySelectorAll('input[name=confirmBukkenCds]').forEach(c => {
    const hit = want.has(c.id);
    // ★`c.checked = hit` の**代入では onclick が走らない**。実測（2026-08-17）で
    //   onclick が全部を制御していた：
    //     toggleInput(code, checked)        → 日付欄の disabled を外し**今日の日付を自動投入**
    //     toggleDisable('seiyaku…', ch)     → 成約チェックを無効化（サイト側で相互排他）
    //     toggleExecBtnDisable(...)         → **一括更新実行ボタンの有効/無効**
    //   代入で立てると日付欄は disabled のまま＝送信されず、実行ボタンも無効のまま。
    //   これで2回、押せないボタンを押して「保存されない」を起こした。**必ず click する。**
    if (c.checked !== hit) c.click();
    (hit ? on : off).push(c.id);
  });
  // ★一括チェック（chk0〜chk3）は必ず外す
  const masters = [];
  d.querySelectorAll('input[type=checkbox]').forEach(c => {
    if (/^chk\\d$/.test(c.id || '')) { c.checked = false; masters.push(c.id); }
  });
  return {on: on, offCount: off.length, masters: masters};
}"""

# ★送信直前の検証。「触らない設計」は「触っていないつもり」で壊れる（谷合さんの指示）。
#   成約のチェックが1つでも立っていたら送信しない。
ASSERT_JS = """(codes) => {
  const d = window.frames['main'].document;
  const seiyaku = [...d.querySelectorAll('input[name=seiyakuBukkenCds]')]
      .filter(c => c.checked).map(c => c.id);
  const confirm = [...d.querySelectorAll('input[name=confirmBukkenCds]')]
      .filter(c => c.checked).map(c => c.id);
  const masters = [...d.querySelectorAll('input[type=checkbox]')]
      .filter(c => /^chk\\d$/.test(c.id || '') && c.checked).map(c => c.id);
  return {seiyakuChecked: seiyaku, confirmChecked: confirm, mastersChecked: masters,
          want: codes.map(c => 'confirm' + c)};
}"""

# ★送信前に「本当に押せる状態か」を見る。img は disabled 属性を持てないので、
#   このサイトは src / class を差し替えるか、ハンドラ側で弾く。両方を記録する。
EXEC_STATE_JS = """(codes) => {
  const d = window.frames['main'].document;
  const b = d.getElementById('exec0');
  if (!b) return {exists: false};
  const dates = codes.map(c => {
    const e = d.getElementById('dateinput' + c);
    return {code: c, exists: !!e, disabled: e ? e.disabled : null,
            value: e ? e.value : null};
  });
  return {exists: true, alt: b.alt || '', src: (b.getAttribute('src') || '').split('/').pop(),
          cls: (b.className || '').toString(), disabledAttr: b.getAttribute('disabled'),
          onclick: (b.getAttribute('onclick') || '').slice(0, 70), dates: dates};
}"""

SIDE_ASSERT_JS = """(codes) => {
  const d = window.frames['main'].document;
  return codes.map(c => {
    const g = e => { const x = d.getElementById(e + 'input' + c);
                     return x ? String(x.value) : null; };
    return {code: c, gyosha: g('gyosha'), tanto: g('tanto'), tel: g('tel')};
  });
}"""

# ★2段階（2026-08-17 実測）。1回目の exec0 は**確認画面（title=元付更新確認）に進むだけ**。
#   確認画面でもう一度「一括更新実行」を押して初めて保存される。
#   掲載指示の「指示する → shijiButton」と同じ形。ここを見落として3回空振りした。
CONFIRM_SCREEN_JS = """() => {
  const d = window.frames['main'].document;
  const txt = (d.body.innerText || '').replace(/\\s+/g, ' ');
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const btns = [...d.querySelectorAll('img,input[type=button],input[type=image],input[type=submit]')]
      .filter(vis)
      .filter(e => /一括更新実行|実行|確定/.test((e.alt || '') + (e.value || '')))
      .map(e => ({tag: e.tagName, id: e.id || '', alt: e.alt || '', value: e.value || '',
                  disabled: e.getAttribute('disabled'),
                  onclick: (e.getAttribute('onclick') || '').slice(0, 60)}));
  const m1 = txt.match(/元付項目更新対象物件は\\s*(\\d+)\\s*件/);
  const m2 = txt.match(/成約対象物件は\\s*(\\d+)\\s*件/);
  return {title: d.title, buttons: btns,
          motozukeCount: m1 ? parseInt(m1[1], 10) : null,
          seiyakuCount: m2 ? parseInt(m2[1], 10) : null,
          text: txt.slice(0, 220)};
}"""

CLICK_CONFIRM_EXEC_JS = """(btnId) => {
  const d = window.frames['main'].document;
  let b = btnId ? d.getElementById(btnId) : null;
  if (!b) {
    const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    b = [...d.querySelectorAll('img,input[type=button],input[type=image],input[type=submit]')]
        .filter(vis).find(e => /一括更新実行/.test((e.alt || '') + (e.value || '')));
  }
  if (!b) return {ok: false, why: '確認画面の一括更新実行ボタンが無い'};
  b.click();
  return {ok: true, id: b.id || '', alt: b.alt || '', value: b.value || ''};
}"""

EXEC_JS = """() => {
  const d = window.frames['main'].document;
  const b = d.getElementById('exec0');
  if (!b) return {ok: false, why: 'img#exec0（一括更新実行）が無い'};
  b.click();
  return {ok: true, alt: b.alt || ''};
}"""


class SeiyakuGuard(Exception):
    """成約チェックが立っている状態で送信しようとした。★絶対に送らない。"""


def confirm_and_exec(page, codes, log, execute=False, date_str=None):
    """日付欄に日付を入れ、『確認済み』にチェックして一括更新実行。→ (ok, 詳細)。

    ★execute=False なら押さない（入力・チェック・アサーションまで）。
    ★date_str=None なら日付を入れない（チェックだけ＝2026-08-17に効かないと分かった経路）。
    """
    fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
    if not ok:
        return False, "元付確認一覧に到達できない"
    # ★日付欄に日付を入れる。「確認済み」チェックだけでは保存されないことが
    #   2026-08-17 に確定した（詳細画面のフォーム実値が変わらなかった）。
    if date_str:
        for c in codes:
            sd = page.evaluate(SET_DATE_JS, {"code": c, "date": date_str})
            log(f"  日付欄 dateinput{c} ← {date_str}: {sd}")
            if not sd.get("ok") or sd.get("value") != date_str:
                return False, f"日付欄に入れられない（{sd}）"
    ck = page.evaluate(CHECK_CONFIRM_JS, codes)
    log(f"  確認済みチェック: ON={ck['on']} / 他{ck['offCount']}件はOFF / "
        f"一括チェック解除={ck['masters']}")
    want = ["confirm" + c for c in codes]
    if sorted(ck["on"]) != sorted(want):
        return False, f"対象だけをチェックできていない（ON={ck['on']} 期待={want}）"
    a = page.evaluate(ASSERT_JS, codes)
    log(f"  送信前の検証: 成約={a['seiyakuChecked']} / "
        f"確認済み={a['confirmChecked']} / 一括={a['mastersChecked']}")
    # ★ここが谷合さんの指示した安全弁。黙って通るより止まる。
    if a["seiyakuChecked"]:
        raise SeiyakuGuard(
            f"成約チェックが立っている（{a['seiyakuChecked']}）。送信しない")
    if a["mastersChecked"]:
        raise SeiyakuGuard(f"一括チェックが立っている（{a['mastersChecked']}）。送信しない")
    if sorted(a["confirmChecked"]) != sorted(want):
        raise SeiyakuGuard(
            f"確認済みのチェックが期待と違う（{a['confirmChecked']} 期待={want}）")
    # ★日付欄が有効で値が入っているかを送信前に確かめる。
    #   disabled な input は送信されない（これを見ていなかったので2回空振りした）。
    st = page.evaluate(EXEC_STATE_JS, codes)
    log(f"  実行ボタンの状態: alt={st.get('alt')!r} src={st.get('src')!r} "
        f"cls={st.get('cls')!r} disabled属性={st.get('disabledAttr')!r}")
    # ★toggleInput は元付会社名・担当・電話も編集可能にする。空のまま送ると消しうる。
    #   実測では既に値が入っていたが、**空だったら送らない**ことを検証として固定する。
    side = page.evaluate(SIDE_ASSERT_JS, codes)
    for x in side:
        log(f"  元付情報 {x['code']}: 会社={x['gyosha']!r} 担当={x['tanto']!r} TEL={x['tel']!r}")
        for k in ("gyosha", "tel"):
            if x.get(k) == "":
                raise SeiyakuGuard(
                    f"元付{k}が空のまま送信しようとしている（{x['code']}）。"
                    "送ると元付情報が消える")
    for dinfo in st.get("dates", []):
        log(f"  日付欄 {dinfo['code']}: disabled={dinfo['disabled']} value={dinfo['value']!r}")
        if dinfo["disabled"]:
            raise SeiyakuGuard(
                f"日付欄が disabled のまま（{dinfo['code']}）。送信しても値が送られない")
        if not dinfo["value"]:
            raise SeiyakuGuard(f"日付欄が空（{dinfo['code']}）。送信しない")
    if not execute:
        return True, "チェックと検証まで（--confirm-write が無いので送信していない）"
    ex = page.evaluate(EXEC_JS)
    log(f"  『一括更新実行』（img#exec0）: {ex}")
    if not ex.get("ok"):
        return False, str(ex)
    page.wait_for_timeout(5000)
    cs = page.evaluate(CONFIRM_SCREEN_JS)
    log(f"  1段目のあと: title={cs['title']!r}")
    log(f"    元付項目更新対象={cs['motozukeCount']}件 / 成約対象={cs['seiyakuCount']}件")
    log(f"    ボタン: {cs['buttons']}")
    if cs["title"] != "元付更新確認":
        return False, f"確認画面に進んでいない（title={cs['title']!r}）: {cs['text'][:140]}"
    # ★確認画面の数字で検証する。成約が0件でないなら絶対に進めない。
    if cs["seiyakuCount"] != 0:
        raise SeiyakuGuard(
            f"確認画面の成約対象が {cs['seiyakuCount']}件（0件でない）。実行しない")
    if cs["motozukeCount"] != len(codes):
        raise SeiyakuGuard(
            f"確認画面の更新対象が {cs['motozukeCount']}件（期待 {len(codes)}件）。実行しない")
    ex2 = page.evaluate(CLICK_CONFIRM_EXEC_JS, None)
    log(f"  2段目『一括更新実行』: {ex2}")
    if not ex2.get("ok"):
        return False, str(ex2)
    page.wait_for_timeout(6000)
    t = page.evaluate("() => { const d = window.frames['main'].document;"
                      " return {title: d.title,"
                      " text: (d.body.innerText||'').replace(/\\s+/g,' ').slice(0,260)}; }")
    log(f"  2段目のあと: title={t['title']!r}")
    log(f"    本文: {t['text'][:200]}")
    return True, {"確認画面": cs, "完了": t}


# ── 日付欄と、詳細画面での実値確認 ────────────────────────────────
# ★2026-08-17：「確認済み」チェックだけでは**保存されない**ことが確定した。
#   一覧の表示（前回元付確認日）は変わらず、かつ**詳細画面のフォーム実値**
#   `${bukkenInputForm.mototsukeKakuninDate}` も 2026/08/10 のままだった。
#   表示遅れではなく未保存。→ 各行の**日付欄**に日付を入れる必要がある。
#     input id='dateinput<物件コード>' name='${mototsukeInputForms[N].mototsukeKakuninDate}'
#   ★検証は**必ず詳細画面のフォーム実値**で行う。一覧の表示は遅延の可能性を排除できない。
DATE_FIELD_JS = """(code) => {
  const d = window.frames['main'].document;
  const byId = d.getElementById('dateinput' + code);
  const rowInputs = [...d.querySelectorAll('input[name*=mototsukeKakuninDate]')].map(e => ({
      id: e.id || '', name: e.name || '', value: e.value,
      maxlength: e.getAttribute('maxlength') || '', readonly: e.readOnly,
      onchange: (e.getAttribute('onchange') || '').slice(0, 60),
      onclick: (e.getAttribute('onclick') || '').slice(0, 60)}));
  return {
    foundById: !!byId,
    byId: byId ? {id: byId.id, name: byId.name, value: byId.value,
                  maxlength: byId.getAttribute('maxlength') || '',
                  readonly: byId.readOnly} : null,
    sample: rowInputs.slice(0, 2),
    total: rowInputs.length,
    // 日付欄の隣にカレンダーのアイコンがあるか（クリック必須なら分かる）
    calendars: [...d.querySelectorAll('img')].filter(
        e => /calendar|cal_|カレンダー/i.test((e.getAttribute('src')||'') + (e.alt||'')))
        .map(e => ({alt: e.alt || '', src: (e.getAttribute('src')||'').split('/').pop(),
                    onclick: (e.getAttribute('onclick')||'').slice(0,60)})).slice(0, 3)
  };
}"""

SET_DATE_JS = """(arg) => {
  const d = window.frames['main'].document;
  const e = d.getElementById('dateinput' + arg.code);
  if (!e) return {ok: false, why: 'dateinput' + arg.code + ' が無い'};
  e.value = arg.date;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  e.dispatchEvent(new Event('blur', {bubbles: true}));
  return {ok: true, id: e.id, name: e.name, value: e.value};
}"""

# 詳細画面（物件情報更新）でフォームの実値を読む。★一覧の表示ではなくここを見る。
OPEN_DETAIL_JS = """(code) => {
  const d = window.frames['main'].document;
  const inp = d.getElementById('dateinput' + code)
           || [...d.querySelectorAll('input[name=confirmBukkenCds]')].find(
                  c => c.id === 'confirm' + code);
  const tr = inp ? inp.closest('tr') : null;
  if (!tr) return {ok: false, why: 'その行が見つからない'};
  const a = [...tr.querySelectorAll('a')].find(
      e => (e.textContent || '').replace(/\\s+/g, '') === '詳細');
  if (!a) return {ok: false, why: '『詳細』リンクが無い',
                  links: [...tr.querySelectorAll('a')].map(
                      e => (e.textContent||'').trim().slice(0,10))};
  a.click();
  return {ok: true};
}"""

READ_DETAIL_JS = """(code) => {
  const d = window.frames['main'].document;
  const es = [...d.getElementsByName('${bukkenInputForm.mototsukeKakuninDate}')];
  // ★詳細画面では ${bukkenInputForm.bukkenCd} が読めなかった（実測 null）。
  //   **別の室の値を読んで誤合格しない**よう、コードが画面に出ているかで照合する。
  //   どのフィールドに入っているかを探して名前も返す（次に触るときの手がかり）。
  const inAll = [...d.querySelectorAll('input,textarea')].filter(
      e => String(e.value || '') === code).map(e => e.name || e.id || '?');
  const bodyHas = (d.body.innerText || '').includes(code);
  const nm = d.getElementsByName('${bukkenInputForm.bukkenNm}')[0];
  const hy = d.getElementsByName('${bukkenInputForm.heyaNo}')[0];
  return {title: d.title, count: es.length, values: es.map(e => e.value),
          codeFields: inAll.slice(0, 4), bodyHasCode: bodyHas,
          bukkenNm: nm ? nm.value : null, heyaNo: hy ? hy.value : null};
}"""


def read_detail_kakunin(page, code, log):
    """詳細画面で元付確認日の**フォーム実値**を読む。→ (値, 情報)。

    ★一覧の『前回元付確認日』は表示が遅れる可能性を排除できない。
      保存されたかどうかはここでしか確実に分からない（2026-08-17 谷合さんの切り分け）。
    """
    fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
    if not ok:
        return None, {"why": "元付確認一覧に行けない"}
    r = page.evaluate(OPEN_DETAIL_JS, code)
    if not r.get("ok"):
        log(f"  ★詳細を開けない: {r}")
        return None, r
    page.wait_for_timeout(4500)
    info = page.evaluate(READ_DETAIL_JS, code)
    log(f"  詳細画面: title={info['title']!r} 物件名={info['bukkenNm']!r} "
        f"号室={info['heyaNo']!r} 元付確認日の欄={info['count']}個 値={info['values']}")
    log(f"    コードの在処: フィールド={info['codeFields']} 本文に出る={info['bodyHasCode']}")
    # ★この照合が無いと、別の室の詳細を読んで「保存された」と誤判定できる
    if not (info["codeFields"] or info["bodyHasCode"]):
        log(f"  ★開いた詳細に {code} が見つからない。別の室の可能性があるので値を使わない")
        return None, info
    val = info["values"][0] if info["values"] else None
    return val, info


def recon(page, log):
    """★読み取りのみ。依頼文§3の未確認4点を実機で確かめる。"""
    log("① 元付確認一覧へ移動")
    fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
    if not ok:
        log("✗ 元付確認一覧に到達できない")
        return 2
    log("② 列見出し")
    for i, hd in enumerate(page.evaluate(HEAD_JS) or []):
        log(f"   見出し{i}: {hd}")
    log("③ 行の読み取り（§2-2の td インデックスと hidden の bukkenCd を確かめる）")
    rows = page.evaluate(ROWS_JS)
    got = [r for r in rows if r.get("code")]
    pre = {}
    for r in got:
        pre[r["code"][:4]] = pre.get(r["code"][:4], 0) + 1
    log(f"   確認日入力欄のある行={len(rows)} / コードが取れた行={len(got)}")
    log(f"   コード先頭4桁の内訳: {pre}   ★1005決め打ちだと取りこぼす")
    for r in rows[:2]:
        log(f"   [{r['i']}] code={r.get('code')!r} name={r.get('codeName')!r}")
        log(f"        確認日欄: {r.get('dateField')}")
        log(f"        セル数={r.get('cellCount')}")
        for j, c in enumerate(r.get("cells", [])):
            if c:
                log(f"          td[{j}] = {c[:52]!r}")
        log(f"        hidden: {r.get('hiddens')}")
    log("④ ★§3-1 更新を確定するボタン（最下部までスクロールしてから全系統を洗う）")
    b = page.evaluate(BUTTONS_JS)
    log(f"   scrollHeight={b['scrollHeight']} / 候補{len(b['items'])}件")
    seen = set()
    for x in b["items"]:
        k = (x["tag"], x["type"], x["id"], x["value"], x["alt"], x["title"], x["text"])
        if k in seen:
            continue
        seen.add(k)
        log(f"     {x['tag']}[{x['type']}] id={x['id']!r} name={x['name']!r} "
            f"value={x['value']!r} alt={x['alt']!r} title={x['title']!r} "
            f"text={x['text']!r} cls={x['cls']!r} y={x['y']} onclick={x['onclick']!r}")
    log("⑤ ★§3-3 一括入力の有無")
    ik = page.evaluate(IKKATSU_JS)
    log(f"   一括系の語: {ik['hasIkkatsuWord']} {ik['words']}")
    for s in ik["selects"]:
        log(f"     select {s['name']!r} id={s['id']!r} value={s['value']!r} opts={s['opts']}")
    log(f"   チェックボックス: {ik['checkboxes']}")
    log(f"   行の外のテキスト入力: {ik['textInputsOutsideRows']}")
    log("⑥ ★§3-4 掲載終了日・残日数・前回確認日の現状")
    cur = []
    for r in got:
        c = r["cells"]
        cur.append({"code": r["code"],
                    "物件": c[3] if len(c) > 3 else "",
                    "残日数": c[14] if len(c) > 14 else "",
                    "前回": c[15] if len(c) > 15 else "",
                    "確認日欄": r["dateField"]["value"]})
    from collections import Counter
    log(f"   残日数の分布: {Counter(x['残日数'] for x in cur).most_common()}")
    log(f"   前回元付確認日の分布: {Counter(x['前回'] for x in cur).most_common()}")
    log(f"   確認日欄の初期値: {Counter(x['確認日欄'] for x in cur).most_common()}")
    ends = keisai_end_dates(page, log)
    log(f"   掲載終了日の分布: {Counter(v['掲載終了日'] for v in ends.values()).most_common()}")
    miss = [x['code'] for x in cur if x['code'] not in ends]
    log(f"   元付確認一覧にあって掲載指示一覧に無いコード: {len(miss)}件 {miss[:4]}")
    log("★recon は読み取りのみ。何も変更していない")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="SUUMOの元付確認日を更新する")
    ap.add_argument("--recon", action="store_true", help="画面構造を読むだけ（何も変えない）")
    ap.add_argument("--probe-side", metavar="CODE",
                    help="toggleInput の副作用（元付会社名・担当・電話）を読む（送信なし）")
    ap.add_argument("--probe-js", metavar="CODE",
                    help="サイト側JSの実体を読む（送信なし）")
    ap.add_argument("--probe-click", metavar="CODE",
                    help="チェックボックスを本物のクリックで押して日付欄の変化を読む（送信なし）")
    ap.add_argument("--probe-forms", metavar="CODE",
                    help="どのフォームに属しているかを読む（書き込みなし）")
    ap.add_argument("--probe-date", metavar="CODE",
                    help="日付欄と詳細画面の実値を読むだけ（書き込みなし）")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--autologin", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--codes")
    ap.add_argument("--from-check-alive", metavar="CSV",
                    help="realpro_dl.py --check-alive の出力。『在り』の室だけを対象にする")
    ap.add_argument("--date", default=None,
                    help="元付確認日に入れる日付（既定は今日・YYYY/MM/DD）。"
                         "--no-date でチェックだけの経路にできる")
    ap.add_argument("--no-date", action="store_true",
                    help="日付欄に入れない（2026-08-17の実測ではこれだけでは保存されない）")
    ap.add_argument("--confirm-write", action="store_true",
                    help="★これが無いと保存しない（掲載指示の実装と同じ安全装置）")
    ap.add_argument("--profile", default=str(K.PROFILE))
    ap.add_argument("--url", default=K.URL)
    ap.add_argument("--login-wait", type=int, default=1800)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    if not (a.recon or a.login or a.dry_run or a.update or a.probe_date or a.probe_forms
            or a.probe_click or a.probe_js
            or a.probe_side):
        ap.error("--recon / --probe-date / --login / --dry-run / --update のどれかが要る")
    log = K.log_factory()
    # ★日付はチェックボックスの onclick が今日を自動で入れる（実測）。
    #   --date は「今日以外を入れたいとき」だけの上書き。既定は触らない。
    if a.no_date:
        a.date = None

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
            page.goto(a.url, wait_until="load")
            page.wait_for_timeout(2500)
            if K.is_login_screen(page):
                if a.autologin and K.try_autologin(page, log):
                    pass
                elif a.login:
                    log(f"ログインしてください（最大{a.login_wait}秒）")
                    t0 = time.time()
                    while time.time() - t0 < a.login_wait and K.is_login_screen(page):
                        page.wait_for_timeout(3000)
                else:
                    log("✗ ログイン画面です。--login か --autologin が要る")
                    return 2
            if K.is_login_screen(page):
                log("✗ ログインを確認できませんでした")
                return 2
            if K.session_dead(page):
                log("✗ セッションが切れています。人がログインし直すこと")
                return 2
            log(f"ログイン確認。url={page.url[-46:]}")
            if a.probe_side:
                fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
                if not ok:
                    return 2
                r = page.evaluate(SIDE_EFFECT_JS, a.probe_side)
                log("★toggleInput の副作用（gyosha/tanto/tel も有効化される）")
                log(f"   labelBase（表示側）= {r['labelBase']!r}")
                log("   チェック前:")
                for x in r["before"]:
                    log(f"     {x['field']:<7} disabled={x['disabled']} value={x['value']!r}")
                log("   チェック後:")
                for x in r["after"]:
                    log(f"     {x['field']:<7} disabled={x['disabled']} value={x['value']!r}")
                log("★読み取りのみ（チェックは元に戻した）")
                return 0
            if a.probe_js:
                fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
                if not ok:
                    return 2
                r = page.evaluate(JS_SOURCE_JS, a.probe_js)
                log("★チェック後の実行ボタンの状態")
                log(f"   {r['execAfterCheck']}")
                log(f"   exec系のID: {r['execIds']}")
                log(f"   bukkenCdList = {r['bukkenCdList']}")
                for k in ("toggleExecBtnDisable", "toggleInput", "onceSubmit"):
                    log(f"   ── {k} ──")
                    log(f"   {r[k]}")
                log("★読み取りのみ（チェックは元に戻した）")
                return 0
            if a.probe_click:
                fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
                if not ok:
                    return 2
                r = page.evaluate(CLICK_PROBE_JS, a.probe_click)
                log("★チェックボックスを本物のクリックで押したときの日付欄の変化")
                log(f"   チェックボックスの属性: {r.get('attrs')}")
                log(f"   押す前: {r.get('before')}")
                log(f"   押した後: {r.get('after')}")
                log(f"   戻した後: {r.get('restored')}")
                log("★サーバには送っていない（画面状態も戻した）")
                return 0
            if a.probe_forms:
                fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
                if not ok:
                    return 2
                f = page.evaluate(FORMS_JS, a.probe_forms)
                log("★フォーム構造（保存されない原因の切り分け）")
                for x in f["forms"]:
                    log(f"   form name={x['name']!r} id={x['id']!r} "
                        f"action={x['action']!r} method={x['method']} 要素数={x['elements']}")
                log(f"   日付欄:   {f['dateField']}")
                log(f"   確認済み: {f['checkbox']}")
                log(f"   exec0:    {f['exec0']}")
                log(f"   mainForm あり={f['hasMainForm']} 要素数={f['mainFormElements']}")
                log(f"   mainForm 内の日付欄の数={f['dateInMainForm']} / "
                    f"確認済みの数={f['confirmInMainForm']}")
                log(f"   url={f['url']}")
                log("★読み取りのみ")
                return 0
            if a.probe_date:
                fr, ok = goto_tab(page, TAB_MOTOZUKE, "元付確認一覧", log)
                if not ok:
                    return 2
                log("① 日付欄の在処")
                df = page.evaluate(DATE_FIELD_JS, a.probe_date)
                log(f"   id='dateinput{a.probe_date}' が見つかる: {df['foundById']}")
                log(f"   その欄: {df['byId']}")
                log(f"   確認日欄の総数={df['total']} 先頭2件={df['sample']}")
                log(f"   カレンダーのアイコン: {df['calendars']}")
                log("② 詳細画面のフォーム実値（★保存されたかはここで見る）")
                val, info = read_detail_kakunin(page, a.probe_date, log)
                log(f"   → 元付確認日の実値 = {val!r}")
                log("★probe は読み取りのみ。何も変更していない")
                if not a.headless:
                    page.wait_for_timeout(20000)
                return 0
            if a.recon:
                rc = recon(page, log)
                if not a.headless:
                    page.wait_for_timeout(30000)
                return rc
            if a.dry_run or a.update:
                codes, bad = ([], [])
                if a.codes:
                    codes, bad = K.read_codes(a.codes)
                if bad:
                    log(f"✗ 12桁の物件コードでない指定: {bad[:5]}")
                    return 2
                if not codes:
                    log("✗ --codes が要る（--from-check-alive は次段で実装）")
                    return 2
                log("■ 実行前の状態（§3-4の前後比較）")
                before = {c: snapshot(page, c, log) for c in codes}
                for c in codes:
                    v, _i = read_detail_kakunin(page, c, log)
                    before[c]["実値"] = v
                for c, v in before.items():
                    log(f"   {c} {v['物件']}: 残日数={v['残日数']!r} "
                        f"前回={v['前回元付確認日']!r} 掲載終了日={v['掲載終了日']!r}")
                if a.dry_run:
                    log("★--dry-run なので何も変更しない")
                    return 0
                try:
                    ok2, detail = confirm_and_exec(page, codes, log,
                                                    execute=a.confirm_write,
                                                    date_str=a.date)
                except SeiyakuGuard as e:
                    log(f"✗ ★安全弁で停止: {e}")
                    return 3
                if not ok2:
                    log(f"✗ {detail}")
                    return 1
                if not a.confirm_write:
                    log("★--confirm-write が無いので送信していない")
                    return 0
                log("■ 実行後の状態")
                after = {c: snapshot(page, c, log) for c in codes}
                log("■ ★詳細画面のフォーム実値で照合（一覧の表示ではなくこちらが真）")
                for c in codes:
                    v, _i = read_detail_kakunin(page, c, log)
                    after[c]["実値"] = v
                log("")
                log("■ ★§3-4 の結果（前 → 後）")
                moved = False
                for c in codes:
                    b, af = before[c], after[c]
                    for k in ("実値", "残日数", "前回元付確認日", "掲載終了日"):
                        mark = "★変化" if b.get(k) != af.get(k) else "変化なし"
                        if b.get(k) != af.get(k):
                            moved = True
                        log(f"   {c} {k}: {b.get(k)!r} → {af.get(k)!r}  {mark}")
                # ★結論を急がない。**前回元付確認日が動いたか**で切り分ける。
                #   動いた＝更新は効いた。掲載終了日が動かないなら前提（確認日で掲載が延びる）が誤り。
                #   動いていない＝**そもそも更新が効いていない**（または表示が1日遅れ）。
                #   この2つを混ぜると「やる意味がない」と誤って結論する。
                # ★保存されたかは**詳細画面の実値**で判定する（一覧の表示は遅延しうる）
                kakunin_moved = any(
                    before[c].get("実値") != after[c].get("実値") for c in codes)
                end_moved = any(
                    before[c].get("掲載終了日") != after[c].get("掲載終了日") for c in codes)
                log("")
                if kakunin_moved and not end_moved:
                    log("✗ ★前回元付確認日は動いたが掲載終了日が動かない。")
                    log("   『元付確認日の更新で掲載が延びる』という前提が誤り。実装を止める（§5）")
                    return 4
                if not kakunin_moved:
                    log("★詳細画面の実値が動いていない＝**更新自体が効いていない**。")
                    log("  『掲載終了日が動かない』とは結論できない（切り分けが未了）。")
                    log("  この画面系は表示が1日遅れることがある（現在掲載中がそう）ので、")
                    log("  翌日に読み直すまで断定しない。")
                    return 5
                return 0
            log("✗ 指定された動作が無い")
            return 1
        finally:
            if a.headless:
                ctx.close()


if __name__ == "__main__":
    sys.exit(main())
