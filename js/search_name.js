// リアプロの検索フォームで「物件名」の欄を探して入力する。
// ★JSはPythonに埋め込まず別ファイルにする（harvest.js の冒頭と同じ理由）。
//
// ★このサイトの物件名欄の name 属性は**未実測**（2026-08-20 時点）。
//   賃料帯の検索（search.js）は実測済みだが、物件名での検索は使ったことがない。
//   → **決め打ちしない。**候補を集めて返し、確信が持てるときだけ入力する。
//   p.field が指定されていればそれを使う（人が実測して渡す経路）。
(p) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // 検索フォームの中のテキスト欄を全部集める（type未指定のinputも拾う）
  const inputs = Array.from(
      document.querySelectorAll('input[type=text], input:not([type]), input[type=search]'))
    .filter(vis);

  const describe = e => {
    // ラベルは「for」→ 親td/th → 直前の見出しセルの順で探す
    let label = '';
    if (e.id) {
      const l = document.querySelector('label[for="' + e.id + '"]');
      if (l) label = (l.textContent || '').replace(/\s+/g, ' ').trim();
    }
    if (!label) {
      const td = e.closest('td');
      const tr = e.closest('tr');
      const th = tr ? tr.querySelector('th') : null;
      if (th) label = (th.textContent || '').replace(/\s+/g, ' ').trim();
      else if (td && td.previousElementSibling) {
        label = (td.previousElementSibling.textContent || '').replace(/\s+/g, ' ').trim();
      }
    }
    return {
      name: e.name || '', id: e.id || '',
      placeholder: e.placeholder || '',
      label: label.slice(0, 40),
      maxlength: e.getAttribute('maxlength') || ''
    };
  };

  const cands = inputs.map(describe);

  // 人が渡した欄があればそれを使う（実測済みの経路）
  if (p && p.field) {
    const e = inputs.find(x => x.name === p.field || x.id === p.field);
    if (!e) return {ok: false, why: '指定された欄が見つからない', field: p.field, candidates: cands};
    e.value = p.name;
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true, used: e.name || e.id, by: '指定', value: e.value, candidates: cands};
  }

  // 自動判定は**名前・ラベル・placeholder のいずれかに物件名を示す語がある欄**に限る。
  // ★1つに絞れないときは入力しない（複数該当は人に返す）。
  const RX = /(物件名|建物名|マンション名|ビル名|bukken|building|estate|tatemono)/i;
  const hit = inputs.filter(e => {
    const d = describe(e);
    return RX.test(d.name) || RX.test(d.id) || RX.test(d.label) || RX.test(d.placeholder);
  });
  if (hit.length === 0) {
    return {ok: false, why: '物件名の欄を特定できない', candidates: cands};
  }
  if (hit.length > 1) {
    return {ok: false, why: '候補が複数あり1つに絞れない',
            hits: hit.map(describe), candidates: cands};
  }
  const e = hit[0];
  e.value = p.name;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, used: e.name || e.id, by: '自動判定', value: e.value,
          matched: describe(e), candidates: cands};
}
