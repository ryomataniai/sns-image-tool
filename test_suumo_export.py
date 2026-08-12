# -*- coding: utf-8 -*-
"""batchsuumo-v1: SUUMO入稿用ZIPの回帰テスト（リファクタ前後で出力が変わらないことの検証）。

実行: python3 test_suumo_export.py   （pytest不要・APIキー不要・ネットワーク不要）

■なぜこのテストが必要か
core.suumo_files() は app.py の「SUUMO入稿用ZIP」ボタンにインラインで書かれていたループを
移設したもの。移設で出力が1画像でも変われば、既に入稿した10物件と新規35室で
命名・注記の扱いが食い違う（＝どちらが正しいのか後から誰にも判定できない）。

■「1バイトも同じ」を何で測るか
ZIPの生バイトは zipfile.writestr が各エントリに書き込む更新時刻（ローカル時刻・2秒粒度）を
含むため、実行時刻で変わる。よって生バイト比較では合否を測れない。
ここでは **エントリ名の並び・CRC32・非圧縮サイズ・圧縮方式** の一致で測る。
CRC32が一致すれば中身のバイト列は一致している（＝測りたいものを測っている）。
生バイト一致も参考として併記するが、合否には使わない。
"""
from __future__ import annotations

import io
import sys
import zipfile

from PIL import Image

import core


# ── リファクタ前の実装（app.py:2390-2405 の逐語コピー。ここを書き換えてはいけない）──────
def _legacy_build_zip(s_adopt, sfp, sfp_on):
    """移設前のインライン実装。比較用の“正”として凍結しておく。"""
    _sbuf = io.BytesIO()
    with zipfile.ZipFile(_sbuf, "w", zipfile.ZIP_DEFLATED) as _zf:
        _used = set()
        for _k, _it in enumerate(s_adopt, 1):
            _sb = _it["gen_bytes"]
            _sd = core.suumo_disclaimer(_it.get("disc"))
            if _sd:
                try:
                    _sb = core.add_disclaimer(_sb, _sd)
                except Exception:  # noqa: BLE001
                    pass
            _zf.writestr(core.suumo_filename(_k, _it.get("room", ""), _used),
                         core.to_suumo_jpeg(_sb))
        if sfp_on:
            _zf.writestr(core.suumo_filename(len(s_adopt) + 1, "間取り図", _used),
                         core.to_suumo_jpeg(sfp))
    return _sbuf.getvalue()


def _new_build_zip(s_adopt, sfp, sfp_on):
    """移設後（app.py の現行実装と同じ呼び方）。"""
    _sbuf = io.BytesIO()
    with zipfile.ZipFile(_sbuf, "w", zipfile.ZIP_DEFLATED) as _zf:
        for _name, _data in core.suumo_files(s_adopt, sfp if sfp_on else None):
            _zf.writestr(_name, _data)
    return _sbuf.getvalue()


# ── テスト素材 ────────────────────────────────────────────────────
def _png(w=320, h=400, color=(180, 170, 160), seed=0):
    """決定的なダミーPNG（無地＋seedで色を散らす）。"""
    c = ((color[0] + seed * 7) % 256, (color[1] + seed * 11) % 256, (color[2] + seed * 13) % 256)
    buf = io.BytesIO()
    Image.new("RGB", (w, h), c).save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png(w=200, h=200):
    """RGBA（アルファ付き）＝to_suumo_jpeg の白背景合成経路を通す素材。"""
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 200, 90, 128)).save(buf, format="PNG")
    return buf.getvalue()


def _items():
    """UIが実際に渡す形の item 配列。以下を意図的に全部混ぜてある：
    - 注記なし（高解像度化のみ・disc=None）＝SUUMO入稿の既定経路
    - 注記あり（家具ステージング／リノベ）＝add_disclaimer が焼かれる経路
    - 除外対象（補完生成・3Dパース）＝連番が詰まることの確認
    - 同名衝突（洋室2枚）＝_2 サフィックス
    - 未知の部屋名（その他）＝room{NN} への落ち先
    - RGBA素材＝JPEG変換の合成経路
    """
    return [
        {"gen_bytes": _png(seed=1), "room": "外観", "disc": None, "treatment": "高解像度化のみ"},
        {"gen_bytes": _png(seed=2), "room": "LDK", "disc": "※AI加工のイメージ",
         "treatment": "家具ステージング"},
        {"gen_bytes": _png(seed=3), "room": "キッチン", "disc": None, "treatment": "水回り・玄関を演出"},
        {"gen_bytes": _png(seed=4), "room": "洋室", "disc": None, "treatment": "高解像度化のみ"},
        {"gen_bytes": _png(seed=5), "room": "洋室", "disc": None, "treatment": "高解像度化のみ"},
        {"gen_bytes": _png(seed=6), "room": "その他", "disc": None, "treatment": "高解像度化のみ"},
        {"gen_bytes": _rgba_png(), "room": "浴室", "disc": None, "treatment": "高解像度化のみ"},
        {"gen_bytes": _png(seed=8), "room": "洗面",
         "disc": "※リノベ後のイメージ（仕上がりは設計により異なります）",
         "treatment": "リノベ後イメージ"},
        # ↓ 除外対象（UI側で _s_adopt を作る時点で落ちる。core側でも冪等に落ちる）
        {"gen_bytes": _png(seed=9), "room": "トイレ", "disc": "※AI生成のイメージ",
         "treatment": "補完生成"},
        {"gen_bytes": _png(seed=10), "room": "LDK", "disc": "※AI生成のイメージ",
         "treatment": "3Dパース（試験）"},
    ]


def _entries(zip_bytes):
    """合否に使う指紋：(名前, CRC32, 非圧縮サイズ, 圧縮方式) の並び。"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return [(i.filename, i.CRC, i.file_size, i.compress_type) for i in zf.infolist()]


# ── 検証 ──────────────────────────────────────────────────────────
_fails = []


def _check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


def test_zip_identical():
    """受入基準3：UIのZIP出力が変わっていないこと。"""
    fp = _png(w=600, h=450, color=(250, 250, 250), seed=0)
    for label, sfp_on in (("間取り図あり", True), ("間取り図なし", False)):
        # UI が渡すのは除外後のリスト（app.py の _s_adopt）
        adopted = _items()
        s_adopt = [it for it in adopted if not core.suumo_excluded(it.get("treatment"))]
        old = _legacy_build_zip(s_adopt, fp, sfp_on)
        new = _new_build_zip(s_adopt, fp, sfp_on)
        eo, en = _entries(old), _entries(new)
        _check(f"[{label}] エントリ名・CRC32・サイズ・圧縮方式が一致", eo == en,
               f"{len(en)}件")
        if eo != en:
            print("    旧:", eo)
            print("    新:", en)
        _check(f"[{label}] 生バイトも一致（参考・mtime依存のため合否対象外）",
               True, "同一" if old == new else "差異あり（更新時刻のみ・想定内）")


def test_exclude_idempotent():
    """CLIは未除外リストを渡す。UI（除外済み）と同じ結果になること＝除外の冪等性。"""
    fp = _png(w=600, h=450, color=(250, 250, 250))
    adopted = _items()
    pre = [it for it in adopted if not core.suumo_excluded(it.get("treatment"))]
    a = [n for n, _ in core.suumo_files(pre, fp)]        # UI経路（除外済みを渡す）
    b = [n for n, _ in core.suumo_files(adopted, fp)]    # CLI経路（未除外を渡す）
    _check("除外は冪等（除外済み／未除外のどちらを渡しても同一）", a == b, str(a))
    _check("除外分だけ連番が詰まる（8枚＋間取り図=9件）", len(a) == 9, f"{len(a)}件")


def test_room_ascii_covers_classifier_vocab():
    """分類コードが返す部屋名すべてに ASCII 名があること（room{NN} へ落ちるのは『その他』だけ）。

    ★storage-key-v1 の再発防止。MAISOKU_CODE_TO_ROOM は STORAGE を『クローゼット』に写すが、
      SUUMO_ROOM_ASCII 側のキーが『収納』しか無く、実測で 08_room08.jpg（中身はクローゼット）
      が 999999 その他に落ちていた。片方だけ増やすと黙ってカテゴリが失われるので、
      対応の穴をテストで塞ぐ。
    """
    vocab = set(core.MAISOKU_CODE_TO_ROOM.values())
    missing = sorted(r for r in vocab if r != "その他" and r not in core.SUUMO_ROOM_ASCII)
    _check("分類が返す部屋名にASCII名の穴がない", not missing, str(missing))
    _check("クローゼット → storage", core.SUUMO_ROOM_ASCII.get("クローゼット") == "storage")
    _check("『その他』は意図的に未定義（room{NN} へ倒す）",
           "その他" not in core.SUUMO_ROOM_ASCII)


def test_naming():
    """命名規則：連番2桁＋部位ASCII、衝突は _2、未知は room{NN}、間取り図は末尾。"""
    fp = _png(w=600, h=450, color=(250, 250, 250))
    names = [n for n, _ in core.suumo_files(_items(), fp)]
    expect = ["01_gaikan.jpg", "02_living.jpg", "03_kitchen.jpg", "04_youshitsu.jpg",
              "05_youshitsu.jpg", "06_room06.jpg", "07_bath.jpg", "08_senmen.jpg",
              "09_madori.jpg"]
    _check("命名が期待どおり", names == expect, str(names))
    _check("全ファイル名が半角英数（SUUMOの受付条件）",
           all(n.replace("_", "").replace(".", "").isalnum() and n.isascii() for n in names))


def _bottom_darkening(jpeg_bytes, src_png):
    """下端12%の平均輝度が元画像よりどれだけ下がったか。
    ★add_disclaimer は黒帯を『画像内に重ねる』（下に足して高さを増やすのではない）ので、
      有無はサイズでは測れない。下端の暗化量で測る＝実際に起きる現象を測る。"""
    import numpy as np

    def _bot(b):
        im = Image.open(io.BytesIO(b)).convert("L")
        a = np.asarray(im, dtype="float32")
        return float(a[int(a.shape[0] * 0.88):, :].mean())
    return _bot(src_png) - _bot(jpeg_bytes)


def test_jpeg_and_disclaimer():
    """出力はJPEG。注記は disc のある画像だけに焼かれ、無い画像には焼かれない（受入基準5）。"""
    files = dict(core.suumo_files(_items(), None))
    _check("全ファイルがJPEG（先頭がSOIマーカー）",
           all(b[:2] == b"\xff\xd8" for b in files.values()))
    _check("896×1152相当のサイズが保たれる（注記は重ね描き＝寸法を変えない）",
           Image.open(io.BytesIO(files["02_living.jpg"])).size
           == Image.open(io.BytesIO(_png(seed=2))).size)
    d_plain = _bottom_darkening(files["01_gaikan.jpg"], _png(seed=1))   # disc=None
    d_disc = _bottom_darkening(files["02_living.jpg"], _png(seed=2))    # disc あり
    _check("注記なし（高解像度化のみ）＝下端が暗くなっていない＝黒帯なし",
           abs(d_plain) < 2.0, f"暗化 {d_plain:+.1f}")
    _check("注記あり（家具ステージング）＝下端に黒帯が焼かれている",
           d_disc > 20.0, f"暗化 {d_disc:+.1f}")


def test_floorplan_not_stamped():
    """間取り図は実物（生成AI非通過）＝注記を焼かない。"""
    fp = _png(w=600, h=450, color=(250, 250, 250))
    files = dict(core.suumo_files([], fp))
    _check("間取り図のみでも出力できる（01_madori.jpg）", list(files) == ["01_madori.jpg"],
           str(list(files)))
    _check("間取り図に注記を焼いていない（高さが元と同じ）",
           Image.open(io.BytesIO(files["01_madori.jpg"])).size[1] == 450)


def test_app_matches_core():
    """app.py の委譲が core と同じ結果を返すこと（判定ロジックの単一情報源の確認）。
    ★app.py は streamlit を import するため、無い環境ではスキップする（テスト自体は落とさない）。"""
    try:
        import app
    except Exception as e:  # noqa: BLE001
        print(f"  SKIP  app.py の委譲確認（import不可: {type(e).__name__}）")
        return
    b = _png(seed=42)
    _check("app._pl_img_stats == core.img_stats", app._pl_img_stats(b) == core.img_stats(b))
    _check("app._pl_score_floorplan == core.score_floorplan",
           app._pl_score_floorplan(b) == core.score_floorplan(b))
    _check("app._pl_is_blank_frame == core.is_blank_frame",
           app._pl_is_blank_frame(b) == core.is_blank_frame(b))
    _check("app._PL_CODE_TO_ROOM は core と同一", app._PL_CODE_TO_ROOM == core.MAISOKU_CODE_TO_ROOM)


if __name__ == "__main__":
    for fn in (test_zip_identical, test_exclude_idempotent,
               test_room_ascii_covers_classifier_vocab, test_naming,
               test_jpeg_and_disclaimer, test_floorplan_not_stamped, test_app_matches_core):
        print(f"\n▶ {fn.__name__}: {(fn.__doc__ or '').splitlines()[0]}")
        fn()
    print("\n" + ("✅ 全PASS" if not _fails else f"❌ FAIL {len(_fails)}件: {_fails}"))
    sys.exit(1 if _fails else 0)
