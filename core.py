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
import math
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


def _concept_line(concept_staging: str) -> str:
    """コンセプトのステージング方向づけを1行に（空＝ノーマル＝追加なし＝回帰なし）。"""
    cs = str(concept_staging or "").strip()
    return f"【コンセプト方向づけ】{cs}\n" if cs else ""


def build_staging_prompt(style_desc: str, room_use: str = "",
                         user_request: str = "", concept_staging: str = "") -> str:
    """実際の空室写真 → 家具ステージング（構造は維持）。

    room_use: "リビング" / "寝室" / "" (おまかせ=広さから自動推定)
    concept_staging: コンセプト(モテ部屋等)の方向づけ。空＝ノーマル＝追加なし（回帰なし）。
      ★構造・設備の【厳守】制約は不変。方向づけは家具/小物/色温度/生活感の演出に限る。
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
        f"{_concept_line(concept_staging)}"      # ★コンセプト方向づけ（空=ノーマル=回帰なし）
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


def build_water_staging_prompt(style_desc: str = "", user_request: str = "",
                               concept_staging: str = "") -> str:
    """水回り（キッチン/浴室/洗面/トイレ）・玄関 → 設備は変えず生活小物だけ演出。
    concept_staging: コンセプト方向づけ（空＝ノーマル＝回帰なし・小物の演出方向のみ）。"""
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
        f"{_concept_line(concept_staging)}"      # ★コンセプト方向づけ（空=ノーマル=回帰なし）
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
                           with_ref: bool = False, user_request: str = "",
                           concept_staging: str = "") -> str:
    """マイソク → 同一住戸の指定部屋の内観を生成するプロンプト。
    with_ref=True のときは2枚目の参照画像に「配色・素材だけ」合わせる指示を足す。
    部屋タイプ別の家具ルールを厳守させ、トイレ等への家具転写を防ぐ。
    concept_staging: 特集の方向づけ（空＝特集なし＝追加なし＝回帰なし）。
    ★v79-feature-reach(a)：ここに特集が届いていなかったため、写真の無い部屋（補完生成）だけ
      テイストが素通りしていた（②③を選んでも間取り図から起こす内観は normal のまま）。"""
    furni = ROOM_TOUR_FURNITURE.get(room_label, "")
    furni_line = f"\n- {furni}" if furni else ""
    # ★ROOM_TOUR_FURNITURE は部屋別の家具を固定で指示する（例 洋室＝ベッドを主役に）。
    #   ③趣味部屋の staging_prompt は「デスク＋本棚」なので、両方を素で並べると1枚のプロンプトの中で
    #   家具の指示が正面衝突する。優先順を明示して解く（ROOM_TOUR_FURNITURE の中身は1文字も変えない
    #   ＝normal/mote の回帰を避けるため）。用途に合わない家具の禁止だけは特集に関わらず厳守のまま。
    # ★空のときは改行すら足さない＝特集なし(normal)は変更前とバイト一致（回帰ゼロの担保）。
    _cst_line = ("\n" + _concept_line(concept_staging).rstrip("\n")) \
        if str(concept_staging or "").strip() else ""
    furni_prio = ("\n- 上の部屋別の家具は『既定』であって固定ではない。"
                  "下に【コンセプト方向づけ】がある場合は、家具の種類はそちらを優先する"
                  "（ただし、その部屋の用途に合わない家具＝トイレや浴室のソファ・水回りのベッド等は"
                  "特集に関わらず絶対に置かない）。" if str(concept_staging or "").strip() else "")
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
        f"{furni_line}{furni_prio}\n"
        f"- インテリアは{style_desc}。住戸全体で統一感を持たせる。\n"
        "- 自然光の入る清潔で心地よい雰囲気。"
        f"{ref_line}{_cst_line}"                 # ★特集の方向づけ（空=特集なし=1バイトも足さない）
        f"{_request_line(user_request)}\n"
        "【厳守】建物の外観・外観写真・間取り図の線や文字・平面図・数字は一切出さない。"
        "内観のみ。実際にあり得ない広さ・設備・眺望を足して誇張しない。"
        "その部屋の用途に合わない家具（トイレや浴室のソファ、水回りのベッド等）を絶対に置かない。"
    )


def build_3d_perspective_prompt(style_desc: str = "", user_request: str = "",
                                concept_staging: str = "") -> str:
    """間取り図 → 斜め上から見下ろす3Dドールハウス風の俯瞰パース（試験）。
    concept_staging: 特集の方向づけ（空＝特集なし＝追加なし＝回帰なし・v79-feature-reach(a)）。"""
    # ★空のときは改行すら足さない＝特集なし(normal)は変更前とバイト一致（回帰ゼロの担保）。
    _cst_line = ("\n" + _concept_line(concept_staging).rstrip("\n")) \
        if str(concept_staging or "").strip() else ""
    return (
        "1枚目の画像は賃貸／中古物件の間取り図（マイソク）です。"
        "この間取りを基に、屋根と手前側の壁を取り払って斜め上から見下ろした"
        "『3Dドールハウス風の俯瞰パース』を、フォトリアルに1枚生成してください。\n"
        "- 各部屋に家具・小物を配置し、間取りの部屋配置・広さ・動線が一目で分かるようにする。\n"
        f"- インテリアは{style_desc}。住戸全体で統一感を持たせる。\n"
        "- 自然な陰影と採光で立体感を出す。"
        f"{_cst_line}"                           # ★特集の方向づけ（空=特集なし=1バイトも足さない）
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
    if n == 0:
        return []
    # 例外は握り潰さず上位へ伝播させる（呼び出し側 app._pl_classify_with_retry が
    # 実例外型・メッセージを警告表示し、1回リトライする）。空結果（arr=[]）は全OTHERで返し、
    # 呼び出し側の「全OTHER→リトライ」の保険が拾う。
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
    返り値: [{"type":正規化種別, "label":図の文字, "jo":帖float|None, "position":位置str}]。空は []。
    ※例外は握り潰さず上位へ伝播（呼び出し側 app._pl_stage_input が警告表示し続行する）。"""
    import json as _json
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


# ── 売買マイソク（レインズ図面）対応 ─────────────────────────────
def _first_json_object(text: str):
    """テキストから最初の『平衡した』JSONオブジェクト文字列を取り出す。
    コードフェンス（```json）や、JSONの後ろに散文が続く出力に強い（greedy正規表現の
    『Extra data』誤爆を防ぐ）。見つからなければ None。"""
    s = text or ""
    start = s.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def pdf_full_text(pdf_bytes: bytes) -> str:
    """PDF全ページのテキストを連結して返す（テキストレイヤ無しページは空）。"""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        return text
    except Exception:  # noqa: BLE001
        return ""


def detect_property_type(text: str) -> str:
    """マイソク全文から賃貸/売買/不明を判定（テキストベース・Gemini不要）。
    返り値: "rent" / "sale" / "unknown"。
    ※賃貸マイソクも初期費用の『総額』『万円』を含むため、総額/万円だけで売買と判定しない。
      賃料・敷金・礼金があれば賃貸を優先する（実データ10件で確認）。"""
    t = text or ""
    rent = any(k in t for k in ("賃料", "敷金", "礼金", "月額賃料"))
    if rent:
        return "rent"
    sale = any(k in t for k in ("価格", "販売価格", "中古マンション", "中古一戸建",
                                "土地価格", "売買代金", "万円"))
    return "sale" if sale else "unknown"


_BLDG_SUFFIX = ("マンション", "ハイツ", "コーポ", "ハイマート", "レジデンス", "ハイム",
                "パレス", "プラザ", "タワー", "ヴィラ", "メゾン", "ストーク", "エルベ",
                "苑", "荘", "館")


def _guess_building_name(text: str):
    """図面テキストから建物名らしき短い行を推定（分割UIのラベル用・best-effort）。"""
    import re as _re
    _skip = ("マンション等", "中古マンション", "分譲マンション", "新築マンション",
             "中古一戸建", "マンション名", "物件種目")
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if any(s in ln for s in _skip) or _re.match(r"^[０-９0-9]+[．.]", ln):
            continue                       # 様式ヘッダ・物件種目カテゴリは建物名でない
        if 2 <= len(ln) <= 30 and any(s in ln for s in _BLDG_SUFFIX):
            return ln
    return None


def split_pdf_properties(pdf_bytes: bytes) -> list:
    """複数物件が連結されたレインズ一括DL PDFを物件ごとのページ範囲に推定分割する。
    返り値: [{"start":0基準開始, "end":排他終了, "pages":ページ数, "label":推定ラベル}]。
    単一/分割不能なら全体1件。テキストの乏しい図面は不正確なことがあるため、
    呼び出し側は必ず手動ページ範囲指定のフォールバックを用意すること（固定2ページ等を仮定しない）。"""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n = doc.page_count
        texts = [doc[i].get_text() for i in range(n)]
        doc.close()
    except Exception:  # noqa: BLE001
        return [{"start": 0, "end": 1, "pages": 1, "label": "物件1"}]
    if n <= 1:
        return [{"start": 0, "end": n, "pages": n, "label": "物件1"}]

    def _is_detail(t):   # 物件明細ページ（面積/間取/価格等を含む十分な本文）
        return len(t) > 250 and (("㎡" in t) or ("専有面積" in t) or ("間取" in t)
                                 or ("総額" in t) or ("価格" in t) or ("賃料" in t))
    detail_idx = [i for i in range(n) if _is_detail(texts[i])]
    if len(detail_idx) <= 1:
        return [{"start": 0, "end": n, "pages": n, "label": _guess_building_name(
            "".join(texts)) or "物件1"}]
    # 各明細ページを1物件の末尾とみなし、直前の非明細（図面/写真）ページを取り込む
    props, prev_end = [], 0
    for k, di in enumerate(detail_idx):
        end = di + 1
        props.append({"start": prev_end, "end": end, "pages": end - prev_end,
                      "label": _guess_building_name(texts[di]) or f"物件{k + 1}"})
        prev_end = end
    if prev_end < n:                       # 末尾に残った図面等は最後の物件に足す
        props[-1]["end"] = n
        props[-1]["pages"] = n - props[-1]["start"]
    return props


def subpdf_bytes(pdf_bytes: bytes, start: int, end: int) -> bytes:
    """ページ範囲[start,end)だけの新規PDFのbytesを返す（物件1件分に切り出す）。"""
    import fitz
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst = fitz.open()
    try:
        end = max(start + 1, min(end, src.page_count))
        dst.insert_pdf(src, from_page=start, to_page=end - 1)
        return dst.tobytes()
    finally:
        src.close()
        dst.close()


def render_pdf_pages(pdf_bytes: bytes, dpi: int = 150, max_pages: int = 6) -> list:
    """PDFページを画像(PNG bytes)にレンダリング（Gemini vision入力用）。"""
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = []
    try:
        for i in range(min(doc.page_count, max_pages)):
            out.append(doc[i].get_pixmap(dpi=dpi).tobytes("png"))
    finally:
        doc.close()
    return out


def extract_sale_facts_vision(client, pdf_bytes: bytes,
                              model="gemini-2.5-flash") -> dict:
    """売買マイソク（レインズ図面）のページ画像を Gemini vision に渡し事実を構造化抽出。
    返り値: pl_facts 互換 dict（name/address/access(list)/madori/area/built/price/fee/
    equipment ＋ 売買固有 shuzen/floor/genkyo/note）。取れない項目は入れない（創作しない）。
    ※例外は握り潰さず上位へ伝播（呼び出し側 app が警告＋リトライする）。
    ※レインズ一括DLは図面ページと詳細ページが別ユニットで交互に並ぶことがあるため、
      価格/面積を含む『詳細ページ』だけをvisionに渡す（隣の別物件の図面を拾わないため）。"""
    import json as _json
    import fitz
    _all = render_pdf_pages(pdf_bytes, max_pages=8)
    if not _all:
        return {}
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        _detail = [i for i in range(min(doc.page_count, len(_all)))
                   if any(k in doc[i].get_text()
                          for k in ("価格", "総額", "専有面積", "㎡"))]
    finally:
        doc.close()
    pages = ([_all[i] for i in _detail][:3] if _detail else _all[:6])
    parts = [_image_part(b, "image/png") for b in pages]
    instruction = (
        "以下は売買物件（中古マンション等）のマイソク／レインズ図面のページ画像です。"
        "図面に印字されている情報だけを根拠に、次の項目をJSONで抽出してください。\n"
        "【重要な注意】\n"
        "・address は『物件の所在地』を書く。図面の隅・下部にある元付会社（不動産会社）の"
        "住所・支店住所・電話番号・担当者名は住所に絶対に含めない（別物）。\n"
        "・equipment は『設備』『条件』欄などに明記された設備のみ。リフォーム内容の説明文・"
        "広告のキャッチコピー・備考の文章から設備を推測して足さない。\n"
        "・図面に記載が無い項目は空文字にする。推測・創作をしない。\n"
        "【出力JSON（これのみ・説明文なし・全ての値は文字列）】\n"
        "{"
        '"name":"建物名（マンション名）",'
        '"address":"物件所在地（元付会社住所ではない）",'
        '"access":["◯◯線◯◯駅 徒歩◯分", ...複数可],'
        '"madori":"間取り（例 3LDK）",'
        '"area":"専有面積（例 61.02㎡）",'
        '"built":"築年月（例 1982年6月）",'
        '"price":"価格（例 3290万円）",'
        '"fee":"管理費（円/月）",'
        '"shuzen":"修繕積立金（円/月）",'
        '"floor":"所在階・向き（例 4階/南）",'
        '"genkyo":"現況（例 空家/居住中）",'
        '"equipment":"設備欄記載の設備を区切りで列挙（記載のみ）",'
        '"note":"備考・特記の主要点"'
        "}"
    )
    resp = client.models.generate_content(model=model, contents=parts + [instruction])
    text = (getattr(resp, "text", "") or "").strip()
    obj = _first_json_object(text)                # 平衡括弧で抽出（散文/フェンス混入に強い）
    data = _json.loads(obj) if obj else {}
    if not isinstance(data, dict):
        data = {}
    facts = {}
    for k in ("name", "address", "madori", "area", "built", "price", "fee",
              "shuzen", "floor", "genkyo", "equipment", "note"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            facts[k] = v.strip()
    acc = data.get("access")
    if isinstance(acc, list):
        acc = [str(a).strip() for a in acc if str(a).strip()]
    elif isinstance(acc, str) and acc.strip():
        acc = [acc.strip()]
    else:
        acc = []
    if acc:
        facts["access"] = acc
    facts["full_text"] = pdf_full_text(pdf_bytes)   # PRコピー/数値バリデータ用
    return facts


# ── 賃料ガード（景表法）─────────────────────────────────────────────
# 賃料/管理費/価格は法令項目。数字抽出型の整形(_mag_yen等)は漢数字が来ると『黙って桁を欠く』
# （例『9万8千円』→『¥98』）。エラーにならず正常な広告として描画される＝最も危険な沈黙破損。
# ★正規化(漢数字→数値)はしない：機械が金額を勝手に解釈しない。人に返す（描画を止める／警告する）。
_MONEY_CLEAN_RE = re.compile(r"[¥\s]*[\d,]+[\s円]*")


def money_is_clean(s) -> bool:
    """金額文字列が『¥/空白/数字/カンマ/円』だけで構成されるか（空も可＝金額なし）。
    False＝漢数字・その他文字の混入＝数字抽出で桁が欠ける危険。"""
    s = str(s or "").strip()
    return (not s) or bool(_MONEY_CLEAN_RE.fullmatch(s))


def money_yen(s) -> str:
    """金額を '¥12,345' へ（数字とカンマのみ抽出）。空は ''。
    ★数字以外(漢数字等)が混入していたら ValueError＝呼び出し側で描画を止める（沈黙破損の防止）。
    正規化はしない（賃料は法令項目・機械が漢数字から金額を作らない＝人に返す）。"""
    s = str(s or "").strip()
    if not s:
        return ""
    if not money_is_clean(s):
        raise ValueError(f"金額に数字以外の文字が含まれます: 『{s}』（自動で数値化はしません）")
    n = re.sub(r"[^\d,]", "", s).strip(",")
    return f"¥{n}" if n else ""


# ── 事実外属性ガード（景表法・factguard-v72）─────────────────────────────
# ban語(誇大語)チェッカーとは別レイヤー。『事実に無い属性を創作する』失敗を捕まえる。
# ★語の禁止リストではない：facts に当該属性の裏付けがあれば通す（南向きがfactsにあれば南向きは可）。
# ★正規化(言い換え)はしない：機械が『夜空→窓辺』に書き換えると人が創作に気づけない＝除去＋警告で人に返す。
# 情感2行(draft_pr_copy)とナレ(polish_narration)が同じこの関数を参照＝1源2消費（別定義にしない）。
# claims=検出する属性主張 / back=factsにあれば裏付け成立とみなす語（claims＋関連する事実語）。
_FACT_ATTR_GROUPS = [
    {"cat": "眺望",
     "claims": ["夜空", "星空", "満天の星", "夜景", "見晴らし", "眺望", "パノラマ", "眺め",
                "海が見える", "海が望める", "山が見える", "富士山", "スカイツリー", "開放的な眺め"],
     "back": ["夜景", "眺望", "見晴らし", "パノラマ", "展望", "眺め良好"]},
    # ★v79-back-direction：旧「日当たり・方角」1グループを2つに割った。
    #   旧構成は back に「日当たり／採光／日照／方角／向き」を含んでいたため、
    #     ・マイソクに『日当たり良好』の1語があるだけで『南向き』という方角の断定まで通る
    #     ・back の『向き』が短すぎて『北向き』『バルコニー南東向き』にヒットし、
    #       北向き物件で『南向き』と書けてしまう
    #   の2つが同時に起きていた（どちらも実測で再現・景表法直撃）。
    #   方角は「その方角の記載があるときだけ」、日当たりは「日当たり系の記載があるとき」と分離する。
    #   ★『方角』『向き』は back から削除した。谷合さんの実マイソク16件の全数調査で出現0件＝
    #     正規表現化などの小改修をせずに単純削除で足りる（過剰除去に倒れる心配が実データ上ない）。
    {"cat": "方角の断定",
     # ★方角そのものを名指しする主張。裏付けは「南向き／南面」の明示のみ。
     #   日当たり・採光の記載では通さない（そこがこのグループを分けた理由そのもの）。
     "claims": ["南向き", "南面"],
     "back": ["南向き", "南面"]},
    {"cat": "日当たり・採光",
     # ★feat-merge-2：ひらがな異表記を追加（既存語は1語も削っていない＝回帰なし）。
     #   従来は漢字表記のみ検出で「やわらかい光の、洗面台。」がガードを素通りしていた（実測確認済み）。
     #   ②自分を整える部屋は光を言いたくなる特集なので、AIが表記を崩した瞬間にガードが効かなくなる。
     #   ★v79-scrub-clause で訂正: 単独「あさひ」は撤回し複合語2つへ置換した。
     #     単独だと大阪に頻出する物件名（朝日プラザ／あさひ荘）に巻き添えで、正当な節まで丸ごと落ちる
     #     （実測: 「あさひプラザの、エントランス。」→ 空）。_NEEDS_REVIEW の「『極』単文字は入れない」と同型の失敗。
     # ★南向き／南面 は back に残す＝南向きの明示があれば日当たりの主張は通る（逆は通らない）。
     "claims": ["陽当たり", "陽当り", "日当たり", "日当り", "朝日が差し",
                "西日", "日差しが差し込む", "光が差し込む", "自然光", "柔らかな光", "陽光が",
                "朝は光", "燦々",
                "やわらかい光", "あたたかい光", "ひかりが差す", "あさひが差す", "あさひが入る"],
     "back": ["南向き", "南面", "陽当たり", "陽当り", "日当たり", "日当り", "採光", "日照"]},
    {"cat": "角部屋・位置",
     "claims": ["角部屋", "角住戸", "最上階"],       # ★物件位置＝検証可能な事実（静的既定コピーの穴）
     "back": ["角部屋", "角住戸", "角地", "最上階"]},
    {"cat": "通風",
     "claims": ["風が抜ける", "風通し良好", "風が通る", "通風良好"],
     "back": ["通風", "風通し", "二面採光", "両面バルコニー", "角部屋"]},
    {"cat": "階数の見え方",
     "claims": ["高層", "見下ろす", "上層階", "眼下", "高台から"],
     "back": ["高層", "上層階", "最上階", "タワー"]},
    {"cat": "静けさ・環境",
     "claims": ["静か", "静けさ", "閑静", "緑豊か", "落ち着いた住宅街", "のどかな", "喧騒を離れ"],
     "back": ["閑静", "静音", "防音", "二重サッシ", "静穏"]},   # ★住宅街/公園はfalse-backingになるので除外
    {"cat": "周辺環境",
     "claims": ["商店街が近い", "商店街がすぐ", "公園が目の前", "公園が近い", "便利な立地",
                "好立地", "駅前の賑わい", "買い物に便利"],
     "back": ["商店街", "公園", "スーパー", "コンビニ", "駅前", "住宅街"]},
]


def _fact_hay(facts) -> str:
    """facts の全値（full_text 含む）を裏付け照合用の文字列に連結。"""
    return " ".join(str(v) for v in (facts or {}).values())


def fact_scrub(text, facts=None):
    """事実外の属性主張（眺望/方角/日当たり/階数/静けさ/周辺）を『節（。／改行）単位』で除去。
    facts に裏付けのある属性は残す（語の禁止でなく事実照合）。正規化・言い換えはしない。
    返り値 (clean, removed[])。★情感2行・ナレの両方がこの1関数を参照。"""
    hay = _fact_hay(facts)
    unbacked = []
    for g in _FACT_ATTR_GROUPS:
        if any(b in hay for b in g["back"]):
            continue                                  # factsに裏付けあり＝この属性は主張OK
        unbacked += g["claims"]
    if not unbacked:
        return str(text or ""), []
    removed, out = [], []
    parts = re.split(r"([。！？\n／/])", str(text or ""))   # 節に分割（区切りは保持）
    for i in range(0, len(parts), 2):
        clause = parts[i]
        delim = parts[i + 1] if i + 1 < len(parts) else ""
        hit = [c for c in unbacked if c in clause]
        if hit:
            removed += hit                            # 事実外属性を含む節ごと落とす（破片を残さない）
        else:
            out.append(clause + delim)
    # 先頭の宙ぶらりん記号・末尾の読点は落とすが、残った節の末尾句点『。』（演出）は保持する
    clean = re.sub(r"[、，]{2,}", "、", "".join(out)).strip("　 \n").lstrip("、，。．！？").rstrip("、，　 ")
    return clean, sorted(set(removed))


def fact_is_clean(text, facts=None) -> bool:
    """事実外の属性主張が無いか（fact_scrub と同一ルール＝1源2消費）。"""
    return not fact_scrub(text, facts)[1]


def wrap_subtitle(text, max_chars=20, max_lines=3):
    """★story-v78 B：ナレ＝字幕一本化の折返し。長いナレ文を字幕の『行』へ分割（日本語＝文字数ベース）。
    句読点（。、）の後ろで区切るのを優先し、無ければ max_chars で強制改行。max_lines 超過分は末尾…で丸める
    （暫定の静的字幕用。1行ずつの切替＝timing は D）。空文字は []。改行・前後空白は潰す。"""
    s = re.sub(r"\s+", "", str(text or ""))
    if not s:
        return []
    segs = [x for x in re.findall(r"[^。、]*[。、]?", s) if x]   # 句読点を含めて断片化
    lines, cur = [], ""
    for seg in segs:
        while len(seg) > max_chars:                # 断片自体が長すぎる→強制分割
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(seg[:max_chars])
            seg = seg[max_chars:]
        if len(cur) + len(seg) <= max_chars:
            cur += seg
        else:
            if cur:
                lines.append(cur)
            cur = seg
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:                     # 暫定：超過は末尾…で丸める（Dで解消）
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][:max_chars - 1] + "…") if lines[-1] else "…"
    return lines


def prepare_subtitle(text, facts=None, max_chars=20, max_lines=3):
    """★story-v78 B：字幕を焼き込む直前の整形＝最終ゲート。
    ① fact_scrub（★人が pl_narr を手編集して事実外属性を書いた場合に落とす。生成経路の fact_scrub とは別＝
       『手編集の穴』を塞ぐもので重複ではない。fact_scrub は idempotent なので二重適用は安全）。
    ② wrap_subtitle で行へ折返す。返り値: (lines[str], removed[str])。"""
    clean, removed = fact_scrub(text or "", facts)
    return wrap_subtitle(clean, max_chars, max_lines), removed


def subtitle_events(beats, cover_sec=0.0):
    """★story-v78 B ③『字数比の1行ずつ切替』：各ビートの字幕行を時刻イベントへ展開（with-timestamps不要）。
    beats=[{lines:[str], dur:float}]（ビート順）。dur=ビートの描画秒(=beat_narr_sec)。
    ビート開始 = cover_sec + Σ(前ビートの dur)（＝ナレ音声の配置と同一・A0の単純化）。
    ビート内 = 各行の『字数比』で尺を按分（wrap_subtitleが句読点優先で折返し済＝行の字数と発話時間がほぼ比例）。
    ★②等分割(最大1213ms)より精度が高い(A1のLDKビートで最大118ms・Claude推定＝真の判定はCの実測Δms)。
    ★D(with-timestamps)成功時は不要／失敗時はこれに退避＝ElevenLabsが落ちても実用水準のフォールバック。
    返り値: [(line, start_sec, end_sec)]。lines空のビートは尺だけ進める（字幕なし・stillビート等）。"""
    events = []
    acc = float(cover_sec or 0.0)
    for b in (beats or []):
        dur = float(b.get("dur") or 0.0)
        lines = [str(l) for l in (b.get("lines") or []) if l and str(l).strip()]
        total = sum(len(l) for l in lines)
        if total > 0 and dur > 0:
            t0 = acc
            for i, l in enumerate(lines):
                d = dur * (len(l) / total)
                end = acc + dur if i == len(lines) - 1 else t0 + d   # 最終行はビート終端ぴったりに
                events.append((l, round(t0, 3), round(end, 3)))
                t0 += d
        acc += dur
    return events


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


# ── コンセプト・プリセット（concept-v70c）：上流で1択→全工程の既定が追従 ─────────────
# ★合格条件＝データ駆動：1コンセプト=1行。残り2つ(career_qol/hobby)は表に行を足すだけで動く。
#   コンセプト名は内部語（顧客向け出力に出さない）。下流は concept_of() だけ参照＝単一の情報源。
#   voice_id は「設定」なので表に直書き（鍵ではない・漏れても実害ゼロ）。None → 既定 ELEVENLABS_VOICE_ID。
# ★feat-ban-1：旧 _MOTE_HARD_NG を _COMMON_HARD_NG へ改名し、全特集共通のハードNGに正式昇格。
#   経緯＝_story_ban_words() が concept_ban_extra("mote") を引数ハードコードしていたため、magtext /
#   story_narration は特集に関係なくこの語群を除去する一方、polish_narration / draft_sns_captions は
#   特集依存だった＝同じ語が経路によって消えたり消えなかったりしていた。共通に上げて食い違いを解消する。
#   機械除去＋警告。型承認（宅建/広告の専門家確認）は別ゲート。
#   ★「可愛い」「かわいい」の2語だけ ban から _NEEDS_REVIEW へ降格（谷合さん判断 2026-08-03）。
#     止めずに人が見る＝設備や内装の素直な形容まで機械的に消していたため。
_COMMON_HARD_NG = [
    "モテ部屋", "モテる", "モテ",
    "エロ", "セクシー", "色気", "誘惑", "抱かれ", "夜のお誘い", "お持ち帰り",
    "美人", "美女", "イケメン", "美脚", "美肌", "スタイル抜群",
    "彼女が喜ぶ", "彼が喜ぶ", "女子力", "男らしい", "女らしい", "主婦向け",
]

CONCEPT_PRESETS = {
    "normal": {
        "label": "ノーマル", "status": "ready",
        "style_default": "ナチュラル/北欧",                      # 現行既定＝回帰（sticky初期値も同じ）
        "staging_prompt": "",                                  # 追加なし＝現行既定（回帰なし）
        "telop": {"style": "", "few_shot": []},
        "narration": {"voice_id": None, "tone": ""},
        "ban_words": [], "caption": {"tone": "", "hashtags": []}, "cover": {"tone": ""},
    },
    "mote": {
        "label": "モテ部屋", "status": "ready",
        "style_default": "ホテルライク",                        # ★見た目の源＝コンセプト（北欧ではない）。谷合さん決定 2026-07-15
        # 谷合さん改稿 2026-07-15。器の _concept_line が『【コンセプト方向づけ】』を前置するため
        # 重複ヘッダ行は値から除外（内容・【厳守】4行は温存）。【厳守】1行目＝造作照明の追加を禁じる
        # 景表法ガード（『間接照明』とだけ書くとAIが天井に造作照明を埋め込む＝実在しない設備の追加）。
        "staging_prompt": (
            "夜の気配と艶を出す。均一に明るくせず、明暗のコントラストを残す。\n"
            "- 灯り: 電球色。置き型の照明器具（フロアランプ／テーブルランプ）で光だまりと陰を作る。"
            "天井から均一に照らさない。\n"
            "- 素材: 光沢とマットの対比。レザー／ベルベット／ガラス／磨いた金属を1〜2点だけ差し、"
            "他はマットでまとめる。\n"
            "- 色: 深い色を1点だけ差す（チャコール／ネイビー／ダークグリーン）。全体は低彩度。\n"
            "- 家具: 視線を低く（ローソファ・ローテーブル）。曲線を1つ入れる。\n"
            "- 余白: 置きすぎない。生活の気配は1〜2点まで（畳んだブランケット、グラス2つ、本）。\n"
            "【厳守】\n"
            "- 照明は置き型の器具のみ。造作の間接照明・ダウンライトの増設・配線の変更はしない。\n"
            "- 人物・人体・シルエットを描かない。\n"
            "- 衣類や寝具の乱れなど直接的な示唆はしない。\n"
            "- 既存の構造・設備・窓・眺望を変えない、足さない。"),
        "telop": {
            "style": ("基準＝『帰りたくない。角部屋。』。これが文体の中心であり上限（これより踏み込まない）。"
                      "時間の匂わせ（終電/夜/朝）・二人称の気配（呼ぶ/見せる/帰す）・生活の生々しさ（眠る/料理/光）。"
                      "言い切り・体言止め・句点で切る。短文。"),
            "few_shot": ["終電を気にしない部屋。", "呼びたくなる、キッチン。", "朝が、悪くない。", "帰りたくない。角部屋。"],
        },
        "narration": {"voice_id": None,     # None → 既定 ELEVENLABS_VOICE_ID(=HIRO)。v70cでvoice作業ゼロ
                      "tone": "低い声・落ち着き・余白。時間の匂わせと言い切り。煽らない。基準『帰りたくない。角部屋。』"},
        "ban_words": _COMMON_HARD_NG,   # ★feat-ban-1で改名（この表自体は feat-dead-1 で削除）
        "caption": {"tone": "余白のある短文・言い切り。生活の気配。誇大にしない。",
                    "hashtags": ["#ひとり暮らし", "#夜が好き", "#帰りたくなる部屋"]},   # ブランド共通に少量追加
        "cover": {"tone": "基準『帰りたくない。角部屋。』の文体。短句・体言止め・句点。",
                  "default": "帰りたくない。角部屋。",     # ★モテの既定コピー（normalは空＝人が書く）
                  "style": "magazine"},                   # ★表紙スタイル既定（mote=雑誌型/normal=simple）
        # 情感2行の静的既定（PRコピー下書きを押さない時に出る）。★家族でなく ふたり／単身 の世界観。
        # facts無関係の属性主張はしない（採光/眺望/角部屋等は fact_scrub が別途照合）。谷合さん調整可・往復前提。
        "sub_template": {
            "LDK": ["終電を、気にしない。", "呼びたくなる、リビング。"],
            "キッチン": ["ふたりで、火を囲む。", "呼びたくなる、キッチン。"],
            "寝室": ["ひとりの夜が、ほどける。", "朝が、悪くない。"],
            "洋室": ["こもりたくなる、部屋。", "夜が、長くなる。"],
            "玄関": ["ただいまが、様になる。"],
            "浴室": ["一日を、流して眠る。"],
            "洗面": ["朝の顔が、決まる。"],
            "バルコニー": ["夜風に、あたる。"],
            "外観": ["帰りたくなる、佇まい。"],
        },
    },
    # ── 枠のみ（v70cは選ぶと normal挙動＋『準備中』表示）。将来 v70d で中身を書くだけ ──
    "career_qol": {
        "label": "キャリア／QOL", "status": "wip",
        "staging_prompt": "", "telop": {"style": "", "few_shot": []},
        "narration": {"voice_id": None, "tone": ""},          # 将来: 女性ボイスIDを この行に書く
        "ban_words": [], "caption": {"tone": "", "hashtags": []}, "cover": {"tone": ""},
    },
    "hobby": {
        "label": "趣味部屋", "status": "wip",
        "staging_prompt": "", "telop": {"style": "", "few_shot": []},
        "narration": {"voice_id": None, "tone": ""},
        "ban_words": [], "caption": {"tone": "", "hashtags": []}, "cover": {"tone": ""},
    },
}
CONCEPT_ORDER = ["normal", "mote", "career_qol", "hobby"]


def concept_of(cid):
    """コンセプト設定を取得。未知は normal。★下流はこの1関数だけ参照＝単一の情報源（分岐を散らさない）。"""
    return CONCEPT_PRESETS.get(cid) or CONCEPT_PRESETS["normal"]


def concept_is_wip(cid):
    """枠のみ（準備中）か。True なら normal 挙動へ倒す＋UIで『準備中』表示。"""
    return CONCEPT_PRESETS.get(cid, {}).get("status") == "wip"


def concept_eff(cid):
    """実効コンセプトid。wip は normal に倒す（＝落とさず現行挙動）。"""
    return "normal" if concept_is_wip(cid) else (cid if cid in CONCEPT_PRESETS else "normal")


def concept_voice_id(cid, default_voice=None):
    """コンセプトの voice_id（表に直書き＝設定）。None → 既定にフォールバック。★鍵はSecrets、設定は表。"""
    return concept_of(concept_eff(cid)).get("narration", {}).get("voice_id") or default_voice


def concept_style_default(cid, default=None):
    """コンセプトが決める『スタイル既定』（INTERIOR_STYLESのキー）。★見た目の単一の情報源＝コンセプト。
    wipは concept_eff で normal に倒れる。未定義なら default。UI側で sticky 追従させる（人が変えたら停止）。"""
    return concept_of(concept_eff(cid)).get("style_default") or default


def concept_ban(cid):
    """そのコンセプトで機械除去する語＝共通ban ＋ コンセプト固有ハードNG。"""
    return list(_PR_BANNED) + list(_SNS_BAN_EXTRA) + list(concept_of(concept_eff(cid)).get("ban_words", []))


def concept_cover_default(cid):
    """表紙コピーの静的既定（コンセプト別）。mote='帰りたくない。角部屋。' / normal='' ＝人が書く/AIで一言。
    ★normalに同格コピーを発明しない（推測を避ける）。covercopy-v73が守る『既定に事実主張』穴もnormalでは消える。"""
    return str(concept_of(concept_eff(cid)).get("cover", {}).get("default", "")).strip()


def concept_cover_style(cid):
    """表紙スタイル既定（コンセプト別）。mote='magazine'（雑誌型=OSAKA ROOMS）/ normal='simple'。
    wipは concept_eff で normal に倒れる。UI側で sticky 追従（人が変えたら停止・pl_style と同型）。"""
    return str(concept_of(concept_eff(cid)).get("cover", {}).get("style", "simple")).strip() or "simple"


def concept_sub_template(cid, room):
    """情感2行の静的既定（コンセプト別・部屋種別）。無ければ None＝呼出側で汎用_PL_SUB_TEMPLATEへ。
    ★静的既定もコンセプトに追従（家族⇔ふたり/単身）。wipは concept_eff で normal に倒れ None＝汎用。"""
    st = concept_of(concept_eff(cid)).get("sub_template") or {}
    lines = st.get(room)
    return "\n".join(lines) if lines else None


def concept_telop(cid):
    """テロップの文体指示＋few-shot例。返り値 (style_str, few_shot_list)。normal/wipは("",[])＝回帰。"""
    t = concept_of(concept_eff(cid)).get("telop") or {}
    return str(t.get("style", "")).strip(), [str(x).strip() for x in (t.get("few_shot") or []) if str(x).strip()]


def concept_tone(cid, key):
    """コンセプトのトーン文字列。key∈{narration, caption, cover}。normal/wipは""＝回帰。"""
    return str((concept_of(concept_eff(cid)).get(key) or {}).get("tone", "")).strip()


def concept_hashtags(cid):
    """コンセプト別ハッシュタグ（ブランド共通に少量追加するだけ）。normal/wipは[]。"""
    return [str(x).strip() for x in ((concept_of(concept_eff(cid)).get("caption") or {}).get("hashtags") or [])
            if str(x).strip()]


def concept_ban_extra(cid):
    """コンセプト固有のハードNG語のみ（共通banは concept_ban 側）。生成物のpost-filter用。"""
    return [w for w in (concept_of(concept_eff(cid)).get("ban_words") or []) if w]


def concept_scrub(cid, text, facts=None):
    """顧客向け出力からコンセプトban語・『モテ』・物件名を機械除去。返り値 (clean, removed[])。
    ★『モテ』の語・物件名は顧客向けに絶対出さない（型承認は別ゲート）。"""
    s = str(text or "")
    removed = []
    for w in concept_ban(cid):
        if w and w in s:
            s = s.replace(w, "")
            removed.append(w)
    name = ((facts or {}).get("name") or "").strip()
    if name and name in s:
        s = s.replace(name, "")
        removed.append(name)
    return re.sub(r"\s+", " ", s).strip(), sorted(set(removed))

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


# 部屋種別の別名（Geminiが自然に言い換える範囲を正規名へ吸収）。★key='正規名'、値=言い換え候補。
#   これが無いと room_subs のキー LDK→リビング 等で seeding の完全一致が外れ、AIコピーが黙って捨てられる。
_ROOM_ALIASES = {
    "LDK": ["リビング", "リビングダイニング", "リビングダイニングキッチン", "居間", "LD", "DK", "ダイニング"],
    "洋室": ["洋間", "寝室", "ベッドルーム", "主寝室", "居室", "個室", "書斎"],
    "寝室": ["洋室", "洋間", "ベッドルーム", "主寝室", "居室"],
    "キッチン": ["台所", "調理場"],
    "玄関": ["エントランス", "土間"],
    "浴室": ["バスルーム", "風呂", "お風呂", "バス", "浴室・洗面"],
    "洗面": ["洗面所", "洗面室", "ランドリー", "脱衣所"],
    "トイレ": ["化粧室", "お手洗い", "手洗い"],
    "バルコニー": ["ベランダ", "テラス"],
    "クローゼット": ["収納", "ウォークインクローゼット", "WIC", "納戸"],
    "外観": ["エクステリア", "建物", "外観・エントランス"],
}


def _normalize_room_key(k, rooms):
    """Geminiの room_subs キー k を、採用部屋リスト rooms 内の正規名へ寄せる。
    完全一致→別名表→部分一致 の順。どれにも当たらなければ None（＝キー不一致＝バグ）。"""
    k = str(k or "").strip()
    if not k:
        return None
    if k in rooms:
        return k
    kl = k.lower()
    for r in rooms:                                   # 別名表：r の言い換え候補に k が含まれるか
        cands = [r] + _ROOM_ALIASES.get(r, [])
        if kl in [c.lower() for c in cands]:
            return r
    for r in rooms:                                   # 部分一致の保険（洋室2→洋室 等）
        if r and (r in k or k in r):
            return r
    return None


_ROOM_TOUR_ORDER = [
    "外観", "玄関", "LDK", "リビング", "ダイニング", "キッチン",
    "洗面", "浴室", "トイレ", "バルコニー", "クローゼット", "収納",
    "洋室", "和室", "寝室",
]  # ★roomsort-v78：標準ツアー順（1箇所）。洋室/和室/寝室=末尾＝物語の落とし所(A2/B3とも洋室で着地)。


def room_tour_rank(room):
    """部屋名を標準ツアー順のランク(int)へ。未知は末尾(len)。同ランクは呼び出し側の安定ソートで元順維持。
    ★接尾辞付き（例『LDK（下中央・10帖）』）は _normalize_room_key の部分一致で寄せる＝新しい正規化を作らない
    （keynorm-v76の教訓：部屋名の正規化は1箇所）。"""
    canon = _normalize_room_key(room, _ROOM_TOUR_ORDER)
    if canon is None:
        return len(_ROOM_TOUR_ORDER)
    return _ROOM_TOUR_ORDER.index(canon)


def _concept_pr_block(concept: str) -> str:
    """draft_pr_copy 用コンセプト方向づけ（表紙タイトル＝cover.tone / 情感2行＝telop.style＋few_shot）。
    normal/wip は空＝回帰。★トーンだけ寄せる（数値・事実・属性は創作させない＝景表法ガードは不変）。"""
    cover = concept_tone(concept, "cover")
    telop_style, few = concept_telop(concept)
    if not (cover or telop_style or few):
        return ""
    parts = ["\n【コンセプト方向づけ】（トーンだけ寄せる。数値・設備・属性は創作しない＝上の厳守事項が優先）："]
    if cover:
        parts.append(f"・title/subtitle のトーン：{cover}")
    if telop_style:
        parts.append(f"・room_subs（情感2行）の文体：{telop_style}")
    if few:
        parts.append("・情感2行の参考例（文体と長さの手本・そのまま流用しない）：" + " / ".join(few))
    return "\n".join(parts) + "\n"


def draft_pr_copy(client, full_text: str, facts: dict, rooms: list,
                  model="gemini-2.5-flash", concept: str = "normal") -> dict:
    """マイソク全文＋事実＋部屋種別 → PRコピー下書き。
    返り値: {titles:[{direction,title,subtitle}x3], highlights:[..], room_subs:{room:2行},
    fallback:bool}。誇大語/facts外数値/超過/未確認属性/バス交通/徒歩不一致 を機械バリデータで除去。
    有効タイトルが0件なら簡易テンプレ（物件名｜間取り）に退避し fallback=True。事実皆無のみ None。
    concept: コンセプト方向づけ（title/情感2行のトーン）。normal/wip＝空＝回帰。コンセプト固有ban語も除去。"""
    import json as _json
    if not full_text and not facts:
        return None
    _cban = concept_ban_extra(concept)      # コンセプト固有ハードNG（性的/容姿/性別役割/モテ 等）
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
        "・【確定事実】以外の数値（徒歩分・面積・賃料/価格・築年・帖数）や設備を創作しない。事実と一致させる。\n"
        "・角部屋/採光良好/通風良好/南向き/日当たり/最上階/新築/築浅/オートロック 等の属性は、"
        "【確定事実】か【マイソク全文】に明記がある場合のみ書く。無ければ書かない。\n"
        "・役割分担（重複排除）：title・subtitle には 間取り(2LDK等)・面積(◯㎡)・徒歩◯分 を書かない"
        "（下部に大きく／別枠で表示するため）。訴求の言葉・魅力に専念する。\n"
        "・highlights(◎) には交通表現（徒歩・バス・◯分）を書かない（別枠で表示）。設備・条件に専念する。\n"
        "・バス便・バス停・バス◯分 は title・subtitle・highlights に書かない（別枠で表示）。\n"
        "・立地が弱い場合（徒歩が長い／バス便のみ）は『駅近』『駅チカ』等を書かない。"
        "その場合はエリア・環境・生活利便に振るか、広さ・間取り等の別方向で訴求する。\n"
        "・最上級/断定（最高・完璧・絶対・日本一・最安・必ず・唯一 等）を使わない（景表法）。断定を避け体験describで。\n"
        "・情感（room_subs）は【部屋の中で完結する事実】（帖数・設備・間取り・角部屋・室内洗濯機置場 等）"
        "から立てる。眺望・方角・日当たり・階数の見え方・静けさ・周辺環境には一切触れない"
        "（マイソクに明示がある場合を除く）。例『夜空』『見晴らし』『南向き』『閑静』は明示が無ければ書かない。\n"
        f"{_concept_pr_block(concept)}"          # ★コンセプト方向づけ（空=ノーマル=回帰）
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

    titles, highlights, room_subs, fact_warn, key_warn = [], [], {}, [], []
    _fact_facts = {**facts, "full_text": full_text}     # 裏付け照合はfacts＋マイソク全文
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
            if _cban and any(w in title for w in _cban):
                continue                                # コンセプト固有ban（性的/容姿/モテ 等）は落とす
            if not _pr_is_clean(title, hay, loc_ban, max_len=_PR_MAX_TITLE,
                                hay_raw=hay_raw, ban_transport=True, access=_access):
                continue                                # 超過/誇大/facts外/未確認属性/バス交通/徒歩不一致は落とす
            if _pr_has_spec(title):                     # C: 間取り/面積/徒歩分は下部特大・bandと重複→載せない
                continue
            if sub and (not _pr_is_clean(sub, hay, loc_ban, max_len=_PR_MAX_SUBTITLE,
                                         hay_raw=hay_raw, ban_transport=True, access=_access)
                        or _pr_has_spec(sub)
                        or (_cban and any(w in sub for w in _cban))):
                sub = ""                                # サブだけNG（コンセプトban含む）なら空に
            titles.append({"direction": str(t.get("direction", "")).strip(),
                           "title": title, "subtitle": sub})
        highlights = [h for h in (data.get("highlights", []) or [])
                      if _pr_is_clean(h, hay, loc_ban, max_len=_PR_MAX_HIGHLIGHT,
                                      hay_raw=hay_raw)
                      and not _pr_has_any_transport(h)
                      and not (_cban and any(w in h for w in _cban))][:5]  # ◎に交通/コンセプトban不可
        room_subs, fact_warn, key_warn = {}, [], []
        for k, v in (data.get("room_subs", {}) or {}).items():
            s = "\n".join(str(x) for x in v) if isinstance(v, list) else str(v)
            rk = _normalize_room_key(k, rooms)          # ★キーを正規部屋名へ（LDK↔リビング等を吸収）
            if rk is None:                              # 対応が取れない＝キー不一致＝バグ（黙って捨てない）
                if s.strip():
                    key_warn.append(f"AIが『{k}』の情感を生成しましたが、部屋の対応が取れず既定に戻しました")
                continue
            if (_pr_is_clean(s.replace("\n", " "), hay, loc_ban, hay_raw=hay_raw)
                    and not (_cban and any(w in s for w in _cban))):
                s, _frm = fact_scrub(s, _fact_facts)    # ★事実外の属性(夜空/眺望/静け等)を節単位で除去
                if _frm:
                    fact_warn.append(f"{rk}の情感から事実外の属性『{'・'.join(_frm)}』を除去しました")
                if s.strip():                           # 全節が事実外で空になったらテンプレに差し戻し
                    room_subs[rk] = s
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
            "fallback": is_fallback, "warnings": sorted(set(fact_warn)),
            "key_warnings": sorted(set(key_warn))}   # ★キー不一致（バグ）＝guard落ち（正常）と別枠


# ── SNS投稿文（Instagram / TikTok）生成 ─────────────────────────────
# 固定フッター/返信は Gemini に生成させずコードで焼く（法令の型を機械保証）。
_SNS_FOOTER = "※家具・小物はAI生成のイメージです／賃料等は掲載時点の情報です／取引態様: 仲介"
_SNS_REPLY = "コメントありがとうございます！DMに詳細をお送りしました📩"
_SNS_DM_TEMPLATE = (
    "はじめまして、お問い合わせありがとうございます！\n"
    "こちらのお部屋の詳細（空室状況・内見のご予約）はLINEでご案内しています👇\n"
    "{LINE_URL}\n"
    "お気軽にどうぞ😊")
# 追加ban（公取協2025 SNS調査の対象語）。既存 _PR_BANNED（最高・破格・激安 等）に足す
# ★v79拡張：NG表現リスト(不動産公正競争規約 誇大/最上級)から、部分一致で正当語に巻き添えしない語のみ追加
#   （v79-0で実測: 20語は巻き添えゼロ／「完全」は完全分離等に巻き添え→_NEEDS_REVIEW／「極」は単文字で積極的等を
#   巻き込むため語自体を入れない）。既存語は1語も削らない。
_SNS_BAN_EXTRA = ["格安", "希少", "超お得", "家賃保証", "掘り出し物", "破格", "激安", "最高", "駅チカ",
                  "完ぺき", "万全", "日本初", "当社だけ", "他に類を見ない", "抜群", "厳選", "最高級",
                  "特級", "買得", "掘出", "土地値", "投売り", "特安", "バーゲン", "安値", "完売",
                  "最強", "圧倒的", "どこよりも"]

# ★v79 needs_review：ブロックはしないが人力確認が要る表現（SNS口語・希少性演出・正当語への巻き添え語）。
#   ban（止める）とは別レイヤー＝止めずにフラグを返すだけ。景表法の最終判断は人（型承認は別ゲート）。
_NEEDS_REVIEW = [
    "かわいい", "可愛い",  # ★feat-ban-1：ban から降格（谷合さん判断）。止めずに人が見る
    "完全",              # 完全分離/完全個室/完全防音（実在機能）に巻き添え→誇大か事実かを人が見る
    "極上", "極み",       # 「極」単文字はbanしない（積極的/究極/北極の巻き添え）＝複合語だけ人力確認
    "正直ナメてた", "ヤバい", "神", "早い者勝ち", "今だけ", "新築みたい", "ホテルのような",
    "即決続出", "問い合わせ殺到", "争奪戦",
]


def needs_review(text):
    """★v79 factguard：ブロックせず『人力確認が要る表現』を検出して返す（SNS口語・希少性演出・
    完全分離等の巻き添え語）。返り値: ヒットした語のリスト（空=確認不要）。★banと別レイヤー＝止めない。"""
    s = str(text or "")
    return [w for w in _NEEDS_REVIEW if w in s]


# ★covercopy-v1：間取り表記トークン（1LDK/2DK/3SLDK/1R/ワンルーム）。数値主張の検出でここだけ除外する。
_RE_MADORI_TOKEN = re.compile(r"[0-9０-９]+\s*[SLDKRＳＬＤＫＲ]{1,4}|ワンルーム")


def _scrub_cover_copy(text, facts=None, limit=14, feature="normal"):
    """★covercopy-v1：表紙コピーの機械ガード（1源）。ban語・最上級・物件名・字数超過・装飾記号を落とす。
    返り値 (clean, warnings)。★空文字を返すことがある（＝全節が落ちた）。既定コピーへ倒すかは**呼出側の判断**で、
    ここでは勝手に埋めない（どこで既定に落ちたかを呼出側が warning に出せるようにするため）。
    ★末尾の句点『。』は残す（『帰りたくなる、1LDK。』のような表紙の語り口＝意図的な演出）。
    ★事実外属性の節除去（fact_scrub）は別レイヤー。呼出側で先に通すこと。"""
    warnings = []
    s = re.sub(r"\s+", "", str(text or "")).strip("　「」『』\"'、・")
    # ★v79-scrub-clause：ここも節単位（14字の短句なので実質「案ごと落ちる」＝magtext側の不採用と整合）。
    s, _bnc = ban_scrub(s, list(_PR_BANNED) + _SNS_BAN_EXTRA + ["モテ", "モテ部屋"] + feature_ng(feature))
    for w in sorted(set(_bnc)):
        warnings.append(f"ban語『{w}』を含む節を除去")
    name = ((facts or {}).get("name") or "").strip()
    if name and name in s:
        s = s.replace(name, "")
        warnings.append("物件名を除去")
    # ★数字：**削除しない**。間取り表記（1LDK等）は特集既定コピー『帰りたくなる、1LDK。』にも含まれる正規の表記で、
    #   機械削除すると『帰りたくなる、LDK。』のように文が壊れる（沈黙破損）。間取り以外の数値主張だけを検出して
    #   人力確認へ回す（採用可否は3案ゲートで人が決める＝景表法の判断を機械に肩代わりさせない）。
    if re.search(r"[0-9０-９]", _RE_MADORI_TOKEN.sub("", s)):
        warnings.append("間取り表記以外の数字を含む（数値主張＝要確認）")
    s = s.strip("　「」『』\"'、・")
    if len(s) > limit:
        s = s[:limit]
        warnings.append(f"{limit}字超のため打ち切り")
    return s, warnings


# ★brand-v1 ブランドマスタ：マストヘッドに焼く誌名を data 駆動にする（issue-v1 と同型）。
#   従来は _v79_masthead 内に "O S A K A   R O O M S" が直書きで、表紙・全ビート・DATA面の3面に
#   固定で焼かれていた（設定・環境変数・UIのどこからも変えられない）。事業B「こだわリノベ」を
#   同じスタジオで作るために引数化する。
#   ★masthead（焼く文字）は字間込みでそのまま持つ。字間を規則で入れないこと＝
#     "OSAKA ROOMS" は英字11字で半角字間、"こだわリノベ" は和字6字。同じ規則を当てると崩れる
#     （実測: 66px で 686px / 396px。全角字間を入れて 726px＝OSAKA ROOMS と同等の視覚重量になる）。
#   ★name（プレーン名）は magtext のプロンプトと area_line フォールバックが使う。焼く文字とは別物
#     （FEATURES の label / feature_display_name と同じ分離）。
BRANDS = {
    "osaka_rooms": {
        "label": "OSAKA ROOMS（賃貸）",
        "name": "OSAKA ROOMS",
        "masthead": "O S A K A   R O O M S",   # ★現行と1バイトも同じ（既定＝賃貸経路の回帰ゼロ）
        "mode": "賃貸ステージング",
    },
    "kodawarinobe": {
        "label": "こだわリノベ（事業B・売買）",
        "name": "こだわリノベ",
        "masthead": "こ　だ　わ　リ　ノ　ベ",   # 全角字間（英字と同じ半角字間だと細く見える）
        "mode": "リノベ提案（事業B）",
    },
}
BRAND_ORDER = ["osaka_rooms", "kodawarinobe"]
DEFAULT_BRAND = "osaka_rooms"


def brand_of(bid):
    """ブランド定義。未知は既定（OSAKA ROOMS）へ倒す＝誌名を騙らない側でなく現行維持側に倒す。"""
    return BRANDS.get(bid) or BRANDS[DEFAULT_BRAND]


def brand_masthead(bid):
    """★マストヘッドに実際に焼く文字（字間込み）。表紙・全ビート・DATA面の3面が同じ文字列を使う。"""
    return str(brand_of(bid).get("masthead", "")) or BRANDS[DEFAULT_BRAND]["masthead"]


def brand_name(bid):
    """プレーンな誌名（字間なし）。magtext のプロンプトと area_line のフォールバックが使う。"""
    return str(brand_of(bid).get("name", "")) or BRANDS[DEFAULT_BRAND]["name"]


def brand_display_name(bid):
    """UI表示名（人が選ぶときに読む名前）。"""
    return str(brand_of(bid).get("label", "")) or brand_name(bid)


def brand_for_mode(mode, default=DEFAULT_BRAND):
    """用途モード（PL_MODES）→ 既定ブランド。★あくまで既定で、UIで手動上書きできること
    （自動だけにすると誤った側で焼いたときに気づけない）。"""
    m = str(mode or "").strip()
    for bid, b in BRANDS.items():
        if b.get("mode") and b["mode"] == m:
            return bid
    return default


# ★v79 特集マスタ（features）＝テイストの唯一の情報源（feat-merge-1）。
#   CONCEPT_PRESETS から『実際に消費されているキーだけ』を移植した。移植した7キー＝
#     style_default / staging_prompt / narration.tone / narration.voice_id / ban_words /
#     caption.tone / caption.hashtags
#   移植しなかったキーと理由（工程0の実測で消費先が死んでいると確定・工程5で CONCEPT_PRESETS ごと削除）：
#     cover.default  … concept_cover_default の呼出元ゼロ（covercopy-v1 で役目終了）
#     cover.style    … _pl_follow_concept_cover_style 自体が呼出元ゼロ
#     cover.tone     … 消費先は draft_pr_copy の title/subtitle＝冒頭フラッシュのみ。表紙挿入ON（既定）で不発
#     telop / sub_template … 消費先は v78 テロップ層（情感2行）。v79 では comment が同じ役割を担うため一本化して落とす
#   ★accent は PIL 実値(RGB)。★label が空文字＝表紙に「特集　○○」枠を描かない合図（normal 用）。
#     表示名が要る場所は feature_display_name() を使うこと（label を表示名で埋めると枠が復活する）。
FEATURES = {
    # ── 特集なし（標準）。統合後の既定＝旧 pl_concept="normal" と完全一致させる回帰防止用 ──
    "normal": {
        "label": "",                       # ★空＝表紙の「特集　○○」枠を描かない（_v79_feature_label 側で分岐）
        "accent": (232, 196, 104),         # 既存GOLD流用（枠は描かないので実質マストヘッド線の色のみ）
        "style_default": "ナチュラル/北欧",  # ★旧 CONCEPT_PRESETS["normal"] と同値＝回帰なし
        "staging_prompt": "",              # ★空＝現行の「追加なし」挙動と完全一致
        "comment_tone": "",
        "cover_hooks": [],                 # ★空。normalに同格コピーを発明しない（旧 concept_cover_default の設計意図を継承）
        "narration": {"voice_id": None, "tone": ""},
        "ban_words": [],
        "caption": {"tone": "", "hashtags": []},
    },
    "mote_heya": {
        "label": "モテ部屋",
        "accent": (232, 196, 104),   # GOLD
        "style_default": "ホテルライク",   # ★見た目の源＝特集（北欧ではない）。谷合さん決定 2026-07-15
        # ★旧 CONCEPT_PRESETS["mote"]["staging_prompt"] をバイト単位でそのまま移設（回帰ゼロの対象）。
        #   旧 FEATURES["mote_heya"]["staging_prompt"] の1行短文は参照ゼロの死にデータだったため破棄。
        #   【厳守】1行目＝造作照明の追加を禁じる景表法ガード（『間接照明』とだけ書くとAIが天井に造作照明を埋める）。
        "staging_prompt": (
            "夜の気配と艶を出す。均一に明るくせず、明暗のコントラストを残す。\n"
            "- 灯り: 電球色。置き型の照明器具（フロアランプ／テーブルランプ）で光だまりと陰を作る。"
            "天井から均一に照らさない。\n"
            "- 素材: 光沢とマットの対比。レザー／ベルベット／ガラス／磨いた金属を1〜2点だけ差し、"
            "他はマットでまとめる。\n"
            "- 色: 深い色を1点だけ差す（チャコール／ネイビー／ダークグリーン）。全体は低彩度。\n"
            "- 家具: 視線を低く（ローソファ・ローテーブル）。曲線を1つ入れる。\n"
            "- 余白: 置きすぎない。生活の気配は1〜2点まで（畳んだブランケット、グラス2つ、本）。\n"
            "【厳守】\n"
            "- 照明は置き型の器具のみ。造作の間接照明・ダウンライトの増設・配線の変更はしない。\n"
            "- 人物・人体・シルエットを描かない。\n"
            "- 衣類や寝具の乱れなど直接的な示唆はしない。\n"
            "- 既存の構造・設備・窓・眺望を変えない、足さない。"),
        "cover_hooks": ["この部屋、自慢したくなる。", "帰りたくなる、1LDK。", "夜が、楽しみになる部屋。"],
        "comment_tone": "ナイトルーティン視点・現在形・照れは話法に（few-shot 2本参照）",
        "narration": {"voice_id": None,     # None → 既定 ELEVENLABS_VOICE_ID(=HIRO)
                      "tone": "低い声・落ち着き・余白。時間の匂わせと言い切り。煽らない。基準『帰りたくない。角部屋。』"},
        # ★feat-ban-1：旧 _MOTE_HARD_NG は _COMMON_HARD_NG として全特集へ昇格したので、
        #   ここは「モテ部屋にだけ効く追加語」の枠になる（現在は無し）。
        "ban_words": [],
        "caption": {"tone": "余白のある短文・言い切り。生活の気配。誇大にしない。",
                    "hashtags": ["#ひとり暮らし", "#夜が好き", "#帰りたくなる部屋"]},
    },
    "totonoeru": {
        "label": "自分を整える部屋",
        "accent": (228, 170, 168),   # ROSE
        "style_default": "ナチュラル/北欧",
        "staging_prompt": (
            "朝の光と清潔感を出す。影を残しすぎず、白とやわらかい影で明るくまとめる。\n"
            "- 灯り: 自然光を主体にする。窓からの光が床や壁に届いている状態。人工照明は点けないか、点けても弱く。\n"
            "- 素材: 綿・リネン・木・陶器など、マットで手ざわりのある素材。光沢は最小限。\n"
            "- 色: 生成り・白・淡いグレーを基調に、くすんだピンクかベージュを1点だけ差す。全体は低彩度。\n"
            "- 家具: 角のとれた形。数を絞り、向きと高さを揃えて整列させる（乱雑にしない）。\n"
            "- 小物: 手入れの気配を1〜2点まで（畳んだタオル、一輪挿し、アロマ、朝のマグ）。\n"
            "- 水回り: 洗面・浴室のカットは物を減らし、鏡と面を清潔に。ボトル類は色を揃えて2〜3本まで。\n"
            "【厳守】\n"
            "- 照明は置き型の器具のみ。造作の間接照明・ダウンライトの増設・配線の変更はしない。\n"
            "- 人物・人体・シルエットを描かない。\n"
            # ★v79-feature-reach 後の訂正: 旧文は「写真に無いのに描き加えない」だった。
            #   補完生成（写真の無い部屋を間取り図から起こす経路）では『写真そのものが存在しない』ため
            #   「独立洗面台を描くな」と読まれうる。②は独立洗面台が訴求の芯で、洗面は写真が付いていない
            #   ことが多い＝②の主力カットがまさにこの経路に乗る。③の「物件事実に無いのに」と同じ
            #   経路非依存の書き方へ揃える（staging 経路の意味は変わらない）。
            "- 設備を足さない。独立洗面台・浴室乾燥・室内物干し・食洗機・宅配ボックス等を、"
            "この住戸に実在しないのに描き加えない（入力画像に写っておらず、物件事実にも記載が無いもの）。\n"
            "- 収納の扉を開けた状態にしない（中身を描き足さない）。\n"
            "- 既存の構造・設備・窓・眺望を変えない、足さない。"),
        # ★光・方角・日当たりを言葉で書かせない：fact_scrub がマイソクに記載の無い採光主張を節ごと削除するため、
        #   ②の芯である「朝の光」は言葉では消える。光は画（staging）で作り、言葉は設備で語る。
        "comment_tone": ("設備×時間の1行（例: 独立洗面台。朝の10分が変わる。）。"
                         "★光・方角・日当たりは言わない（それは画で見せる）。設備語で語る。"),
        "cover_hooks": ["がんばった日の、帰る場所。", "朝の支度が、好きになる。", "暮らしを、ていねいに。"],
        "narration": {"voice_id": None,   # ★女性ボイスID決定後にここへ直書き（鍵ではない・設定）
                      "tone": "落ち着き・押しつけない・言い切り。設備は事実として言い、感情を足しすぎない。"},
        # ★②固有ban：オートロック＝安全/防犯と断定するのは優良誤認リスク（法務は専門家確認が前提）。
        #   ②は設備訴求が芯なので、この語が一番出やすい。
        "ban_words": ["安心", "安全", "防犯", "セキュリティ万全", "完全防犯"],
        "caption": {"tone": "やわらかい短文・言い切り。設備は事実として書く。誇大にしない。",
                    "hashtags": ["#暮らしを整える", "#朝時間", "#ひとり暮らしの部屋"]},
    },
    "hobby": {
        "label": "趣味部屋",
        "accent": (168, 205, 172),   # SAGE
        "style_default": "ナチュラル/北欧",
        # ★定義書(07-17)の③には「自転車壁掛け」とあるが、同じ定義書の賃貸staging原則
        #   「壁・建具は1mmも変えない／入居者が実際に再現できるもの」と矛盾する（賃貸で壁にビスは打てない）。
        #   再現不能な状態の提示になるため自立式に置換した。
        "staging_prompt": (
            "「好きなものが置いてある部屋」を、1つの趣味に絞って作る。詰め込まない。\n"
            "- 趣味の選定: 1物件1趣味。部屋の広さ・形状から逆算して1つだけ選ぶ"
            "（デスク／本と棚／自転車／楽器／プロジェクター等）。2つ以上を同じ物件に混ぜない。\n"
            "- 灯り: 昼の自然光に、手元の照明を1つ。作業している気配を光で示す。\n"
            "- 素材: 木・スチール・布を混ぜる。過度に整えない（使っている部屋に見せる）。\n"
            "- 色: 生成り・木・グレーを基調に、セージグリーンかモスを1点だけ。全体は低彩度。\n"
            "- 家具: 床の余白を残し『まだ置ける』を見せる。棚は高くしすぎない。\n"
            "- 余白: 使いかけの気配を1〜2点まで（開いた本、マグ、ヘッドホン）。コレクションを大量に並べない。\n"
            "【厳守】\n"
            "- 壁・床・建具に固定する表現をしない。壁掛けラック・ビス留めの棚・造作の飾り棚は描かない。"
            "自立式ラック・突っ張り式・置き型で表現する（入居者が原状回復できる範囲だけ）。\n"
            "- 照明は置き型の器具のみ。造作の間接照明・ダウンライトの増設・配線の変更はしない。\n"
            "- 人物・人体・シルエットを描かない。\n"
            "- 防音・楽器可・ペット可を想起させる物を、物件事実に無いのに置かない"
            "（ドラムセット・グランドピアノ・アンプ等）。\n"
            "- 既存の構造・設備・窓・眺望を変えない、足さない。"),
        "comment_tone": ("趣味の動線1行。帖数・広さは facts にある時だけ言う"
                         "（例: 洋室6帖は、デスクを置いてもまだ余裕がある。）"),
        # ★旧案「趣味に、1部屋あげる。」を落とした理由：_scrub_cover_copy の数値検出で
        #   「間取り表記以外の数字を含む（数値主張＝要確認）」警告が立つ。cover_hooks は AI3案が全滅したときの
        #   最後の砦（feature_fallback）なので、警告つきの文言では fallback として機能しない。
        "cover_hooks": ["好きを、まんなかに。", "デスクから、はじまる部屋。", "好きなものと、暮らす。"],
        "narration": {"voice_id": None,
                      "tone": "気負わない・少し楽しそう・言い切り。趣味を説明しすぎない。"},
        "ban_words": ["防音", "楽器可", "ペット可", "DIY自由"],
        "caption": {"tone": "気負わない短文。好きなものを説明しすぎない。",
                    "hashtags": ["#趣味部屋", "#デスク環境", "#好きなものに囲まれて"]},
    },
}
# ★セレクタの並び＝この明示リスト（dictのキー順に依存しない／CONCEPT_ORDER と同型）。
FEATURE_ORDER = ["normal", "mote_heya", "totonoeru", "hobby"]


def feature_of(fid):
    """特集id の定義を返す。★未知は normal（旧実装は None。concept_of と同型に揃えた）。
    下流は これだけ参照＝単一の情報源。呼出側の `feature_of(x) or {}` は dict が常に真なので無害。
    ★挙動変更の実害を確認済み：未知idのとき表紙が『特集　モテ部屋』を騙っていた（rtv:1479）のが
      normal＝枠なしになる。issue-v1 の『取れないときに既定を騙らない』と同じ方針。"""
    return FEATURES.get(fid) or FEATURES["normal"]


def feature_display_name(fid):
    """UI表示名（人が選ぶときに読む名前）。★label とは別物：label は表紙の枠に焼く文字で、
    normal は空＝枠を描かない合図。両者を1つにすると normal に表示名を入れた瞬間に表紙の枠が復活する。
    ★feat-merge-3：旧名 feature_label は『焼く文字』に読めて役割が逆に伝わるため改名した。"""
    lb = str(feature_of(fid).get("label", "")).strip()
    return lb or "特集なし（標準）"


def feature_style_default(fid, default=None):
    """特集が決める『スタイル既定』（INTERIOR_STYLES のキー）。★見た目の単一の情報源＝特集。
    未定義なら default。UI側で sticky 追従させる（人が変えたら停止）。"""
    return feature_of(fid).get("style_default") or default


def feature_staging(fid):
    """特集の staging プロンプト追記。normal は ''＝追加なし（回帰）。
    ★これが今まで無かったため FEATURES[*]['staging_prompt'] は参照ゼロの死にデータだった（feat-merge-2 で接続）。"""
    return str(feature_of(fid).get("staging_prompt", "") or "")


def feature_voice_id(fid, default_voice=None):
    """特集の voice_id（表に直書き＝設定）。None → 既定にフォールバック。★鍵はSecrets、設定は表。"""
    return (feature_of(fid).get("narration") or {}).get("voice_id") or default_voice


def feature_tone(fid, key):
    """特集のトーン文字列。key∈{narration, caption}。normal は ''＝回帰。
    ★key='cover' は用意しない：旧 cover.tone の消費先（draft_pr_copy の title/subtitle）が
      冒頭フラッシュ以外に無く、既定設定では焼かれないため移植しなかった（工程0の実測）。"""
    return str((feature_of(fid).get(key) or {}).get("tone", "")).strip()


def feature_hashtags(fid):
    """特集別ハッシュタグ（ブランド共通に少量追加するだけ）。normal は []。"""
    return [str(x).strip() for x in ((feature_of(fid).get("caption") or {}).get("hashtags") or [])
            if str(x).strip()]


def feature_ban_extra(fid):
    """★その特集にだけ効く追加NG語（②の安心/安全/防犯、③の防音/楽器可 等）。共通分は含まない。"""
    return [w for w in (feature_of(fid).get("ban_words") or []) if w]


def feature_ng(fid):
    """★feat-ban-1：生成物から機械除去するハードNG＝全特集共通 ＋ その特集固有。
    ★下流の post-filter は全部これを使う（経路ごとに集合が違う状態を作らない）。
    共通語群を「モテ部屋を選んだときだけ」効かせていたのが従来の食い違いの原因だった。"""
    return list(_COMMON_HARD_NG) + feature_ban_extra(fid)


def feature_ban(fid):
    """その特集で機械除去する語＝景表法ban ＋ ハードNG（共通＋特集固有）。旧 concept_ban と同型。"""
    return list(_PR_BANNED) + list(_SNS_BAN_EXTRA) + feature_ng(fid)


# ★v79 room_facts_map（部屋⇔映像/文字/設備の対応表・★最初から3用途スキーマ）:
#   ①facts→ビート割当・タグ(v79-5=facts_keys) ②focal注入(v79-4=focal・Kling主語) ③big_text主語(v79-5=focal_ja)。
#   video_type=ROOM_PROMPTS/build_kling_prompt のキー。motion=動き量既定（狭室=minimal）。
ROOM_FACTS_MAP = {
    "外観":   {"video_type": "exterior", "focal": "the building facade and entrance", "focal_ja": "外観",
               "motion": "minimal", "facts_keys": ["オートロック", "宅配ボックス", "駐輪場", "エレベーター"]},
    "玄関":   {"video_type": "entrance", "focal": "the entrance and shoe cabinet", "focal_ja": "玄関",
               "motion": "minimal", "facts_keys": ["オートロック", "モニター付インターホン",
                                                    "カメラ付きインターホン", "宅配ボックス", "シューズボックス"]},
    "LDK":    {"video_type": "ldk", "focal": "the sofa area by the window", "focal_ja": "リビング",
               "motion": "normal", "facts_keys": ["エアコン", "ネット無料", "インターネット無料",
                                                   "角部屋", "フローリング"]},
    "キッチン": {"video_type": "kitchen", "focal": "the kitchen counter and window", "focal_ja": "キッチン",
                "motion": "normal", "facts_keys": ["システムキッチン", "都市ガス", "ガスコンロ", "IH", "給湯"]},
    "洋室":   {"video_type": "bedroom", "focal": "the bed and the window light", "focal_ja": "洋室",
               "motion": "normal", "facts_keys": ["クローゼット", "収納", "エアコン"]},
    "寝室":   {"video_type": "bedroom", "focal": "the bed and the window light", "focal_ja": "寝室",
               "motion": "normal", "facts_keys": ["クローゼット", "収納", "エアコン"]},
    "浴室":   {"video_type": "bathroom", "focal": "the bathtub", "focal_ja": "浴室",
               "motion": "minimal", "facts_keys": ["バス・トイレ別", "追焚", "浴室乾燥"]},
    "洗面":   {"video_type": "washroom", "focal": "the vanity and mirror", "focal_ja": "洗面",
               "motion": "minimal", "facts_keys": ["独立洗面台", "室内洗濯機置場"]},
    "トイレ": {"video_type": "toilet", "focal": "the toilet", "focal_ja": "トイレ",
               "motion": "minimal", "facts_keys": ["温水洗浄便座", "ウォシュレット"]},
    "バルコニー": {"video_type": "balcony", "focal": "the balcony and the outside view", "focal_ja": "バルコニー",
                 "motion": "normal", "facts_keys": ["バルコニー", "南向き"]},
    "クローゼット": {"video_type": "generic", "focal": "the closet storage", "focal_ja": "収納",
                 "motion": "minimal", "facts_keys": ["クローゼット", "ウォークインクローゼット", "収納"]},
}


def room_facts_map(room):
    """★v79 room_facts_map（3用途: facts→ビート割当/タグ／focal注入(v79-4)／big_text主語(v79-5)）。未知は generic 既定。"""
    return ROOM_FACTS_MAP.get(room, {"video_type": "generic", "focal": "the room",
                                     "focal_ja": room or "部屋", "motion": "normal", "facts_keys": []})

# ── 投稿文テンプレ（設定画面で編集可・caption_templates.json がデフォルト）──────────
# footer は{date}を含む複数行。生成時にJST当日へ置換（Geminiに書かせない＝法務注記の改変防止）。
_DEFAULT_CAPTION_TEMPLATES = {
    "footer": ("※家具・小物はAI生成のイメージです\n"
               "※賃料等は掲載時点の情報です\n"
               "※取引態様: 仲介\n"
               "※{date}時点で募集中の物件です。タイミングにより成約済みの場合があります"),
    "cta": "気になる方はコメントに「詳細」とどうぞ📩",
    "area_hashtags": ["#大阪賃貸", "#大阪お部屋探し", "#関西賃貸", "#大阪1LDK", "#賃貸暮らし"],
    "reply": _SNS_REPLY,
    "dm": _SNS_DM_TEMPLATE,
}
# フッター必須要素（編集で法務注記が消える事故を構造的に防ぐ）。(検査トークン, 説明)
_CAPTION_FOOTER_REQUIRED = [
    ("AI生成", "「AI生成」を含む注記（例：家具・小物はAI生成のイメージです）"),
    ("取引態様", "「取引態様」の表示（例：取引態様: 仲介）"),
    ("{date}", "時点注記のプレースホルダ {date}（例：{date}時点で募集中の物件です）"),
]


def jst_date_str(d=None) -> str:
    """キャプション生成日をJSTで『YYYY年M月D日』。時点注記の {date} 置換用（サーバー側自動挿入）。"""
    import datetime as _dt
    if d is None:
        d = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9)))
    return f"{d.year}年{d.month}月{d.day}日"


def default_caption_templates() -> dict:
    """caption_templates.json（リポジトリ）を読み、無ければ内蔵デフォルト。欠けたキーは内蔵で補完。"""
    import json as _json
    try:
        p = Path(__file__).parent / "caption_templates.json"
        if p.exists():
            d = _json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {**_DEFAULT_CAPTION_TEMPLATES, **d}
    except Exception:  # noqa: BLE001  壊れたJSONでも既定で動作継続
        pass
    return dict(_DEFAULT_CAPTION_TEMPLATES)


def validate_caption_templates(tpl: dict):
    """テンプレ編集値を検査。返り値 (errors, warnings)。errors が非空なら保存不可。
    errors: フッター必須要素（AI生成/取引態様/{date}）の欠落。
    warnings: フッター/CTA に混入した ban 語（景表法・公取協）。"""
    errors, warnings = [], []
    footer = str((tpl or {}).get("footer") or "")
    cta = str((tpl or {}).get("cta") or "")
    for token, desc in _CAPTION_FOOTER_REQUIRED:
        if token not in footer:
            errors.append(f"必須要素が不足：{desc}")
    ban = list(_PR_BANNED) + _SNS_BAN_EXTRA
    hit = sorted({w for w in ban if w and (w in footer or w in cta)})
    if hit:
        warnings.append("フッター/CTAに誇大・ban語：" + "／".join(hit))
    return errors, warnings


def _sns_access_pick(access):
    """access から代表1駅（駅への直接徒歩・最短。バス便は除外）を (station, walk_min) で返す。無ければ (None,None)。
    駅表記は『◯◯駅 徒歩N分』も『「福島」徒歩N分』（賃貸マイソクの括弧表記）も拾う。"""
    import re as _re
    best_st, best_min = None, None
    for a in (access or []):
        if "バス" in a:
            continue
        m = _re.search(r"徒歩\s*(\d+)\s*分", a)
        if not m:
            continue
        mn = int(m.group(1))
        head = a[:m.start()]
        b = _re.search(r"[「『]([^」』]+)[」』]", head)      # 「福島」→ 福島駅
        if b:
            st = b.group(1) + "駅"
        else:
            d = _re.search(r"([^\s、,]+駅)", head)           # ◯◯駅
            st = d.group(1) if d else (head.strip().split()[-1] if head.strip() else "最寄駅")
        if best_min is None or mn < best_min:
            best_min, best_st = mn, st
    return best_st, best_min


# ★issue-v1：マストヘッド2行目のエリア表記用ローマ字（app._PL_AREA_ROMAJI から移設＝1源）。
#   辞書に無い駅は「日本語のまま」返す（ローマ字を推測生成しない＝誤記事故の防止）。西区/ドーム前導線を追加。
_AREA_ROMAJI = {
    "福島": "FUKUSHIMA", "野田": "NODA", "梅田": "UMEDA", "大阪": "OSAKA", "難波": "NAMBA",
    "なんば": "NAMBA", "天王寺": "TENNOJI", "本町": "HOMMACHI", "心斎橋": "SHINSAIBASHI",
    "京橋": "KYOBASHI", "淀屋橋": "YODOYABASHI", "中之島": "NAKANOSHIMA", "天満": "TENMA",
    "桜川": "SAKURAGAWA", "西九条": "NISHIKUJO", "弁天町": "BENTENCHO", "新大阪": "SHIN-OSAKA",
    "谷町": "TANIMACHI", "北浜": "KITAHAMA", "堀江": "HORIE", "南堀江": "MINAMI-HORIE",
    # ── 西区/大正/中津まわりの導線（福島固定表記の事故対策で追加）──
    "九条": "KUJO", "阿波座": "AWAZA", "西長堀": "NISHI-NAGAHORI", "肥後橋": "HIGOBASHI",
    "ドーム前": "DOME-MAE", "ドーム前千代崎": "DOME-MAE", "大正": "TAISHO", "中津": "NAKATSU",
}


def _area_romaji(station_key: str) -> str:
    """★issue-v1：駅名（『駅』を除いた文字列）→ エリア表記。辞書に無ければ日本語のまま返す（推測ローマ字化はしない）。
    ★マイソク頻出の『JR福島駅』『地下鉄本町駅』のように事業者/路線名が前置される表記を後方一致（最長）で拾う
      ＝これが無いと『JR福島』がそのままマストヘッドに出る（実測で検出した不具合）。
    ★駅名でないもの（『◯◯線』＝路線名／『最寄』＝_sns_access_pick のプレースホルダ）は空を返し、
      呼出側で『ISSUE NN』単独へ倒す（＝取得失敗を地名らしき文字列で取り繕わない）。"""
    key = (station_key or "").replace("駅", "").strip()
    if not key or key.endswith("線") or key == "最寄":
        return ""
    if key in _AREA_ROMAJI:
        return _AREA_ROMAJI[key]
    cands = [k for k in _AREA_ROMAJI if len(k) < len(key) and key.endswith(k)]
    return _AREA_ROMAJI[max(cands, key=len)] if cands else key


def magazine_issue_line(facts, issue_no=1, area_override="") -> str:
    """★issue-v1：動く雑誌マストヘッド2行目『ISSUE 03  /  OSAKA・NISHIKUJO』を組む1源。
    エリアの決定順＝ area_override（人の手入力）＞ facts の代表駅（_sns_access_pick）のローマ字 ＞ 空。
    ★エリアが取れないときに FUKUSHIMA 等の既定値を出さない（＝物件と異なるエリアを焼き込む事故の再発防止）。
      取れなければ 'ISSUE 03' 単独へフォールバックする。
    ★辞書に無い駅はローマ字を推測せず日本語のまま返す（_v79_sans_r＝ゴシックで描画可）。この場合
      'OSAKA・' は付けない（旧 _pl_cover_subline の判断を踏襲＝英日混在の不格好を避ける）。
    号数は数字以外を落として2桁ゼロ詰め。空・0・数字なしは '01'。"""
    nn = re.sub(r"\D", "", str(issue_no if issue_no is not None else "")).zfill(2)[:2]
    if not nn or nn == "00":
        nn = "01"
    area = str(area_override or "").strip().upper()      # 人の手入力を優先（英小文字はマストヘッドの全大文字に合わせる）
    if not area:
        st_name, _ = _sns_access_pick((facts or {}).get("access"))
        area = _area_romaji(st_name)                     # 未知駅＝日本語のまま／駅名でなければ空
    if not area:
        return f"ISSUE {nn}"                             # ★エリア不明＝既定エリアを騙らない
    head = "OSAKA・" if area.encode("ascii", "ignore").decode() == area else ""
    return f"ISSUE {nn}  /  {head}{area}"


def _feature_caption_line(feature: str) -> str:
    """draft_sns_captions 用 特集のトーン1行（hook/area_blurの文体）。normal＝空＝回帰。
    ★feat-merge-3：情報源を CONCEPT_PRESETS から FEATURES へ移した（テイストの単一情報源）。"""
    tone = feature_tone(feature, "caption")
    return f"・hook/文体のトーン：{tone}（数値・設備は創作しない＝上の厳守が優先）。\n" if tone else ""


def draft_sns_captions(client, facts: dict, templates: dict = None,
                       gen_date: str = None, model="gemini-2.5-flash",
                       feature: str = "normal") -> dict:
    """マイソク事実から Instagram×2 / TikTok×2 / コメント返信テンプレ を生成。
    ★数値（賃料・管理費・㎡・徒歩分）・設備は facts 由来をコードで固定し改変させない。
      Gemini はフック文・エリアぼかし・設備の選定・ハッシュタグの創作のみ。フッター/CTA/エリア大ハッシュタグ/
      返信・DMは templates（設定画面で編集可・既定は caption_templates.json）から取得しコード側で固定（Gemini非経由）。
      footer 内 {date} は gen_date（無指定ならJST当日）へサーバー側で置換＝時点注記。
      誇大/ban語は生成後にコードで除去し warnings に記録。徒歩8分超は『駅近』等も禁止。
    返り値: {ig_a, ig_b, tt_a, tt_b, reply, dm, warnings}。事実が皆無なら None。"""
    import json as _json
    tpl = templates or default_caption_templates()
    date_str = gen_date or jst_date_str()
    footer = str(tpl.get("footer") or _DEFAULT_CAPTION_TEMPLATES["footer"]).replace("{date}", date_str)
    cta = str(tpl.get("cta") or _DEFAULT_CAPTION_TEMPLATES["cta"])
    area_tags = list(tpl.get("area_hashtags") or _DEFAULT_CAPTION_TEMPLATES["area_hashtags"])
    reply = str(tpl.get("reply") or _DEFAULT_CAPTION_TEMPLATES["reply"])
    dm = str(tpl.get("dm") or _DEFAULT_CAPTION_TEMPLATES["dm"])
    rent = (facts.get("rent") or "").strip()
    price = (facts.get("price") or "").strip()
    fee = (facts.get("fee") or "").strip()
    madori = (facts.get("madori") or "").split("[")[0].strip()
    area = (facts.get("area") or "").replace("㎡", "").strip()
    equip_raw = (facts.get("equipment") or "").strip()
    station, walk = _sns_access_pick(facts.get("access"))
    if not (rent or price or madori or area):
        return None
    # 構造化した事実行（コードで固定＝賃料には管理費を必ず併記／徒歩分は facts 値のみ）
    ma_line = f"▷ {madori}／{area}㎡" if (madori or area) else ""
    if rent:
        money_line = f"▷ 家賃{rent}＋管理費{fee}" if fee else f"▷ 家賃{rent}（管理費は要確認）"
    elif price:
        money_line = f"▷ 価格{price}"
    else:
        money_line = ""
    walk_line = f"▷ {station} 徒歩{walk}分" if (station and walk is not None) else ""

    # ── Gemini creative（フック/ぼかし/設備選定/ハッシュタグ）。1コール・temperature低め ──
    facts_json = _json.dumps({k: v for k, v in facts.items() if k != "full_text"}, ensure_ascii=False)
    instr = (
        "あなたは賃貸物件のSNS運用者です。以下の【物件事実】だけを根拠に、Instagram/TikTok投稿の"
        "『創作パート』をJSONで出力してください（数値や設備は創作しない）。\n"
        "厳守：\n"
        "・誇大/最上級（最高・完璧・絶対・日本一・最安・激安・破格・格安・希少 等）を使わない（景表法）。\n"
        "・物件名・番地は出さない。area_blur は駅名は出してよいが街をぼかす（例『大阪市内・環状線沿線』）。\n"
        "・equip は【物件事実】の equipment 欄に書かれている設備の語だけを、そのままの表記で最大3つ選ぶ"
        "（欄に無い設備は絶対に足さない）。\n"
        "・hook は1行・数字は事実のみ。フックA=数字/コスパ訴求（必ず具体的な金額または数字を含める）、"
        "フックB=特徴/内装訴求。\n"
        "・hashtags(IG)は15〜20個：エリア大5・エリア小4・属性5・ニッチ4の配分。TikTokは4個。\n"
        f"{_feature_caption_line(feature)}"      # ★特集のトーン（空=特集なし=回帰）
        "出力JSON（これのみ・説明なし）：\n"
        '{"ig_a":{"hook":"","area_blur":"","equip":[],"hashtags":[]},'
        '"ig_b":{"hook":"","area_blur":"","equip":[],"hashtags":[]},'
        '"tt_a":{"text":"全角60字以内","hashtags":[]},'
        '"tt_b":{"text":"全角60字以内","hashtags":[]}}\n'
        "【物件事実】"
    )
    warnings = []
    # ★賃料ガード（景表法）：投稿文は rent/fee/price を生値で表示する。数字以外(漢数字等)が混入
    #   していたら人に知らせる（表示は verbatim なので止めないが、金額の異常を沈黙させない）。
    for _lbl, _v in (("賃料", rent), ("管理費", fee), ("価格", price)):
        if _v and not money_is_clean(_v):
            warnings.append(f"{_lbl}『{_v}』に数字以外の文字が含まれます（投稿文にそのまま表示・要確認）")
    data = {}
    try:
        resp = client.models.generate_content(model=model, contents=[instr + facts_json])
        obj = _first_json_object(getattr(resp, "text", "") or "")
        data = _json.loads(obj) if obj else {}
        if not isinstance(data, dict):
            data = {}
    except Exception as e:  # noqa: BLE001  握り潰さず記録（事実部分だけでも返す＝実運用を止めない）
        warnings.append(f"AIによるフック/ハッシュタグ生成に失敗（{type(e).__name__}）。事実部分のみで出力します。")

    ban = list(_PR_BANNED) + _SNS_BAN_EXTRA + feature_ng(feature)  # ＋ハードNG（共通＋特集固有）
    if walk is None or walk > 8:                     # 徒歩8分超/不明→立地訴求語も禁止
        ban += _PR_LOCATION_WORDS
    con_tags = feature_hashtags(feature)             # 特集別タグ（ブランド共通に少量追加のみ）

    def _clean(s):
        """★v79-scrub-clause：ban は節ごと除去。ハッシュタグのような単一トークンは区切りが無いので
        丸ごと落ちる＝『#の住まい』のような壊れたタグを作らない（空タグは _tags 側で除外済み）。"""
        s, _bn = ban_scrub(str(s or ""), ban)
        warnings.extend(sorted(set(_bn)))
        return s.strip()

    def _tags(raw, n_max):
        out = []
        for t in (raw or []):
            t = _clean(t).replace(" ", "").replace("　", "")
            if not t:
                continue
            if not t.startswith("#"):
                t = "#" + t
            if t not in out:
                out.append(t)
        return out[:n_max]

    def _equip_line(raw):   # ★設備欄に実在する語だけ（欄外・広告文からの創作を防ぐ）
        eq = [e.strip() for e in (raw or []) if isinstance(e, str) and e.strip()
              and e.strip() in equip_raw][:3]
        return "▷ " + "／".join(eq) if eq else ""

    def _ig(d):
        body = [_clean(d.get("hook", "")), "", _clean(d.get("area_blur", ""))]
        body += [x for x in (ma_line, money_line, walk_line, _equip_line(d.get("equip"))) if x]
        # ハッシュタグ = テンプレのエリア大（固定・先頭）＋ コンセプト別（少量）＋ Gemini の残り（重複除去・最大20）
        tags = _tags(list(area_tags) + list(con_tags) + list(d.get("hashtags") or []), 20)
        # footer/CTA はテンプレ確定値をそのまま（編集時に必須要素ガード＋ban検査済み＝ここでは無改変）
        body += ["", cta, "", footer, "", " ".join(tags)]
        return "\n".join(body)

    def _tt(d):
        txt = _clean(d.get("text", ""))
        if len(txt) > 60:
            txt = txt[:59] + "…"
        # TikTokは4個上限＝Geminiの内容タグ優先、余ればコンセプト別を末尾に少量
        return f"{txt}\n" + " ".join(_tags(list(d.get("hashtags") or []) + list(con_tags), 4))

    return {
        "ig_a": _ig(data.get("ig_a", {})), "ig_b": _ig(data.get("ig_b", {})),
        "tt_a": _tt(data.get("tt_a", {})), "tt_b": _tt(data.get("tt_b", {})),
        "reply": reply, "dm": dm, "warnings": sorted(set(warnings)),
    }


# ── AIナレーション原稿（narration-v68）──────────────────────────────────────────
# 失敗構造の教訓：後処理の速度圧縮で辻褄合わせをしない＝「シーン尺に収まる原稿」を生成段階で作る。
_NARR_CPS = 4.2   # 低い声の男性ナレーターの目安：約4.2字/秒


def narration_char_limit(dur_sec) -> int:
    """1シーンのナレ字数上限。4.2字/秒 ×(尺−0.3秒の安全マージン)。5秒→約20字。
    ★係数4.2は不変（原稿が尺を超えて音が乱れた失敗の防波堤）。谷合さん実測で21字/5秒=4.2字/秒が
      収まると確認済み。過剰だったのは旧『−1秒』バッファ→v70bで『−0.3秒』(v70aの許容超過と同値)へ。"""
    try:
        d = float(dur_sec)
    except (TypeError, ValueError):
        d = 5.0
    return max(6, int(round(_NARR_CPS * (d - 0.3))))


# ── ビート→カット割り当て＋タイムライン（story-v78 A0）─────────────────────────
# ★ナレの単位(ビート=部屋)とカットの単位(画像)を分離。参照(tokyo.spectre)は「ナレの単位≠カットの単位」。
# ★Kling生成尺は{5,10}のみ（app.py selectbox [5,10]・generate_clip_fal duration・コード確認済）。
#   ceil(秒/5)は在庫を見ず破綻→字数の物理天井=在庫×10×係数。総尺はトリムでナレ秒に寄せる（速度変更でない）。
# ★係数は narrmeas 実測待ちの暫定5.26（引数で受け、Eユニットで確定）。_NARR_CPS(=4.2)とは別（旧値）。
_BEAT_COEF_PROVISIONAL = 5.26   # ★暫定（22字/4.18秒の1サンプル実測）。複数実測で確定するまで動かさない
_BEAT_CLIP_SECS = (5, 10)       # Klingが公開している生成尺（任意秒は未確認＝ここに量子化＋トリム）


def beat_ceiling_chars(stock, coefficient=_BEAT_COEF_PROVISIONAL, max_clip=10):
    """ビートに物理的に入る最大字数 = 在庫画像数 × 最大クリップ尺 × 係数。★Aに渡す『天井』。"""
    return int((stock or 0) * max_clip * coefficient)


# ★ビート内カット境界のxfade尺(videofix-v58)。ビート境界はハードカット(0)＝累積ズレを断つ(谷合さん確定)。
_BEAT_XFADE_SEC = 0.6


def _split_trims(total, cuts):
    """total を cuts(生成尺) に分割。各トリム ≤ その生成尺。均等→頭打ち→残りを他へ再配分。
    返り値 Σ = min(total, Σcuts)。"""
    trims = [0.0] * len(cuts)
    remaining = min(total, sum(cuts))
    for _ in range(len(cuts) + 1):
        active = [i for i in range(len(cuts)) if trims[i] < cuts[i] - 1e-9]
        if not active or remaining <= 1e-9:
            break
        share = remaining / len(active)
        for i in active:
            add = min(share, cuts[i] - trims[i])
            trims[i] += add
            remaining -= add
    return trims


def allocate_beat_cuts(chars, stock, coefficient=_BEAT_COEF_PROVISIONAL, xfade=_BEAT_XFADE_SEC):
    """ビートの字数と在庫画像数からカット割り当て＋per-cutトリムを返す（谷合さんQ2の順序）。
    ① 在庫の5秒で足りる→5s ② 足りない→一部を10s生成→トリム ③ 在庫×10でも足りない→上限で防ぐ(overflow)。
    ★ビート内xfade(0.6s×境界)が食う分をトリムに足す（Σtrim=narr+0.6×(n-1)）→描画=Σtrim−0.6×(n-1)=narr（ズレ0）。
    ★ビート境界はハードカット(呼出側)＝ビート間は食われない→総尺=Σnarr ちょうど。
    返り値: {narr_sec, cuts:[5|10,...], trims:[..], rendered_sec, xfade_intra, overflow, hold_sec, fal_cost_units}。"""
    narr = (chars or 0) / coefficient
    stock = int(stock or 0)
    if stock <= 0:
        return {"narr_sec": round(narr, 2), "cuts": [], "trims": [], "rendered_sec": 0.0,
                "xfade_intra": 0, "overflow": narr > 0, "hold_sec": round(narr, 2), "fal_cost_units": 0.0}
    if stock * 5 >= narr:                         # ① 5秒カットで足りる
        cuts = [5] * min(max(1, math.ceil(narr / 5)), stock)
    elif stock * 10 >= narr:                      # ② 足りない→最小数だけ10s生成→トリム
        n10 = min(stock, math.ceil((narr - stock * 5) / 5))   # 5s→10sの格上げで+5s/枚
        cuts = [10] * n10 + [5] * (stock - n10)
    else:                                         # ③ 在庫×10でも足りない（A側の上限が漏れた）＝最後の砦
        cuts = [10] * stock
    n = len(cuts)
    padded = narr + xfade * (n - 1)               # ★ビート内xfadeが食う分を足す（nは不変）
    while sum(cuts) < padded and 5 in cuts:        # パディング後もカバー不足なら5s→10s格上げ
        cuts[cuts.index(5)] = 10
    trims = _split_trims(padded, cuts)
    rendered = sum(trims) - xfade * (n - 1)        # 描画尺（ビート内xfade分を引く）＝理想はnarr
    return {"narr_sec": round(narr, 2), "cuts": cuts, "trims": [round(t, 2) for t in trims],
            "rendered_sec": round(rendered, 2), "xfade_intra": n - 1,
            "overflow": sum(cuts) < padded,
            "hold_sec": round(max(0.0, narr - rendered), 2),   # ③漏れ時のみ>0（末尾フリーズ＋警告）
            "fal_cost_units": sum(c / 5 for c in cuts)}        # 5s=1単位・10s=2単位


def beat_generation_targets(beats, coefficient=_BEAT_COEF_PROVISIONAL):
    """A（物語生成）へ渡す3つ。★天井だけ渡すと全ビートが天井に張り付き等長・単調（体言止めと同型）。
    beats=[{room, stock}]。返り値:
    {ceilings:{room:字数天井}, budget_chars:狙う総字数(水位≠天井), rhythm:『揃えない』指示}。"""
    ceilings = {b["room"]: beat_ceiling_chars(b.get("stock", 0), coefficient) for b in beats}
    budget = round(len(beats) * 35)   # 承認済みの1ビート平均≈35字(176/5)＝天井の半分。狙う水位。
    return {"ceilings": ceilings, "budget_chars": budget,
            "rhythm": "ビート長を揃えないこと（凸凹が物語のリズム。承認済み例=23/40/49/31/33字）"}


def _narr_clip(s: str, limit: int) -> str:
    """1行をban語除去のうえ字数上限にハード収束（超過は打ち切り）。改行・前後空白は潰す。"""
    s = re.sub(r"\s+", "", str(s or ""))
    for w in list(_PR_BANNED) + _SNS_BAN_EXTRA:
        if w:
            s = s.replace(w, "")
    return s[:limit]


# ★few_shot＝谷合さん執筆の2本のみ（Claude作の例は1つも入れない＝癖の量産を防ぐ・story-v78 §2）。
#   例1=A系(独白・誰にも言っていない) / 例2=B系(視聴者への語りかけ)。文体・水準の見本であり丸写し禁止。
_STORY_FEWSHOT = (
    "【例1｜「明日、あの子が来る」＝独白（A系・誰にも言っていない）】\n"
    "玄関｜明日、あの子が来る。とりあえず、靴を整えてスリッパを用意。\n"
    "LDK｜レコードを出してみる。彼女の好みは？コーヒー片手に妄想。1LDK、36平米。角部屋で良かった。\n"
    "キッチン｜冷蔵庫に、ちょっとしたおつまみを今日のうちに作っておく。ワインはちょっといいやつ。\n"
    "風呂｜風呂も、一応みがいた。シャンプーヨシ、トリートメントヨシ。\n"
    "洋室｜慣れないベッドメイキング。……いや、何も起きないと思うけど。\n"
    "\n"
    "【例2｜「イケてる男のナイトルーティン」＝視聴者への語りかけ（B系）】\n"
    "玄関｜21時、帰宅。靴を脱いで、ひと安心。引っ越したばかりなのにw\n"
    "キッチン｜今日はパスタ。湯を沸かしてる間に、ワインを開ける。\n"
    "LDK｜好きな音楽をかけてワインを片手に一息。この空間が落ち着く。1LDK、36平米。角部屋で良かった。\n"
    "風呂｜湯船で音楽を1曲。ゆったりした曲でリラックス。\n"
    "洋室｜明日も仕事。少し背伸びして買ったベッドでぐっすり眠る。おやすみなさい。\n"
)


def _story_ban_words(feature_id="normal"):
    """物語ナレの禁止語＝PRban（最上級/断定）＋SNSban＋容姿/性的ハードNG（来訪者の容姿・性的示唆）。
    ★主人公自身の動作（手が丁寧になる 等）は含めない＝A-1で実測ロック済み。"""
    return list(_PR_BANNED) + list(_SNS_BAN_EXTRA) + feature_ng(feature_id) + ["モテ", "モテ部屋"]


def story_narration(client, beats, facts, situation, style="独白",
                    budget_sec=33, coefficient=_BEAT_COEF_PROVISIONAL,
                    model="gemini-2.5-flash", feature_id="normal") -> dict:
    """★story-v78 A: 全ビートを『1回のGeminiコール』で1つの連続した物語として生成する。
    beats=[{room, stock}] 部屋順（🔀整列後）。situation=シチュエーション文。style='独白'(A系)/'語りかけ'(B系)。
    ★Aに渡すのは3つ（beat_generation_targets）: 各ビート字数上限／総字数予算(≈budget_sec秒)／ビート長を揃えない。
      上限だけ渡すと全ビート天井に張り付き等長・単調（体言止めと同型）になるため。
    後処理: JSON解析→beats順に整合→fact_scrub(事実外属性を節単位で除去)→物語ban除去→上限超過は警告(切らない)。
    ★normalize_reading(音声用)は適用しない＝字幕に忠実な生テキストを返す（TTS時に正規化＝A-3/B）。
    ★角部屋・最上階など位置属性は fact_scrub が facts裏付け無しなら落とす（プロンプトでも明示）。
    返り値: {lines:[{room,text,ceiling,over}], warnings:[str], prompt:str, raw:str}。beats空/client無なら None。"""
    import json as _json
    beats = [b for b in (beats or []) if b.get("room")]
    if not beats or client is None:
        return None
    n = len(beats)
    tg = beat_generation_targets(beats, coefficient)
    ceilings = tg["ceilings"]
    facts_json = _json.dumps({k: v for k, v in (facts or {}).items()
                              if k in ("madori", "area", "rent", "price", "fee",
                                       "equipment", "features", "full_text")},
                             ensure_ascii=False)
    _addr = "視聴者への語りかけ（一人でも孤独にしない・見ている人がいる）" if style == "語りかけ" \
        else "独白（誰にも言っていない。心の中の実況）"
    beat_lines = "\n".join(f"{i+1}. {b['room']}（上限{ceilings.get(b['room'], 0)}字）"
                           for i, b in enumerate(beats))
    instr = (
        "あなたは不動産ルームツアー動画の男性ナレーター（低い声・落ち着き・言い切り・煽らない）です。\n"
        "以下のシチュエーションで、全ビートを『1つの連続した物語』として書いてください。"
        "各ビートが前のビートを受けていること。断片的なポエムにしない。\n\n"
        f"■ シチュエーション：{situation}\n"
        f"■ 語りの立ち位置：{_addr}\n\n"
        "■ 必ず守る書き方（谷合さんの承認例から抽出した規則）：\n"
        "・時制は現在形／実況。「〜した」の報告にしない（✅出してみる・妄想・ヨシ・慣れない　❌出してみた・みがいた）。\n"
        "・照れは話し方に出す。語彙で説明しない（やっていることは全然ちょっとじゃない。話し方だけが照れている）。\n"
        "・物と時間で示す。言葉で説明・否定しない（✅トリートメントヨシ　❌見せる予定は、ない）。\n"
        "・行末の形を揃えない（体言止めを強制しない。全行を名詞で終えると単調になる）。\n"
        "・最初のビートで状況説明（視聴者は文脈ゼロで見に来る）。誰が・何が・どこ＋物件の数字を1つ。\n"
        "・物件情報（間取り・面積・角部屋）は、それが画に映っているビートで言う（面積・広さはLDKで言う。"
        "キッチンや風呂で面積を言わない＝言葉と画がズレる）。\n"
        "・★角部屋・最上階などの『位置』は、下の物件事実に明記があるときだけ言う。無ければ書かない。\n"
        "・眺望／方角／日当たり／静けさ／周辺環境は書かない（物件事実に無い属性の創作は禁止）。\n"
        "・物件名・『モテ』等の内部語・誇大語・最上級は使わない。絵文字は使わない。\n\n"
        "■ 承認済みの見本2本（この文体・この水準に合わせる。丸写ししない）：\n"
        f"{_STORY_FEWSHOT}\n"
        "■ ビート構成（この順番・部屋名・各ビートの字数上限）：\n"
        f"{beat_lines}\n\n"
        "■ 尺の狙い：\n"
        f"・全体で約{budget_sec}秒（{tg['budget_chars']}字相当）を狙う。これは狙う水位であって天井ではない。\n"
        f"・各ビートの上限を超えない。ただし上限に張り付かない。{tg['rhythm']}。\n\n"
        f"■ 物件事実（この数字・属性だけ使う。無い属性を創作しない）：{facts_json}\n\n"
        f"出力はJSON配列のみ（説明・前置きなし・要素数={n}・順番はビート構成どおり）：\n"
        '[{"room":"…","text":"…"}, …]'
    )
    warnings, lines, raw = [], [], ""
    try:
        resp = client.models.generate_content(model=model, contents=[instr])
        raw = getattr(resp, "text", "") or ""
        m = re.search(r"\[.*\]", raw, re.S)
        arr = _json.loads(m.group(0)) if m else []
    except Exception as e:  # noqa: BLE001  握り潰さず記録（呼び出し側で簡易継続）
        warnings.append(f"物語ナレの生成に失敗（{type(e).__name__}）。")
        arr = []
    by_room = {}
    for o in arr if isinstance(arr, list) else []:
        if isinstance(o, dict) and o.get("room"):
            by_room.setdefault(str(o["room"]).strip(), str(o.get("text", "")).strip())
    ban = _story_ban_words(feature_id)
    for i, b in enumerate(beats):
        room = b["room"]
        # 位置対応（room名一致）→ 無ければ index 対応の保険
        text = by_room.get(room)
        if text is None and i < len(arr) and isinstance(arr[i], dict):
            text = str(arr[i].get("text", "")).strip()
        text = text or ""
        # ① 事実外属性を節単位で除去（角部屋を含む・facts照合）
        text, removed = fact_scrub(text, facts)
        for r in removed:
            warnings.append(f"{room}: 事実外属性『{r}』を含む節を除去（factsに裏付けなし）。")
        # ② 物語ban（容姿/性的/誇大）を除去。主人公の動作は対象外（A-1で実測）
        for w in ban:
            if w and w in text:
                text = text.replace(w, "")
                warnings.append(f"{room}: 禁止語『{w}』を除去。")
        # ③ 上限超過は警告のみ（物語を壊さない・overflowは下流A0が防ぐ＋overで見える）
        ceil = ceilings.get(room, 0)
        _len = len(re.sub(r"\s+", "", text))
        over = ceil > 0 and _len > ceil
        if over:
            warnings.append(f"{room}: 上限{ceil}字に対し{_len}字（超過。原稿短縮 or 在庫追加を）。")
        lines.append({"room": room, "text": text, "ceiling": ceil, "over": over})
    return {"lines": lines, "warnings": sorted(set(warnings)), "prompt": instr, "raw": raw}


# ★シチュエーション（story-v78 §3・13→6に削減。軸＝「全部屋を回る口実になるか」）。
#   need=必要な部屋（いずれか在れば提案・空=どんな物件でも）。style=独白(A系)/語りかけ(B系)。
# ★v79-feature-reach(b)：各エントリに feature を追加した。従来は検出部屋でしか絞らず特集を一切見ないため、
#   ②③を選んでも A系（モテ部屋の世界観）しか出てこなかった。feature=None＝全特集共通。
#   ★A系/B3 の文面・style・need は1文字も変えていない（mote_heya の回帰を避けるため）。
#   ★B1 を共通に置くのは、特集なし(normal)を選んだときに候補がゼロにならないようにするため。
#     各特集にも need 空のものを1本以上置いてある（候補ゼロで物語生成が始められない状態を作らない）。
STORY_SITUATIONS = [
    {"id": "A1", "style": "独白", "need": ["玄関"], "feature": "mote_heya",
     "text": "引っ越したって言ったら、気になってる女友達が来た",
     "label": "A1｜気になってる女友達が来た（独白）"},
    {"id": "A2", "style": "独白", "need": [], "feature": "mote_heya",
     "text": "明日、あの子が来る。そわそわしながら仕込んでいる",
     "label": "A2｜明日、あの子が来る（独白・どんな物件でも）"},
    {"id": "A3", "style": "独白", "need": ["玄関", "外観"], "feature": "mote_heya",
     "text": "飲みの帰り「近いんでしょ？」って言われた",
     "label": "A3｜飲みの帰りに寄られた（独白）"},
    {"id": "A4", "style": "独白", "need": ["玄関"], "feature": "mote_heya",
     "text": "駅まで送るつもりが、雨が降ってきた",
     "label": "A4｜雨で戻ってきた（独白）"},
    {"id": "B1", "style": "語りかけ", "need": [], "feature": None,
     "text": "引っ越してきた初日。一人で、部屋を見て回っている",
     "label": "B1｜引っ越し初日のルームツアー（語りかけ・どんな物件でも）"},
    {"id": "B3", "style": "語りかけ", "need": ["キッチン"], "feature": "mote_heya",
     "text": "イケてる男のナイトルーティン",
     "label": "B3｜ナイトルーティン（語りかけ）"},
    # ── ②自分を整える部屋（谷合さんFB後に文面確定・ban / needs_review / 数字は全て回避済み）──
    {"id": "C1", "style": "独白", "need": ["洗面"], "feature": "totonoeru",
     "text": "朝、いちばんに顔を洗う。今日は少し早く起きた",
     "label": "C1｜朝いちばんに顔を洗う（独白）"},
    {"id": "C2", "style": "独白", "need": ["玄関"], "feature": "totonoeru",
     "text": "残業して帰ってきた。とりあえず全部いったん置く",
     "label": "C2｜残業して帰ってきた（独白）"},
    {"id": "C3", "style": "語りかけ", "need": [], "feature": "totonoeru",
     "text": "なにもしない休日。部屋を整えるところから始める",
     "label": "C3｜なにもしない休日（語りかけ・どんな物件でも）"},
    {"id": "C4", "style": "語りかけ", "need": ["浴室"], "feature": "totonoeru",
     "text": "湯船にお湯をためている間に、部屋を片づける",
     "label": "C4｜湯船をためている間に（語りかけ）"},
    # ── ③趣味部屋 ──
    {"id": "D1", "style": "独白", "need": [], "feature": "hobby",
     "text": "引っ越してきた。どの部屋を「好きなこと」に使うか決める",
     "label": "D1｜どの部屋を好きなことに使うか（独白・どんな物件でも）"},
    {"id": "D2", "style": "語りかけ", "need": ["洋室"], "feature": "hobby",
     "text": "空いている部屋がある。ここを自分の場所にしようと思う",
     "label": "D2｜空いている部屋を自分の場所に（語りかけ）"},
    {"id": "D3", "style": "語りかけ", "need": [], "feature": "hobby",
     "text": "在宅の日。仕事のあと、そのまま好きなことに切り替える",
     "label": "D3｜在宅の日の切り替え（語りかけ・どんな物件でも）"},
    {"id": "D4", "style": "独白", "need": ["LDK"], "feature": "hobby",
     "text": "休みの日、朝から好きなことだけをして過ごす",
     "label": "D4｜休みの日は好きなことだけ（独白）"},
]


def story_situations_for(rooms, feature_id="normal"):
    """検出部屋 rooms ＋ 特集 で成立するシチュエーションだけ返す（§4 / v79-feature-reach(b)）。
    need空=常に／need有=いずれか在れば。エントリ側の feature=None は全特集共通の意味。
    ★引数の既定は "normal"（None＝絞らない、ではない）。呼出側が feature_id を渡し忘れたときに
      全特集の世界観が混ざったリストを返すと、モテ部屋の選択肢に②③が紛れても気づけない。
      既定を normal にしておけば「候補が共通1本だけ」という目に見える壊れ方になり、必ず直される。
      絞り込みを意図的に外したいときだけ明示的に None を渡す。
    ★玄関が無ければ A1/A3/A4 を出さない・キッチンが無ければ B3 を出さない。"""
    rset = set(r for r in (rooms or []) if r)
    out = []
    for s in STORY_SITUATIONS:
        if s["need"] and not any(r in rset for r in s["need"]):
            continue
        if feature_id is not None and s.get("feature") not in (None, feature_id):
            continue                      # 他特集の世界観は出さない（共通=None は常に出す）
        out.append(s)
    return out


def money_fields(facts) -> dict:
    """★sale-v1：表紙の金額表示を組む唯一の入口。返り値 {price, price_sub, kind}。
    kind = 'rent'（賃料）／'sale'（販売価格）／''（金額が取れない）。
    ★賃貸の戻り値は従来と1バイトも変えない（回帰ゼロ）。
    ★売買は price をマイソク由来の日本語表記のまま出す（『3,350万円』）。
      賃貸の書式 '¥' + 数字 を流用すると『¥3,350万円』という存在しない表記になる。
    ★売買の price_sub は管理費と修繕積立金を併記する。事業Bの訴求は『月々いくら』なので
      修繕積立金が落ちると投稿の意味が失われる。
    ★core と app の2箇所で金額を組んでいた状態を解消するための1源（app は これを呼ぶだけ）。"""
    rent = (facts.get("rent", "") or "").strip()
    fee = (facts.get("fee", "") or "").strip()
    price_raw = (facts.get("price", "") or "").strip()
    shuzen = (facts.get("shuzen", "") or "").strip()
    if rent:                                   # ── 賃貸（従来経路・1バイトも変えない）
        return {"price": "¥" + rent.replace("円", "").strip(),
                "price_sub": (f"管理費 {fee}/月" if fee else ""), "kind": "rent"}
    if price_raw:                              # ── 売買
        _subs = []
        if fee:
            _subs.append(f"管理費 {fee}/月")
        if shuzen:
            _subs.append(f"修繕積立金 {shuzen}/月")
        return {"price": price_raw, "price_sub": " ／ ".join(_subs), "kind": "sale"}
    # 金額が取れない（従来どおり管理費だけは出す）
    return {"price": "", "price_sub": (f"管理費 {fee}/月" if fee else ""), "kind": ""}


def _mag_price_fields(facts, brand_id=DEFAULT_BRAND):
    """★v79-5 表紙の facts由来3要素（price/price_sub/area_line・deterministic）。hookはfeatureから選択（AI自由生成しない）。
    ★sale-v1：金額は money_fields に委譲（賃貸/売買の分岐を2箇所に書かない）。"""
    import re
    _mf = money_fields(facts)
    price, price_sub = _mf["price"], _mf["price_sub"]
    # area_line：access最短徒歩から『{駅}、駅{n}分。』／無ければ間取り・面積
    band = ""
    for a in (facts.get("access") or []):
        if "バス" not in a and re.search(r"徒歩\s*\d+\s*分", a):
            band = a
            break
    m_st = re.search(r"([^\s　]+?)駅", band)
    m_wk = re.search(r"徒歩\s*(\d+)\s*分", band)
    if m_st and m_wk:
        area_line = f"{m_st.group(1)}、駅{m_wk.group(1)}分。"
    else:
        _mad = (facts.get("madori", "") or "").split("[")[0].strip()
        # ★brand-v1：誌名の直書きをやめ、選択中のブランドから引く（誌名が2箇所にある状態を断つ）。
        area_line = (" ".join(x for x in (_mad, (facts.get("area", "") or "").strip()) if x)
                     or brand_name(brand_id))
    return {"price": price, "price_sub": price_sub, "area_line": area_line}


# ★否定文脈ガード（景表法）：設備語が「満車/厳禁/不可/なし」等と近接して現れる＝『無い/使えない』
#   →タグ/big_text/commentから除外（偽の可用性を作らない）。強マーカー=近接8字／弱マーカー(なし系)=近接3字。
#   ★『無』単体・『無料』は除外語に入れない（駐輪場無料をポジティブのまま残す）。
_FACT_NEG_STRONG = ("満車", "空きなし", "空き無し", "空無し", "厳禁", "禁止", "利用不可",
                    "使用不可", "利用できません", "使用できません", "ご遠慮", "不可",
                    "×", "✕", "✗", "✖")
_FACT_NEG_WEAK = ("なし", "無し", "ありません")   # ★近接必須（他設備の否定を誤爆しない・『無料』は含まない）


def fact_negated(key: str, text: str) -> bool:
    """key が text 中で否定文脈と近接して現れるか（景表法：偽の可用性を作らない）。
    強マーカー(満車/厳禁/禁止/不可/×等)=key出現の直前4字～直後8字／弱マーカー(なし/無し/ありません)=直後3字のみ。
    ★複数出現のいずれかが否定なら True（保守的＝一度でも『無い/使えない』記載があれば主張しない）。
    ★『駐輪場無料』は否定にしない（無料は_FACT_NEG_*に無い）。key空/未出現→False。"""
    key = str(key or "").strip()
    text = str(text or "")
    if not key or key not in text:
        return False
    start = 0
    while True:
        i = text.find(key, start)
        if i < 0:
            return False
        end = i + len(key)
        strong_win = text[max(0, i - 4): end + 8]         # 強マーカーは前後に広め
        weak_win = text[end: end + 3]                     # 弱マーカーは直後のみ（誤爆防止）
        if any(mk in strong_win for mk in _FACT_NEG_STRONG) or \
           any(mk in weak_win for mk in _FACT_NEG_WEAK):
            return True
        start = end


def drop_clauses_containing(text: str, words) -> tuple:
    """text から、words のいずれかを含む節（。/、/／/　区切り）を落とす。返り値 (clean, removed[])。
    ★節単位で落とす＝語だけ抜いて破片を残さない（fact_scrub と同じ思想）。
    ★v79-scrub-clause：否定設備ガード（旧 _drop_neg_clauses）と ban 除去（ban_scrub）が
      この1関数を共有する＝節の切り方を2箇所に書かない。"""
    if not text or not words:
        return text, []
    import re as _re
    parts = _re.split(r"([。、／　\n])", str(text))
    kept, removed, i = [], [], 0
    while i < len(parts):
        seg = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        hit = next((w for w in words if w and w in seg), None)
        if hit:
            removed.append(hit)
        else:
            kept.append(seg + sep)
        i += 2
    if not removed:
        return str(text), []          # ★1語も当たらなければ1文字も触らない（回帰ゼロの担保）
    # ★末尾の『。』は落とさない。『帰りたくなる、1LDK。』の句点は表紙・文末の意図的な演出で、
    #   旧 _drop_neg_clauses は無条件 strip("。") していた＝ban 経路をここへ寄せた時点で
    #   ヒットの有無に関わらず句点が消える回帰になっていた（自己テストで検出）。
    #   前後の整え方は fact_scrub と同一にそろえる（節除去の後始末を2通り持たない）。
    clean = re.sub(r"[、，]{2,}", "、", "".join(kept))
    return clean.strip("　 \n").lstrip("、，。．／").rstrip("、，　 "), removed


def ban_scrub(text, ban_words) -> tuple:
    """★v79-scrub-clause：ban語を『語ごと置換』でなく『節ごと除去』する。返り値 (clean, removed[])。
    ★経緯＝置換方式だと『安心の、オートロック。』→『の、オートロック。』のように助詞始まりの断片が残り、
      そのまま動画に焼かれていた（警告は出るが止まらない）。covercopy-v1 で数字の機械削除をやめて
      検出のみに切り替えたのと同じ型の沈黙破損。壊れた日本語を残すより、その節を落とす。
    ★全節が落ちて空になった場合の受け皿は既存仕様にある（comment 空＝ナレ無ビート／
      表紙コピーは案ごと不採用→feature_fallback）。
    ★除去は常に「多い側」に倒れる＝景表法ガードを緩める方向には決して動かない。"""
    return drop_clauses_containing(text, ban_words)


def _first_sentence(text: str) -> tuple:
    """★narr-fix系：comment を1文に（最初の句点『。』まで）。句点の後にまだ文があれば第1文のみ採用。
    返り値 (clean, truncated)。LDKの2文comment（『〜深呼吸。〜しようかな。』）の長文化・見切れを止める。
    ★句点が無い/末尾のみ＝1文＝そのまま（truncated=False）。"""
    s = str(text or "").strip()
    i = s.find("。")
    if 0 <= i < len(s) - 1:                 # 句点の後にまだ文字がある＝2文以上
        return s[:i + 1], True
    return s, False


# ★不自然表現ガード（谷合指定）：広さ訴求の『床が余る』系は日本語として不自然→自然な便益表現へ。長い順に置換。
_MAG_REWRITE = [("床が余っている", "余裕がある"), ("床が余ってる", "余裕がある"),
                ("床が余って", "余裕があって"), ("床が余る", "余裕がある"), ("床が余り", "余裕があり")]


def _rewrite_unnatural(text: str) -> tuple:
    """『床が余る』系→『余裕がある』系に置換（生成側の後処理ガード）。返り値 (clean, changed)。"""
    s = str(text or "")
    changed = False
    for a, b in _MAG_REWRITE:
        if a in s:
            s = s.replace(a, b)
            changed = True
    return s, changed


_BLD_TYPES = ("マンション", "アパート", "ハイツ", "コーポ", "レジデンス", "テラス", "ハウス", "ヴィラ")


def _safe_big_fallback(room: str, facts: dict) -> tuple:
    """★magfit-v79c④：big_textがfact_scrub等で空化した時の、方角非依存の安全な金色見出しフォールバック。
    構造/設備の事実だけで作る（方角/眺望を含まない＝再度fact_scrubを通しても消えない）。返り値 (big, accent)。空は返さない。
    外観=建物種別＋階数／トイレ=バストイレ別／玄関=シューズ／洗面=独立洗面台／その他=部屋名。"""
    r = str(room or "")
    _ft = str(facts.get("full_text", "")) + " " + str(facts.get("name", ""))
    _eq = facts.get("equipment")
    _eqt = ("／".join(_eq) if isinstance(_eq, list) else str(_eq or "")) + " " + _ft
    if "外観" in r:
        _bt = next((t for t in _BLD_TYPES if t in _ft), "")
        _m = re.search(r"(?:地上)?\s*(\d+)\s*階建", _ft)
        _fl = f"地上{_m.group(1)}階建て" if _m else ""
        if _bt and _fl:
            return f"{_bt}、{_fl}。", _fl
        if _bt:
            return f"{_bt}。", _bt
        if _fl:
            return f"{_fl}。", _fl
    if "トイレ" in r:
        if any(k in _eqt for k in ("バス・トイレ別", "バストイレ別", "独立")):
            return "独立したトイレ。", "独立したトイレ"
        return "トイレ。", "トイレ"
    if "玄関" in r:
        if "シューズ" in _eqt:
            return "シューズボックス完備。", "シューズボックス"
        return "玄関まわり。", "玄関まわり"
    if "洗面" in r:
        if "独立洗面台" in _eqt:
            return "独立洗面台。", "独立洗面台"
        return "洗面まわり。", "洗面まわり"
    return f"{r}。", r                          # 汎用：部屋名（絶対に空にしない）


# ★v79-6 DATA面：スペック表のカテゴリ語（否定でない実在設備のみ表示）。方角は明記時のみ「建物」行に含める。
_DATA_DIR_RE = re.compile(r"(南東|南西|北東|北西|南向き|北向き|東向き|西向き|南面|北面|東面|西面)")
_DATA_SEC_KW = ("オートロック", "モニター付インターホン", "カメラ付きインターホン", "TVモニター付インターホン",
                "防犯カメラ", "宅配ボックス", "ディンプルキー")
_DATA_WATER_KW = ("バス・トイレ別", "バストイレ別", "独立洗面台", "追焚", "浴室乾燥", "温水洗浄便座",
                  "ウォシュレット", "室内洗濯機置場", "洗面化粧台")
_DATA_STORE_KW = ("ウォークインクローゼット", "クローゼット", "シューズボックス", "納戸", "床下収納", "収納")


def build_data_rows(facts: dict) -> list:
    """★v79-6 DATA面のスペック表行を facts から組む。返り値 [(label, value)]。
    ★取れない行は省略（空欄/ダミー/推測なし）・方角はマイソク明記時のみ「建物」行・否定facts(満車等)は除外・生値寸法(10x6)はstrip。"""
    import re as _re
    _ft = str(facts.get("full_text", ""))
    _eq = facts.get("equipment")
    _eqt = ("／".join(_eq) if isinstance(_eq, list) else str(_eq or "")) + " " + _ft

    def _strip_dim(s):   # 生寸法トークン（10x6 等）を落とす（app._strip_raw_dim と同仕様・core内複製）
        return _re.sub(r"\s*\d+(?:\.\d+)?\s*[xX×✕]\s*\d+(?:\.\d+)?\s*", " ", str(s or "")).strip()

    def _present(kws):   # 否定でない実在設備だけ（重複語は長い方優先）
        out = []
        for k in kws:
            if k in _eqt and not fact_negated(k, _eqt) and not any(k in o or o in k for o in out):
                out.append(k)
        return out

    rows = []
    _mad = _strip_dim((facts.get("madori", "") or "").split("[")[0].strip())
    _ar = _strip_dim((facts.get("area", "") or "").strip())
    _v = " ／ ".join(x for x in (_mad, _ar) if x)
    if _v:
        rows.append(("間取り", _v))
    # 建物：種別／階建／築年／方角（★方角は _ft に明記があるときだけ）
    _bt = next((t for t in _BLD_TYPES if t in _ft), "")
    _m = _re.search(r"(?:地上)?\s*(\d+)\s*階建", _ft)
    _fl = f"地上{_m.group(1)}階建て" if _m else ""
    _blt = _strip_dim((facts.get("built", "") or "").strip())
    _dm = _DATA_DIR_RE.search(_ft)
    _dir = _dm.group(1) if _dm else ""      # ★明記時のみ（fact_scrub方針＝真偽が取れない方角は出さない と整合）
    _v = " ／ ".join(x for x in (_bt, _fl, _blt, _dir) if x)
    if _v:
        rows.append(("建物", _v))
    _acc = facts.get("access")
    _av = _strip_dim((_acc[0] if isinstance(_acc, list) and _acc else str(_acc or "")).strip())
    if _av:
        rows.append(("交通", _av))
    _rent = (facts.get("rent", "") or "").strip()
    _fee = (facts.get("fee", "") or "").strip()
    if _rent:
        rows.append(("賃料", _rent + (f"（＋管理費 {_fee}）" if _fee else "")))
    # ★sale-v1：売買の金額行。従来は price/shuzen を読む分岐が無く、DATA面に金額が1行も出なかった。
    #   ★_price があるときだけ（＝売買）追加する。賃貸は上の『賃料』行に管理費を括弧書きする従来仕様のまま
    #     ＝管理費が二重に出ない。取れない行は従来どおり省略。
    _price = (facts.get("price", "") or "").strip()
    _shuzen = (facts.get("shuzen", "") or "").strip()
    if _price:
        rows.append(("価格", _price))
        if _fee:
            rows.append(("管理費", _fee + "/月"))
        if _shuzen:
            rows.append(("修繕積立金", _shuzen + "/月"))
    # 初期費用（敷金・礼金・保証金 が full_text に明記のときだけ）
    _init = []
    for _lbl, _key in (("敷金", "敷金"), ("礼金", "礼金"), ("保証金", "保証金")):
        _mm = _re.search(_key + r"[：:\s]*([0-9０-９]+(?:[.．]\d+)?\s*(?:ヶ月|カ月|か月|万円|円|なし|無))", _ft)
        if _mm:
            _init.append(f"{_lbl}{_mm.group(1)}")
    if _init:
        rows.append(("初期費用", " ／ ".join(_init)))
    # 契約（定期借家/普通借家/保証会社 が明記のときだけ・上位語優先で重複排除）
    _ct = []
    for k in ("定期借家", "普通借家", "保証会社利用必須", "保証会社"):
        if k in _ft and not any(k in c or c in k for c in _ct):
            _ct.append(k)
    if _ct:
        rows.append(("契約", " ／ ".join(_ct)))
    for _lbl, _kw in (("セキュリティ", _DATA_SEC_KW), ("水まわり", _DATA_WATER_KW), ("収納・他", _DATA_STORE_KW)):
        _p = _present(_kw)
        if _p:
            rows.append((_lbl, "・".join(_p)))
    return rows


# ★v79-note：AI生成イメージの明示（景表法）。表紙・ビート面・DATA面が同じ文言を使うための1源。
#   ここを直せば3面すべてが変わる（面ごとに書き分けない＝文言のドリフトを構造的に防ぐ）。
AI_IMAGE_NOTE = "※家具・小物はAI生成のイメージ"


def ai_note_line(facts: dict = None, gen_date_str: str = "") -> str:
    """★v79-note：表紙・ビート面に焼く1行注記。『AI生成イメージ』＋『◯年◯月時点の情報』。
    年月は data_note_date と同じ規則（マイソク記載を優先→無ければ呼出側の生成日）＝3面で年月がずれない。"""
    ym = data_note_date(facts or {}, gen_date_str)
    return AI_IMAGE_NOTE + (f"　※{ym}時点の情報" if ym else "")


def data_note_date(facts: dict, gen_date_str: str = "") -> str:
    """★v79-6 注記の年月：マイソクに情報日付があればそれ、無ければ生成日（呼出側が渡す）。景表法『現況優先』併記前提。
    full_text から『YYYY年M月』or『YYYY/M』を拾う（情報日付/更新日 近傍優先）。無ければ gen_date_str。
    ★sale-v1 で修正: 近傍ラベルが無いときの緩い拾い方が full_text 内の『どの年月でも』拾っていたため、
      売買マイソクの築年月（例 1982年6月）を情報時点として焼いていた（実測: 表紙注記が
      『※1982年6月時点の情報』になる）。注記は景表法の表示なので、誤った年月を騙るくらいなら生成日に倒す。
      対策2つ = ①築系ラベル（築年月/築/建築/竣工/新築）の近傍にある年月は情報日付として採らない
      ②生成年より古すぎる年（前年より前）は採らない（情報時点は直近のはず）。
      ★情報日付/更新日 が明記されている経路（_near）は従来どおり最優先で不変。"""
    import re as _re
    _ft = str(facts.get("full_text", ""))
    _near = _re.search(r"(?:情報日付|情報登録日|更新日|作成日)[^\d]{0,6}(\d{4})[年/\-.](\d{1,2})", _ft)
    if _near:
        return f"{_near.group(1)}年{int(_near.group(2))}月"
    _gen_y = 0
    _gm = _re.search(r"(\d{4})", str(gen_date_str or ""))
    if _gm:
        _gen_y = int(_gm.group(1))
    for _m in _re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月", _ft):
        _before = _ft[max(0, _m.start() - 8):_m.start()]
        if _re.search(r"築|建築|竣工|新築", _before):
            continue                       # ★築年月＝情報時点ではない
        if _gen_y and int(_m.group(1)) < _gen_y - 1:
            continue                       # ★古すぎる年は情報時点として採らない
        return f"{_m.group(1)}年{int(_m.group(2))}月"
    return gen_date_str


def _kana_ok(kana: str, comment: str) -> bool:
    """★narr-fix-d：narration_kana（commentの全ひらがな読み）を採用してよいか。
    ①非空 ②ほぼ仮名（漢字が全体の1割以下＝読みになっている）③commentと長さが乖離しない（幻覚/欠落でない）。
    ★呼び側でさらに『commentが後処理で改変されていない』ことも条件にする（stale kana=画面と別内容を読む事故を防ぐ）。"""
    k = str(kana or "").strip()
    c = str(comment or "").strip()
    if not k or not c:
        return False
    kanji = len(re.findall(r"[一-鿿]", k))
    if kanji > max(1, len(k) // 10):                 # 漢字が1割超＝読みに変換できていない
        return False
    if not (0.6 * len(c) <= len(k) <= 4.0 * len(c)):  # 長さ乖離＝幻覚（増やした）/欠落（縮めた）
        return False
    return True


def _kana_reject_reason(kana: str, comment: str, cmt_clean: bool) -> str:
    """★narr-fix診断：narration_kana を不採用にすべき理由（採用可なら空文字）。どの条件で弾かれたかを可視化＝
    『kanaが弾かれる条件』の切り分け用。空kana（Gemini未出力）は呼び側で別途『未出力』警告する（ここでは空扱い）。"""
    k = str(kana or "").strip()
    c = str(comment or "").strip()
    if not k or not c:
        return ""
    if not cmt_clean:
        return "comment改変(事実外/ban/否定/1文化)でstale"
    kanji = len(re.findall(r"[一-鿿]", k))
    if kanji > max(1, len(k) // 10):
        return f"漢字残存{kanji}字(Geminiが読みに変換していない)"
    if not (0.6 * len(c) <= len(k) <= 4.0 * len(c)):
        return f"長さ乖離(comment{len(c)}字/kana{len(k)}字)"
    return ""


def magtext(client, beats, facts, feature_id, budget_sec=33, model="gemini-2.5-flash",
            brand_id=DEFAULT_BRAND) -> dict:
    """★v79-5 magtext：動く雑誌の文字面を『1回のGeminiコール』で生成（A-unit刷新・story_narration同型）。
    beats=[{room, stock}]（部屋順・🔀後）。返り値:
      {beats:[{room_label,big_text,accent_word,comment,narration_text,narration_kana,tags,over_tags,needs_review}],
       cover:{area_line,price,price_sub,hook_candidates:[str],hook,hook_source,needs_review},
       data_rows:[str], warnings:[str], prompt, raw}
    ★big_text/comment=facts由来（数字は映るカットで＝面積/角部屋はLDK・帖数は各室）。room_facts_map の focal_ja/facts_keys を使う。
    ★covercopy-v1：cover は『この物件のための表紙コピー3案』を自由生成（hook_candidates）。採用は人（app側3案ラジオ）で、
      自動採用はしない＝型承認ゲートは維持。全案に fact_scrub/ban/_scrub_cover_copy/needs_review を通す。
      全案がガードで落ちたら feature.cover_hooks[0] へフォールバックし hook_source='feature_fallback' を立てる。
    ★few_shot 2本＋抽出規則は mote_heya の comment 規則として使う（削除しない）。後処理: fact_scrub/ban/needs_review/タグ最大3差分。
    ★narr-fix-a：narration_text = comment のみ（big_textは読まない＝特大文字は視聴者が読む・声はコメントを添えるだけ）。comment空＝ナレ無。"""
    import json as _json
    beats = [b for b in (beats or []) if b.get("room")]
    if not beats or client is None:
        return None
    feat = feature_of(feature_id) or {}
    hooks = feat.get("cover_hooks") or []
    _equip = facts.get("equipment")
    _equip_text = "／".join(_equip) if isinstance(_equip, list) else str(_equip or "")
    _feat_text = str(facts.get("features", "")) + " " + str(facts.get("full_text", ""))
    warnings = []                                      # ★否定ガード等の警告を早期から溜める
    _src_neg = _equip_text + " " + _feat_text          # ★否定文脈照合用（full_text＝マイソク原文を含む）
    # ★否定文脈ガード（景表法）：設備語が満車/厳禁/不可/なし等と近接＝『無い/使えない』→タグ・文面から除外。
    #   候補語＝全room_facts_mapのfacts_keys ∪ equipment項目。除外語は magtext 全経路で使わない。
    _amenities = set()
    for _rm in ROOM_FACTS_MAP.values():
        _amenities.update(_rm.get("facts_keys", []))
    if isinstance(_equip, list):
        _amenities.update(_equip)
    _negated = sorted(w for w in _amenities if w and fact_negated(w, _src_neg))
    for w in _negated:
        warnings.append(f"『{w}』は満車/不可/なし等の否定記載があるため除外しました（景表法）。")
    # 各beat：room_facts_map の focal_ja と、facts に実在し否定でない facts_keys（この部屋に映る設備）
    _binfo = []
    for b in beats:
        m = room_facts_map(b["room"])
        present = [k for k in m["facts_keys"]
                   if (k in _equip_text or k in _feat_text) and k not in _negated]
        _binfo.append({"room": b["room"], "focal_ja": m["focal_ja"], "facts_keys": present})
    facts_json = _json.dumps({k: v for k, v in (facts or {}).items()
                              if k in ("madori", "area", "rent", "fee", "access", "equipment",
                                       "features", "full_text")}, ensure_ascii=False)
    _mote = feature_id == "mote_heya"
    instr = (
        # ★brand-v1：誌名を直書きしない（生成トーンがブランドに引きずられるため1源から差し込む）。
        f"あなたは不動産SNS『動く雑誌 {brand_name(brand_id)}』の編集者です。各ビートの画面文字を作ります。\n"
        f"■ 特集：{feat.get('label','')}（トーン：{feat.get('comment_tone','')}）\n\n"
        "■ 必ず守る（景表法・雑誌トーン）：\n"
        "・big_text＝そのビートに映る事実のみ（例『リビング10帖、角部屋。』）。★数字は映っているカットでだけ言う"
        "（面積㎡・角部屋はLDK／帖数は各部屋）。キッチンで面積を言わない。\n"
        "・accent_word＝big_text 中のキーワード1語だけ（色を変える対象。例『角部屋』）。big_textに必ず含まれる語。\n"
        "・comment＝facts由来の編集コメント。★全角24字以内・1文（句点『。』は1つまで・2文にしない）・現在形。"
        "★『床が余る』のような不自然な表現は使わない。広さは『余裕がある』『ゆったり置ける』『空間にゆとり』等の自然な便益で。"
        "factsに無い要素の断定は禁止。\n"
        "・narration_kana＝comment を耳で聞いて正しい『全ひらがなの読み』（漢字は文脈に沿った読み・"
        "★助詞の『は』は『わ』、『へ』は『え』と発音どおりに書く・"
        "英字/数字/単位もひらがな読みに展開（例 LDK→えるでぃーけー・36㎡→さんじゅうろくへいべい）・"
        "句読点は残す・commentに無い言葉を足さない）。"
        "例 comment『このLDKは、一日の疲れを流す場所。』→ narration_kana『このえるでぃーけーわ、いちにちのつかれをながすばしょ。』\n"
        "・行末の形を揃えない。眺望/方角/日当たり/静けさ/周辺環境は書かない（facts明示が無ければ）。\n"
        "・★角部屋・最上階などの位置は、物件事実に明記があるときだけ言う。物件名・誇大語・最上級は使わない。\n"
        + (f"・★次の設備・条件は『満車/不可/なし/厳禁』等の否定記載があるため、"
           f"『ある』『使える』かのように書かない（景表法）：{_json.dumps(_negated, ensure_ascii=False)}\n"
           if _negated else "")
        + ("・comment のトーンは下の承認例2本に合わせる（現在形・照れは話し方に・物と時間で示す・説明しない）。\n"
           f"{_STORY_FEWSHOT}\n" if _mote else "")
        + "■ 表紙（cover）：この物件のための表紙コピーを3案つくる（hook_candidates）。\n"
        "・全角14字以内・1文。読点『、』を1つ入れて2行に割れる形が望ましい（例『帰りたくなる、1LDK。』）。\n"
        "・facts に実在する要素だけを根拠にする。眺望/方角/日当たり/静けさ/周辺環境は facts に明記が無ければ書かない。\n"
        "・物件名・建物名・最上級（最高/絶対/唯一/破格/激安/希少）・『モテ』等の内部語は使わない。"
        "数値の主張（面積・賃料・徒歩分）も書かない（間取り表記『1LDK』のような型は可）。\n"
        "・3案は互いに違う切り口にする（例：帰宅の情景／間取りの使い方／時間帯の情景）。\n"
        f"・特集トーン（{feat.get('comment_tone','')}）に合わせる。参考までに特集の既定コピー："
        f"{_json.dumps(hooks, ensure_ascii=False)}（そのまま流用せず、この物件のために書く）\n\n"
        "■ ビート構成（この順・部屋名・主語focal・その部屋に映る設備facts）：\n"
        f"{_json.dumps(_binfo, ensure_ascii=False)}\n\n"
        f"■ 物件事実（この数字・属性だけ使う）：{facts_json}\n\n"
        "出力はJSONのみ（説明なし）。形式：\n"
        '{"cover":{"hook_candidates":["…","…","…"]},'
        '"beats":[{"room":"…","big_text":"…","accent_word":"…","comment":"…","narration_kana":"…"}]}'
    )
    cover_out, beats_out, raw = {}, [], ""   # ★warnings は上で初期化済み（否定ガード警告を保持）
    parsed = None
    for _try in range(2):   # ★malformed JSON は1回リトライ（story_narration同型）
        try:
            resp = client.models.generate_content(model=model, contents=[instr])
            raw = getattr(resp, "text", "") or ""
            m = re.search(r"\{.*\}", raw, re.S)
            parsed = _json.loads(m.group(0)) if m else None
            if isinstance(parsed, dict):
                break
        except Exception as e:  # noqa: BLE001
            if _try == 1:
                warnings.append(f"文字面の生成に失敗（{type(e).__name__}）。")
    parsed = parsed if isinstance(parsed, dict) else {}
    _ban = _story_ban_words(feature_id)

    def _clean(text):
        """fact_scrub＋ban除去。返り値 (clean, removed[], banned[])。
        ★v79-scrub-clause：ban は語の置換でなく ban_scrub（節ごと除去）。
          置換だと『安心の、オートロック。』→『の、オートロック。』の断片が動画に焼かれていた。"""
        t, rm = fact_scrub(text or "", facts)
        t, bn = ban_scrub(t, _ban)
        return t.strip(), rm, bn
    _by = {}
    for o in (parsed.get("beats") or []):
        if isinstance(o, dict) and o.get("room"):
            _by.setdefault(str(o["room"]).strip(), o)
    for i, b in enumerate(beats):
        room = b["room"]
        o = _by.get(room) or ((parsed.get("beats") or [])[i]
                              if i < len(parsed.get("beats") or []) else {}) or {}
        _big_raw = str(o.get("big_text", "")).strip()   # ★空化検知用（後処理でbig_textがまるごと消える＝見出し欠落）
        big, _rm1, _bn1 = _clean(str(o.get("big_text", "")))
        cmt, _rm2, _bn2 = _clean(str(o.get("comment", "")))
        # ★否定文脈ガード：AIが否定設備を『ある』かのように書いた節を落とす（景表法・タグと同じ_negated基準）。
        big, _ng1 = drop_clauses_containing(big, _negated)
        cmt, _ng2 = drop_clauses_containing(cmt, _negated)
        # ★comment は1文に（2文comment暴走→見切れ防止・描画側は2行折返し）。narration_kanaはstale化するので後で不採用。
        cmt, _trunc = _first_sentence(cmt)
        if _trunc:
            warnings.append(f"{room}: commentが2文以上→第1文のみ採用（見切れ防止）。")
        # ★不自然表現ガード：『床が余る』系→自然な便益表現（big_text/comment 両方）。空返し防御（変換で消さない）。
        _bb, _rw1 = _rewrite_unnatural(big)
        big = _bb if _bb.strip() else big              # 万一空になったら元を維持（テキスト欠落を作らない）
        _cc, _rw2 = _rewrite_unnatural(cmt)
        cmt = _cc if (_cc.strip() or not cmt.strip()) else cmt
        if _rw1 or _rw2:
            warnings.append(f"{room}: 不自然表現『床が余る』系→自然表現に修正。")
        # ★magfit-v79c④：big_text が後処理でまるごと空化＝金色見出し欠落→方角非依存の安全フォールバックで復活（空のまま出さない）。
        #   原因は⚠️警告で可視化継続。accentはフォールバックの語を使う（色分けの前提＝big内に含む）。
        _acc_override = None
        if not big.strip():
            _fb, _fbacc = _safe_big_fallback(room, facts)
            big, _acc_override = _fb, _fbacc
            if _big_raw:
                warnings.append(f"⚠️ {room}: big_text（見出し）が後処理で空化→安全フォールバック『{big}』"
                                f"（方角非依存・元『{_big_raw}』＝事実外/否定/禁止で全節除去の可能性）。")
            else:
                warnings.append(f"{room}: big_text未生成→安全フォールバック『{big}』。")
        acc = _acc_override if _acc_override else str(o.get("accent_word", "")).strip()
        if acc and acc not in big:            # accent_word は big_text に含まれる語のみ（色分けの前提）
            acc = ""
        nr = sorted(set(needs_review(big) + needs_review(cmt)))
        for r in set(_rm1 + _rm2):
            warnings.append(f"{room}: 事実外属性『{r}』を除去。")
        for w in set(_bn1 + _bn2):
            warnings.append(f"{room}: 禁止語『{w}』を除去。")
        for w in set(_ng1 + _ng2):
            warnings.append(f"{room}: 否定記載の設備『{w}』を文面から除去（景表法）。")
        # ★narr-fix-d：narration_kana（commentの全ひらがな読み・Geminiの文脈読み）。TTSはこれを読む＝誤読クラス根絶。
        #   採用条件＝ガード3件＋『commentが後処理で改変されていない』（改変時はkanaがstale＝画面と別内容を読む→不採用）。
        #   不採用時は空にして run側が normalize_reading(comment)＝辞書経路へフォールバック（黙らず警告）。
        _kana = str(o.get("narration_kana", "")).strip()
        _cmt_clean = not (_rm2 or _bn2 or _ng2 or _trunc or _rw2)   # commentが後処理で削られ/短縮/書換されていない
        if cmt and not _kana:                               # ★診断：Geminiがkanaを出力しなかった（②の候補）
            warnings.append(f"{room}: 🈚 Geminiがnarration_kana未出力→辞書読み。")
        elif _kana:
            _kreason = _kana_reject_reason(_kana, cmt, _cmt_clean)
            if _kreason:                                    # ★診断：どの条件で弾かれたかを明示（③の候補）
                warnings.append(f"{room}: 🈚 読み仮名不採用（{_kreason}）→辞書読み。")
                _kana = ""
        # ★タグ：room_facts_map の facts_keys で facts に実在し否定でないもの・最大3・超過は over_tags（DATAへ）
        m = room_facts_map(room)
        _present = [k for k in m["facts_keys"]
                    if (k in _equip_text or k in _feat_text) and k not in _negated]
        _seen, _tags = set(), []
        for k in _present:
            if not any(k in t or t in k for t in _tags):
                _tags.append(k)
        beats_out.append({
            "room_label": room, "big_text": big, "accent_word": acc, "comment": cmt,
            # ★narr-fix-a：ナレは comment のみ読む（big_textは特大文字で視聴者が読む・声はコメントを添えるだけ）。
            #   発話量が半減し音声被りの主因が消える。comment空＝narration_text空＝そのビートはナレ無（無音＋BGM）。
            "narration_text": cmt,
            # ★narr-fix-d：narration_kana＝TTSが読む全ひらがな読み（空＝辞書フォールバック）。narration_textは表示/参照用。
            "narration_kana": _kana,
            "tags": _tags[:3], "over_tags": _tags[3:], "needs_review": nr})
    # ★covercopy-v1 cover：この物件のための表紙コピーを3案生成し、全案に機械ガードを通す。
    #   採用は人（app側の3案ラジオ）＝自動採用しない。ガード＝fact_scrub/ban（_clean）→物件名/字数（_scrub_cover_copy）
    #   →needs_review。空になった案は落とし、全滅したら特集既定へフォールバック（表紙コピーを空にしない）。
    _c = parsed.get("cover") or {}
    _raw_cands = _c.get("hook_candidates")
    _raw_cands = _raw_cands if isinstance(_raw_cands, list) else []
    # ★needs_review は『どの案が』引っかかったかを案ごとに保持する（needs_review_by_hook）。
    #   全案ぶんを1本のリストに混ぜると、選ぼうとしている案が安全かをUIで判断できず、
    #   「数値主張は人が弾く」という設計の前提そのものが崩れる（＝ゲートが機能する条件）。
    _cands, _nr_map, _seen_h = [], {}, set()
    for _h in _raw_cands[:5]:                          # 3案想定・多く返っても5案までしか見ない
        _t0 = str(_h or "").strip()
        if not _t0:
            continue
        _t, _rmh, _bnh = _clean(_t0)                   # 事実外属性の節除去＋ban（ビート面と同一レイヤー）
        # ★feat-ban-1：特集の ban も通す（②の安心/安全/防犯 等が表紙コピーに素通りしていた穴を塞ぐ）
        _t, _wsh = _scrub_cover_copy(_t, facts, feature=feature_id)
        _reasons = []
        for r in set(_rmh):
            warnings.append(f"表紙コピー『{_t0}』: 事実外属性『{r}』を除去。")
        for w in set(_bnh):
            warnings.append(f"表紙コピー『{_t0}』: 禁止語『{w}』を除去。")
        for w in _wsh:
            warnings.append(f"表紙コピー『{_t0}』: {w}。")
            if "数字" in w:                            # 数値主張は消さず人力確認へ回す（沈黙破損を作らない）
                _reasons.append("間取り表記以外の数字（数値主張）")
        if not _t:
            warnings.append(f"表紙コピー『{_t0}』: ガードで全部消えたため不採用。")
            continue                                   # ★落とした案の理由は needs_review に残さない（選べない案なので）
        # ★feat-ban-1：ban は語を置換して消すため、途中の語だけ抜けると『の、オートロック。』のような
        #   壊れた断片が「選べる案」として残る（②の安心/安全/防犯 を通したことで実際に発生した）。
        #   ガードは通ったが人が選べる文になっていない＝案ごと落とす。既存の『全滅したら特集既定へ
        #   フォールバック』が受け皿になるので、表紙コピーが空になることはない。
        if _bnh:
            warnings.append(f"表紙コピー『{_t0}』: 禁止語の除去で文が壊れるため案ごと不採用。")
            continue
        if _t in _seen_h:
            continue
        _seen_h.add(_t)
        _cands.append(_t)
        _reasons += needs_review(_t)                   # 巻き添え語（SNS口語/希少性演出等）＝止めずに人へ回す
        if _reasons:
            _nr_map[_t] = sorted(set(_reasons))
    _hook_source = "ai"
    if not _cands:                                     # ★全滅＝黙って既定に落ちない（app側で警告表示）
        _cands = [hooks[0]] if hooks else [""]
        _hook_source = "feature_fallback"
        warnings.append("⚠️ 表紙コピーを生成できない／全案がガードで空になったため、特集の既定コピーを使用します。")
    cover_out = {**_mag_price_fields(facts, brand_id), "hook_candidates": _cands, "hook": _cands[0],
                 "hook_source": _hook_source,
                 # ★案ごとの理由（UIがどの案に⚠️を付けるかの情報源）。{コピー本文: [理由,…]}
                 "needs_review_by_hook": _nr_map,
                 # 平坦版は互換用（📖メッセージの⚠️要確認サマリー等）。どの案かが分かる形で並べる。
                 "needs_review": sorted(f"『{_h}』{_r}" for _h, _rs in _nr_map.items() for _r in _rs)}
    # data_rows：どのビートにも割り当てられなかった設備（差分方式・over_tags含む）＝DATAビート素材（描画はv79-6）
    _used = set()
    for bo in beats_out:
        _used.update(bo["tags"])
    _all_present = []
    for b in beats:
        for k in room_facts_map(b["room"])["facts_keys"]:
            if (k in _equip_text or k in _feat_text) and k not in _all_present:
                _all_present.append(k)
    data_rows = [k for k in _all_present if k not in _used]
    return {"beats": beats_out, "cover": cover_out, "data_rows": data_rows,
            "warnings": sorted(set(warnings)), "prompt": instr, "raw": raw}


def draft_narration(client, facts: dict, scene_labels, dur_sec=5,
                    model="gemini-2.5-flash") -> dict:
    """各シーン1文のナレ原稿を生成。★字数上限を各行にハード適用（超過は打ち切り＝尺に収まる保証）。
    構成: 先頭=フック（賃料等の数字を含む・ban語なし）／中間=各部屋の一言／末尾=CTA。
    トーン: 低い声の男性ナレーター向け・言い切り・短文・煽らない（モテ部屋トーン）。
    返り値: {lines:[str], limit:int, warnings:[str]}。scene_labels 空/事実皆無なら None。"""
    import json as _json
    labels = [str(x or "").strip() for x in (scene_labels or [])]
    if not labels:
        return None
    limit = narration_char_limit(dur_sec)
    rent = (facts.get("rent") or "").strip()
    price = (facts.get("price") or "").strip()
    madori = (facts.get("madori") or "").split("[")[0].strip()
    money = f"家賃{rent}" if rent else (f"価格{price}" if price else "")
    n = len(labels)
    facts_json = _json.dumps({k: v for k, v in facts.items()
                              if k in ("madori", "area", "rent", "price", "fee", "equipment")},
                             ensure_ascii=False)
    instr = (
        f"あなたは不動産ルームツアー動画の男性ナレーター（低い声・落ち着き・言い切り・煽らない）です。\n"
        f"{n}シーンぶんのナレを、各シーン1文・**各{limit}字以内**で作ってください（厳守）。\n"
        "・先頭シーンはフック（可能なら家賃/価格の数字を1つ入れる・誇大語や最上級は使わない）。\n"
        "・中間シーンは各部屋の魅力を体言止め/言い切りで一言。\n"
        f"・末尾シーンはCTA（例『気になったら、コメントで。』）。\n"
        "・敬語は最小限、短く。数字は事実のみ。絵文字・記号・改行を使わない。\n"
        f"出力はJSON配列のみ（説明なし・要素数={n}）：[\"…\",\"…\",…]\n"
        f"シーン構成（順番・部屋名）：{_json.dumps(labels, ensure_ascii=False)}\n"
        f"物件事実：{facts_json}\n参考の数字：{money or '（数字なし）'}"
    )
    warnings = []
    lines = []
    try:
        resp = client.models.generate_content(model=model, contents=[instr])
        txt = getattr(resp, "text", "") or ""
        m = re.search(r"\[.*\]", txt, re.S)          # JSON配列を抽出（_first_json_objectは{}専用）
        arr = _json.loads(m.group(0)) if m else []
        if isinstance(arr, list):
            lines = [str(x) for x in arr]
    except Exception as e:  # noqa: BLE001  握り潰さず記録（簡易テンプレで続行）
        warnings.append(f"AIナレ原稿の生成に失敗（{type(e).__name__}）。簡易テンプレで出力します。")

    # 行数を n に整える（不足はテンプレ補完・過剰は切り詰め）
    def _fallback(i):
        if i == 0:
            return (money + "、この立地。") if money else "この部屋、見てほしい。"
        if i == n - 1:
            return "気になったら、コメントで。"
        return f"{labels[i]}、ここが効く。"
    out = []
    for i in range(n):
        raw = lines[i] if i < len(lines) and str(lines[i]).strip() else _fallback(i)
        clipped = _narr_clip(raw, limit)
        if len(re.sub(r"\s+", "", str(raw))) > limit:
            warnings.append(f"シーン{i+1}: {limit}字超のため打ち切り。")
        out.append(clipped or _narr_clip(_fallback(i), limit))
    return {"lines": out, "limit": limit, "warnings": sorted(set(warnings))}


# ── ナレーション読み正規化（narsync-v70a）─────────────────────────────────────
# 日本語TTS対策：英字/記号を確定辞書でカタカナ・和数へ。AIに読みを推測させない（誤読防止）。
# ★テロップ側は変換しない（目向け表記維持）。ナレ欄の自動下書き/整え時のみ適用。
def _num_to_wa(n) -> str:
    """整数を和数読み（万・千）へ。79000→7万9千 / 8000→8千 / 120000→12万 / 88000→8万8千。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    man, rem = divmod(n, 10000)
    sen, rem2 = divmod(rem, 1000)
    parts = []
    if man:
        parts.append(f"{man}万")
    if sen:
        parts.append(f"{sen}千")
    if rem2:
        parts.append(f"{rem2}")            # 百以下の端数はそのまま（稀）
    return "".join(parts) or "0"


# 読み変換テーブル（トークン→読み）。★単一正規表現の交替＋長い順で「最長一致」を保証する枠組み。
#   単純 str.replace の順次適用は『光』→『ひかり』が『光熱費』を壊す事故を起こすため採用しない。
#   value が自分自身＝『守る複合語』（単独語への誤変換を防ぐ）。今後の語追加もこの表に足すだけ。
_READ_TABLE = {
    # ── 守る複合語（identity）：単独『光』などへの誤変換を機械的に防ぐ ──
    "光熱費": "光熱費", "観光": "観光", "日光": "日光", "光ファイバー": "光ファイバー",
    "光回線": "光回線", "蛍光灯": "蛍光灯", "蛍光": "蛍光", "採光": "採光", "陽光": "陽光",
    "手帖": "手帖",
    # ── 間取り（長い順）──
    "4LDK": "フォーエルディーケー", "3LDK": "スリーエルディーケー",
    "2LDK": "ツーエルディーケー", "1LDK": "ワンエルディーケー",
    "2DK": "ツーディーケー", "1DK": "ワンディーケー",
    "LDK": "エルディーケー", "DK": "ディーケー", "1K": "ワンケー", "1R": "ワンルーム",
    # ── 略語・単位 ──
    "WIC": "ウォークインクローゼット", "SRC": "エスアールシー", "SOHO": "ソーホー",
    "RC": "アールシー", "JR": "ジェイアール",
    "㎡": "平米", "m²": "平米", "m2": "平米",
    # ── TTS誤読補正（複数文字＝単体誤変換の罠なし。貢献『献』単体/創業『創』(る無し)は無傷）──
    "献立": "こんだて", "創る": "つくる",
    # ── 単独語（複合語を上で守った後にだけ効く）──
    "快適": "かいてき", "光": "ひかり",
    # ── 記号 ──
    "＋": "、プラス", "+": "、プラス",
}
# ── ★narr-fix-c：ふりがな辞書（誤読補正）を外部JSONから読み込みマージ（データ駆動＝コード変更なしで追記可能）──
#   reading_dict.json = {表記: 読み}。ElevenLabsの誤読を1行足すだけで直せる。無い/壊れ→空（既存表で継続・止めない）。
#   __で始まるキーは注記としてスキップ。最長一致の枠組み（_READ_RE）は下で全キーから再構築＝JSON語も守られる。
def _load_reading_dict() -> dict:
    """reading_dict.json（core.pyと同ディレクトリ）から {表記:読み} を読む。失敗時は空dict（フェイルセーフ）。"""
    import json as _json
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "reading_dict.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        return {str(k): str(v) for k, v in d.items()
                if k and v and not str(k).startswith("__")}
    except Exception:  # noqa: BLE001  辞書が無い/壊れても読み正規化自体は止めない
        return {}


_READ_TABLE.update(_load_reading_dict())   # ★外部ふりがな辞書を既存表にマージ（同キーはJSON優先＝上書きで修正可能）
# 長い順に並べた単一正規表現＝各位置で長い候補を先に試す＝最長一致。
_READ_RE = re.compile("|".join(re.escape(k) for k in
                                sorted(_READ_TABLE.keys(), key=len, reverse=True)))


def normalize_reading(text: str) -> str:
    """ナレ読み正規化。英字・記号・数字絡みを確定的にカタカナ/和数へ（1LDK→ワンエルディーケー・
    ㎡→平米・¥79,000/79,000円→7万9千円・(数字)帖→じょう・単独『光』→ひかり）。
    ★最長一致の枠組みで『光熱費→ひかり熱費』『手帖→手じょう』等の事故を機械的に防ぐ。AI非依存。"""
    s = str(text or "")

    def _yen(m):
        return _num_to_wa(m.group(1).replace(",", "")) + "円"
    s = re.sub(r"¥\s*([\d,]+)", _yen, s)          # ¥79,000 → 7万9千円
    s = re.sub(r"([\d,]+)\s*円", _yen, s)          # 79,000円 → 7万9千円
    s = re.sub(r"(\d+)\s*帖", r"\1じょう", s)       # ★数字+帖のみ（手帖=てちょうを壊さない）
    # ★笑いの w/ｗ（連続可）を音声から除去（字幕には残す＝別経路・story-v78）。HIROが「ダブリュー」と読むのを防ぐ。
    #   直前が日本語(非ASCII)かつ直後が句読点/空白/行末のときだけ＝URL(www.)や英単語内wを壊さない（最長一致の規律）。
    s = re.sub(r"(?<=[^\x00-\x7f])[wｗ]+(?=[。、，！？!?\s]|$)", "", s)
    s = _READ_RE.sub(lambda m: _READ_TABLE[m.group(0)], s)   # 辞書：最長一致で置換
    return s


def narration_has_ascii(text: str) -> bool:
    """TTS直前の安全網：読み上げに不向きなASCII英字が残っているか（自動変換はしない・警告用）。"""
    return bool(re.search(r"[A-Za-z]", str(text or "")))


def polish_narration(client, text: str, dur_sec=5, facts: dict = None,
                     model="gemini-2.5-flash", feature: str = "normal") -> dict:
    """テロップ（目向け・体言止め）を耳向けの口語ナレへ整える。
    ★字数上限（正規化後）にハード収束・読み正規化・ban語/物件名/モテ除去。返り値 {text, limit, warnings}。
    feature: ナレのトーン方向づけ（FEATURES[*].narration.tone）。normal＝空＝回帰。特集固有banも除去。
    ★feat-merge-3：旧 concept= から改名（情報源を FEATURES に一本化）。"""
    limit = narration_char_limit(dur_sec)
    facts = facts or {}
    _ntone = feature_tone(feature, "narration")
    _tone_line = f"・トーン：{_ntone}\n" if _ntone else ""
    instr = (
        "次のテロップ文を、低い声の男性ナレーターが読む『耳向けの一文』に整えてください。\n"
        f"・{limit}字以内・言い切り・短文・煽らない。誇大/最上級/断定は使わない。\n"
        f"{_tone_line}"                              # ★特集のトーン（空=特集なし=回帰）
        "・眺望・方角・日当たり・階数の見え方・静けさ・周辺環境には触れない"
        "（例『夜空』『見晴らし』『南向き』『閑静』はマイソクに明示が無ければ書かない）。部屋の中の事実だけ。\n"
        "・物件名・『モテ』等の内部語は使わない。数字や単位はそのまま残してよい。\n"
        "出力は本文のみ（説明・記号・引用符・改行なし）。\nテロップ："
    )
    warnings, out = [], str(text or "")
    try:
        resp = client.models.generate_content(model=model, contents=[instr + str(text or "")])
        raw = (getattr(resp, "text", "") or "").strip().splitlines()
        out = raw[0] if raw else str(text or "")
    except Exception as e:  # noqa: BLE001  握り潰さず記録（元テロップの正規化で続行）
        warnings.append(f"整え生成に失敗（{type(e).__name__}）。元テロップを正規化しました。")
    out = normalize_reading(out)                   # 読み正規化（英字/記号→カナ/和数）
    name = (facts.get("name") or "").strip()
    # ★v79-scrub-clause：ban は語の置換でなく節ごと除去（助詞始まりの断片を耳に流さない）。
    out, _bn = ban_scrub(out, list(_PR_BANNED) + _SNS_BAN_EXTRA + ["モテ部屋", "モテ"] + feature_ng(feature))
    for w in sorted(set(_bn)):
        warnings.append(f"ban語『{w}』を含む節を除去")
    if name and name in out:
        out = out.replace(name, "")
        warnings.append("物件名を除去")
    out, _frm = fact_scrub(out, facts)             # ★事実外属性(夜空等)の伝播を遮断（情感2行→ナレ）
    if _frm:
        warnings.append(f"事実外の属性『{'・'.join(_frm)}』を除去（マイソクに明示なし）")
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > limit:
        out = out[:limit]
        warnings.append(f"{limit}字超のため打ち切り")
    return {"text": out, "limit": limit, "warnings": sorted(set(warnings))}


# ★covercopy-v1：draft_cover_copy（表紙キャッチ1本のGemini生成）を削除。唯一の呼出元 app._pl_cover_ai_cb が
#   死にコード（on_click登録先ゼロ）で推移的に到達不能であり、表紙コピー生成は magtext の3案生成に一本化した
#   （残すと『表紙コピーを作る経路』が2つになる＝issue-v1 で断った2源化の温床）。
#   機械ガード部分は _scrub_cover_copy() として保存済み（magtext が使用）。


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
