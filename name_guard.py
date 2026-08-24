#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実物件名がコミットに混ざるのを止めるガード。

    python3 name_guard.py --all       # 作業ツリー全体を見る（追跡・未追跡とも）
    python3 name_guard.py --staged    # ステージ済みの内容だけ見る（pre-commit 用）
    python3 name_guard.py --self-test

■なぜ要るか
2026-08-24 に測ったところ、Public リポの追跡済みファイルに実物件名が22件残っていた。
未追跡ファイルにも6件あり、`.gitignore` は未追跡50件を1つも無視していなかった。
croco-bridge が AUTO_COMMIT=1 で走るので、**人間が気をつける余地がない。**
「気をつける」では止まらないものは、コミットが弾かれる形にするしかない。

■2段構え
  1. 実名リスト（★リポ外）との突き合わせ。EXACT と、そこから機械生成した VARIANT。
     ソース側はブランド接頭辞を落として書く（`BRAND地名Uno_903` → `地名Uno_903`）ので、  # name-guard: ok（架空の例）
     リストの素の突き合わせだけでは 8/24 に9件取り逃した。括弧の旧称も同様。
  2. リストが無くても効く構造パターン（`棟名_号室` / `棟名 数字号室`）。
     リストはリポ外にあり、他所や CI では読めない。**読めない時に素通りさせない**ため。

■★このファイルに実名を書かないこと
リポは Public。リストは環境変数 SNS_NAME_LIST か既定パスから読む。読めなければ
構造パターンだけで走り、その旨を警告する（黙って合格にはしない）。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import unicodedata

DEFAULT_LIST = pathlib.Path(
    "~/Downloads/エンクス/03_物件提案くん/SUUMO入稿_75枠_20260806/"
    "_実棟名リスト_スキャン用_20260824.txt").expanduser()

# 走査しない拡張子・ディレクトリ
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "output"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".mp4", ".pdf", ".zip", ".pyc", ".ico"}

# ★実名ではないので通すもの（消すと判定の根拠が失われる）
#   - 架空名: サンプル / ダミー / テスト用の棟A・棟B
#   - 駅名・地名そのもの（core.py の駅名→ローマ字辞書）
#   - 公開されている社名・ブランド接頭辞（yield_report.py の SAMTY_PREFIXES 等）
SAFE_WORDS = ("サンプル", "ダミー", "ﾀﾞﾐｰ", "example", "Example", "EXAMPLE",
              "テスト用", "テスト物件", "棟A", "棟B", "foo", "bar", "hoge")

# 行そのものが実名ではないと分かる形。
#   1つめ: 駅名・地名 → ローマ字 の対応表（core.py）。地名は実物件名ではない。
#   2つめ: 明示的な除外指定。★誤検出を通すための逃げ道は、grep できる形で1つだけ用意する。
SAFE_LINES = (
    re.compile(r'^\s*(?:"[^"]+"\s*:\s*"[A-Z][A-Z0-9\-]*"\s*,?\s*)+$'),
    re.compile(r"name-guard:\s*ok"),
)

# 構造パターン: 棟名らしき塊 + 号室
#   カタカナ/英数/中黒で始まり、漢字も混じってよい4文字以上の塊のあとに _123 か 1203号室。
_STRUCT = [
    re.compile(r"[ァ-ヶーA-Za-z][ァ-ヶーA-Za-z0-9・一-鿿]{3,}_\d{3,4}(?!\d)"),
    re.compile(r"[ァ-ヶーA-Za-z][ァ-ヶーA-Za-z0-9・一-鿿]{3,}\s*\d{3,4}号室"),
]


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def load_names(path: pathlib.Path | None) -> tuple[list[str], list[str], str | None]:
    """リストを読み、(EXACT, VARIANT, 警告) を返す。読めなければ ([], [], 警告)。"""
    if path is None or not path.exists():
        return [], [], f"実名リストが読めない（{path}）。構造パターンだけで走る。"
    raw = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    exact = sorted({_nfc(x) for x in raw if x and not x.startswith("#")},
                   key=len, reverse=True)

    # VARIANT: 先頭の英数ブランド接頭辞を落とした形。
    #   `BRAND地名Uno_903` → `地名Uno_903` / `名前(旧_旧称)_503` → `名前` と `旧称`  # name-guard: ok
    #   ★短い残りは号室が付いている時だけ採る。残りが駅名と同形になり、
    #     core.py の駅名→ローマ字辞書に誤爆するため。
    var = set()
    _ROOM = re.compile(r"(_\d{3,4}|\s*\d{3,4}\s*号室)$")

    def _add(x: str) -> None:
        """棟名部分が4文字以上ある時だけ採る。★号室だけの断片は採らない。"""
        x = x.strip(" 　・_")
        if not x or x[0] == "_":
            return
        stem = _ROOM.sub("", x).strip(" 　・_")      # 号室を外した棟名部分
        if len(stem) < 4:
            return                                   # `_701` のような断片を弾く
        if len(stem) >= 6 or _ROOM.search(x):
            var.add(x)

    for n in exact:
        # 先頭の英数ブランド接頭辞を落とす
        m = re.match(r"^[A-Za-z0-9\-\.\s]{2,}(.+)$", n)
        if m:
            _add(m.group(1))
        # 括弧の旧称: `名前(旧_旧称)_503` → `名前` と `旧称`
        m = re.match(r"^(.*?)[（(]\s*(?:旧[_:：]?)?(.*?)[）)](.*)$", n)
        if m:
            _add(m.group(1))
            _add(m.group(2))
    variant = sorted(var - set(exact), key=len, reverse=True)
    return exact, variant, None


def scan_text(text: str, exact: list[str], variant: list[str]) -> list[tuple[int, str, str, str]]:
    """→ [(行番号, 種別, 当たった名前, 行)]。1行につき最長一致の1件だけ返す。"""
    out = []
    for i, line in enumerate(_nfc(text).splitlines(), 1):
        if any(w in line for w in SAFE_WORDS) or any(rx.search(line) for rx in SAFE_LINES):
            continue
        hit = next((("EXACT", n) for n in exact if n in line), None)
        if hit is None:
            hit = next((("VARIANT", n) for n in variant if n in line), None)
        if hit is None:
            for rx in _STRUCT:
                m = rx.search(line)
                if m:
                    hit = ("STRUCT", m.group(0))
                    break
        if hit:
            out.append((i, hit[0], hit[1], line.strip()[:120]))
    return out


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout


def iter_all(repo: pathlib.Path):
    """追跡・未追跡を問わず作業ツリーのテキストを (パス, 中身) で返す。"""
    rels = [p for p in _git(repo, "ls-files", "-z").split("\0") if p]
    rels += [p for p in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
    for rel in rels:
        f = repo / rel
        if f.suffix.lower() in SKIP_SUFFIX or set(f.parts) & SKIP_DIRS:
            continue
        try:
            yield rel, f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def iter_staged(repo: pathlib.Path):
    """ステージ済みの**内容**を返す。作業ツリーではなく index を見る。"""
    names = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    for rel in [p for p in names.split("\0") if p]:
        if pathlib.Path(rel).suffix.lower() in SKIP_SUFFIX:
            continue
        blob = subprocess.run(["git", "-C", str(repo), "show", f":{rel}"],
                              capture_output=True)
        if blob.returncode:
            continue
        try:
            yield rel, blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue


def run(repo: pathlib.Path, staged: bool, list_path: pathlib.Path | None) -> int:
    exact, variant, warn = load_names(list_path)
    if warn:
        print(f"⚠ {warn}", file=sys.stderr)
    else:
        print(f"実名リスト: EXACT {len(exact)}件 / VARIANT {len(variant)}件（機械生成）")

    files = iter_staged(repo) if staged else iter_all(repo)
    total = 0
    for rel, text in files:
        for i, kind, name, line in scan_text(text, exact, variant):
            total += 1
            print(f"{rel}:{i}: [{kind}] «{name}»\n      {line}")
    if total:
        where = "ステージ済みの内容" if staged else "作業ツリー"
        print(f"\n❌ {where}に実物件名らしき記述が {total}件。", file=sys.stderr)
        if staged:
            print("   コミットを中止した。実名を伏せてから再実行すること。\n"
                  "   誤検出なら name_guard.py の SAFE_WORDS に足すか、"
                  "   意図的に通す場合だけ git commit --no-verify。", file=sys.stderr)
        return 1
    print("\n✅ 実物件名 0件")
    return 0


def _self_test() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {detail}" if detail else ""))
        ok = ok and cond

    # ★架空名。SAFE_WORDS に当たる語（サンプル等）を使うと素通りしてテストにならない。
    ex, va = ["ヨソノナ梅田タワー"], ["ヨソノナ本町_101"]  # name-guard: ok（架空名のフィクスチャ）
    # 正常系
    check("EXACT で拾う", scan_text("# ヨソノナ梅田タワー で実測\n", ex, [])[0][1] == "EXACT")  # name-guard: ok（架空名のフィクスチャ）
    check("VARIANT で拾う", scan_text("x ヨソノナ本町_101 y\n", [], va)[0][1] == "VARIANT")  # name-guard: ok（架空名のフィクスチャ）
    check("STRUCT で拾う（リスト無しでも効く）",
          scan_text("# 実測 カタカナレジデンス_903 が該当\n", [], [])[0][1] == "STRUCT")  # name-guard: ok（架空名のフィクスチャ）
    check("STRUCT: 号室表記も拾う",
          scan_text("カタカナレジデンス 1203号室\n", [], [])[0][1] == "STRUCT")  # name-guard: ok（架空名のフィクスチャ）
    # 境界値
    check("SAFE_WORDS を含む行は通す", scan_text("サンプルレジデンス_0703\n", [], []) == [])
    check("★12桁のダミーコードは拾わない", scan_text('A = "100521000001"\n', [], []) == [])
    check("駅名だけの行は拾わない", scan_text('"西長堀": "NISHI-NAGAHORI",\n', [], []) == [])
    check("社名の接頭辞リストは拾わない",
          scan_text('SAMTY_PREFIXES = ("S-RESIDENCE", "S-FORT")\n', [], []) == [])
    check("短い塊は STRUCT に掛けない", scan_text("ab_101\n", [], []) == [])
    check("1行1件（最長一致優先）",
          len(scan_text("ヨソノナ梅田タワー と カタカナレジデンス_903\n", ex, [])) == 1)  # name-guard: ok（架空名のフィクスチャ）
    # 異常系
    check("リストが無くても落ちない", load_names(pathlib.Path("/nope/nope.txt"))[2] is not None)
    check("リスト未指定でも落ちない", load_names(None)[2] is not None)
    check("NFD の入力を NFC で照合する",
          scan_text(unicodedata.normalize("NFD", "ヨソノナ梅田タワー\n"), ex, []) != [])  # name-guard: ok（架空名のフィクスチャ）
    print("\n" + ("✅ 全PASS" if ok else "❌ FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="実物件名がコミットに混ざるのを止める")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="作業ツリー全体")
    g.add_argument("--staged", action="store_true", help="ステージ済みだけ（pre-commit）")
    g.add_argument("--self-test", action="store_true")
    ap.add_argument("--list", default=os.environ.get("SNS_NAME_LIST"),
                    help="実棟名リストのパス（既定: 環境変数 SNS_NAME_LIST → 規定パス）")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    repo = pathlib.Path(_git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel").strip()
                        or pathlib.Path.cwd())
    lp = pathlib.Path(a.list).expanduser() if a.list else DEFAULT_LIST
    return run(repo, a.staged, lp)


if __name__ == "__main__":
    sys.exit(main())
