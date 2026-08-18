#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keisaishiji-v1: SUUMO（forrent）の掲載指示を出す。

    python3 suumo_keisai.py --recon                          # 画面構造を読むだけ（何も変えない）
    python3 suumo_keisai.py --shiji 掲載 --codes a.txt --dry-run
    python3 suumo_keisai.py --shiji 掲載 --codes a.txt
    python3 suumo_keisai.py --verify --codes a.txt

■このスクリプトが扱うのは「掲載指示」だけ。物件登録は suumo_register.py。

■★対象は必ず**物件コード（12桁）**で特定する。部屋キーや物件名では特定しない。
  2026-08-14 に、ある室が**2レコード**登録されているのが見つかった
  （同一室に2つの物件コード。片方が掲載中・片方が保留）。物件名で検索すると両方ヒットするので、
  一括チェックのまま操作すると意図しない側を触る＝**他人の掲載を止める事故**になる。
  行の innerHTML から \\b(1005\\d{8})\\b を拾い、対象コードのものだけ checked にする。

■★「指示する」を押しただけでは保存されない。`shijiButton`（一括更新実行）で初めて保存される。
  さらに**画面をまたぐと保存されない**（画面に明記あり）。8/12に谷合さんがこれを踏んで、
  ページ1で保留・ページ2で掲載を指示 → 何も保存されなかった。
  → 表示件数を200件にして1ページに載せる。

■★認証情報は扱わない。ログインは人が入れる（--login）か、ブラウザの自動入力に任せる
  （--autologin＝入っているかだけ見てボタンを押す）。値は読まない・出さない。
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
     照合対象がゼロ件、**または照合対象が自分自身**なら、
     それは「合格」ではなく**「照合不能」**。
     **3値（正常 / 異常 / 判定不能）に分け、判定不能を正常に倒さない。**
     2026-08-17〜18 に同じ型を4回出した：
       ・収穫条件の外の室を「★消失」とした（母集団に無いだけ）
       ・「掲載終了日が動かない＝前提が誤り」と結論しかけた（そもそも保存されていなかった）
       ・空集合の「変化なし」を合格として表示した（読み取りが空だっただけ）
       ・**自分自身と比較して必ず合格になった**（suumo_teiki.py --verify が
         `<code>_before.json` を**書いてから読む**順序で、是正後の値を「是正前」として
         保存していた。差分は必ず空になり、どんなに壊れていても合格する）
     ★4つに共通するのは「**照合が成立していないのに合格を出す**」こと。
       3と4は特に近く、比較相手が無い／比較相手が自分という違いしかない。
       → **合格を出す前に「何と何を比べたか」を言えるか確かめる。**言えないなら照合不能。
  5) **全項目を持つフォームで一部だけ変えるときは、変更前後の全項目を差分して、
     意図した項目以外が変わっていないことを検証する。**
     物件情報更新画面は物件の全項目を持ち、「登録」で**全部が再送信される**。
     2026-08-17 に同型を1回踏んだ（toggleInput が元付会社名・担当・電話も編集可能にしていた）。

"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

URL = "https://www.fn.forrent.jp/fn/"
PROFILE = Path.home() / ".suumo_pw2"
CODE_RX = re.compile(r"\b(1005\d{8})\b")

# 掲載指示の値（2026-08-13〜14 実測）
SHIJI = {"掲載": "shijiFlg1_1", "保留": "shijiFlg1_0"}
SEL_KIND = "selectShijiU"              # 「チェックした物件を」= ネット（U＝画面上部）
SEL_VALUE = "flgValue_shijiFlg1U"      # 「に」= 掲載/保留
KIND_NET = "shijiFlg1"
TAB_KEISAI = "a.dsSameActionReload-Tab3"   # 「掲載指示一覧」タブ。innerTextは使えない（行内に50件超）


def service_hours_ng(now=None):
    """SUUMO入稿システムの利用可能時間外かを返す。→ 理由の文字列 or None。

    ★ログイン画面に明記されている：**8:00〜24:00（月曜は9:00〜24:00・日曜は8:00〜23:00）**。
      2026-08-18 の 7:17 に、時間外でログインを試して `login.action` に留まった。
      **エラー文言もパスワード欄も出ない**ので、画面からは理由が分からない
      （「原因不明」として調査に入りかけた。答えはログイン画面のテキストに書いてあった）。
      → 押す前に時計で判定する。失敗を繰り返すとアカウントロックの恐れがあるため
        （mikke名義で谷合さんは変更できない）。
    """
    import datetime
    n = now or datetime.datetime.now()
    w = n.weekday()                     # 0=月 … 6=日
    open_h = 9 if w == 0 else 8
    close_h = 23 if w == 6 else 24
    label = f"{'月火水木金土日'[w]}曜は {open_h}:00〜{close_h}:00"
    if n.hour < open_h:
        mins = (open_h - n.hour) * 60 - n.minute
        return f"利用時間外（{n:%H:%M}・{label}）。あと{mins}分で開く"
    if n.hour >= close_h:
        return f"利用時間外（{n:%H:%M}・{label}）"
    return None


def log_factory(prefix=""):
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:6.1f}s] {prefix}{msg}", flush=True)
    return log


def is_login_screen(page) -> bool:
    for fr in page.frames:
        try:
            if fr.locator("input[type=password]").count() > 0:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def session_dead(page) -> bool:
    """セッションが切れているか。★切れたら止めて人を呼ぶ（黙って再ログインしない）。"""
    try:
        txt = page.evaluate(
            "() => { let s=''; for (let i=0;i<window.frames.length;i++) {"
            " try { s += window.frames[i].document.body.innerText || ''; } catch(e) {} }"
            " return s; }")
    except Exception:  # noqa: BLE001
        return False
    return "セッションタイムアウト" in txt or "セッションが切れ" in txt


def main_frame(page):
    """操作対象は frames['main']。★window.frames は iterable ではないので name で取る。"""
    fr = page.frame(name="main")
    if fr is None:                       # ログイン直後などフレーム名が付いていないことがある
        for f in page.frames:
            try:
                if f.locator(TAB_KEISAI).count() or f.locator(
                        f'[name="${{keisaiSearchForm.bukkenCd}}"]').count():
                    return f
            except Exception:  # noqa: BLE001
                pass
    return fr


def read_codes(spec: str):
    """--codes は「ファイル」か「カンマ区切り」。→ 12桁コードのリスト（順序を保つ）。"""
    p = Path(spec).expanduser()
    raw = p.read_text(encoding="utf-8") if p.is_file() else spec
    codes, bad = [], []
    for tok in re.split(r"[,\s]+", raw):
        tok = tok.strip()
        if not tok or tok.startswith("#"):
            continue
        if re.fullmatch(r"\d{12}", tok):
            if tok not in codes:
                codes.append(tok)
        else:
            bad.append(tok)
    return codes, bad


# ── 導線 ──────────────────────────────────────────────────────
# ★依頼文§2-2 は「上部メニューは画像なので座標クリックが要る」。座標は画面幅で動くので、
#   まず**直リンク（action URL）が無いか**を読む。無ければ座標に落ちる。
NAVI_DUMP = """() => {
  const out = {frames: window.frames.length, names: [], navi: null};
  const fs = document.querySelectorAll('frame, iframe');
  fs.forEach(f => out.names.push({name: f.name || '', src: (f.getAttribute('src')||'').slice(0,70)}));
  let nd = null;
  try { nd = window.frames['navi'].document; } catch (e) { return out; }
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  out.navi = {
    links: [...nd.querySelectorAll('a[href], area[href]')].filter(vis).map(e => ({
      tag: e.tagName, text: (e.textContent||'').trim().slice(0,20),
      href: (e.getAttribute('href')||'').slice(0,90),
      onclick: (e.getAttribute('onclick')||'').slice(0,90),
      alt: (e.querySelector && e.querySelector('img') ? (e.querySelector('img').alt||'') : '')
    })),
    imgs: [...nd.querySelectorAll('img')].filter(vis).map(e => ({
      alt: e.alt||'', name: e.getAttribute('name')||'', id: e.id||'',
      src: (e.getAttribute('src')||'').split('/').pop(),
      onclick: (e.getAttribute('onclick')||'').slice(0,90),
      x: Math.round(e.getBoundingClientRect().left), y: Math.round(e.getBoundingClientRect().top),
      w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height)
    })),
    maps: [...nd.querySelectorAll('map area')].map(e => ({
      alt: e.alt||'', coords: e.getAttribute('coords')||'',
      href: (e.getAttribute('href')||'').slice(0,90),
      onclick: (e.getAttribute('onclick')||'').slice(0,90)}))
  };
  return out;
}"""

MAIN_DUMP = """() => {
  const d = window.frames['main'] ? window.frames['main'].document : document;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const nm = s => [...d.getElementsByName(s)].length;
  const sel = id => { const e = d.getElementById(id);
    return e ? {found: true, value: e.value,
                options: [...e.options].map(o => o.value + '=' + (o.text||'').trim())} : {found: false}; };
  return {
    title: d.title,
    tab3: d.querySelectorAll('a.dsSameActionReload-Tab3').length,
    tabs: [...d.querySelectorAll('a[class*=Tab]')].map(e => ({
      cls: e.className, text: (e.textContent||'').trim().slice(0,14)})),
    bukkenCd: nm('${keisaiSearchForm.bukkenCd}'),
    bukkenNm: nm('${keisaiSearchForm.bukkenNm}'),
    searchBtn: [...d.querySelectorAll('input.jokenSearchBtn')].map(e => e.value),
    changeShiji: d.querySelectorAll('input[name=changeShiji]').length,
    otherChecks: [...d.querySelectorAll('input[type=checkbox]')].filter(vis)
      .filter(e => e.name !== 'changeShiji')
      .map(e => ({name: e.name||'', id: e.id||'', checked: e.checked})),
    selectShijiU: sel('selectShijiU'),
    flgValueU: sel('flgValue_shijiFlg1U'),
    shijiButton: !!d.getElementById('shijiButton'),
    shijiSuru: [...d.querySelectorAll('input[type=button]')].filter(vis)
      .map(e => e.value).filter(v => v && v.length < 14),
    kensuLinks: [...d.querySelectorAll('a')].filter(vis)
      .map(e => (e.textContent||'').replace(/\\s+/g,''))
      .filter(t => /現在掲載|件表示|掲載指示済|掲載中/.test(t)).slice(0, 14),
    bodyHead: (d.body.innerText||'').replace(/\\s+/g,' ').slice(0, 300)
  };
}"""


# ★実測（2026-08-14）：メニューの番号は総当たりで確定させた。
#   MNU1R0000=TOP / 0001=新規物件登録 / **0002=情報更新一覧（＝更新・掲載指示）** /
#   0008=お役立ち / 0009=物件一括操作 / 0010=クライアントログ / 0011=管理 / 0012=オーナーレポート
MENU_KOSHIN = "MNU1R0002_f.action"


# 行の読み取り。★物件コードは**行内の hidden input** から取る（2026-08-14 実測）。
#
#   <input type="hidden" name="${keisaiShijiOrderInfo[2].bukkenCd}"  value="100485546236">
#   <input type="hidden" name="${keisaiShijiOrderInfo[2].shijiFlg1}" value="0">   0=保留 1=掲載
#   <input type="checkbox" name="changeShiji" id="ikkatsuShiji_3" checked>
#
# ★依頼文§3-3 は「行の innerHTML から \b(1005\d{8})\b を拾う」としていたが、**これは危ない**：
#   1) **既存物件のコードは 1004 始まり**（実測 100485546236 / 100499179639 / 100492434649）。
#      1005 は我々が8/13〜14に登録した分の番号帯にすぎず、29行中6行が取りこぼされた。
#   2) 行内には画像URL（/online/img/bukken/236/100485546236/…）にもコードが出るので、
#      正規表現の最初のヒットが本当にその行の物件コードとは限らない。
#   3) **表示順と配列インデックスがずれる**（表示3,4,6,7 に対し配列は [2],[3],[5],[6]）。
#      「i番目の行」で対応づけてはいけない。
#   → hidden の bukkenCd を唯一の出所にする。現在の指示値も同じ行から取れる。
ROW_DUMP = """() => {
  const d = window.frames['main'].document;
  const boxes = [...d.querySelectorAll('input[name=changeShiji]')];
  // ★name は `${keisaiShijiOrderInfo[2].bukkenCd}` で**末尾が } **。
  //   endsWith('.bukkenCd') では外れる（実際に全行 null になった）。
  const pick = (tr, suffix) => {
    const e = [...tr.querySelectorAll('input[type=hidden]')].find(
        x => (x.name || '').includes('.' + suffix + '}'));
    return e ? e.value : null;
  };
  return boxes.map((c, i) => {
    const tr = c.closest('tr');
    if (!tr) return {i: i, code: null, err: 'trが無い'};
    const txt = (tr.innerText || '').replace(/\\s+/g, ' ').trim();
    return {
      i: i,
      cbId: c.id || '',
      code: pick(tr, 'bukkenCd'),
      shijiFlg1: pick(tr, 'shijiFlg1'),
      shijiFlg2: pick(tr, 'shijiFlg2'),
      checked: c.checked,
      disabled: c.disabled,
      text: txt.slice(0, 170)
    };
  });
}"""


# 一覧の列見出し。★列の意味を推測しない（「12 32 0.1 0」がどれなのか目で決めない）。
HEADER_DUMP = """() => {
  const d = window.frames['main'].document;
  const box = d.querySelector('input[name=changeShiji]');
  if (!box) return null;
  const tbl = box.closest('table');
  if (!tbl) return null;
  const heads = [...tbl.querySelectorAll('tr')].slice(0, 4).map(
      tr => [...tr.querySelectorAll('th,td')].map(
          c => (c.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 16)));
  const row = box.closest('tr');
  const cells = row ? [...row.querySelectorAll('td')].map(
      c => (c.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 20)) : [];
  return {heads: heads, cells: cells, cellCount: cells.length};
}"""

# 表示件数の切り替え。★どの要素かは未確認なので候補を洗い出す
KENSU_DUMP = """() => {
  const d = window.frames['main'].document;
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  return {
    selects: [...d.querySelectorAll('select')].filter(vis)
      .filter(e => [...e.options].some(o => /\\b(50|100|200)\\b/.test(o.text || o.value)))
      .map(e => ({name: e.name || '', id: e.id || '', value: e.value,
                  options: [...e.options].map(o => o.value + '=' + (o.text || '').trim())})),
    links: [...d.querySelectorAll('a')].filter(vis)
      .map(e => ({text: (e.textContent || '').replace(/\\s+/g, ''),
                  href: (e.getAttribute('href') || '').slice(0, 50),
                  onclick: (e.getAttribute('onclick') || '').slice(0, 60)}))
      .filter(x => /件|表示|ページ|次|前/.test(x.text) && x.text.length < 12).slice(0, 20),
    pager: (d.body.innerText || '').replace(/\\s+/g, ' ')
             .match(/[\\d,]+件[^|]{0,40}/g) || []
  };
}"""


# ★物件コードの在処を突き止めるための調査。行のinnerHTMLに無い行が実在した（29行中6行）。
CODE_HUNT = """() => {
  const d = window.frames['main'].document;
  const boxes = [...d.querySelectorAll('input[name=changeShiji]')];
  const digits = h => [...new Set((h.match(/\\d{9,14}/g) || []))].slice(0, 8);
  return boxes.slice(0, 4).map((c, i) => {
    const tr = c.closest('tr');
    const html = tr ? tr.innerHTML : '';
    return {
      i: i,
      cb: {value: c.value, name: c.name, id: c.id || '',
           attrs: [...c.attributes].map(a => a.name + '=' + String(a.value).slice(0, 30))},
      hidden: tr ? [...tr.querySelectorAll('input[type=hidden]')].map(
          e => (e.name || e.id || '?') + '=' + String(e.value).slice(0, 24)) : [],
      links: tr ? [...tr.querySelectorAll('a[href],a[onclick]')].map(
          e => ((e.getAttribute('href') || '') + '|' + (e.getAttribute('onclick') || '')).slice(0, 90)
        ).slice(0, 4) : [],
      digits_in_row: digits(html),
      rowlen: html.length
    };
  });
}"""


def goto_keisai(page, log):
    """情報更新一覧 → 掲載指示一覧タブ まで移動する。→ (mainフレーム, 到達したか)。

    ★依頼文§2-2 は「上部メニューは画像なので座標クリックが要る」としていたが、実測では
      `<a href="MNU1R00NN_f.action">` のリンクだった。座標は使わない（画面幅で動くため）。
    """
    fr = main_frame(page)
    if fr is not None and fr.locator(TAB_KEISAI).count() == 0:
        log(f"  情報更新一覧へ移動（{MENU_KOSHIN}）")
        if not page.evaluate(MENU_PROBE, MENU_KOSHIN):
            log("  ★メニューのリンクが見つからない")
            return fr, False
        page.wait_for_timeout(3000)
        fr = main_frame(page)
    if fr is None:
        log("  ★main フレームが見つからない")
        return None, False
    n = fr.locator(TAB_KEISAI).count()
    if n == 0:
        log(f"  ★掲載指示一覧タブ（{TAB_KEISAI}）が無い。title={fr.title()!r}")
        return fr, False
    fr.locator(TAB_KEISAI).first.click()
    page.wait_for_timeout(3000)
    fr = main_frame(page)
    ttl = fr.title() if fr else "?"
    log(f"  {TAB_KEISAI} を押した → title={ttl!r}")
    return fr, ttl == "掲載指示一覧"


# ★実測（2026-08-14）：naviメニューは**画像ではなくリンク**だった。
#   依頼文§2-2 の「画像なのでDOMから辿れない・座標クリックが要る」は誤り。
#   `<a href="MNU1R00NN_f.action?id=...">` が9本あり、text も alt も空。
#   → **番号でしか区別できない**ので、1度だけ総当たりして番号を確定させる（--probe-menu）。
# ★href に毎回変わるセッションid（?id=...）が付く。完全一致で探すと1回移動した時点で
#   全部外れる（実測）。**MNU番号の前方一致**で、毎回その場のDOMから取り直す。
MENU_PROBE = """(prefix) => {
  const nd = window.frames['navi'].document;
  const a = [...nd.querySelectorAll('a[href]')].find(
      e => (e.getAttribute('href')||'').startsWith(prefix));
  if (!a) return false;
  a.click();
  return true;
}"""


def frame_slots(page):
    """TOP画面の掲載枠を読む。→ {'指示': n, '枠': n, '残り': n} or None。

    ★§3-2「枠が埋まっていると掲載に指示できない」の事前確認に使う。
      8/26は45枠が新規に開くので起きないはずだが、**枠数は必ず読む**（推測しない）。
    """
    try:
        txt = page.evaluate(
            "() => { const d = window.frames['main'].document;"
            " return (d.body.innerText||'').replace(/\\s+/g,' '); }")
    except Exception:  # noqa: BLE001
        return None
    # ★表記が画面で違う（実測）。TOPと掲載指示一覧の両方に対応する。
    #   TOP:        「ネット掲載 30 指示 / 30 枠 残り0 枠」
    #   掲載指示一覧: 「枠状況 30 指示 残り0 枠 …」（ネットが先頭の1組目）
    m = re.search(r"ネット掲載\s*(\d+)\s*指示\s*/\s*(\d+)\s*枠\s*残り\s*(\d+)\s*枠", txt)
    if m:
        return {"指示": int(m.group(1)), "枠": int(m.group(2)), "残り": int(m.group(3)),
                "出所": "TOP"}
    m = re.search(r"枠状況\s*(\d+)\s*指示\s*残り\s*(\d+)\s*枠", txt)
    if m:
        shiji, nokori = int(m.group(1)), int(m.group(2))
        return {"指示": shiji, "枠": shiji + nokori, "残り": nokori, "出所": "掲載指示一覧"}
    return None


def probe_menu(page, log):
    """naviの各メニューを1度だけ辿って、href → main の title を確定させる。

    ★読み取りのみ（メニュー移動だけで、何も保存しない）。ログアウトは踏まない。
    """
    nav = page.evaluate(NAVI_DUMP)
    hrefs = sorted({re.match(r"(MNU1R\d+_f\.action)", x["href"]).group(1)
                    for x in (nav.get("navi") or {}).get("links", [])
                    if re.match(r"MNU1R\d+_f\.action", x["href"])})
    log(f"  メニュー候補 {len(hrefs)}本を辿る（ログアウトは踏まない）")
    found = {}
    for h in hrefs:
        try:
            if not page.evaluate(MENU_PROBE, h):
                log(f"    {h}: リンクが見つからない（画面が変わった）")
                continue
            page.wait_for_timeout(2600)
            info = page.evaluate(
                "() => { const d = window.frames['main'].document;"
                " return {title: d.title,"
                "  tabs: [...d.querySelectorAll('a[class*=Tab]')].map(e => e.className),"
                "  head: (d.body.innerText||'').replace(/\\s+/g,' ').slice(0,60)}; }")
            found[h] = info
            log(f"    {h}: title={info['title']!r} tabs={len(info['tabs'])} "
                f"head={info['head'][:44]!r}")
        except Exception as e:  # noqa: BLE001
            log(f"    {h}: 例外 {type(e).__name__}: {str(e)[:60]}")
    return found


def recon(page, log):
    """画面構造を読むだけ。★何も変更しない（クリックは移動のみ）。"""
    log("① frameset と navi メニュー")
    nav = page.evaluate(NAVI_DUMP)
    log(f"  frames={nav['frames']} / {nav['names']}")
    if nav.get("navi"):
        n = nav["navi"]
        log(f"  navi: リンク{len(n['links'])} / 画像{len(n['imgs'])} / imagemap area{len(n['maps'])}")
        for x in n["links"]:
            log(f"    A  text={x['text']!r} alt={x['alt']!r} href={x['href']!r} onclick={x['onclick']!r}")
        for x in n["imgs"]:
            log(f"    IMG alt={x['alt']!r} name={x['name']!r} src={x['src']!r} "
                f"({x['x']},{x['y']} {x['w']}x{x['h']}) onclick={x['onclick']!r}")
        for x in n["maps"]:
            log(f"    AREA alt={x['alt']!r} coords={x['coords']!r} href={x['href']!r} "
                f"onclick={x['onclick']!r}")
    else:
        log("  ★navi フレームを読めない")
    log("② main フレームの現状")
    m = page.evaluate(MAIN_DUMP)
    for k in ("title", "tab3", "tabs", "bukkenCd", "bukkenNm", "searchBtn", "changeShiji",
              "otherChecks", "selectShijiU", "flgValueU", "shijiButton", "shijiSuru",
              "kensuLinks"):
        log(f"  {k}: {m[k]}")
    log(f"  本文の先頭: {m['bodyHead']}")
    sl = frame_slots(page)
    log(f"③ 掲載枠（TOP画面から）: {sl}")
    log("④ naviメニューの番号を確定させる")
    menus = probe_menu(page, log)
    log("⑤ 掲載指示一覧まで移動して、依頼文§2-3/2-4のセレクタが実在するか見る")
    fr, ok = goto_keisai(page, log)
    m2 = page.evaluate(MAIN_DUMP)
    for k in ("title", "tab3", "bukkenCd", "bukkenNm", "searchBtn", "changeShiji",
              "otherChecks", "selectShijiU", "flgValueU", "shijiButton", "shijiSuru",
              "kensuLinks"):
        log(f"  {k}: {m2[k]}")
    log(f"  本文の先頭: {m2['bodyHead'][:200]}")
    log(f"  → 掲載指示一覧に到達: {ok}")
    log("⑥ 表示件数の切り替え（§4-1『200件にしてから』の実体を探す）")
    k = page.evaluate(KENSU_DUMP)
    log(f"  件数のselect: {k['selects']}")
    log(f"  それらしいリンク: {k['links']}")
    log(f"  件数の表記: {k['pager'][:6]}")
    log("⑦ 行の読み取り（hidden の bukkenCd から）")
    rows = page.evaluate(ROW_DUMP)
    got = [r for r in rows if r["code"]]
    log(f"  行数={len(rows)} / コードが取れた行={len(got)}")
    pre = {}
    for r in got:
        pre[r["code"][:4]] = pre.get(r["code"][:4], 0) + 1
    log(f"  コードの先頭4桁の内訳: {pre}  ★1005決め打ちだと取りこぼす")
    for r in rows[:3]:
        log(f"    [{r['i']}] id={r['cbId']} code={r['code']!r} "
            f"ネット指示={r['shijiFlg1']!r} checked={r['checked']}")
        log(f"         {r['text'][:110]!r}")
    log("⑨ 一覧の列見出し（列の意味を推測しないため）")
    h = page.evaluate(HEADER_DUMP)
    if h:
        for i, hd in enumerate(h["heads"]):
            log(f"    見出し行{i}: {hd}")
        log(f"    1行目のセル({h['cellCount']}個): {h['cells']}")
    else:
        log("    ★見出しを読めない")
    log("⑧ ★物件コードの在処を突き止める（行のinnerHTMLに無い行が6件あった）")
    for h in page.evaluate(CODE_HUNT):
        log(f"    [{h['i']}] rowlen={h['rowlen']} 行内の9〜14桁={h['digits_in_row']}")
        log(f"         checkbox: {h['cb']}")
        log(f"         hidden: {h['hidden']}")
        log(f"         links: {h['links']}")
    return nav, m, menus, m2


def try_autologin(page, log) -> bool:
    """ブラウザの自動入力が効いている場合に限りログインボタンを押す。
    ★パスワードの値は読まない・出力しない・入力しない。入っているか（長さ>0）だけ見る。
      suumo_register.try_autologin と同じ方針（押せるものが4系統あるので候補を並べる）。"""
    for fr in page.frames:
        try:
            pw = fr.locator("input[type=password]")
            if pw.count() == 0:
                continue
            filled = pw.first.evaluate("e => (e.value || '').length > 0")
            idf = fr.locator("input[type=text]")
            id_filled = (idf.count() > 0
                         and idf.first.evaluate("e => (e.value || '').length > 0"))
            log(f"  自動入力の状態: ID={'あり' if id_filled else 'なし'} "
                f"パスワード={'あり' if filled else 'なし'}")
            if not (filled and id_filled):
                return False
            for sel in ("input.loginButton", "input[type=image][id=Image7]",
                        "input[type=image]", "input[type=submit]"):
                b = fr.locator(sel)
                for i in range(b.count()):
                    if b.nth(i).is_visible():
                        log(f"  ログインボタンを押す（{sel}）")
                        b.nth(i).click()
                        page.wait_for_timeout(4000)
                        return True
            log("  ★ログインボタンが見つからない")
            return False
        except Exception:  # noqa: BLE001
            continue
    return False


# ── 検証（--verify）─────────────────────────────────────────────
# ★必ず「現在掲載指示済」フィルタ経由で読む。
#   「現在掲載中」は**反映が1日遅れる**ので、指示直後は前日の状態が出る。
#   2026-08-17 に、フィルタ前の一覧（29行）とフィルタ後（30行）で母集団が食い違い、
#   「26室が存在しない」という誤った観察をした。**どちらを見ているかを明示する。**
FILTER_SHIJIZUMI_JS = """() => {
  const d = window.frames['main'].document;
  const a = [...d.querySelectorAll('a')].find(
      e => (e.innerText || '').replace(/\\s+/g, '') === '現在掲載指示済');
  if (!a) return {ok: false, why: '『現在掲載指示済』のリンクが無い'};
  a.click();
  return {ok: true};
}"""

# 表示件数。★45室でも既定50件なら1ページに載るが、75室になると載らない。
SET_PAGE_SIZE_JS = """(n) => {
  const d = window.frames['main'].document;
  const sels = [...d.querySelectorAll('select')].filter(
      e => [...e.options].some(o => String(o.value) === String(n)));
  if (!sels.length) return {ok: false, why: '件数のselectが無い'};
  const s0 = sels[0];
  if (String(s0.value) === String(n)) return {ok: true, already: true, value: s0.value};
  s0.value = String(n);
  s0.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, already: false, value: s0.value};
}"""


def read_shijizumi(page, log, page_size=200):
    """「現在掲載指示済」の一覧を読む。→ (｛コード: 行情報｝, 該当件数) or (None, None)。

    ★戻り値の None は「読めなかった」＝**照合不能**。空辞書（0件）とは区別する。
    """
    fr, ok = goto_keisai(page, log)
    if not ok:
        log("  ★掲載指示一覧に到達できない")
        return None, None
    ps = page.evaluate(SET_PAGE_SIZE_JS, page_size)
    log(f"  表示件数を{page_size}件に: {ps}")
    if ps.get("ok") and not ps.get("already"):
        page.wait_for_timeout(3500)
    r = page.evaluate(FILTER_SHIJIZUMI_JS)
    if not r.get("ok"):
        log(f"  ★フィルタできない: {r}")
        return None, None
    page.wait_for_timeout(3800)
    if session_dead(page):
        log("  ✗ セッションが切れた")
        return None, None
    n = hit_count(page)
    rows = page.evaluate(CELLS_JS)
    out = {}
    for x in rows:
        if not x["code"]:
            continue
        c = x["cells"]
        out[x["code"]] = {
            "物件": c[COL["物件"]] if len(c) > COL["物件"] else "",
            "指示ネット": c[COL["指示ネット"]] if len(c) > COL["指示ネット"] else "",
            "掲載終了日": c[COL["掲載終了日"]] if len(c) > COL["掲載終了日"] else "",
        }
    log(f"  現在掲載指示済: 該当{n}件 / 行{len(rows)} / コードが取れた{len(out)}件")
    # ★行はあるのにコードが取れない＝読み取りが壊れている。空と混同しない
    if rows and not out:
        log("  ★行はあるのに物件コードが1件も取れない（読み取りが壊れている）")
        return None, n
    return out, n


def verify(page, codes, dropped, expect_total, log, page_size=200):
    """★掲載指示の検証。→ 終了コード（0=合格 / 1=不一致 / 3=照合不能）。

    受入基準（2026-08-17 谷合さんの指示）:
      1. 照合対象がゼロ件なら「合格」ではなく**照合不能**。終了コードを分ける
      2. 実行前後でコード集合が一致しない（＝空を含む）なら失敗
      3. **「現在掲載指示済」フィルタ経由**で読む（「現在掲載中」は1日遅れる）
      4. 指示した室が全部見つかることと、落とした室が1件も残っていないことを
         **別々に**判定する
    """
    got, n = read_shijizumi(page, log, page_size)
    # ── 基準1：読めなかった／0件は「照合不能」。合格に倒さない ──
    if got is None:
        log("")
        log("■ 判定: ★照合不能（一覧を読めなかった）")
        log("  『問題なし』ではない。人が画面を見ること")
        return 3
    if not got:
        log("")
        log(f"■ 判定: ★照合不能（現在掲載指示済が0件・該当{n}件）")
        log("  指示が1件も入っていないのか、読み取りが外れているのか区別できない")
        return 3
    # ── 基準4：2つを別々に判定する ──
    missing = [c for c in codes if c not in got]
    remaining = [c for c in (dropped or []) if c in got]
    log("")
    log(f"■ 現在掲載指示済 {len(got)}件")
    for c in codes:
        v = got.get(c)
        log(f"   {'◯' if v else '✗'} {c}  " +
            (f"{v['物件'][:28]:<30}指示={v['指示ネット']} 終了日={v['掲載終了日']}"
             if v else "★見つからない"))
    for c in (dropped or []):
        v = got.get(c)
        log(f"   {'✗' if v else '◯'} 落とした室 {c}  " +
            ("★まだ残っている" if v else "残っていない"))
    ng = []
    log("")
    log(f"■ 判定（4項目を別々に）")
    log(f"   1. 読み取り: {len(got)}件（照合可能）")
    ok2 = not missing
    log(f"   2. 指示した室が全部ある: {'◯' if ok2 else '✗'} "
        f"（{len(codes) - len(missing)}/{len(codes)}）"
        + (f"  見つからない: {missing}" if missing else ""))
    if not ok2:
        ng.append("指示した室が見つからない")
    ok3 = not remaining
    if dropped:
        log(f"   3. 落とした室が1件も残っていない: {'◯' if ok3 else '✗'}"
            + (f"  残っている: {remaining}" if remaining else ""))
        if not ok3:
            ng.append("落とした室が残っている")
    else:
        log("   3. 落とした室: 指定なし（判定しない）")
    if expect_total is not None:
        ok4 = len(got) == expect_total
        log(f"   4. 件数が期待どおり: {'◯' if ok4 else '✗'} "
            f"（{len(got)} / 期待{expect_total}）")
        if not ok4:
            ng.append(f"件数が {len(got)}（期待{expect_total}）")
    else:
        log(f"   4. 件数: {len(got)}件（--expect-total 未指定なので判定しない）")
    log("")
    if ng:
        log(f"■ ✗ 不一致 {len(ng)}件: {ng}")
        return 1
    log("■ ◯ 合格")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="SUUMOの掲載指示を出す（登録は suumo_register.py）")
    ap.add_argument("--recon", action="store_true", help="画面構造を読むだけ（何も変えない）")
    ap.add_argument("--login", action="store_true", help="ブラウザを開いて人がログインする")
    ap.add_argument("--autologin", action="store_true",
                    help="ブラウザの自動入力が効いていればログインボタンを押す（値は読まない）")
    ap.add_argument("--shiji", choices=list(SHIJI), help="掲載 か 保留")
    ap.add_argument("--verify", action="store_true", help="『現在掲載指示済』で照合する")
    ap.add_argument("--codes", help="物件コード12桁。ファイルかカンマ区切り")
    ap.add_argument("--dropped", dest="dropped_raw",
                    help="--verify で『落とした室』として1件も残っていないことを確かめる12桁")
    ap.add_argument("--expect-total", type=int, default=None,
                    help="--verify で現在掲載指示済の総件数の期待値")
    ap.add_argument("--page-size", type=int, default=200,
                    help="--verify の表示件数（既定200）")
    ap.add_argument("--dup-test", nargs=2, metavar=("物件名", "対象コード"),
                    help="★受入基準3：物件名で検索して重複レコードを両方出した状態で、"
                         "指定した側だけがチェックされることを検証する")
    ap.add_argument("--confirm-write", action="store_true",
                    help="★これが無いと『一括更新実行』を押さない＝保存されない。"
                         "経路の確認だけしたいときは付けないこと")
    ap.add_argument("--dry-run", action="store_true",
                    help="検索してヒットした室を出すだけ。指示は出さない")
    ap.add_argument("--profile", default=str(PROFILE))
    ap.add_argument("--url", default=URL)
    ap.add_argument("--login-wait", type=int, default=1800)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    if not (a.recon or a.login or a.shiji or a.verify or a.dry_run or a.dup_test):
        ap.error("--recon / --login / --shiji / --verify のどれかが要る")
    log = log_factory()

    codes = []
    if a.codes:
        codes, bad = read_codes(a.codes)
        if bad:
            log(f"✗ 12桁の物件コードでない指定が {len(bad)}件: {bad[:5]}")
            log("  ★部屋キーや物件名では特定しない（同じ部屋が複数レコードある）")
            return 2
        log(f"対象 {len(codes)}件: {codes[:6]}{' …' if len(codes) > 6 else ''}")
    # ★--dup-test は対象を引数で受けるので --codes は要らない
    a.dropped_codes = []
    if getattr(a, "dropped_raw", None):
        a.dropped_codes, dbad = read_codes(a.dropped_raw)
        if dbad:
            log(f"✗ --dropped に12桁でない指定: {dbad[:5]}")
            return 2
        log(f"落とした室 {len(a.dropped_codes)}件")
    if (a.shiji or a.verify or a.dry_run) and not codes and not a.dup_test:
        ap.error("--shiji / --verify には --codes が要る")

    # ★利用時間外なら**ブラウザを開く前に**止める（ログイン失敗を積まない）
    _hrs = service_hours_ng()
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
            if is_login_screen(page):
                if a.autologin and try_autologin(page, log):
                    pass
                elif a.login:
                    log(f"ブラウザを開きました。ログインしてください（最大{a.login_wait}秒）")
                    t0 = time.time()
                    while time.time() - t0 < a.login_wait and is_login_screen(page):
                        page.wait_for_timeout(3000)
                else:
                    log("✗ ログイン画面です。--login か --autologin を付けて実行すること")
                    return 2
            if is_login_screen(page):
                log("✗ ログインを確認できませんでした")
                return 2
            if session_dead(page):
                log("✗ セッションが切れています。人がログインし直すこと（自動で再試行しない）")
                return 2
            log(f"ログイン確認。url={page.url[-50:]}")
            if a.recon:
                recon(page, log)
                log("★recon は読み取りのみ。何も変更していない")
                if not a.headless:
                    log("ブラウザは開いたままにします（60秒）")
                    page.wait_for_timeout(60000)
                return 0
            if a.dup_test:
                name, target = a.dup_test
                if not re.fullmatch(r"\d{12}", target):
                    log(f"✗ 対象は12桁の物件コード（{target!r}）")
                    return 2
                ok2, detail = dup_test(page, name, target, log,
                                       execute=a.confirm_write)
                if not ok2:
                    log(f"✗ {detail}")
                    return 1
                log(f"  {detail}")
                if not a.confirm_write:
                    log("★--confirm-write が無いので送信していない")
                    return 0
                # ★2段階（恒久ルール1）。指示する → 一括更新実行 → 完了画面
                sv = page.evaluate(SET_SELECT_JS, SHIJI[a.shiji or "保留"])
                log(f"  select: {sv}")
                cs = page.evaluate(CLICK_SHIJI_JS)
                log(f"  1段目『指示する』: {cs}")
                page.wait_for_timeout(1800)
                ex = page.evaluate(EXEC_JS)
                log(f"  2段目『一括更新実行』: {ex}")
                page.wait_for_timeout(4500)
                done = read_done(page)
                log(f"  完了画面: title={done['title']!r} 枠={done.get('枠')} "
                    f"指示={done.get('指示')} 残り={done.get('残り')}")
                if done["title"] != "掲載指示完了":
                    log(f"✗ 完了画面に遷移していない: {done['text'][:120]}")
                    return 1
                log("■ 実行後の状態を読み直す")
                # ★完了画面には検索フォームが無い。**一覧へ戻ってから**読み直す。
                #   戻らずに読むと結果が空になり、「対象外は変わっていない」が
                #   **何も検証しないまま通る**（2026-08-17 に実際に偽の合格を出した）。
                fr2, ok3 = goto_keisai(page, log)
                if not ok3:
                    log("✗ 一覧に戻れないので照合できない")
                    return 1
                rows, n = search_name(page, name, log)
                after = {r["code"]: r["shijiFlg1"] for r in (rows or []) if r["code"]}
                log(f"  実行後の指示値: {after}")
                before = detail["before"]
                # ★空なら「変化なし」ではなく**照合できていない**。合格にしてはいけない。
                if set(after) != set(before):
                    log(f"✗ 照合できない（実行前={sorted(before)} 実行後={sorted(after)}）")
                    return 1
                bad = [c for c in after if c != target and before.get(c) != after.get(c)]
                for c in sorted(after):
                    mark = "← 対象" if c == target else ""
                    log(f"   {c}: {before.get(c)!r} → {after.get(c)!r} {mark}")
                if bad:
                    log(f"✗ ★対象外のレコードが変わった: {bad}")
                    return 1
                log("◯ 対象外のレコードは1件も変わっていない（受入基準3）")
                return 0
            if a.dry_run or (a.shiji and a.dry_run):
                return dry_run(page, codes, log)
            if a.shiji:
                fr, ok = goto_keisai(page, log)
                if not ok:
                    log("✗ 掲載指示一覧に到達できない")
                    return 2
                sl = frame_slots(page)
                log(f"枠の状況（一覧から）: {sl}")
                if kind_needs_slot(a.shiji) and sl and sl.get("残り", 0) < len(codes):
                    log(f"✗ 空き枠 {sl.get('残り')} < 指示したい {len(codes)}室。"
                        "先に落とす分を『保留』にして枠を空けること（§3-2）")
                    return 2
                results = []
                for i, code in enumerate(codes, 1):
                    log(f"[{i}/{len(codes)}] {code} を『{a.shiji}』に")
                    ok2, detail = shiji_one(page, code, a.shiji, log,
                                            execute=a.confirm_write)
                    results.append((code, ok2, detail))
                    if not ok2:
                        log(f"   ✗ {detail}")
                        log("   ★ここで止める（部分適用しない）")
                        break
                    log(f"   ◯ {detail if isinstance(detail, str) else 'OK'}")
                ng = [c for c, o, _d in results if not o]
                log("")
                log(f"■ 成功 {sum(1 for _c, o, _d in results if o)}室 / 失敗 {len(ng)}室"
                    + ("" if a.confirm_write else "  ※--confirm-write が無いので保存していない"))
                return 0 if not ng else 1
            if a.verify:
                return verify(page, codes, a.dropped_codes, a.expect_total, log,
                              page_size=a.page_size)
            log("✗ 指定された動作が無い")
            return 1
        finally:
            if a.headless:
                ctx.close()



# ── 検索と一覧の読み取り ─────────────────────────────────────────
# 列の対応（2026-08-14 実測・見出しから確定）
#   [1] 沿線/駅 住所  [2] 交通数  [3] 物件名 部屋番号  [4] 賃料 管理 敷金 礼金
#   [5] 取引態様      [6] 「間取 外観 内観 全部 名寄せ」＝2つの数字
#   [9] 詳細PV/日     [10] 掲載終了日  [11] 掲載指示(ネット)  [12] 掲載指示(会社間)
COL = {"住所": 1, "交通数": 2, "物件": 3, "金額": 4, "態様": 5, "画像と名寄せ": 6,
       "PV": 9, "掲載終了日": 10, "指示ネット": 11, "指示会社間": 12}

SEARCH_JS = """(code) => {
  const d = window.frames['main'].document;
  const set = (nm, v) => { const es = [...d.getElementsByName(nm)];
    es.forEach(e => { e.value = v; }); return es.length; };
  // ★物件名は必ず空にする。前回の検索語が残ると絞り込みが二重にかかる
  const nNm = set('${keisaiSearchForm.bukkenNm}', '');
  const nCd = set('${keisaiSearchForm.bukkenCd}', code);
  const btn = [...d.querySelectorAll('input.jokenSearchBtn')].find(e => e.value === '検索');
  if (!btn) return {ok: false, why: '検索ボタンが無い', nCd: nCd, nNm: nNm};
  btn.click();
  return {ok: true, nCd: nCd, nNm: nNm};
}"""

CELLS_JS = """() => {
  const d = window.frames['main'].document;
  const pick = (tr, suffix) => {
    const e = [...tr.querySelectorAll('input[type=hidden]')].find(
        x => (x.name || '').includes('.' + suffix + '}'));
    return e ? e.value : null;
  };
  return [...d.querySelectorAll('input[name=changeShiji]')].map(c => {
    const tr = c.closest('tr');
    const cells = tr ? [...tr.querySelectorAll('td')].map(
        x => (x.innerText || '').replace(/\\s+/g, ' ').trim()) : [];
    return {code: tr ? pick(tr, 'bukkenCd') : null,
            shijiFlg1: tr ? pick(tr, 'shijiFlg1') : null,
            cbId: c.id || '', checked: c.checked, cells: cells};
  });
}"""


# ★受入基準3の検証用。**物件名で検索すると重複レコードが両方出る**（＝危険な配置）。
#   コード検索だと1行しか出ないので、絞り込みの正しさを試せない。
#   この経路で「両方表示されている状態でも対象だけがチェックされる」ことを確かめる。
SEARCH_NAME_JS = """(name) => {
  const d = window.frames['main'].document;
  const set = (nm, v) => { const es = [...d.getElementsByName(nm)];
    es.forEach(e => { e.value = v; }); return es.length; };
  set('${keisaiSearchForm.bukkenCd}', '');
  const n = set('${keisaiSearchForm.bukkenNm}', name);
  const btn = [...d.querySelectorAll('input.jokenSearchBtn')].find(e => e.value === '検索');
  if (!btn) return {ok: false, why: '検索ボタンが無い'};
  btn.click();
  return {ok: true, n: n};
}"""


def search_name(page, name, log):
    """物件名で検索する（部分一致）。→ (rows, 該当件数)。"""
    r = page.evaluate(SEARCH_NAME_JS, name)
    if not r.get("ok"):
        log(f"  ★検索できない: {r}")
        return [], None
    page.wait_for_timeout(3500)
    if session_dead(page):
        return None, None
    return page.evaluate(CELLS_JS), hit_count(page)


def dup_test(page, name, target, log, execute=False):
    """★受入基準3：重複レコードが**両方表示されている状態**で、指定した側だけを触れるか。

    2026-08-14 に、ある室が2レコード（同一室に2つの物件コード）見つかった。
    物件名で検索すると両方ヒットするので、一括チェックのまま操作すると
    **他人の掲載を止める事故**になる。
    """
    fr, ok = goto_keisai(page, log)
    if not ok:
        return False, "掲載指示一覧に到達できない"
    rows, n = search_name(page, name, log)
    if rows is None:
        return False, "セッション切れ"
    log(f"  物件名『{name}』で検索: 該当{n}件 / 行{len(rows)}")
    for r in rows:
        log("    " + fmt_row(r))
    codes = [r["code"] for r in rows if r["code"]]
    if target not in codes:
        return False, f"対象 {target} が結果に無い（{codes}）"
    if len(codes) < 2:
        return False, (f"重複が1件しか出ていない（{codes}）。"
                       "両方出ていない状態では受入基準3を検証できない")
    before = {r["code"]: r["shijiFlg1"] for r in rows if r["code"]}
    log(f"  実行前の指示値: {before}")
    ck = page.evaluate(CHECK_ONLY_JS, [target])
    log(f"  チェック: ON={ck['on']} OFF={ck['off']} 一括チェック解除={ck['masters']}")
    if ck["on"] != [target]:
        return False, f"対象だけをチェックできていない（ON={ck['on']}）"
    others = [c for c in codes if c != target]
    if any(o in ck["on"] for o in others):
        return False, f"他のレコードもチェックされている（{ck['on']}）"
    log(f"  ★同じ画面に {len(codes)}行あるが、ONは対象1件だけ・他{len(others)}件はOFF")
    if not execute:
        return True, {"before": before, "codes": codes,
                      "note": "チェックの検証まで（--confirm-write が無いので送信していない）"}
    return True, {"before": before, "codes": codes, "checked": ck["on"]}


def hit_count(page):
    """「N件該当しました。」を読む。→ int or None。"""
    try:
        txt = page.evaluate("() => (window.frames['main'].document.body.innerText||'')"
                            ".replace(/\\s+/g,' ')")
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"([\d,]+)件該当しました", txt)
    return int(m.group(1).replace(",", "")) if m else None


def search_code(page, code, log):
    """物件コードで検索して、ヒットした行を返す。→ (rows, 該当件数)。"""
    r = page.evaluate(SEARCH_JS, code)
    if not r.get("ok"):
        log(f"  ★検索できない: {r}")
        return [], None
    page.wait_for_timeout(3200)
    if session_dead(page):
        log("  ✗ セッションが切れた（止める）")
        return None, None
    return page.evaluate(CELLS_JS), hit_count(page)


def fmt_row(r):
    c = r["cells"]
    g = lambda k: c[COL[k]] if len(c) > COL[k] else ""    # noqa: E731
    shiji = {"1": "掲載", "0": "保留"}.get(str(r["shijiFlg1"]), f"?({r['shijiFlg1']})")
    return (f"{r['code']}  {g('物件')[:30]:<32}{g('金額')[:22]:<24}"
            f"画像/名寄せ={g('画像と名寄せ'):<8}現在={shiji:<4}"
            f"終了日={g('掲載終了日')[:12]}")


def dry_run(page, codes, log):
    """検索してヒットした室を出すだけ。★指示は出さない（受入基準1）。"""
    fr, ok = goto_keisai(page, log)
    if not ok:
        log("✗ 掲載指示一覧に到達できない")
        return 2
    miss, found = [], []
    for i, code in enumerate(codes, 1):
        rows, n = search_code(page, code, log)
        if rows is None:
            return 2
        hit = [r for r in rows if r["code"] == code]
        other = [r for r in rows if r["code"] != code]
        log(f"[{i}/{len(codes)}] {code}  該当{n}件 / 行{len(rows)}")
        for r in hit:
            log("   ◯ " + fmt_row(r))
        for r in other:
            # ★同じ検索結果に別コードの行が混ざる＝重複レコードの疑い。必ず出す
            log("   ※ 同じ結果に別コードの行: " + fmt_row(r))
        if not hit:
            log(f"   ✗ この物件コードの行が無い（該当{n}件）")
            miss.append(code)
        else:
            found.append(code)
    log("")
    log(f"■ ヒット {len(found)}件 / 見つからない {len(miss)}件")
    if miss:
        log(f"✗ 見つからない物件コードがあるので止める（部分適用しない）: {miss}")
        return 1
    return 0


# ── 掲載指示（書き込み）─────────────────────────────────────────
# ★依頼文§2-4 の4ステップ。ただし押す前に必ず実DOMを見る（推測でクリックしない）。
#   1) 対象コードの行だけ checked にする（★他のチェックボックスは全部外す）
#   2) selectShijiU = ネット + change
#   3) flgValue_shijiFlg1U = 掲載/保留 + change
#   4) 「指示する」→ shijiButton（一括更新実行）。**4を押すまで保存されない**
CHECK_ONLY_JS = """(codes) => {
  const d = window.frames['main'].document;
  const want = new Set(codes);
  const pick = (tr, suffix) => {
    const e = [...tr.querySelectorAll('input[type=hidden]')].find(
        x => (x.name || '').includes('.' + suffix + '}'));
    return e ? e.value : null;
  };
  const on = [], off = [];
  d.querySelectorAll('input[name=changeShiji]').forEach(c => {
    const tr = c.closest('tr');
    const code = tr ? pick(tr, 'bukkenCd') : null;
    // ★コードが取れない行は必ず false にする（既定ONのまま巻き込まないため）
    const hit = !!code && want.has(code);
    c.checked = hit;
    (hit ? on : off).push(code || '(コード不明)');
  });
  // ★§3-4 の一括チェック（keisaiIkkatsuCheck1/2 は既定ON・nameが空でidのみ）も外す
  const masters = [];
  d.querySelectorAll('input[type=checkbox]').forEach(c => {
    if (c.name === 'changeShiji') return;
    if (/^keisaiIkkatsuCheck/.test(c.id || '')) { c.checked = false; masters.push(c.id); }
  });
  return {on: on, off: off, masters: masters};
}"""

SET_SELECT_JS = """(v) => {
  const d = window.frames['main'].document;
  const s1 = d.getElementById('selectShijiU');
  const s2 = d.getElementById('flgValue_shijiFlg1U');
  if (!s1 || !s2) return {ok: false, why: 'selectが無い'};
  s1.value = 'shijiFlg1';
  s1.dispatchEvent(new Event('change', {bubbles: true}));
  s2.value = v;
  s2.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, s1: s1.value, s2: s2.value,
          s2opts: [...s2.options].map(o => o.value)};
}"""

CLICK_SHIJI_JS = """() => {
  const d = window.frames['main'].document;
  const b = [...d.querySelectorAll('input[type=button]')].filter(
      e => e.value === '指示する' && e.getBoundingClientRect().width > 0);
  if (!b.length) return {ok: false, why: '『指示する』が無い'};
  b[0].click();
  return {ok: true, n: b.length};
}"""

EXEC_JS = """() => {
  const d = window.frames['main'].document;
  const b = d.getElementById('shijiButton');
  if (!b) return {ok: false, why: 'shijiButton が無い'};
  b.click();
  return {ok: true};
}"""

DONE_JS = """() => {
  const d = window.frames['main'].document;
  const t = (d.body.innerText || '').replace(/\\s+/g, ' ');
  return {title: d.title, text: t.slice(0, 400)};
}"""


def kind_needs_slot(kind: str) -> bool:
    """『掲載』は枠を消費する。『保留』は枠を空ける側なので事前チェックは不要。"""
    return kind == "掲載"


def read_done(page):
    """完了画面の3数字を読む。→ {'枠': n, '指示': n, '残り': n, 'title': …} or None。"""
    d = page.evaluate(DONE_JS)
    m = re.search(r"ネット\s+(\d+)\s+(\d+)\s+(\d+)", d["text"])
    out = {"title": d["title"], "text": d["text"][:200]}
    if m:
        out.update({"枠": int(m.group(1)), "指示": int(m.group(2)),
                    "残り": int(m.group(3))})
    return out


def shiji_one(page, code, kind, log, execute=False):
    """1室に掲載指示を出す（one-by-one）。→ (ok, 詳細)。

    ★execute=False のときは「指示する」まで押して**保存しない**（shijiButtonを押さない）。
      画面を離れれば破棄されるので、経路の確認に使える（§3-1の「画面をまたぐと保存されない」）。
    """
    rows, n = search_code(page, code, log)
    if rows is None:
        return False, "セッション切れ"
    hit = [r for r in rows if r["code"] == code]
    if not hit:
        return False, f"この物件コードの行が無い（該当{n}件）"
    if len(rows) != 1:
        # コード検索は該当1件になるのが実測。増えていたら前提が壊れている
        log(f"  ※ コード検索の結果が {len(rows)}行ある（1行のはず: {[r['code'] for r in rows]}）")
    before = hit[0]["shijiFlg1"]
    ck = page.evaluate(CHECK_ONLY_JS, [code])
    log(f"  チェック: ON={ck['on']} OFF={ck['off']} 一括チェック解除={ck['masters']}")
    if ck["on"] != [code]:
        return False, f"対象だけをチェックできていない（ON={ck['on']}）"
    sv = page.evaluate(SET_SELECT_JS, SHIJI[kind])
    log(f"  select: {sv}")
    if not sv.get("ok") or sv.get("s2") != SHIJI[kind]:
        return False, f"selectを設定できない（{sv}）"
    cs = page.evaluate(CLICK_SHIJI_JS)
    log(f"  『指示する』: {cs}")
    if not cs.get("ok"):
        return False, str(cs)
    page.wait_for_timeout(1800)
    if not execute:
        return True, f"『指示する』まで実行（保存していない）。現在値={before}"
    ex = page.evaluate(EXEC_JS)
    log(f"  『一括更新実行』: {ex}")
    if not ex.get("ok"):
        return False, str(ex)
    page.wait_for_timeout(4000)
    done = read_done(page)
    log(f"  完了画面: title={done['title']!r} 枠={done.get('枠')} "
        f"指示={done.get('指示')} 残り={done.get('残り')}")
    if done["title"] != "掲載指示完了":
        return False, f"完了画面に遷移していない（title={done['title']!r}）: {done['text'][:120]}"
    return True, done


if __name__ == "__main__":
    sys.exit(main())
