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


def extract_pdf_photos(pdf_bytes: bytes, min_px: int = 250):
    """PDF（マイソク）に埋め込まれたラスタ画像を抽出。
    min(w,h) >= min_px のものを PNGバイト列で返す。
    returns: list of (png_bytes, w, h)。"""
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out, seen = [], set()
    try:
        for pno in range(doc.page_count):
            for im in doc.get_page_images(pno, full=True):
                xref = im[0]
                if xref in seen:
                    continue
                seen.add(xref)
                d = doc.extract_image(xref)
                w, h = d.get("width", 0), d.get("height", 0)
                if min(w, h) >= min_px:
                    try:
                        img = Image.open(BytesIO(d["image"])).convert("RGB")
                        buf = BytesIO()
                        img.save(buf, format="PNG")
                        out.append((buf.getvalue(), w, h))
                    except Exception:  # noqa: BLE001
                        pass
    finally:
        doc.close()
    return out


def _disclaimer_font(size: int):
    """注記用の日本語フォント（carouselのfind_jp_fontを遅延参照）。"""
    p = None
    try:
        import carousel  # 遅延import（循環回避）
        p = carousel.find_jp_font()
    except Exception:  # noqa: BLE001
        p = None
    from PIL import ImageFont
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            pass
    return ImageFont.load_default()


def add_disclaimer(png_bytes: bytes, text: str = "※AI加工のイメージ") -> bytes:
    """生成画像の下部に注記帯（半透明黒＋白文字）を焼き込む。"""
    from PIL import Image, ImageDraw
    img = Image.open(BytesIO(png_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    fs = max(20, W // 38)
    font = _disclaimer_font(fs)
    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    pad = max(8, fs // 2)
    band_h = th + pad * 2
    draw.rectangle([0, H - band_h, W, H], fill=(0, 0, 0, 120))
    draw.text((pad, H - band_h + pad - bbox[1]), text, font=font,
              fill=(255, 255, 255, 255),
              stroke_width=max(1, fs // 12), stroke_fill=(0, 0, 0, 255))
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def build_staging_prompt(style_desc: str) -> str:
    """実際の空室写真 → 家具ステージング（構造は維持）。"""
    return (
        "入力画像は賃貸物件の実際の室内写真（多くは空室）です。"
        "この部屋の壁・窓・床・扉・広さ・構造・設備は一切変えずに維持したまま、"
        "画像を高解像度・高精細に整え、"
        f"{style_desc}の家具・小物を自然に配置して、生活感のある部屋にしてください。\n"
        "【厳守】実際にない窓・眺望・設備を足さない。部屋を実際より広く見せない。"
        "壁の色・間取り・設備のグレードを変えない。"
        "画像内に文字・ロゴ・透かし・数字を一切入れない。"
    )


def build_enhance_prompt() -> str:
    """実際の室内写真 → 内容を変えず高解像度化のみ（水回り向け）。"""
    return (
        "入力画像は賃貸物件の実際の室内写真です。"
        "写っている内容（壁・窓・床・設備・物・広さ・構造）を一切変えず、"
        "何も追加・削除せずに、圧縮ノイズや粗さだけを取り除いて"
        "高解像度・高精細にきれいに整えてください。"
        "家具や物を新たに足さない。画像内に文字・ロゴ・透かし・数字を入れない。"
    )


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
    "リノベ北欧ミッドセンチュリー": "オークのヘリンボーン床と木目を基調に、ダスティブルーやミントグリーンを差し色にし、木脚のミッドセンチュリー家具とグリーンのベルベットソファ・球体ペンダントを効かせた、明るく上質なリノベーションスタイル",
    "インダストリアル": "コンクリート躯体現しの天井と配管、モルタルのキッチン、黒アイアンとレザー、無垢の木床を組み合わせた、都会的でクールなインダストリアル・リノベスタイル",
    "レトロヴィンテージ": "暖色の間接照明、ヴィンテージの木製家具とレコード、多くの観葉植物とグリーンのソファで満たした、ノスタルジックで趣味的なミッドセンチュリー・ヴィンテージスタイル",
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


# ルームツアー用：部屋ごとのプロンプトヒント（マイソクから各部屋を生成）
ROOM_TOUR_PRESETS = {
    "玄関": "玄関・エントランス。シューズボックス、たたき、廊下の入口が見える構図。",
    "LDK": "リビング・ダイニング・キッチンのある明るいメインの生活空間。",
    "洋室": "個室の洋室（寝室）。ベッドと収納のある落ち着いた空間。",
    "洋室2": "もう一つの洋室（書斎・子ども部屋など）。",
    "キッチン": "システムキッチンまわり。作業スペースと収納。",
    "浴室": "清潔なユニットバス（浴槽・シャワー）。",
    "洗面所": "洗面化粧台・脱衣スペース。",
    "トイレ": "清潔なトイレ空間。",
    "バルコニー": "バルコニーと、そこから見える屋外・空の抜け感。",
}


def build_room_tour_prompt(style_desc: str, room_label: str, room_hint: str,
                           with_ref: bool = False) -> str:
    """マイソク → 同一住戸の指定部屋の内観を生成するプロンプト。
    with_ref=True のときは2枚目の参照画像にトーンを合わせる指示を足す。"""
    ref_line = (
        "\n- 参照として渡した2枚目の画像（同じ住戸の別カット）と、"
        "床材・壁の色・照明・全体のスタイルを必ず揃える。"
        if with_ref else ""
    )
    return (
        "1枚目の画像は賃貸物件のマイソク／間取り図です。"
        f"この同一住戸の中の「{room_label}」の内観写真を、フォトリアルに1枚生成してください。\n"
        f"- {room_hint}\n"
        f"- インテリアは{style_desc}。住戸全体で統一感を持たせる。\n"
        "- 自然光の入る清潔で心地よい雰囲気。"
        f"{ref_line}\n"
        "【厳守】建物の外観・外観写真・間取り図の線や文字・平面図・数字は一切出さない。"
        "内観のみ。実際にあり得ない広さ・設備・眺望を足して誇張しない。"
    )


def generate_from_images(client, images, prompt, model=DEFAULT_MODEL,
                         aspect="4:5", size="1K", retries=1, add_safety=True):
    """複数の入力画像 [(bytes, mime), ...] ＋プロンプト → PNGバイト列。
    成功で (bytes, None)、失敗で (None, error_str)。"""
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
    parts = [_image_part(b, m) for (b, m) in images]
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=parts + [full_prompt], config=cfg
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
