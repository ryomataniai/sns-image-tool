# -*- coding: utf-8 -*-
"""
SNS画像量産ツール 共通コア (core.py)
=====================================
CLI版 (generate_images.py) と Web版 (app.py) の両方から使う生成ロジック。
将来 Next.js/Vercel 版に移す際も、この生成手順をそのまま移植できる。
"""
from __future__ import annotations  # Python 3.9 で str|None 注釈を許可

import base64
import csv
import os
import re
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

# ----------------------------------------------------------------------
# 設定
# ----------------------------------------------------------------------
# デフォルトは最安・量産向きの 2.5 Flash Image。
DEFAULT_MODEL = "gemini-2.5-flash-image"

MODELS = [
    "gemini-2.5-flash-image",   # Nano Banana（最安・量産）
    "gemini-3.1-flash-image",   # Nano Banana 2（高品質・※価格要確認）
    "gemini-3-pro-image",       # Nano Banana Pro（最高品質・※価格要確認）
]

# 1枚あたり参考単価(USD)。実費は請求で要確認。
PRICE_PER_IMAGE = {
    "gemini-2.5-flash-image": 0.039,
    "gemini-3.1-flash-image": 0.039,
    "gemini-3-pro-image": 0.134,
}

ASPECT_RATIOS = ["4:5", "1:1", "9:16", "16:9", "3:4", "2:3"]
SIZES = ["512", "1K", "2K", "4K"]

# 全プロンプト共通で末尾に付与する安全文言（線引き：特定物件に見せない）
SAFETY_SUFFIX = (
    " 文字・ロゴ・透かしは入れない。特定の実在物件ではなく"
    "「暮らしのイメージ」として生成。"
)

USD_TO_JPY = 155  # 表示用の概算レート


# ----------------------------------------------------------------------
# ユーティリティ
# ----------------------------------------------------------------------
def slugify(text: str, maxlen: int = 32) -> str:
    """ファイル名用に簡易整形。"""
    text = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:maxlen] if text else "img"


def get_api_key(explicit: str | None = None) -> str | None:
    """明示キー → 環境変数の順で取得。"""
    if explicit:
        return explicit.strip()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def get_client(api_key: str | None = None):
    """Geminiクライアントを返す。失敗時は例外メッセージを上げる。"""
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai 未インストール: pip install google-genai --break-system-packages"
        ) from e
    key = get_api_key(api_key)
    if not key:
        raise RuntimeError(
            "APIキーが未設定です。環境変数 GEMINI_API_KEY を設定するか、"
            "UIのキー欄に入力してください。取得: https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=key)


def estimate_cost(n_images: int, model: str) -> tuple[float, float]:
    """(USD, JPY) の推定コストを返す。"""
    usd = n_images * PRICE_PER_IMAGE.get(model, 0.039)
    return usd, usd * USD_TO_JPY


# ----------------------------------------------------------------------
# 生成ロジック（CLI / Web / 将来のサーバ版で共通利用）
# ----------------------------------------------------------------------
def generate_image_bytes(client, prompt, model=DEFAULT_MODEL,
                         aspect="4:5", size="1K", retries=1,
                         add_safety=True):
    """1プロンプト→PNGバイト列。成功で (bytes, None)、失敗で (None, error_str)。
    retries=1（最大2回）。画像生成は成功時に課金されるため、無駄なリトライは抑える。"""
    from google.genai import types

    full_prompt = prompt + (SAFETY_SUFFIX if add_safety else "")

    # SDKバージョン差を吸収：ImageConfig が image_size を持つ版のみ渡す
    # （google-genai 1.x は aspect_ratio のみ、2.x 系は image_size 等も対応）
    ic_fields = types.ImageConfig.model_fields
    ic_kwargs = {"aspect_ratio": aspect}
    if size and "image_size" in ic_fields:
        ic_kwargs["image_size"] = size
    cfg = types.GenerateContentConfig(
        response_modalities=["Image"],
        image_config=types.ImageConfig(**ic_kwargs),
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=[full_prompt], config=cfg
            )
            # inline_data の生バイトから直接取り出す（SDKのバージョン差に強い）
            for part in resp.parts:
                blob = getattr(part, "inline_data", None)
                raw = getattr(blob, "data", None) if blob is not None else None
                if raw:
                    if isinstance(raw, str):           # 念のためbase64対応
                        raw = base64.b64decode(raw)
                    try:                                # PNGに正規化
                        im = Image.open(BytesIO(raw)).convert("RGB")
                        out = BytesIO()
                        im.save(out, format="PNG")
                        return out.getvalue(), None
                    except Exception:                   # noqa: BLE001
                        return raw, None                # 最悪そのまま返す
            # API応答はあったが画像なし＝セーフティ拒否等。リトライしても無駄なので即返す
            return None, "画像が返らず（セーフティ拒否 or プロンプト不備の可能性）"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt < retries:
                time.sleep(2 * (attempt + 1))  # 簡易バックオフ
    return None, last_err


# ----------------------------------------------------------------------
# 画像入力（マイソク／間取り図 → 内観シミュレーション）
# ----------------------------------------------------------------------
def pdf_page_to_png(pdf_bytes: bytes, page_index: int = 0, dpi: int = 150) -> bytes:
    """PDF（マイソク）の指定ページをPNGバイト列に変換。PyMuPDF使用。"""
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        idx = max(0, min(page_index, doc.page_count - 1))
        pix = doc[idx].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    finally:
        doc.close()


def pdf_page_count(pdf_bytes: bytes) -> int:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def _image_part(image_bytes: bytes, mime_type: str = "image/png"):
    """アップロード画像を Gemini contents 用の Part に変換（SDK差を吸収）。"""
    from google.genai import types
    if hasattr(types.Part, "from_bytes"):
        return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    return types.Part(inline_data=types.Blob(data=image_bytes, mime_type=mime_type))


# 内観生成のスタイル・部屋プリセット（UIから選ばせる）
INTERIOR_STYLES = {
    "ナチュラル/北欧": "明るい木目とオフホワイト基調のナチュラル北欧スタイル",
    "和モダン": "木と和紙の質感を活かした落ち着いた和モダンスタイル",
    "ホテルライク": "低彩度でまとめた上質なホテルライクスタイル",
    "シンプルモダン": "白と黒を基調にしたミニマルなシンプルモダンスタイル",
    "カフェ風": "ヴィンテージ木材とグリーンを効かせたカフェ風スタイル",
}
INTERIOR_ROOMS = ["おまかせ", "リビング", "寝室", "ダイニング/キッチン", "ワンルーム全体"]


def build_interior_prompt(style_desc: str, room: str, staged: bool = True) -> str:
    """マイソク／間取り図 → 内観写真 生成用プロンプトを組み立てる。
    staged=True: 家具ありの暮らしのイメージ / False: 家具なしの空室。"""
    room_line = "" if room == "おまかせ" else f"・{room}を主役にする。"
    if staged:
        body = (
            "この画像は賃貸物件の間取り図（またはマイソク）です。"
            "この間取りの部屋の配置・広さの雰囲気を参考に、"
            "実在しそうな居住空間の『内観写真』をフォトリアルに1枚生成してください。\n"
            f"- インテリアは{style_desc}。\n"
            f"{room_line}\n"
            "- 自然光の入る明るく心地よい生活シーン。"
        )
    else:
        body = (
            "この画像は賃貸物件の間取り図（またはマイソク）です。"
            "この間取りの部屋の配置・広さの雰囲気を参考に、"
            "『家具のない清潔な空室』の内観写真をフォトリアルに1枚生成してください。\n"
            "- 白い壁とフローリング、生活感なし。\n"
            f"{room_line}\n"
        )
    rules = (
        "\n【厳守】\n"
        "- 建物の外観・外観写真・間取り図の線や文字・平面図は一切含めない。内観のみ。\n"
        "- 実際にはあり得ない広さ・眺望・窓・設備を足して誇張しない。自然で現実的な広さ感。\n"
        "- 家具や小物で不自然に空間を広く見せない。"
    )
    return body + rules


def generate_from_image_bytes(client, image_bytes, prompt, model=DEFAULT_MODEL,
                              aspect="4:5", size="1K", mime_type="image/png",
                              retries=1, add_safety=True):
    """入力画像（間取り図/マイソク）＋プロンプト → PNGバイト列。
    成功で (bytes, None)、失敗で (None, error_str)。generate_image_bytes の画像入力版。"""
    from google.genai import types

    full_prompt = prompt + (SAFETY_SUFFIX if add_safety else "")

    ic_fields = types.ImageConfig.model_fields
    ic_kwargs = {"aspect_ratio": aspect}
    if size and "image_size" in ic_fields:
        ic_kwargs["image_size"] = size
    cfg = types.GenerateContentConfig(
        response_modalities=["Image"],
        image_config=types.ImageConfig(**ic_kwargs),
    )
    img_part = _image_part(image_bytes, mime_type)
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=[img_part, full_prompt], config=cfg
            )
            for part in resp.parts:
                blob = getattr(part, "inline_data", None)
                raw = getattr(blob, "data", None) if blob is not None else None
                if raw:
                    if isinstance(raw, str):
                        raw = base64.b64decode(raw)
                    try:
                        im = Image.open(BytesIO(raw)).convert("RGB")
                        out = BytesIO()
                        im.save(out, format="PNG")
                        return out.getvalue(), None
                    except Exception:  # noqa: BLE001
                        return raw, None
            return None, "画像が返らず（セーフティ拒否 or プロンプト不備の可能性）"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    return None, last_err


# ----------------------------------------------------------------------
# プロンプトCSV（CLI用。Webはテキスト欄から直接渡す）
# ----------------------------------------------------------------------
def load_prompts(path: str):
    """CSV → [(id, prompt, count)]。必須列 prompt、任意列 id/count。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"プロンプトCSVが見つかりません: {path}")
    rows = []
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "prompt" not in (reader.fieldnames or []):
            raise ValueError("CSVに 'prompt' 列がありません。")
        for i, row in enumerate(reader, 1):
            prompt = (row.get("prompt") or "").strip()
            if not prompt:
                continue
            pid = (row.get("id") or "").strip() or f"{i:02d}_{slugify(prompt)}"
            try:
                cnt = int((row.get("count") or "1").strip())
            except ValueError:
                cnt = 1
            rows.append((pid, prompt, max(1, cnt)))
    if not rows:
        raise ValueError("有効なプロンプトが1件もありません。")
    return rows


def build_plan(rows, per_prompt_count=1):
    """(id, prompt) に展開。count列とCLI/UIの倍率を掛ける。"""
    plan = []
    for pid, prompt, cnt in rows:
        total = cnt * per_prompt_count
        if total == 1:
            plan.append((pid, prompt))
        else:
            for n in range(1, total + 1):
                plan.append((f"{pid}_{n:02d}", prompt))
    return plan
