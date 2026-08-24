// 検索条件を設定する（値は Python から渡す）。実測パラメータのみ使う。
(p) => {
  const setCb = (name, values) => {
    let n = 0;
    document.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(e => {
      const want = values.indexOf(e.value) >= 0;
      if (e.checked !== want) e.click();
      if (want && e.checked) n++;
    });
    return n;
  };
  const nWard = setCb('city_code[]', p.wards);
  const nLay = setCb('room_layout_id[]', p.layouts);
  const div = document.querySelector('input[name=diversion]');
  if (div && !div.checked) div.click();
  const sel = (n, v) => {
    const e = document.getElementsByName(n)[0];
    if (!e) return null;
    e.value = v;
    e.dispatchEvent(new Event('change', {bubbles: true}));
    return e.value;
  };
  return {ward: nWard, layout: nLay, diversion: div ? div.checked : null,
          lo: sel('rental_cost1', p.lo), hi: sel('rental_cost2', p.hi)};
}
