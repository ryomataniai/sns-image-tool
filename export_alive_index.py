#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import-suumo-v1: リアプロ収穫JSON → 物件提案くんの生存確認インデックス（取得層）。

    python3 export_alive_index.py --harvest 07_分類結果/harvest_全件_20260814.json \
                                  --out alive_index.json

■何のためのファイルか
物件提案くん側（TypeScript）が「この室はまだリアプロに載っているか」を引くための索引。
室キー（room_key）で引けるようにするのがここの仕事で、**判定はしない**。
判定は scripts/check-alive-suumo.ts が持つ。

■★収穫条件を必ず一緒に出す（これがこのファイルの本体）
収穫は「4区 × 1K/1DK/1LDK × 賃料lo〜hi × web転載可」で絞っている。
**条件外の室は最初から載らない**ので、無いことを『消失』と呼ぶと誤報になる。
realpro_dl.py の _room_scope() と同じ考え方で、提案くん側にも同じ制約を効かせるため、
収穫の meta をそのまま scope として書き出す。

  実測（2026-08-14）: 登録済み130室の照合で34室が『無い』と出たが、内訳は
  既存物件28室・条件外4室・データ無し2室で、条件内で本当に消えている室はゼロだった。
  実測（2026-08-18）: mikke の既存25件のうち13件が収穫条件の外。
  これを消失と読むと**生きている物件を半分以上まとめて成約済みにする**。

■room_key を書き直さないこと
棟名の正規化（NFKC→タイプ接頭辞除去→記号除去→大文字化）と号室の先頭ゼロ除去は
realpro_dl.py が唯一の実装。TypeScript 側で作り直すと必ず片方が腐るので、
**索引をここで作って渡す**という形にしている。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from realpro_dl import room_key


_DATE_RX = re.compile(r"(20\d{2})[-/]?(\d{1,2})[-/]?(\d{1,2})")


def harvest_date(meta: dict, harvest_path: Path):
    """収穫日を決める。→ (YYYY-MM-DD, 出典) / 決められなければ (None, 理由)。

    ★これが statusCheckedAt になる。**実行日ではない。**
      「いつ操作したか」ではなく「いつ確認したか」を入れる。実行日を入れると、
      8/14の収穫を毎日流すだけで永遠に『今日確認済み』になり、鮮度ガードが
      一度も発火しない（= ガードを無効化する）。
      古い収穫を流したときに鮮度が戻らないのが正しい挙動。
    ★決められなければ None を返して索引を作らない。今日に倒さない。
    """
    m = _DATE_RX.search(str(meta.get("collected") or ""))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "meta.collected"
    # ファイル名の規約 harvest_全件_YYYYMMDD.json。推測ではなく命名規約なので使う
    m = _DATE_RX.search(harvest_path.stem)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}", "ファイル名"
    return None, "meta.collected にもファイル名にも日付が無い"


def build_index(harvest: dict, harvest_path: Path) -> dict:
    meta = harvest.get("meta") or {}
    rows = harvest.get("rows") or []

    hdate, hsrc = harvest_date(meta, harvest_path)
    if hdate is None:
        raise SystemExit(
            f"[error] 収穫日を決められません（{hsrc}）。\n"
            f"        収穫日は statusCheckedAt になる値なので、実行日で代用しません。"
        )

    rooms: dict[str, dict] = {}
    dup = 0
    for r in rows:
        k = room_key(r.get("name"), r.get("room"))
        if k in rooms:
            # ★同じ室が2行ある＝収穫の重複。先勝ちにして件数だけ出す（黙って上書きしない）
            dup += 1
            continue
        rooms[k] = {
            "name": r.get("name"),
            "room": r.get("room"),
            "state": r.get("state"),
            "nyukyo": r.get("nyukyo"),
            "rent": r.get("rent"),
            "area": r.get("area"),
            "updated": r.get("updated"),
            "agent": r.get("agent"),
        }

    scope = {
        # 賃料は**円**。提案くんの Property.rent は万円なので、あちらで 10000 倍して比べる
        "rentMinYen": meta.get("rent_min"),
        "rentMaxYen": meta.get("rent_max"),
        "wards": meta.get("wards") or [],
        "layouts": meta.get("layouts") or [],
    }
    missing = [k for k, v in scope.items() if v in (None, [])]
    if missing:
        raise SystemExit(
            f"[error] 収穫JSONの meta に {missing} がありません。\n"
            f"        収穫条件が分からないと『条件外』と『消失』を区別できないため、"
            f"索引を作りません。"
        )

    return {
        "meta": {
            "importSource": "import-suumo-v1",
            # ★statusCheckedAt に入る値。収穫日の 00:00 JST に倒す。
            #   収穫時刻は『13:0x』のように分が伏せ字で正確に取れないので、
            #   **その日のいちばん早い時刻**にして「実際より古く見せる」側に寄せる。
            #   鮮度の誤差は古い側に倒すのが安全（新しく見せると期限切れを見逃す）。
            "harvestCheckedAt": f"{hdate}T00:00:00+09:00",
            "harvestDate": hdate,
            "harvestDateSource": hsrc,
            "harvestCollected": meta.get("collected"),
            "harvestCount": meta.get("count"),
            "indexedRooms": len(rooms),
            "duplicateRows": dup,
            "scope": scope,
        },
        "rooms": rooms,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="リアプロ収穫JSON → 生存確認インデックス")
    ap.add_argument("--harvest", required=True, help="harvest_全件_*.json")
    ap.add_argument("--out", required=True, help="出力JSON")
    a = ap.parse_args(argv)

    hp = Path(a.harvest).expanduser()
    if not hp.is_file():
        ap.error(f"--harvest が見つかりません: {hp}")
    idx = build_index(json.loads(hp.read_text(encoding="utf-8")), hp)

    op = Path(a.out).expanduser()
    op.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    m = idx["meta"]
    s = m["scope"]
    print(f"収穫 {m['harvestCount']}件 → 索引 {m['indexedRooms']}室"
          f"（重複 {m['duplicateRows']}行は先勝ち）→ {op}")
    print(f"  収穫条件: 賃料 {s['rentMinYen']:,}〜{s['rentMaxYen']:,}円 / "
          f"{'・'.join(s['wards'])} / {'・'.join(s['layouts'])}")
    print(f"  収穫日: {m['harvestDate']}（出典: {m['harvestDateSource']}）"
          f" → statusCheckedAt には {m['harvestCheckedAt']} が入る")
    print(f"  収穫日時(原文): {m['harvestCollected']}")
    print("  ★この条件の外にある物件は、載っていなくても『消失』ではない。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
