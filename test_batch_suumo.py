# -*- coding: utf-8 -*-
"""batchsuumo-v1: batch_suumo.py の結合テスト（モデル呼び出しだけをスタブ化）。

実行:
    python3 test_batch_suumo.py [マイソクPDFのフォルダ]
    （省略時は SUUMO入稿_75枠_20260806/01_マイソク を見る）

■何を測るテストか / 何を測っていないか
測る（実物のマイソクPDFを使う・APIキー不要・課金なし）:
  - PDFからの写真抽出・白紙枠の除外・間取り図のローカル判定
  - 分類コード → 部屋名 → 標準ツアー順の並び → ファイル名（連番＋部位ASCII）
  - フォルダ構造・896×1152・注記の黒帯なし・_manifest.csv
  - 1枚の生成失敗で1室が落ちないこと／1室の失敗で全体が止まらないこと
  - サマリCSVの内容・既存フォルダのスキップ・NFD(macOS)の --only 一致
測らない:
  - 実際の画像生成品質（Geminiの出力そのもの）。ここはスタブに置き換えている。
    ★つまり本テストが全PASSでも「生成結果が使える画質か」は未検証。
      それは --only で1室だけ実行して人が目視するしかない（受入基準2）。
"""
from __future__ import annotations

import csv
import io
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image

import core

_DEFAULT_IN = Path("/Users/taniairyouma/Downloads/エンクス/03_物件提案くん/"
                   "SUUMO入稿_75枠_20260806/01_マイソク")

_fails = []


def _check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


# ── スタブ ────────────────────────────────────────────────────────
def _stub_png(w=896, h=1152):
    """生成結果の代役。実測の出力寸法（Gemini size=2K × aspect 4:5 → 896×1152）に合わせる。
    ★一様色にすると crop_uniform_borders が全面を余白と見なし得るので、中央に模様を置く。"""
    im = Image.new("RGB", (w, h), (120, 130, 140))
    for y in range(h // 4, h * 3 // 4, 8):
        for x in range(w // 4, w * 3 // 4, 8):
            im.putpixel((x, y), (240, 60, 30))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class _Stub:
    """core.generate_from_images / classify_maisoku_images を差し替えるスタブ。

    fail_on: この回数目の生成呼び出しをエラーにする（1枚失敗しても続行することの確認用）。
    classify: 部屋コードの決め方。実物PDFの画像枚数に合わせて機械的に割り振る。
    """

    def __init__(self, fail_on=(), classify_mode="cycle"):
        self.calls = 0
        self.fail_on = set(fail_on)
        self.classify_mode = classify_mode
        self.classify_calls = 0

    def generate_from_images(self, client, images, prompt, **kw):
        self.calls += 1
        assert kw.get("size") == "2K", "size=2K で呼ばれていない"
        assert kw.get("aspect") == "4:5", "aspect=4:5 で呼ばれていない"
        assert kw.get("add_safety") is False, "add_safety=False で呼ばれていない"
        if self.calls in self.fail_on:
            return None, "STUB: 意図的な生成失敗"
        return _stub_png(), None

    def detect_text_subject(self, client, images, model=None):
        """文字主題の判定スタブ。実データでは給湯リモコン等に1件立つのが実測なので、
        先頭から3枚目に1件だけ立てて manifest の text_subject 列を通す。"""
        return ["" if i != 2 else "給湯リモコン" for i in range(len(images))]

    def classify_maisoku_images(self, client, images, model=None):
        self.classify_calls += 1
        n = len(images)
        if self.classify_mode == "all_other":
            return [["OTHER"] for _ in range(n)]
        cycle = ["EXTERIOR", "KITCHEN", "BATH", "WASH", "TOILET", "BEDROOM",
                 "BALCONY", "STORAGE", "ENTRANCE", "LIVING", "MAP", "OTHER"]
        return [[cycle[i % len(cycle)]] for i in range(n)]


def _install(stub, monkey):
    monkey.append((core, "generate_from_images", core.generate_from_images))
    monkey.append((core, "classify_maisoku_images", core.classify_maisoku_images))
    monkey.append((core, "detect_text_subject", core.detect_text_subject))
    monkey.append((core, "get_client", core.get_client))
    core.generate_from_images = stub.generate_from_images
    core.classify_maisoku_images = stub.classify_maisoku_images
    core.detect_text_subject = stub.detect_text_subject
    core.get_client = lambda *a, **k: object()          # APIキー不要にする


def _restore(monkey):
    for obj, name, orig in monkey:
        setattr(obj, name, orig)
    monkey.clear()


def _run(in_dir, out_dir, extra, stub):
    import batch_suumo
    monkey = []
    _install(stub, monkey)
    try:
        rc = batch_suumo.main(["--in", str(in_dir), "--out", str(out_dir)] + extra)
    finally:
        _restore(monkey)
    return rc


def _read_summary(out_dir: Path):
    f = sorted(out_dir.glob("_batch_summary_*.csv"))[-1]
    with f.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ── テスト ────────────────────────────────────────────────────────
def test_single_room(in_dir, tmp):
    """受入基準2相当：1室だけ実行し、構造・命名・896×1152・注記なしを確認（生成はスタブ）。"""
    out = tmp / "single"
    stub = _Stub()
    rc = _run(in_dir, out, ["--only", "難波大国町Uno_903", "--since-ts", "20260812"], stub)
    _check("終了コード0", rc == 0, f"rc={rc}")
    dirs = [p for p in out.iterdir() if p.is_dir()]
    _check("出力フォルダが1つできる", len(dirs) == 1, str([p.name for p in dirs]))
    if not dirs:
        return
    d = dirs[0]
    jpgs = sorted(p.name for p in d.glob("*.jpg"))
    _check("ファイル名が 連番2桁_部位ASCII.jpg", all(
        len(n.split("_")[0]) == 2 and n.split("_")[0].isdigit() and n.endswith(".jpg")
        for n in jpgs), str(jpgs))
    _check("全ファイル名が半角英数（SUUMOの受付条件）",
           all(n.isascii() for n in jpgs))
    _check("連番が1から連続している", [int(n[:2]) for n in jpgs] == list(range(1, len(jpgs) + 1)),
           str([int(n[:2]) for n in jpgs]))
    _check("_manifest.csv がある", (d / "_manifest.csv").is_file())
    sizes = {Image.open(p).size for p in d.glob("*.jpg") if "madori" not in p.name}
    _check("生成画像は896×1152（間取り図は実物パススルーなので対象外）",
           sizes == {(896, 1152)}, str(sizes))
    # 注記の黒帯：既定 高解像度化のみ＝disc=None なので下端が暗化しない（受入基準5）
    import numpy as np
    stub_bot = np.asarray(Image.open(io.BytesIO(_stub_png())).convert("L"),
                          dtype="float32")[-140:, :].mean()
    worst = 0.0
    for p in d.glob("*.jpg"):
        if "madori" in p.name:
            continue
        bot = np.asarray(Image.open(p).convert("L"), dtype="float32")[-140:, :].mean()
        worst = max(worst, stub_bot - bot)
    _check("注記の黒帯が焼かれていない（下端の暗化なし）", worst < 3.0, f"最大暗化 {worst:+.1f}")
    # manifest の中身
    with (d / "_manifest.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    _check("manifestの行数＝出力ファイル数", len(rows) == len(list(d.glob("*.jpg"))),
           f"{len(rows)}行")
    _check("manifestに全行SUUMOカテゴリコードが入っている",
           all(r["suumo_category"] for r in rows))
    _check("manifestのfileが実ファイルと一致",
           sorted(r["file"] for r in rows) == jpgs)
    _check("manifestにtext_subject列がある（文字主題の手がかり）",
           all("text_subject" in r for r in rows))
    _check("文字主題が1件だけ立っている（スタブどおり）",
           sum(1 for r in rows if r.get("text_subject")) == 1,
           str([r["file"] for r in rows if r.get("text_subject")]))
    _check("クローゼットが storage に寄る（999999に落ちない）",
           all(r["suumo_category"] != "999999" or r["room"] != "クローゼット" for r in rows),
           str([(r["file"], r["room"], r["suumo_category"]) for r in rows
                if r["room"] == "クローゼット"]))
    print("    出力:", jpgs)
    print("    カテゴリ:", [f"{r['file']}→{r['suumo_category']}" for r in rows])


def test_skip_existing(in_dir, tmp):
    """既存フォルダは既定でスキップ（二重生成＝二重課金を防ぐ）。--overwrite で作り直す。"""
    out = tmp / "single"          # test_single_room の出力を再利用
    stub = _Stub()
    rc = _run(in_dir, out, ["--only", "難波大国町Uno_903", "--since-ts", "20260812"], stub)
    _check("2回目はスキップされ生成APIを呼ばない", stub.calls == 0, f"{stub.calls}回")
    _check("スキップでも終了コード0", rc == 0, f"rc={rc}")
    rows = _read_summary(out)
    _check("サマリのstatusがSKIP", [r["status"] for r in rows] == ["SKIP"], str(rows))
    stub2 = _Stub()
    _run(in_dir, out, ["--only", "難波大国町Uno_903", "--since-ts", "20260812",
                       "--overwrite"], stub2)
    _check("--overwrite なら作り直す", stub2.calls > 0, f"{stub2.calls}回")


def test_partial_failure(in_dir, tmp):
    """受入基準4相当：1枚の生成失敗で1室が落ちず、statusがPARTIALになりサマリに出る。"""
    out = tmp / "partial"
    stub = _Stub(fail_on=(2, 4))
    rc = _run(in_dir, out, ["--only", "難波大国町Uno_903", "--since-ts", "20260812"], stub)
    rows = _read_summary(out)
    _check("失敗が混ざってもフォルダは作られる",
           any(p.is_dir() for p in out.iterdir()), "")
    _check("statusがPARTIAL", rows and rows[0]["status"] == "PARTIAL",
           str(rows[0] if rows else None))
    _check("noteに失敗枚数が出る", rows and "生成失敗2枚" in rows[0]["note"],
           rows[0]["note"] if rows else "")
    _check("PARTIALでも終了コード0（失敗室0件）", rc == 0, f"rc={rc}")
    _check("連番は成功分で1から連続する（欠番を作らない）",
           [int(n.name[:2]) for n in sorted((out / rows[0]["key"]).glob("*.jpg"))]
           == list(range(1, len(list((out / rows[0]["key"]).glob("*.jpg"))) + 1)))


def test_multi_room_continues(in_dir, tmp):
    """受入基準4相当：ある室が全滅しても次の室に進み、最後にサマリを出す。"""
    out = tmp / "multi"
    # 1室目の全画像を落とす（1室あたり最大14枚 → 1〜14を失敗指定）
    stub = _Stub(fail_on=range(1, 15))
    rc = _run(in_dir, out, ["--since-ts", "20260812", "--limit", "3"], stub)
    rows = _read_summary(out)
    _check("3室ぶんサマリに出る", len(rows) == 3, f"{len(rows)}室")
    _check("1室目はFAIL", rows and rows[0]["status"] == "FAIL", str(rows[0] if rows else None))
    _check("FAILの室はフォルダを作らない", not (out / rows[0]["key"]).exists() if rows else False)
    _check("2室目以降は処理が続く（OK/PARTIAL）",
           all(r["status"] in ("OK", "PARTIAL") for r in rows[1:]),
           str([r["status"] for r in rows]))
    _check("失敗室があるので終了コード1", rc == 1, f"rc={rc}")
    _check("中途半端な .partial フォルダが残っていない",
           not list(out.glob(".*.partial")), str(list(out.glob('.*.partial'))))


def test_nfd_only_match(in_dir, tmp):
    """macOSのNFDファイル名に対して、NFCで打った --only が一致すること。"""
    import batch_suumo
    nfd_names = [p.name for p in in_dir.glob("*.pdf")
                 if unicodedata.normalize("NFC", p.name) != p.name]
    _check("入力にNFDのファイル名が存在する（このテストが意味を持つ前提）",
           bool(nfd_names), f"{len(nfd_names)}件")
    t, _ = batch_suumo.resolve_targets(in_dir, "", ["エスリードレジデンス"], 0)  # NFCで指定
    _check("NFCで打った --only がNFDファイルに一致する", len(t) > 0, f"{len(t)}件")
    _check("キーがNFCに正規化されている",
           all(unicodedata.normalize("NFC", k) == k for k, _ in t))


def test_dedup_newest(in_dir, tmp):
    """重複PDF（再DL分）は最新タイムスタンプの1本だけを採ること＝二重生成の防止。"""
    import batch_suumo
    t, stat = batch_suumo.resolve_targets(in_dir, "20260812", None, 0)
    _check("8/12分の対象が35室", len(t) == 35, f"{len(t)}室")
    _check("採用PDFは各キー1本だけ", len({k for k, _ in t}) == len(t))
    _check("古い重複版が除外されている", stat["dropped_old"] > 0, f"{stat['dropped_old']}件")
    olds = [p.name for k, p in t if "_20260812" not in p.stem]
    _check("採用された35本すべてが8/12版", not olds, str(olds[:3]))


def test_treatment_guard(in_dir, tmp):
    """--treatment に高解像度化以外を渡したら、黙って作らずエラーで止まること。"""
    import batch_suumo
    try:
        batch_suumo.main(["--in", str(in_dir), "--out", str(tmp / "x"),
                          "--treatment", "家具ステージング", "--dry-run"])
        _check("家具ステージングは拒否される", False, "受け付けてしまった")
    except SystemExit as e:
        _check("家具ステージングは拒否される（argparse error）", e.code == 2, f"code={e.code}")


if __name__ == "__main__":
    in_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_IN
    if not in_dir.is_dir():
        print(f"入力フォルダが無い: {in_dir}")
        sys.exit(2)
    tmp = Path(tempfile.mkdtemp(prefix="batchsuumo_test_"))
    print(f"入力: {in_dir}\n作業: {tmp}")
    try:
        for fn in (test_single_room, test_skip_existing, test_partial_failure,
                   test_multi_room_continues, test_nfd_only_match, test_dedup_newest,
                   test_treatment_guard):
            print(f"\n▶ {fn.__name__}: {(fn.__doc__ or '').splitlines()[0]}")
            try:
                fn(in_dir, tmp)
            except Exception as e:  # noqa: BLE001  1テストの例外で全体を止めない
                _check(f"{fn.__name__} が例外", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    sys.exit(1 if _fails else 0)
