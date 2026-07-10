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


def _wrap_text_to_width(draw, text: str, font, max_w: float, max_lines: int) -> list:
    """max_w(px) に収まるよう貪欲に折返し。max_lines を超える分は末尾を … で切り詰める。"""
    lines, cur = [], ""
    for ch in text:
        if not cur or draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = (last + "…") if last else "…"
    return lines


def add_disclaimer(png_bytes: bytes, text: str = "※AI加工のイメージ") -> bytes:
    """生成画像の下部に注記帯（半透明黒＋白文字）を焼き込む。長い文言でも右端で切れない
    ようフォント自動縮小→2行折返し→…切詰めで画像幅に必ず収める（法令注記の可読性維持）。"""
    from PIL import Image, ImageDraw
    img = Image.open(BytesIO(png_bytes)).convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    pad = max(8, W // 80)
    max_w = W - 2 * pad
    base_fs = max(20, W // 34)
    min_fs = max(13, W // 60)
    fs = base_fs                                   # まず1行で収まる最大フォントへ縮小
    while fs > min_fs and draw.textlength(text, font=_disclaimer_font(fs)) > max_w:
        fs -= 1
    font = _disclaimer_font(fs)
    if draw.textlength(text, font=font) <= max_w:
        lines = [text]
    else:                                          # 最小フォントでも入らない→2行→…切詰め
        lines = _wrap_text_to_width(draw, text, font, max_w, max_lines=2)
    bb = draw.textbbox((0, 0), "※あAg1｜", font=font)
    line_h = int((bb[3] - bb[1]) * 1.4)
    pad_v = max(6, fs // 3)
    band_h = line_h * len(lines) + pad_v * 2
    draw.rectangle([0, H - band_h, W, H], fill=(0, 0, 0, 130))
    y = H - band_h + pad_v
    sw = max(1, fs // 12)
    for ln in lines:
        draw.text((pad, y - bb[1]), ln, font=font, fill=(255, 255, 255, 255),
                  stroke_width=sw, stroke_fill=(0, 0, 0, 255))
        y += line_h
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def crop_uniform_borders(png_bytes: bytes, min_keep: float = 0.55,
                         bright: float = 240.0, var_tol: float = 40.0,
                         med_cap: float = 3.0, jump: float = 150.0,
                         look: int = 10, min_rows: int = 12,
                         max_frac: float = 0.30) -> bytes:
    """上下の『明るい余白帯』（生成AIが正方素材を縦横比変換した際の白レターボックス）を
    自動トリミング。実画像20枚＋誤爆テストで検証したパラメータで判定する。
    帯の条件（上端・下端それぞれ／下端は配列反転で同じ関数）：
      1) 端から連続して『行平均の最小chが bright 以上（＝白い）』かつ『行内分散の最大chが
         var_tol 未満（＝ほぼ平坦）』の行を数える（高さの max_frac まで）。
      2) その行数 t が min_rows 未満、または max_frac に達したら帯なし（0）。
      3) 帯全体の分散の中央値が med_cap 以上なら帯でない（帯は全体が極端に平坦なはず）。
      4) 帯の内側 look 行の最大分散が jump 以下なら帯でない（内側の境界＝写真開始が急峻でない）。
    ※旧方式は端ノイズ行で走査停止／内側半分の分散中央値がしきい値2.0の境界を跨いで取りこぼした
      （辰巳402再生成の 05_洋室=2.08・08_クローゼット=1.50）。本方式は bright＋jump境界で頑健化。
    横は切らない。3Dパースは呼び出し側で除外（明るい背景の俯瞰図は誤爆しうるため）。
    切りすぎ（min_keep未満）は切らない。"""
    import numpy as np
    try:
        img = Image.open(BytesIO(png_bytes)).convert("RGB")
    except Exception:  # noqa: BLE001
        return png_bytes
    arr = np.asarray(img, dtype=np.float32)
    H, W, _ = arr.shape
    cap = H * max_frac

    def _band(m, v):
        """index 0 側の端から白帯の行数を返す（帯判定を満たさなければ 0）。
        m: 各行平均の最小チャンネル値, v: 各行分散の最大チャンネル値。"""
        t = 0
        while t < cap and m[t] >= bright and v[t] < var_tol:
            t += 1
        if t < min_rows or t >= cap:
            return 0
        if float(np.median(v[:t])) >= med_cap:            # 帯は全体が極端に平坦
            return 0
        if float(v[t:t + look].max()) <= jump:            # 内側の境界が急峻
            return 0
        return t

    m = arr.mean(axis=1).min(axis=1)          # (H,) 各行平均の最小チャンネル値
    v = arr.var(axis=1).max(axis=1)           # (H,) 各行分散の最大チャンネル値
    t = _band(m, v)                                       # 上端
    b = H - _band(m[::-1], v[::-1])                        # 下端（反転して同判定）
    if (t, b) == (0, H):                                   # 帯なし
        return png_bytes
    if (b - t) < H * min_keep:                             # 切りすぎ防止（誤検出回避）
        return png_bytes
    out = BytesIO()
    img.crop((0, t, W, b)).save(out, format="PNG")
    return out.getvalue()


def build_staging_prompt(style_desc: str, room_use: str = "",
                         user_request: str = "") -> str:
    """実際の空室写真 → 家具ステージング（構造は維持）。

    room_use: "リビング" / "寝室" / "" (おまかせ=広さから自動推定)
    """
    if room_use == "リビング":
        furni = ("この洋室はリビングとして使う想定です。"
                 "ソファ・ローテーブル・テレビボード・ラグ・観葉植物など"
                 "リビングにふさわしい家具を配置してください。ベッドは置かないでください。")
    elif room_use == "寝室":
        furni = ("この洋室は寝室として使う想定です。"
                 "ベッド・ナイトテーブル・寝室用の照明・ラグなど"
                 "寝室にふさわしい家具を配置してください。ソファやダイニングは置かないでください。")
    else:
        furni = ("洋室の場合は部屋の広さから用途を推定し、"
                 "広い洋室にはリビング家具（ソファ・ローテーブル等）、"
                 "狭い洋室には寝室家具（ベッド等）を配置してください。")
    return (
        "入力画像は賃貸物件の実際の室内写真（多くは空室）です。"
        "（この機能は居室＝リビング・寝室・洋室向けです。）"
        "この部屋の壁・窓・床・扉・天井・建具・広さ・構造・設備は一切変えずに維持したまま、"
        "画像を高解像度・高精細に整え、"
        f"{style_desc}の色調・家具テイストに合わせて、"
        "家具・小物・ラグ・据置型の照明器具だけを自然に追加し、"
        "生活感のある部屋にしてください。"
        "壁・天井・床・建具・キッチン・水回りなどの内装や設備は入力画像のまま一切変更しないでください。\n"
        f"{furni}"
        f"{_request_line(user_request)}\n"
        "【厳守】実際にない窓・眺望・設備を足さない。部屋を実際より広く見せない。"
        "壁の色・間取り・設備のグレードを変えない。"
        "入力画像で壁になっている面に、窓・扉・開口部・別室への抜けを一切新設しない"
        "（壁は壁のまま維持する）。窓の無い壁にカーテンを描かない"
        "（カーテンは窓の存在を含意するため＝窓の捏造につながる）。カーテンは窓が写っている場合のみ、その窓に掛ける。"
        "明るさは照明・採光の演出で表現し、窓を追加して明るくしようとしない。"
        "既存の開口部・入口・隣室への抜け・下がり壁・室内窓・扉を、塞いだり・壁にしたり・消したりしない。"
        "奥に見える部屋や通路もそのまま残す。"
        "天井・床・壁の素材や仕上げを変えない。"
        "躯体現しの天井・露出配管・ヘリンボーン床などの内装改変を行わない（家具の追加のみ）。"
        "キッチン・浴室・洗面などの水回り設備を追加・変更・撤去しない。"
        "照明は天井の作り付けを変えず、置き型・スタンド照明の追加に留める。"
        "天井に不自然な四角い枠・パネル・線を描き足さない（点検口などを勝手に強調しない）。"
        "万一この画像が居室以外（浴室・洗面・トイレ・キッチン・玄関・収納など）だった場合でも、"
        "家具で埋めたり窓・別室を新たに描き足したりせず、現況の用途・構造を保ったまま高解像度化に留める。"
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


def is_blank_image(image_bytes, std_threshold: float = 10.0) -> bool:
    """ほぼ白紙・単色（＝意味のない画像）かどうかをローカルで判定する。"""
    try:
        from PIL import Image, ImageStat
        im = Image.open(BytesIO(image_bytes)).convert("L")
        im.thumbnail((200, 200))
        return ImageStat.Stat(im).stddev[0] < std_threshold
    except Exception:  # noqa: BLE001
        return False


def classify_rooms(client, images, model="gemini-2.5-flash"):
    """複数の室内写真をまとめて相対判定し、各写真の推奨処理ラベルを返す。

    洋室が複数ある場合、最も広く見えるものをリビング、狭いものを寝室に割り当てる。
    返り値: 各写真の推奨ラベル（app.py の TREAT と一致）のリスト。
    """
    import json as _json
    n = len(images)
    default = ["おまかせステージング"] * n
    if n == 0:
        return default
    try:
        parts = [_image_part(b, "image/png") for b in images]
        instruction = (
            f"以下は賃貸物件の室内写真{n}枚です（先頭から順に0〜{n-1}）。"
            "各写真の部屋種別を判定してください。"
            "居室（洋室・和室）が複数ある場合、最も広く見える居室をLIVING、"
            "それより狭い居室をBEDROOMとしてください。"
            "キッチンはKITCHEN、玄関はENTRANCE、浴室・洗面・トイレはWATER、"
            "廊下・バルコニーなど室内だが用途不明なものはOTHER、"
            "白紙・ロゴ・地図・間取り図・建物外観・文字だけの画像など、"
            "室内写真でないもの・判断がつかないものはSKIPとしてください。"
            f"出力はJSON配列のみ・長さ{n}。"
            '例: ["LIVING","BEDROOM","KITCHEN","WATER","SKIP"]。説明文は書かないこと。'
        )
        resp = client.models.generate_content(
            model=model, contents=parts + [instruction]
        )
        text = (getattr(resp, "text", "") or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        arr = _json.loads(m.group(0)) if m else []
    except Exception:  # noqa: BLE001
        return default

    mapping = {
        "LIVING": "リビングとしてステージング",
        "BEDROOM": "寝室としてステージング",
        "KITCHEN": "水回り・玄関を演出",
        "ENTRANCE": "水回り・玄関を演出",
        "WATER": "高解像度化のみ",
        "OTHER": "おまかせステージング",
        "SKIP": "使わない",
    }
    out = []
    for i in range(n):
        key = arr[i].upper() if i < len(arr) and isinstance(arr[i], str) else "OTHER"
        out.append(mapping.get(key, "おまかせステージング"))
    return out


def build_water_staging_prompt(style_desc: str = "", user_request: str = "") -> str:
    """水回り（キッチン/浴室/洗面/トイレ）・玄関 → 設備は変えず生活小物だけ演出。"""
    return (
        "入力画像は賃貸物件の水回り（キッチン・浴室・洗面・トイレ）または玄関の実際の写真です。"
        "設備・造作・構造・広さ・グレードは一切変えずに維持したまま高解像度・高精細に整え、"
        "その場所に合った生活小物だけを自然に少量だけ置いてください。"
        "キッチンなら調理小物・観葉植物・カゴなど、"
        "洗面なら畳んだタオル・小物・グリーン"
        "（洗濯機置き場＝防水パンが写っている場合は、そこに生活感のある洗濯機を1台自然に置く）、"
        "浴室なら入浴剤やタオル、トイレならグリーンや小物、"
        "玄関なら観葉植物・傘立て・ウォールデコ・少量の小物などを置いてください。"
        f"足す生活小物のテイストは{style_desc}に合わせてよいですが、"
        "既存の棚・扉・キャビネット・壁・壁パネル・床・建具などの色・木目・素材・仕上げ・グレードは"
        "一切変えないこと（例：濃い色の棚やアクセント壁を明るい木目に変えない）。"
        "全体は清潔感のある高解像度な仕上がりに整えてください。"
        f"{_request_line(user_request)}\n"
        "【厳守】実際にない設備（食洗機・浴室乾燥・収納・窓など）を絶対に足さない。"
        "入力画像で壁になっている面に、窓・扉・開口部・別室への抜けを一切新設しない（壁は壁のまま維持）。"
        "下駄箱・靴箱は玄関の造作が写っている場合のみ整える程度に留め、新たに新設しない。"
        "（防水パンがある場合の洗濯機は、入居者が持ち込む家電＝暮らしのイメージなので置いてよい。）"
        "蛇口・コンロ・便器・浴槽・框などの設備や造作の形・数・グレードを変えない。"
        "棚・キャビネット・扉・壁パネル・建具の色・仕上げ・素材を変えない（既存の色をそのまま維持する）。"
        "玄関は靴を大量に散らかさない。部屋を実際より広く見せない。"
        "天井に不自然な四角い枠・パネル・線を描き足さない（点検口などを勝手に強調しない）。"
        "画像内に文字・ロゴ・透かし・数字を一切入れない。"
    )


# リノベ後の部屋別指示（機能＝部屋種別は不変、度合いだけリノベ）
_RENO_ROOM_LINES = {
    "LDK": "床・壁・天井・照明・建具を刷新し、家具を配置したリビング・ダイニングの完成イメージにする。居室のまま。",
    "リビング": "床・壁・天井・照明・建具を刷新し、家具を配置したリビングの完成イメージにする。居室のまま。",
    "洋室": "床・壁・天井・照明・建具を刷新し、家具を配置した洋室の完成イメージにする。洋室のまま。",
    "寝室": "床・壁・天井・照明・建具を刷新し、家具を配置した寝室の完成イメージにする。寝室のまま。",
    "キッチン": "新しいシステムキッチン・カウンターに刷新してよい。ただしキッチンの位置・給排水位置は維持し、"
               "別室（リビング等）や存在しない窓を新たに捏造しない。キッチンのまま。",
    "浴室": "新しいユニットバスに刷新してよい。ただし必ず浴槽（バスタブ）付きとし、シャワーのみ化しない。"
           "ラウンジチェアや家具・観葉植物を置かず、浴室のまま。位置・広さを維持する。",
    "洗面": "新しい洗面化粧台に刷新してよい。設備を撤去・移動せず、洗面所のまま。別室や存在しない窓を捏造しない。",
    "トイレ": "新しい便器・内装に刷新してよい。設備を撤去・移動せず、トイレのまま。別室や存在しない窓を捏造しない。",
    "玄関": "床・壁・照明・建具を刷新してよい。玄関のまま。座具・家具・別室・存在しない窓を足さない。",
    "廊下": "床・壁・照明を刷新してよい。廊下のまま。家具・別室・存在しない窓を足さない。",
    "クローゼット": "大きく改変せず、清掃・整頓程度の現況イメージに留める。別室やリビングを捏造しない。",
    "バルコニー": "大きく改変せず、ほぼ現況のイメージに留める。",
    "その他": "大きく改変せず、ほぼ現況のイメージに留める。別室・存在しない窓を捏造しない。",
}


def build_renovation_prompt(style_desc: str = "", user_request: str = "",
                            room: str = "") -> str:
    """中古物件の現況写真 → リノベ後の完成イメージ（room-aware・部屋の機能は不変）。

    room を渡すと部屋別に刷新の度合いを制御（浴室はラウンジ化しない等）。
    room未指定（""）は後方互換の汎用リノベ文（旧ステージングツール用）。
    """
    room_line = _RENO_ROOM_LINES.get(room)
    if room_line is None:
        # 後方互換：room未指定は従来の汎用リノベ（居室相当）
        room_line = ("床・壁・天井・照明・建具・キッチンや水回り設備・収納などの内装をそのテイストに合わせて更新し、"
                     "家具・小物も配置して暮らしのイメージが伝わる完成度にする（設備の撤去や位置移動は行わない）。")
    return (
        "入力画像は中古物件の現況（リフォーム前）の室内写真です。"
        "この部屋を購入後にリフォーム／リノベーションした『完成予想イメージ』を、"
        "フォトリアルに1枚生成してください。\n"
        f"- 全体を{style_desc}のテイストで刷新してよい（ただし仕上げ・意匠のリフォーム範囲に留める）。\n"
        f"- {room_line}\n"
        f"{_request_line(user_request)}\n"
        "【厳守】リノベ後も同じ機能の部屋であること。別用途の部屋（リビング等）や"
        "存在しない窓・空間を新たに捏造しない。"
        "窓の位置・部屋の基本的な広さ・階高・柱や梁など動かせない構造は現況を尊重し、"
        "実際にあり得ない広さ・眺望に誇張しない。"
        "更新は仕上げ・意匠のリフォーム範囲に留め、間取り変更・設備の撤去・水回り位置の移動は行わない。"
        "浴室・ユニットバスは日本の住宅として必ず浴槽（バスタブ）付きとし、"
        "浴槽を撤去してシャワーのみにしない。"
        "全体は『同じ部屋をリフォームした後』とわかる範囲に留め、別物件に見えるほど改変しない。"
        "画像内に文字・ロゴ・透かし・数字を一切入れない。"
    )


def pick_reference_photo(client, pdf_bytes):
    """マイソクPDFから室内写真を抽出し、トーン参照に最適な1枚を返す。

    優先度：リビング → 寝室 → その他の居室 → 水回り。
    白紙・ロゴ・地図・図面・外観（SKIP）は除外。見つからなければ None。
    """
    try:
        photos = extract_pdf_photos(pdf_bytes, min_px=250)
    except Exception:  # noqa: BLE001
        return None
    cand = [p[0] for p in photos if not is_blank_image(p[0])]
    if not cand:
        return None
    try:
        labels = classify_rooms(client, cand)
    except Exception:  # noqa: BLE001
        labels = [""] * len(cand)
    priority = {
        "リビングとしてステージング": 0,
        "寝室としてステージング": 1,
        "おまかせステージング": 2,
        "水回り・玄関を演出": 3,
    }
    best, best_rank = None, 99
    for b, lab in zip(cand, labels):
        if lab == "使わない":      # SKIP（図面・外観・地図など）は参照にしない
            continue
        rank = priority.get(lab, 4)
        if rank < best_rank:
            best_rank, best = rank, b
    return best if best is not None else cand[0]


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


def _request_line(user_request: str) -> str:
    """ユーザーの自由記述の要望を、安全ルールを崩さない範囲で反映する一文。"""
    req = (user_request or "").strip()
    if not req:
        return ""
    return ("\n- 追加のご要望（下記の【厳守】に反しない範囲で可能な限り反映）："
            f"{req}\n")


def build_interior_prompt(style_desc: str, room: str, staged: bool = True,
                          user_request: str = "") -> str:
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
    return body + _request_line(user_request) + rules


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

# ルームツアー用：部屋タイプ別の「置いてよい家具／絶対に置かない家具」ルール。
# 参照写真の家具（例：リビングの緑ソファ）がトイレ等に転写される事故を防ぐ。
ROOM_TOUR_FURNITURE = {
    "玄関": "シューズボックス・ベンチ・姿見・観葉植物・傘立てなど玄関にふさわしいものだけを置く。"
            "ソファ・ベッド・ダイニングテーブル・キッチン設備は絶対に置かない。",
    "LDK": "ソファ・ローテーブル・ダイニングテーブルと椅子・テレビボード・ラグ・観葉植物など"
           "リビングダイニングの家具を置く。ベッド・便器・浴槽・洗面台は置かない。",
    "洋室": "ベッドを主役に、ナイトテーブル・チェスト・ラグ・照明など寝室の家具だけを置く。"
            "ソファのセットを主役にしない。ダイニング・便器・浴槽・キッチンは置かない。",
    "洋室2": "デスク・チェア・本棚などの書斎／子ども部屋、またはベッド中心の個室にする。"
             "ソファのセットを主役にしない。ダイニング・便器・浴槽・キッチンは置かない。",
    "キッチン": "システムキッチンと調理小物・食器・観葉植物などキッチンまわりのものだけを置く。"
              "ソファ・ベッド・便器・浴槽は置かない。",
    "浴室": "浴槽とシャワー、入浴剤やタオルなど浴室の物だけにする。"
            "ソファ・椅子・ベッド・ダイニング・便器など、家具や他の部屋の設備は絶対に置かない。",
    "洗面所": "洗面化粧台・洗濯機スペース・畳んだタオル・小物だけを置く。"
             "ソファ・ベッド・椅子・ダイニングは置かない。",
    "トイレ": "便器と手洗い、小さな棚・タオル・グリーン程度だけにする。"
            "ソファ・椅子・ベッド・ダイニング・大型家具は絶対に置かない（トイレにソファは不自然）。",
    "バルコニー": "屋外のバルコニー・ベランダ。小さなアウトドアチェアやグリーン程度は可。"
              "ソファ・ベッド・便器など室内の家具・設備は置かない。",
}


def build_room_tour_prompt(style_desc: str, room_label: str, room_hint: str,
                           with_ref: bool = False, user_request: str = "") -> str:
    """マイソク → 同一住戸の指定部屋の内観を生成するプロンプト。
    with_ref=True のときは2枚目の参照画像に「配色・素材だけ」合わせる指示を足す。
    部屋タイプ別の家具ルールを厳守させ、トイレ等への家具転写を防ぐ。"""
    furni = ROOM_TOUR_FURNITURE.get(room_label, "")
    furni_line = f"\n- {furni}" if furni else ""
    ref_line = (
        "\n- 参照として渡した2枚目の画像（同じ住戸の別カット、または住戸全体の3D俯瞰パース）からは、"
        "床材・壁の色・木部やファブリックの色味・照明・全体のスタイルの雰囲気『だけ』を合わせる。"
        "2枚目に写っている家具や物の種類・配置はコピーせず、この部屋に合う家具（上記）に必ず従う。"
        "俯瞰パースの構図はコピーせず、必ずこの部屋の目線（アイレベル）の内観にする。"
        if with_ref else ""
    )
    return (
        "1枚目の画像は賃貸物件のマイソク／間取り図です。"
        f"この同一住戸の中の「{room_label}」の内観写真を、フォトリアルに1枚生成してください。\n"
        f"- {room_hint}"
        f"{furni_line}\n"
        f"- インテリアは{style_desc}。住戸全体で統一感を持たせる。\n"
        "- 自然光の入る清潔で心地よい雰囲気。"
        f"{ref_line}"
        f"{_request_line(user_request)}\n"
        "【厳守】建物の外観・外観写真・間取り図の線や文字・平面図・数字は一切出さない。"
        "内観のみ。実際にあり得ない広さ・設備・眺望を足して誇張しない。"
        "その部屋の用途に合わない家具（トイレや浴室のソファ、水回りのベッド等）を絶対に置かない。"
    )


def build_3d_perspective_prompt(style_desc: str = "", user_request: str = "") -> str:
    """間取り図 → 斜め上から見下ろす3Dドールハウス風の俯瞰パース（試験）。"""
    return (
        "1枚目の画像は賃貸／中古物件の間取り図（マイソク）です。"
        "この間取りを基に、屋根と手前側の壁を取り払って斜め上から見下ろした"
        "『3Dドールハウス風の俯瞰パース』を、フォトリアルに1枚生成してください。\n"
        "- 各部屋に家具・小物を配置し、間取りの部屋配置・広さ・動線が一目で分かるようにする。\n"
        f"- インテリアは{style_desc}。住戸全体で統一感を持たせる。\n"
        "- 自然な陰影と採光で立体感を出す。"
        f"{_request_line(user_request)}\n"
        "【厳守】間取り図の線・寸法・文字・平面図そのものは出さない。3Dの立体パースにする。"
        "実在しない広さ・階数・設備を誇張しない。"
        "画像内に文字・ロゴ・透かし・数字を一切入れない。"
    )


# ----------------------------------------------------------------------
# マイソク丸ごと → 実写真ベースのルームツアー（実写真ステージング＋穴の補完）
# ----------------------------------------------------------------------
# 分類コード → 表示ラベル
TOUR_ROOM_LABEL = {
    "LIVING": "リビング", "BEDROOM": "洋室", "KITCHEN": "キッチン",
    "BATH": "浴室", "WASH": "洗面所", "TOILET": "トイレ",
    "ENTRANCE": "玄関", "HALLWAY": "廊下", "STORAGE": "収納",
    "BALCONY": "バルコニー", "OTHER": "室内",
}
# 分類コード → 実写真の処理方法（実写真は構造を維持したまま演出する）
_TOUR_TREATMENT = {
    "LIVING": "staging_living", "BEDROOM": "staging_bedroom",
    "KITCHEN": "water", "BATH": "water", "WASH": "water",
    "TOILET": "water", "ENTRANCE": "water",
    "HALLWAY": "enhance", "STORAGE": "enhance", "BALCONY": "enhance",
    "OTHER": "staging_omakase",
}
# 実際の居室・設備として扱うコード（EXTERIOR/MAP/FLOORPLAN/BLANK は土台に使わない）
_TOUR_ROOM_CODES = set(TOUR_ROOM_LABEL.keys())
# 「写真が無い部屋」を生成で補うときの、ラベル→分類コード対応
GAP_LABEL_TO_CODE = {
    "玄関": "ENTRANCE", "トイレ": "TOILET", "洗面所": "WASH",
    "浴室": "BATH", "キッチン": "KITCHEN", "バルコニー": "BALCONY",
}


def classify_maisoku_images(client, images, model="gemini-2.5-flash"):
    """マイソク抽出画像を細かい部屋種別コードで分類（マルチラベル）。Gemini呼び出しは1回。
    返り値: 各画像について『写っている部屋コードのリスト』（例 [["BEDROOM"],["KITCHEN","WASH"],...]）。
    ※日本の賃貸マイソクは1枚に複数部屋（キッチン＋洗面台 等）が写るため複数コードを返す。
      主に写っている部屋を各配列の先頭に置く（主種別＝先頭コード）。
    後方互換: Geminiが単一コード（文字列）を返しても [code] に包んで返す。"""
    import json as _json
    n = len(images)
    default = [["OTHER"] for _ in range(n)]
    if n == 0:
        return default
    try:
        parts = [_image_part(b, "image/png") for b in images]
        instruction = (
            f"以下は不動産マイソクから抽出した画像{n}枚です（先頭から順に0〜{n-1}）。"
            "各画像に写っている部屋を『すべて』次のコードで挙げてください。日本の賃貸マイソクは"
            "1枚に複数部屋（例：キッチンと洗面台、洗面とトイレ）が写ることが多いので、"
            "写っていれば複数挙げる。主に写っている部屋を配列の先頭に置くこと。\n"
            "コード：\n"
            "LIVING=リビング/居間、BEDROOM=洋室・和室などの居室、KITCHEN=キッチン、"
            "BATH=浴室、WASH=洗面・脱衣所（洗面台）、TOILET=トイレ、"
            "WASHER_PAN=室内洗濯機置場・防水パン（洗濯機用の四角い防水パン・給水栓・排水口が"
            "写っている場合のみ。洗面/脱衣と一緒に写ることが多いのでWASHと併記してよい）、"
            "ENTRANCE=室内側から見た玄関土間・上がり框・靴箱（屋内）、HALLWAY=廊下、"
            "STORAGE=収納・クローゼット・ウォークインクローゼット(WIC)・納戸・シューズクローク"
            "（棚やハンガーパイプ主体で、生活家具〈ベッド/ソファ〉や掃き出し窓が無い小部屋は居室でなくSTORAGE）、"
            "BALCONY=バルコニー・ベランダ、FLOORPLAN=間取り図・平面図、"
            "EXTERIOR=屋外から写した建物外観・外壁・共用部・玄関ドアの外側"
            "（空・外壁タイル・道路・駐車場などが写る屋外写真は必ずEXTERIOR）、"
            "MAP=地図・案内図、BLANK=白紙・単色・ロゴ・文字のみ、OTHER=室内だが判別不能。\n"
            f"出力はJSON配列のみ・長さ{n}。各要素はその画像のコード配列（1つ以上）。説明文は書かない。"
            '例: [["BEDROOM"],["KITCHEN","WASH"],["WASH","WASHER_PAN"],'
            '["BATH"],["FLOORPLAN"],["EXTERIOR"]]。'
        )
        resp = client.models.generate_content(model=model, contents=parts + [instruction])
        text = (getattr(resp, "text", "") or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        arr = _json.loads(m.group(0)) if m else []
    except Exception:  # noqa: BLE001
        return default
    out = []
    for i in range(n):
        el = arr[i] if i < len(arr) else None
        if isinstance(el, list):
            codes = [str(c).upper() for c in el if isinstance(c, str) and c.strip()]
        elif isinstance(el, str):          # 後方互換：単一コード文字列
            codes = [el.upper()]
        else:
            codes = []
        out.append(codes or ["OTHER"])
    return out


# 間取り図の記載ラベル → 部屋種別（PL_ROOMS 準拠）へ正規化
_FLOORPLAN_TYPE_NORMALIZE = {
    "居室": "洋室", "洋室": "洋室", "和室": "洋室", "寝室": "洋室", "洋": "洋室",
    "LDK": "LDK", "DK": "LDK", "LD": "LDK", "リビング": "LDK", "リビングダイニング": "LDK",
    "キッチン": "キッチン", "K": "キッチン", "台所": "キッチン",
    "浴室": "浴室", "ユニットバス": "浴室", "UB": "浴室", "バス": "浴室", "風呂": "浴室",
    "洗面": "洗面", "洗面所": "洗面", "脱衣所": "洗面", "洗面脱衣": "洗面",
    "トイレ": "トイレ", "WC": "トイレ", "便所": "トイレ",
    "玄関": "玄関", "エントランス": "玄関",
    "ホール": "その他", "廊下": "その他", "ろうか": "その他",
    "ウォークインクローゼット": "クローゼット", "WIC": "クローゼット", "納戸": "クローゼット",
    "収納": "クローゼット", "クローゼット": "クローゼット", "CL": "クローゼット",
    "シューズクローク": "クローゼット", "SIC": "クローゼット", "SC": "クローゼット", "物入": "クローゼット",
    "バルコニー": "バルコニー", "ベランダ": "バルコニー", "BL": "バルコニー", "テラス": "バルコニー",
    "その他": "その他",
}


def _normalize_floorplan_type(raw):
    """間取り図のtypeラベルを PL_ROOMS 種別に正規化（部分一致でフォールバック）。"""
    s = str(raw or "").strip()
    if s in _FLOORPLAN_TYPE_NORMALIZE:
        return _FLOORPLAN_TYPE_NORMALIZE[s]
    for key, val in _FLOORPLAN_TYPE_NORMALIZE.items():   # 部分一致（例「洋室(1)」）
        if key in s:
            return val
    return "その他"


def read_floorplan_rooms(client, floorplan_bytes, model="gemini-2.5-flash"):
    """間取り図画像1枚をGeminiに読ませ、記載ラベルから部屋を列挙。
    返り値: [{"type":正規化種別, "label":図の文字, "jo":帖float|None, "position":位置str}]。失敗/空は []。"""
    import json as _json
    try:
        part = _image_part(floorplan_bytes, "image/png")
        instruction = (
            "この画像は日本の賃貸物件の間取り図（平面図）です。"
            "図に文字で書かれている部屋・空間をすべて列挙してください（線や寸法ではなく、記載ラベルを読む）。\n"
            "各部屋を {\"type\":種別, \"label\":図の文字そのまま, \"jo\":帖数, \"position\":位置} で表現。\n"
            "type は次から：居室 / LDK / DK / キッチン / 浴室 / 洗面 / トイレ / 玄関 / ホール / "
            "ウォークインクローゼット / 納戸 / 収納 / シューズクローク / クローゼット / バルコニー / その他。\n"
            "jo は『6』『6.2帖』等の畳数を数値で（記載が無ければ null）。"
            "position は図の中の大まかな位置（上/下/左/右/中央/左上 等、分からなければ空文字）。\n"
            "同じ部屋が複数あれば複数要素で列挙。出力はJSON配列のみ・説明文なし。\n"
            "例：[{\"type\":\"居室\",\"label\":\"洋室\",\"jo\":6,\"position\":\"上\"},"
            "{\"type\":\"LDK\",\"label\":\"LDK\",\"jo\":11,\"position\":\"下\"},"
            "{\"type\":\"ウォークインクローゼット\",\"label\":\"WIC\",\"jo\":null,\"position\":\"右\"}]"
        )
        resp = client.models.generate_content(model=model, contents=[part, instruction])
        text = (getattr(resp, "text", "") or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        arr = _json.loads(m.group(0)) if m else []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for d in arr if isinstance(arr, list) else []:
        if not isinstance(d, dict):
            continue
        jo = d.get("jo")
        try:
            jo = float(jo) if jo is not None else None
        except (TypeError, ValueError):
            jo = None
        out.append({
            "type": _normalize_floorplan_type(d.get("type") or d.get("label")),
            "label": str(d.get("label", "")).strip(),
            "jo": jo,
            "position": str(d.get("position", "")).strip(),
        })
    return out


# マイソクの主なラベル（ラベル行の次行が値。賃貸mikke/RealNetPro形式）
_FACT_LABELS = ["物件種目", "物件名", "号室名", "所在地", "交通", "建築構造", "間取タイプ",
                "専有面積", "開口部方位", "築年", "現況/入居時期", "賃料", "共益費・管理費",
                "敷金", "礼金", "保証金", "駐車場", "備 考", "備考"]


def parse_maisoku_facts(pdf_bytes: bytes) -> dict:
    """賃貸マイソクPDFから事実をラベルベースで抽出。取れない項目は入れない（創作しない）。
    返り値キー: name/address/access(list)/madori/area/built/rent/fee/equipment/full_text。"""
    facts = {}
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
    except Exception:  # noqa: BLE001
        return facts
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    labelset = set(_FACT_LABELS)

    def val(label):
        for i, ln in enumerate(lines):
            if ln == label and i + 1 < len(lines) and lines[i + 1] not in labelset:
                return lines[i + 1].strip()
        return None

    name = val("物件名")
    if name:
        facts["name"] = name.replace("　", " ").strip()
    for key, label, cond in [("address", "所在地", None), ("built", "築年", None),
                             ("madori", "間取タイプ", None), ("fee", "共益費・管理費", None)]:
        v = val(label)
        if v:
            facts[key] = v
    area = val("専有面積")
    if area and "㎡" in area:
        facts["area"] = area
    rent = val("賃料")
    if rent:
        facts["rent"] = rent.replace(" ", "")
    # 交通：交通ラベル直後の、路線/徒歩/バスを含む行を次ラベルまで収集
    access = []
    for i, ln in enumerate(lines):
        if ln == "交通":
            j = i + 1
            while (j < len(lines) and lines[j] not in labelset
                   and re.search(r"(徒歩|バス|「.+」|\d+\s*分)", lines[j])):
                access.append(lines[j].strip())
                j += 1
            break
    if access:
        facts["access"] = access
    # 設備：【…】ブロック or 設備キーワードを含む行（best-effort）
    eq = [ln for ln in lines if ("【" in ln) or re.search(
        r"(エアコン|追い焚き|バス・トイレ別|洗濯機置場|独立洗面|洗髪洗面|カウンターキッチン|"
        r"オートロック|インターホン|ウォークインクローゼット|室内洗濯|床下収納|BS|CS)", ln)]
    if eq:
        facts["equipment"] = " ".join(dict.fromkeys(eq))[:600]
    facts["full_text"] = text
    return facts


# PRコピーの禁止語（景表法：最上級・断定）
_PR_BANNED = ["最高", "完璧", "絶対", "日本一", "最安", "必ず", "唯一", "100%", "激安", "破格",
              "特選", "掘り出し", "No.1", "ナンバーワン", "最上級", "究極", "業界一", "他にない"]

# 立地訴求語（弱立地の物件では条件付きで禁止＝おとり/優良誤認の防止）
_PR_LOCATION_WORDS = ["駅近", "駅チカ", "駅すぐ", "駅から近い", "好立地", "アクセス良好", "アクセス抜群"]
_PR_STATION_WALK_THRESHOLD = 10   # 駅への直接徒歩がこの分数を超える／バス便のみ→立地訴求語を禁止

# 表紙/PRコピーの文字数上限（実測ベースの暫定値・表紙レンダリングを見て調整可）
_PR_MAX_TITLE = 16       # タイトル
_PR_MAX_SUBTITLE = 24    # サブタイトル
_PR_MAX_HIGHLIGHT = 14   # ◎魅力ポイント 各1つ

# 事実照合が必要な非数値の属性（同義グループ）：グループ内のどれかが facts/全文に
# あれば裏付けありとみなす（表記ゆれで枯れないように）。無ければ その案・その◎ を落とす。
_PR_ATTRIBUTE_GROUPS = [
    ["日当たり", "日当り", "陽当たり", "陽当り"],
    ["採光良好", "採光", "明るい"],
    ["通風良好", "通風", "風通し"],
    ["南向き", "南面", "全室南向き"],
    ["東南向き", "南東向き"], ["東向き"], ["西向き"], ["北向き"],
    ["角部屋", "角住戸"], ["角地"],
    ["最上階"], ["新築"], ["築浅"],
    ["オートロック"], ["宅配ボックス", "宅配BOX"],
    ["ペット可", "ペット相談"], ["楽器可", "楽器相談"], ["二人入居可", "2人入居可"],
]

# 交通のうち title/sub/◎ で禁止するのは「バス関連」のみ（band に一本化）。
# 駅への直接徒歩（◯◯駅 徒歩◯分）は禁止しない＝下の数値バリデータで facts と照合する。
# ※「バス・トイレ別」等の設備語を誤検出しないよう、バスは 便/停/◯分 の文脈のみ交通扱い。
_PR_TRANSPORT_BAN = r"バス便|バス停|バス\s*\d+\s*分"


def _pr_has_banned_transport(s: str) -> bool:
    """禁止交通表現（バス便/バス停/バス◯分）を含むか。設備語『バス・トイレ別』は誤検出しない。"""
    return bool(re.search(_PR_TRANSPORT_BAN, s or ""))


# 重複排除（C）：表紙の下部特大＝間取り/面積、band＝駅徒歩に出るため、コピー本文に重複させない
_PR_MADORI_RE = r"[1-9]\d?\s*[SLDKR]{1,4}|ワンルーム"
_PR_AREA_RE = r"\d+(?:\.\d+)?\s*(?:㎡|平米|平方メートル|m2)"


def _pr_has_spec(s: str) -> bool:
    """title/subtitle 用：間取り(2LDK等)・面積(㎡)・徒歩◯分 を含むか（下部特大/bandと重複）。"""
    s = s or ""
    return bool(re.search(_PR_MADORI_RE, s) or re.search(_PR_AREA_RE, s)
                or re.search(r"徒歩\s*\d+\s*分", s))


def _pr_has_any_transport(s: str) -> bool:
    """highlights 用：交通表現(徒歩/バス便/バス停/バス◯分/◯分)を含むか（bandと重複）。
    設備語『バス・トイレ別』は誤検出しない（バス単独は交通扱いしない）。"""
    return bool(re.search(r"徒歩|バス便|バス停|バス\s*\d+\s*分|\d+\s*分", s or ""))


def _pr_walk_mismatch(s: str, access) -> bool:
    """s 内の『徒歩◯分』の◯が、facts.access の直接徒歩(バス便除く)のどれとも一致しなければ True。
    ＝実在しない徒歩分の誇張を落とす（band は facts verbatim なので影響しない）。"""
    mins = [int(x) for x in re.findall(r"徒歩\s*(\d+)\s*分", s or "")]
    if not mins:
        return False
    valid = set()
    for a in (access or []):
        if "バス" in a:                      # バス便内の徒歩は駅への直接徒歩ではない
            continue
        valid.update(int(x) for x in re.findall(r"徒歩\s*(\d+)\s*分", a))
    return any(m not in valid for m in mins)


def _pr_attr_unsupported(s: str, hay_raw: str) -> list:
    """s 内で使われた属性(同義グループ)のうち、hay_raw に同義語すら無いものの代表語を返す。"""
    hay = hay_raw or ""
    bad = []
    for grp in _PR_ATTRIBUTE_GROUPS:
        if any(w in s for w in grp) and not any(w in hay for w in grp):
            bad.append(grp[0])
    return bad


def _pr_shortest_direct_walk(access):
    """access から『駅への直接徒歩』の最短分を返す。バス便内の徒歩は除外。無ければ None。"""
    mins = []
    for a in (access or []):
        if "バス" in a:          # バス便の徒歩は駅からの直接徒歩ではない
            continue
        mins += [int(x) for x in re.findall(r"徒歩\s*(\d+)\s*分", a)]
    return min(mins) if mins else None


def _pr_location_banned(access):
    """弱立地（最短直接徒歩>閾値 or バス便のみ or 交通情報なし）なら立地訴求語リストを返す。"""
    w = _pr_shortest_direct_walk(access)
    weak = (w is None) or (w > _PR_STATION_WALK_THRESHOLD)
    return _PR_LOCATION_WORDS if weak else []


def _pr_norm(s: str) -> str:
    """数値照合用の正規化（カンマ/空白/全角空白を除去）。"""
    import re as _re
    return _re.sub(r"[,\s　]", "", s or "")


def _pr_bad_numbers(out_text: str, haystack_norm: str) -> list:
    """出力中の『数値＋単位』が haystack（facts/全文）に無ければ返す＝facts外の数値。"""
    import re as _re
    bad = []
    for m in _re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(分|㎡|円|帖|畳|年|万円|万|階)", out_text):
        if _pr_norm(m.group(0)) not in haystack_norm:
            bad.append(m.group(0))
    return bad


def _pr_is_clean(s: str, haystack_norm: str, extra_banned=(), *,
                 max_len=None, hay_raw="", ban_transport=False, access=None) -> bool:
    """次を全て満たすと True：誇大語なし／弱立地の立地訴求語なし／facts外の数値なし。
    追加（任意）：max_len 以内／バス交通表現なし（ban_transport）／徒歩分が facts と一致
    （access）／属性語が hay_raw に裏付けあり。"""
    if not isinstance(s, str) or not s.strip():
        return False
    if any(b in s for b in _PR_BANNED):
        return False
    if any(b in s for b in extra_banned):
        return False
    if max_len is not None and len(s.strip()) > max_len:   # 文字数上限（超過は落とす）
        return False
    if ban_transport and _pr_has_banned_transport(s):      # バス交通は band 一本化
        return False
    if access is not None and _pr_walk_mismatch(s, access):  # 徒歩分が facts と不一致
        return False
    if _pr_bad_numbers(s, haystack_norm):                  # facts外の数値
        return False
    if hay_raw and _pr_attr_unsupported(s, hay_raw):       # facts未確認の属性
        return False
    return True


def draft_pr_copy(client, full_text: str, facts: dict, rooms: list,
                  model="gemini-2.5-flash") -> dict:
    """マイソク全文＋事実＋部屋種別 → PRコピー下書き。
    返り値: {titles:[{direction,title,subtitle}x3], highlights:[..], room_subs:{room:2行},
    fallback:bool}。誇大語/facts外数値/超過/未確認属性/バス交通/徒歩不一致 を機械バリデータで除去。
    有効タイトルが0件なら簡易テンプレ（物件名｜間取り）に退避し fallback=True。事実皆無のみ None。"""
    import json as _json
    if not full_text and not facts:
        return None
    facts_json = _json.dumps(facts, ensure_ascii=False)
    rooms_json = _json.dumps(rooms, ensure_ascii=False)
    hay = _pr_norm(full_text + " " + facts_json)       # 数値照合用（正規化）
    hay_raw = full_text + " " + facts_json             # 属性語照合用（生テキスト）
    loc_ban = _pr_location_banned(facts.get("access"))  # 弱立地なら駅近等を禁止
    base_instruction = (
        "あなたは賃貸物件のSNS広告コピーライターです。以下の【確定事実】と【マイソク全文】だけを根拠に、"
        "日本語のPRコピー下書きをJSONで出力してください。\n"
        "厳守事項：\n"
        f"・文字数厳守：title は{_PR_MAX_TITLE}文字以内、subtitle は{_PR_MAX_SUBTITLE}文字以内、"
        f"highlights は各{_PR_MAX_HIGHLIGHT}文字以内。表紙に大きく載るため簡潔に。超過は不可。\n"
        "・【確定事実】以外の数値（徒歩分・面積・賃料・築年・帖数）や設備を創作しない。事実と一致させる。\n"
        "・角部屋/採光良好/通風良好/南向き/日当たり/最上階/新築/築浅/オートロック 等の属性は、"
        "【確定事実】か【マイソク全文】に明記がある場合のみ書く。無ければ書かない。\n"
        "・役割分担（重複排除）：title・subtitle には 間取り(2LDK等)・面積(◯㎡)・徒歩◯分 を書かない"
        "（下部に大きく／別枠で表示するため）。訴求の言葉・魅力に専念する。\n"
        "・highlights(◎) には交通表現（徒歩・バス・◯分）を書かない（別枠で表示）。設備・条件に専念する。\n"
        "・バス便・バス停・バス◯分 は title・subtitle・highlights に書かない（別枠で表示）。\n"
        "・立地が弱い場合（徒歩が長い／バス便のみ）は『駅近』『駅チカ』等を書かない。"
        "その場合はエリア・環境・生活利便に振るか、広さ・間取り等の別方向で訴求する。\n"
        "・最上級/断定（最高・完璧・絶対・日本一・最安・必ず・唯一 等）を使わない（景表法）。断定を避け体験describで。\n"
        "出力JSON（これのみ・説明文なし）：\n"
        '{"titles":[{"direction":"立地|間取り|設備","title":"...","subtitle":"..."}(3案・方向を必ず分ける)],'
        '"highlights":["◎...(設備/条件から3〜5個)"],'
        '"room_subs":{"部屋種別":"情感1行目\\n情感2行目"}}\n'
        f"【部屋種別リスト】{rooms_json}\n【確定事実】{facts_json}\n【マイソク全文】\n"
    )
    stricter = ("\n※前回は文字数超過・間取り/面積/徒歩分の重複・交通表現・事実未確認の属性が混入しました。"
                "上限を守り、title/subに間取り・面積・徒歩分を書かず、◎に交通を書かないでください。\n")

    def _call(instr):
        try:
            resp = client.models.generate_content(
                model=model, contents=[instr + full_text[:4000]])
            text = (getattr(resp, "text", "") or "").strip()
            m = re.search(r"\{.*\}", text, re.S)
            return _json.loads(m.group(0)) if m else None
        except Exception:  # noqa: BLE001
            return None

    titles, highlights, room_subs = [], [], {}
    for attempt in range(2):                            # 有効タイトル0なら1回だけ再生成
        data = _call(base_instruction + (stricter if attempt else ""))
        if not isinstance(data, dict):
            continue
        titles = []
        for t in data.get("titles", []) or []:
            if not isinstance(t, dict):
                continue
            title = str(t.get("title", "")).strip()
            sub = str(t.get("subtitle", "")).strip()
            _access = facts.get("access")
            if not _pr_is_clean(title, hay, loc_ban, max_len=_PR_MAX_TITLE,
                                hay_raw=hay_raw, ban_transport=True, access=_access):
                continue                                # 超過/誇大/facts外/未確認属性/バス交通/徒歩不一致は落とす
            if _pr_has_spec(title):                     # C: 間取り/面積/徒歩分は下部特大・bandと重複→載せない
                continue
            if sub and (not _pr_is_clean(sub, hay, loc_ban, max_len=_PR_MAX_SUBTITLE,
                                         hay_raw=hay_raw, ban_transport=True, access=_access)
                        or _pr_has_spec(sub)):
                sub = ""                                # サブだけNGなら空に
            titles.append({"direction": str(t.get("direction", "")).strip(),
                           "title": title, "subtitle": sub})
        highlights = [h for h in (data.get("highlights", []) or [])
                      if _pr_is_clean(h, hay, loc_ban, max_len=_PR_MAX_HIGHLIGHT,
                                      hay_raw=hay_raw)
                      and not _pr_has_any_transport(h)][:5]   # C: ◎に交通表現を載せない（band重複）
        room_subs = {}
        for k, v in (data.get("room_subs", {}) or {}).items():
            s = "\n".join(str(x) for x in v) if isinstance(v, list) else str(v)
            if _pr_is_clean(s.replace("\n", " "), hay, loc_ban, hay_raw=hay_raw):
                room_subs[str(k)] = s
        if titles:                                      # 有効タイトルが出たら確定
            break
    # 退避：有効タイトルが1件も無ければ P1b-1 の簡易テンプレ（物件名 ｜ 間取り）にフォールバック
    is_fallback = False
    if not titles:
        _name = (facts.get("name") or "").strip()
        _madori = (facts.get("madori") or "").split("[")[0].strip()
        _area = (facts.get("area") or "").strip()
        fb = " ｜ ".join(x for x in (_name, _madori) if x) or _area
        if fb:
            titles = [{"direction": "", "title": fb, "subtitle": ""}]
            is_fallback = True
    if not titles and not highlights and not room_subs:
        return None
    return {"titles": titles[:3], "highlights": highlights, "room_subs": room_subs,
            "fallback": is_fallback}


def plan_maisoku_photo_tour(client, pdf_bytes, min_px: int = 250):
    """マイソクPDF → 実写真ベースのルームツアー計画を作る。

    returns dict:
      real:       [ {bytes, code, label, treatment} ... ]  # 実室内写真（演出対象）
      floor_plan: bytes | None                              # 抽出した間取り図
      anchor:     bytes | None                              # 配色アンカー（居室の実写真優先）
      covered:    set(codes)                                # 実写真でカバー済みの部屋コード
    """
    try:
        photos = extract_pdf_photos(pdf_bytes, min_px=min_px)
    except Exception:  # noqa: BLE001
        photos = []
    cand = [p[0] for p in photos if not is_blank_image(p[0])]
    codes = classify_maisoku_images(client, cand) if cand else []

    real, floor_plan = [], None
    for b, c in zip(cand, codes):
        if c == "FLOORPLAN":
            if floor_plan is None:
                floor_plan = b
            continue
        if c in ("EXTERIOR", "MAP", "BLANK"):
            continue
        if c in _TOUR_ROOM_CODES:
            real.append({
                "bytes": b, "code": c,
                "label": TOUR_ROOM_LABEL.get(c, "室内"),
                "treatment": _TOUR_TREATMENT.get(c, "staging_omakase"),
            })

    # 配色アンカー：リビング → 居室 → キッチン → いずれか の順で選ぶ
    anchor = None
    for pref in ("LIVING", "BEDROOM", "KITCHEN"):
        for it in real:
            if it["code"] == pref:
                anchor = it["bytes"]
                break
        if anchor is not None:
            break
    if anchor is None and real:
        anchor = real[0]["bytes"]

    covered = {it["code"] for it in real}
    return {"real": real, "floor_plan": floor_plan, "anchor": anchor, "covered": covered}


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
