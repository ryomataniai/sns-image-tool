// リアプロの検索結果から1件ずつ収穫する。
// ★JSはPythonに埋め込まず別ファイルにする。ヒアドキュメント経由で埋め込むと
//   \n や \t がファイル書き込み時に実際の改行・タブになり、JSの文字列リテラルが壊れる
//   （実際に split('\n') が壊れて SyntaxError になった）。
(() => {
  const NL = String.fromCharCode(10);
  const norm = s => (s || '').replace(/[ \t　]+/g, ' ').trim();
  const anchors = Array.from(document.querySelectorAll('a[id^="factsheet_"][id$="_1"]'));
  const tbl = anchors.length ? anchors[0].closest('table.main_table_list2') : null;
  if (!tbl) return [];
  const all = norm(tbl.innerText).split(NL).map(norm).filter(Boolean);
  const starts = [];
  all.forEach((l, i) => { if (/号室$/.test(l)) starts.push(i); });
  const out = [];
  anchors.forEach((a, k) => {
    const id = a.id.replace('factsheet_', '').replace('_1', '');
    const s = starts[k];
    if (s === undefined) return;
    const e = (k + 1 < starts.length) ? starts[k + 1] : all.length;
    const blk = all.slice(s, e);
    const j = blk.join(' | ');
    const nm = (blk[0] || '').match(/^(.*?)\s*(\S+)号室$/);
    out.push({
      id: id,
      name: nm ? nm[1] : (blk[0] || ''),
      room: nm ? nm[2] : '',
      addr: ((j.match(/住所：([^|]+)/) || [])[1] || '').trim(),
      access: ((j.match(/沿線：([^|]+)/) || [])[1] || '').trim(),
      state: (j.match(/空室|退去予定|新築|建築中|定期借家|居住中/) || [''])[0],
      nyukyo: (j.match(/即入|相談|内装中|[0-9]{4}\/[0-9]{1,2}(\/[0-9]{1,2})?/) || [''])[0],
      layout: (j.match(/(ワンルーム|1LDK|1DK|1K|2LDK|2DK|2K|3LDK|3DK|3K)/) || [''])[0],
      area: ((j.match(/([0-9.]+)㎡/) || [])[1] || ''),
      rent: ((j.match(/㎡\s*\|?\s*([0-9,]+)円/) || [])[1] || ''),
      ad: ((j.match(/\[BK\]\s*([^|]+)/) || [])[1] || '').trim(),
      // 一覧の「お問合せ先」。★社名が出ない元付がいる（実測『◯◯オフィス:TEL0X-XXXX-XXXX』）。
      // その場合は支店名しか取れない＝社名はTELで見分けるか元付版PDFを見る。
      // 末尾の 'TEL' や区切り記号はここで落とす（社名の比較キーにするため）。
      agent: ((j.match(/お問合せ先\s*([^|]*?)\s*0\d[\d-]+/) || [])[1] || '')
             .replace(/[:：]?\s*TEL[:：]?\s*$/i, '').replace(/[\s:：]+$/, '').trim(),
      tel: ((j.match(/(0\d[\d-]{7,})/) || [])[1] || ''),
      updated: ((j.match(/更新日：([^|]+)/) || [])[1] || '').trim(),
      raw: j.slice(0, 240)
    });
  });
  return out;
})
