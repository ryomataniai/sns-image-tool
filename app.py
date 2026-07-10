# -*- coding: utf-8 -*-
"""
物件SNSスタジオ — 社内Web版 (app.py)
========================================
サイドバー常設ナビ（st.navigation）で以下のツールを提供:
  ホーム／動画をつくる／内観画像をつくる（マイソク→内観）／
  実写真ステージング／カルーセルをつくる／背景素材をつくる／設定

各ツール本体は render_*() 関数。生成ロジックは core.py / carousel.py を共有。
"""

import io
import os
import zipfile
from pathlib import Path

import streamlit as st

import core
import carousel

st.set_page_config(page_title="物件SNSスタジオ", page_icon=":material/apartment:",
                   layout="wide")


# ----------------------------------------------------------------------
# Secrets / 認証
# ----------------------------------------------------------------------
def get_secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:  # noqa: BLE001
        return os.environ.get(key, default)


def check_password():
    app_pw = get_secret("APP_PASSWORD", "")
    if not app_pw:
        return
    if st.session_state.get("auth_ok"):
        return

    def _verify():
        st.session_state["auth_ok"] = (st.session_state.get("pw_input") == app_pw)
        st.session_state["pw_input"] = ""

    st.title("物件SNSスタジオ")
    st.caption("エンクス社内ツール。パスワードを入力してください。")
    st.text_input("パスワード", type="password", key="pw_input", on_change=_verify)
    if st.session_state.get("auth_ok") is False:
        st.error("パスワードが違います。")
    st.stop()


check_password()
GEMINI_KEY = (get_secret("GEMINI_API_KEY")
              or st.session_state.get("manual_gemini_key")
              or core.get_api_key())


def make_client():
    return core.get_client(GEMINI_KEY)


# ----------------------------------------------------------------------
# ホーム / 設定 ページ
# ----------------------------------------------------------------------
def render_home():
    st.title("物件SNSスタジオ")
    st.info("写真がある → **動画をつくる** ／ マイソクだけ → "
            "**内観画像をつくる** で画像化してから動画へ。")
    st.markdown("#### 何をしますか？")

    # (page, icon, 名称, 1行説明, よく使う)
    cards = [
        (page_pipeline, ":material/auto_awesome_motion:", "物件から動画をつくる",
         "マイソク/写真 → 内観画像 → ルームツアー動画までを一気通貫で。", True),
        (page_video, ":material/movie:", "動画をつくる（画像から）",
         "手持ちの部屋画像を、字幕・BGM付きルームツアー動画に。", False),
        (page_maisoku, ":material/apartment:", "内観画像をつくる",
         "マイソク／間取り図・写真から、内観イメージ画像を生成。", False),
        (page_carousel, ":material/view_carousel:", "カルーセルをつくる",
         "トピック → コピー → 背景 → 文字入れで、投稿カルーセルを自動作成。", False),
        (page_background, ":material/image:", "背景素材をつくる",
         "暮らしのイメージ背景を一括生成（文字なしの素材）。", False),
    ]
    for row in range(0, len(cards), 2):
        cols = st.columns(2)
        for col, (page, icon, name, desc, featured) in zip(cols, cards[row:row + 2]):
            with col.container(border=True):
                if featured:
                    st.markdown(":orange-background[**よく使う**]")
                st.markdown(f"### {icon} {name}")
                st.caption(desc)
                if st.button("はじめる →", key=f"home_go_{name}",
                             type="primary" if featured else "secondary",
                             use_container_width=True):
                    st.switch_page(page)


def render_settings():
    st.title("設定")
    if GEMINI_KEY:
        st.success("APIキー: 検出済み（Secrets/環境変数）")
    else:
        st.text_input("Gemini APIキー", type="password", key="manual_gemini_key",
                      help="https://aistudio.google.com/apikey で取得")
    st.caption("生成画像にはSynthIDの不可視透かしが入ります。"
               "商用利用可否はGoogleの利用規約を最終確認してください。")
    st.caption("build: crop-fix-v53 (白帯crop判定を実20枚で再設計・bright/jump境界で頑健化)")


# ======================================================================
# 背景素材をつくる（旧タブ1: 単発画像量産）
# ======================================================================
def render_background():
    st.caption("プロンプト一覧から「暮らしのイメージ」を一括生成（文字なし背景素材）")

    c1, c2, c3 = st.columns(3)
    s_model = c1.selectbox("モデル", core.MODELS, index=0, key="s_model")
    s_aspect = c2.selectbox("比率", core.ASPECT_RATIOS, index=0, key="s_aspect")
    s_count = c3.slider("1案あたり枚数", 1, 5, 1, key="s_count")
    s_max = st.number_input("総枚数の安全上限", 1, 200, 50, key="s_max")
    s_safety = st.checkbox("安全文言を自動付与（推奨）", value=True, key="s_safety",
                           help="「文字・ロゴなし／特定実在物件でないイメージ」を付与")

    if "single_results" not in st.session_state:
        st.session_state.single_results = []

    sample_path = Path(__file__).parent / "prompts_sample.csv"

    def _load_sample_prompts():
        try:
            rows = core.load_prompts(str(sample_path))
            st.session_state["s_text"] = "\n".join(r[1] for r in rows)
        except Exception as e:  # noqa: BLE001
            st.session_state["_s_load_err"] = str(e)

    def _clear_single():
        st.session_state.single_results = []

    cc1, cc2 = st.columns(2)
    cc1.button("📋 サンプル読込", use_container_width=True, key="s_load",
               on_click=_load_sample_prompts)
    cc2.button("🗑️ 結果クリア", use_container_width=True, key="s_clear",
               on_click=_clear_single)
    if st.session_state.get("_s_load_err"):
        st.error(f"サンプル読込失敗: {st.session_state.pop('_s_load_err')}")

    s_text = st.text_area("プロンプト（1行に1案）", height=180, key="s_text")
    lines = [ln.strip() for ln in s_text.splitlines() if ln.strip()]
    total = len(lines) * s_count
    usd, jpy = core.estimate_cost(total, s_model)
    m1, m2, m3 = st.columns(3)
    m1.metric("案", f"{len(lines)}")
    m2.metric("生成枚数", f"{total}")
    m3.metric("推定コスト", f"${usd:.2f}", f"≈{jpy:.0f}円")

    over = total > s_max
    if over:
        st.error(f"総枚数 {total} が上限 {s_max} を超過。")

    if st.button("🎨 画像を生成", type="primary", key="s_gen",
                 disabled=(total == 0 or over), use_container_width=True):
        try:
            client = make_client()
        except RuntimeError as e:
            st.error(str(e)); st.stop()
        rows = [(f"{i+1:02d}_{core.slugify(p)}", p, 1) for i, p in enumerate(lines)]
        plan = core.build_plan(rows, s_count)
        prog = st.progress(0.0, text="生成中…")
        res, fail = [], 0
        for i, (pid, pr) in enumerate(plan, 1):
            data, err = core.generate_image_bytes(client, pr, s_model, s_aspect, "1K",
                                                  add_safety=s_safety)
            if data:
                res.append((pid, data, pr))
            else:
                fail += 1; st.warning(f"✗ {pid}: {err}")
            prog.progress(i/len(plan), text=f"生成中… {i}/{len(plan)}")
        prog.empty()
        st.session_state.single_results = res
        if res:
            st.success(f"完了: {len(res)}/{len(plan)} 枚" + (f"（{fail}失敗）" if fail else ""))

    res = st.session_state.single_results
    if res:
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pid, data, _ in res:
                zf.writestr(f"{pid}.png", data)
        st.download_button("⬇️ 全画像をZIP", zbuf.getvalue(), "sns_images.zip",
                           "application/zip", use_container_width=True, key="s_zip")
        cols = st.columns(3)
        for idx, (pid, data, pr) in enumerate(res):
            with cols[idx % 3]:
                st.image(data, use_container_width=True)
                st.caption(pr[:50])
                st.download_button("⬇️", data, f"{pid}.png", "image/png",
                                   key=f"s_dl_{idx}", use_container_width=True)


# ======================================================================
# カルーセルをつくる（旧タブ2: カルーセル自動生成）
# ======================================================================
def render_carousel():
    st.caption("トピックを入れるだけ → コピー生成 → 背景生成 → 文字を焼いて完成カルーセル")

    cc1, cc2, cc3 = st.columns([2, 1, 1])
    topic = cc1.text_input("トピック", placeholder="例）賃貸の初期費用 / 内見でみるべき点 / 一人暮らしの家具選び",
                           key="c_topic")
    n_body = cc2.slider("本文枚数", 2, 8, 4, key="c_nbody")
    brand = cc3.text_input("ブランド名", value="@enks_chintai", key="c_brand")
    use_ai_bg = st.checkbox("背景をAI生成する（OFFなら単色＝無料・最速）", value=True, key="c_aibg")

    n_slides = n_body + 2  # 表紙 + 本文 + CTA
    bg_usd, bg_jpy = core.estimate_cost(n_slides if use_ai_bg else 0, "gemini-2.5-flash-image")
    st.caption(f"想定: {n_slides}枚（表紙＋本文{n_body}＋CTA）"
               + (f" / 背景AI {n_slides}枚 ≈ ${bg_usd:.2f}（{bg_jpy:.0f}円）＋コピー生成少額"
                  if use_ai_bg else " / 背景は単色（画像コスト0）"))

    # --- ステップ1: コピー生成 ---
    if st.button("① 構成（コピー）を生成", type="primary", key="c_copy",
                 disabled=(not topic), use_container_width=True):
        try:
            client = make_client()
            with st.spinner("コピーを生成中…"):
                st.session_state.spec = carousel.generate_carousel_copy(client, topic, n_body)
            # 新トピックを確実に反映：生成のたびに編集ウィジェットのキーを変える(nonce方式)。
            # 固定キーだと2回目以降にStreamlitが前回値を保持し、前トピックが焼かれてしまう。
            st.session_state.gen_nonce = st.session_state.get("gen_nonce", 0) + 1
            st.session_state.pop("carousel_imgs", None)  # 旧トピックの完成画像も破棄
            st.success("構成を生成しました。下で文言を確認・編集できます。")
        except Exception as e:  # noqa: BLE001
            st.error(f"コピー生成失敗: {e}")

    spec = st.session_state.get("spec")
    if spec:
        st.divider()
        st.subheader("✏️ 文言の確認・編集")
        _n = st.session_state.get("gen_nonce", 0)  # 生成ごとに変わる→ウィジェット再初期化
        spec["cover_headline"] = st.text_input("表紙：見出し", spec["cover_headline"], key=f"e_ch_{_n}")
        spec["cover_sub"] = st.text_input("表紙：サブ", spec["cover_sub"], key=f"e_cs_{_n}")
        for i, s in enumerate(spec["slides"]):
            with st.expander(f"本文 {i+1}：{s['title']}", expanded=False):
                s["title"] = st.text_input("見出し", s["title"], key=f"e_t{i}_{_n}")
                s["body"] = st.text_area("本文", s["body"], key=f"e_b{i}_{_n}", height=80)
        spec["cta_text"] = st.text_input("CTA：誘導文", spec["cta_text"], key=f"e_cta_{_n}")

        # 投稿キャプション＋ハッシュタグ（Business Suiteへコピペ用）
        if spec.get("caption") or spec.get("hashtags"):
            st.markdown("**📝 投稿キャプション（コピーして貼り付け）**")
            cap = (spec.get("caption", "") + "\n\n" + spec.get("hashtags", "")).strip()
            st.code(cap, language=None)

        # --- ステップ2: 画像生成 ---
        if st.button("② カルーセル画像を生成", type="primary", key="c_render",
                     use_container_width=True):
            bg_map = {}
            if use_ai_bg:
                try:
                    client = make_client()
                except RuntimeError as e:
                    st.error(str(e)); st.stop()
                prompts = carousel.bg_prompts_of(spec)
                prog = st.progress(0.0, text="背景を生成中…")
                keys = list(prompts.keys())
                for i, k in enumerate(keys, 1):
                    pr = prompts[k]
                    if pr:
                        data, _ = core.generate_image_bytes(
                            client, pr, "gemini-2.5-flash-image", "4:5", "1K")
                        bg_map[k] = data
                    prog.progress(i/len(keys), text=f"背景を生成中… {i}/{len(keys)}")
                prog.empty()
            with st.spinner("文字を焼き込み中…"):
                st.session_state.carousel_imgs = carousel.render_carousel(spec, bg_map, brand)
            st.success("カルーセルが完成しました。")

    imgs = st.session_state.get("carousel_imgs")
    if imgs:
        st.divider()
        st.subheader(f"📚 完成カルーセル（{len(imgs)}枚）")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in imgs:
                zf.writestr(name, data)
        st.download_button("⬇️ カルーセルをZIPでダウンロード", zbuf.getvalue(),
                           "carousel.zip", "application/zip",
                           use_container_width=True, key="c_zip")
        cols = st.columns(3)
        for idx, (name, data) in enumerate(imgs):
            with cols[idx % 3]:
                st.image(data, use_container_width=True)
                st.download_button("⬇️", data, name, "image/png",
                                   key=f"c_dl_{idx}", use_container_width=True)


# ======================================================================
# 内観画像をつくる（旧タブ3: マイソク／間取り図 → 内観シミュレーション）
# ======================================================================
def render_maisoku():
    st.caption("マイソク／間取り図をアップ → その間取りを参考にAIが内観イメージを生成します。"
               "（建物外観・図面の線や文字は出さず、内観のみ）")

    up = st.file_uploader("マイソク／間取り図（PNG・JPG・PDF）",
                          type=["png", "jpg", "jpeg", "webp", "pdf"], key="m_upload")

    # PDFは指定ページを画像化してから使う
    input_png = None
    if up is not None:
        raw = up.getvalue()
        is_pdf = (up.type == "application/pdf") or up.name.lower().endswith(".pdf")
        if is_pdf:
            try:
                n_pages = core.pdf_page_count(raw)
                page = 1
                if n_pages > 1:
                    page = st.number_input(
                        f"PDFページ（全{n_pages}ページ・間取り図のあるページを選択）",
                        min_value=1, max_value=n_pages, value=1, step=1, key="m_pdf_page")
                input_png = core.pdf_page_to_png(raw, page_index=int(page) - 1, dpi=150)
            except Exception as e:  # noqa: BLE001
                st.error(f"PDF変換に失敗: {e}")
        else:
            input_png = raw

    mc1, mc2 = st.columns(2)
    style_name = mc1.selectbox("内観スタイル", list(core.INTERIOR_STYLES.keys()), key="m_style")
    model = mc2.selectbox("モデル", core.MODELS, index=0, key="m_model",
                          help="2.5-flash-imageが最安。品質重視ならNano Banana 2 (3.1) を試す")

    mode = st.radio("生成モード", ["暮らしのイメージ（家具あり1枚）",
                                   "ビフォーアフター（空室＋家具あり 2枚）",
                                   "マイソク丸ごと→実写真ルームツアー（推奨）",
                                   "複数部屋を一括生成（画像）",
                                   "3Dパース（間取り俯瞰イメージ・試験）"],
                    key="m_mode")

    # モード別オプション
    room = core.INTERIOR_ROOMS[0]
    rooms, keep_style, ref_photo = [], True, None
    gap_rooms = []
    if mode.startswith("マイソク丸ごと"):
        st.caption("マイソク内の実際の室内写真を土台に演出します。"
                   "実物ベースなので間取りと乖離しません。写真の無い部屋だけ、実写真のトーンに"
                   "合わせて生成で補います（間取り図も自動抽出して整合を取ります）。")
        st.radio("仕上げ",
                 ["賃貸（現況のまま家具だけ）", "リノベ後（フル刷新の完成イメージ）"],
                 horizontal=True, key="m_finish",
                 help="賃貸＝壁・床・設備を変えず家具・小物だけ足す（優良誤認回避）。"
                      "リノベ後＝床・壁・天井・照明・水回りまで刷新した完成イメージ"
                      "（事業B＝中古購入＋リノベ提案向け）。")
        gap_rooms = st.multiselect(
            "写真が無い部屋で生成して補うもの", ["玄関", "トイレ", "洗面所", "浴室", "バルコニー"],
            default=["トイレ"], key="m_gap",
            help="マイソクに写真が無い部屋だけをここから生成します。"
                 "実写真がある部屋は自動で除外されるので、重複はしません。")
        st.checkbox("間取り図もカットに含める（SNSツアー用・抽出した実物をそのまま添付）",
                    value=True, key="m_include_fp",
                    help="マイソクから抽出した間取り図を、ツアーの1カットとして出力に含めます。"
                         "生成AIは通さず実物をそのまま使うので正確です。")
        st.checkbox("収納・廊下のカットも含める",
                    value=False, key="m_include_storage_hall",
                    help="収納やクローゼット・廊下のカットは見栄えがしにくいので既定では除外します。"
                         "含めたい場合だけONにしてください。")
    elif mode.startswith("複数部屋"):
        rooms = st.multiselect(
            "生成する部屋（カット）", list(core.ROOM_TOUR_PRESETS.keys()),
            default=["玄関", "LDK", "洋室", "浴室", "トイレ"], key="m_rooms")
        keep_style = st.checkbox("トーンを揃える（参照写真、なければ最初のカットを基準に統一）",
                                 value=True, key="m_keepstyle")
        if st.session_state.get("maisoku_perspective") is not None:
            st.checkbox(
                "生成済みの3Dパースを全体の配色アンカーに使う（一貫性重視・推奨）",
                value=True, key="m_persp_anchor",
                help="先に生成した3Dパースには住戸全体の配色・素材が1枚に入っています。"
                     "これを各部屋の基準にすると部屋間の統一感が上がります。"
                     "手動で参照写真をアップした場合はそちらが優先されます。")
        ref_photo = st.file_uploader(
            "雰囲気の参照写真（任意・未指定ならマイソク内の写真を自動使用）",
            type=["png", "jpg", "jpeg", "webp"], key="m_refphoto",
            help="未指定の場合、アップしたマイソク内の室内写真（リビング優先）を自動で"
                 "参照トーンに使います。手動でアップすると、その写真のトーンで上書きします")
        st.caption("※写真のない部屋（トイレ等）は間取り＋参照写真から推定生成した"
                   "『イメージ』です。実物と異なるため投稿・提案時は『※イメージ』注記を強めに。")
    else:
        room = st.selectbox("主役の部屋", core.INTERIOR_ROOMS, key="m_room")

    m_request = st.text_area(
        "要望（任意）", key="m_request",
        placeholder="例：ソファはグレー系、観葉植物多め、南向きの明るい雰囲気、"
                    "子ども部屋っぽく など。※実際にない設備・広さは足しません",
        help="スタイルに加えて、色味・家具・雰囲気などの希望を自由に書けます")

    if input_png is not None:
        st.image(input_png, caption="入力（この画像を参考に生成）", width=280)

    # マイソク丸ごとモードはPDF必須 → 押下前に理由を明示し、ボタンを無効化（事後エラー回避）
    _needs_pdf = mode.startswith("マイソク丸ごと")
    _has_pdf = up is not None and (
        (up.type == "application/pdf") or up.name.lower().endswith(".pdf"))
    _pdf_missing = _needs_pdf and not _has_pdf
    if _pdf_missing:
        st.warning("このモードはマイソクの「PDF」が必要です（埋め込み写真を抽出します）。"
                   "PDFをアップすると生成できます。")
    gen_disabled = ((input_png is None)
                    or (mode.startswith("複数部屋") and not rooms)
                    or _pdf_missing)
    if st.button("🏠 内観を生成", type="primary", key="m_gen",
                 disabled=gen_disabled, use_container_width=True):
        try:
            client = make_client()
        except RuntimeError as e:
            st.error(str(e)); st.stop()

        img_bytes = input_png
        mime = "image/png"
        style_desc = core.INTERIOR_STYLES[style_name]
        results = []  # (ラベル, bytes)

        if mode.startswith("マイソク丸ごと"):
            _is_pdf = up is not None and (
                (up.type == "application/pdf") or up.name.lower().endswith(".pdf"))
            if not _is_pdf:
                st.error("このモードはマイソクの「PDF」アップが必要です（埋め込み写真を抽出します）。")
            else:
                raw = up.getvalue()
                with st.spinner("マイソクから室内写真と間取り図を抽出・分類中…"):
                    plan = core.plan_maisoku_photo_tour(client, raw)
                real = plan["real"]
                anchor = plan["anchor"]
                floor_plan = plan["floor_plan"]
                # 収納・廊下は既定で除外（見栄えがしにくいため）
                if not st.session_state.get("m_include_storage_hall"):
                    real = [it for it in real if it["code"] not in ("STORAGE", "HALLWAY")]
                # 写真の無い部屋のうち、実写真でカバーされていないものだけ生成対象にする
                gaps = [g for g in gap_rooms
                        if core.GAP_LABEL_TO_CODE.get(g) not in plan["covered"]]
                if not real and not gaps:
                    st.error("マイソクから使える室内写真が抽出できませんでした。"
                             "画像主体のマイソクか、別ページをお試しください。")
                total = len(real) + len(gaps)
                # 間取り図をカットに含める（生成AIを通さず実物をそのまま添付）
                if st.session_state.get("m_include_fp") and floor_plan is not None:
                    results.append(("間取り図", floor_plan))
                if total > 0:
                    _reno_c = st.session_state.get("m_finish", "").startswith("リノベ")
                    st.caption(
                        f"実写真 {len(real)}枚を"
                        + ("リノベ後イメージに変換" if _reno_c else "ステージング")
                        + f"＋写真の無い部屋 {len(gaps)}件を生成します。"
                        + ("間取り図も1カットとして添付します。" if (
                            st.session_state.get("m_include_fp") and floor_plan is not None)
                           else ""))
                    reno = st.session_state.get("m_finish", "").startswith("リノベ")
                    suffix = "リノベ" if reno else "実写真"
                    prog = st.progress(0.0, text=(
                        "実写真をリノベ後イメージに変換中…" if reno else "実写真をステージング中…"))
                    done = 0
                    seen = {}
                    # ① 実写真を土台に演出（賃貸＝構造維持で家具のみ／リノベ＝フル刷新の完成イメージ）
                    for it in real:
                        lbl = it["label"]
                        seen[lbl] = seen.get(lbl, 0) + 1
                        disp = lbl if seen[lbl] == 1 else f"{lbl}{seen[lbl]}"
                        tr = it["treatment"]
                        if reno:
                            # リノベ後：床・壁・天井・照明・水回りまで刷新した完成イメージ
                            p = core.build_renovation_prompt(style_desc, m_request)
                        elif tr == "staging_living":
                            p = core.build_staging_prompt(style_desc, "リビング", m_request)
                        elif tr == "staging_bedroom":
                            p = core.build_staging_prompt(style_desc, "寝室", m_request)
                        elif tr == "water":
                            p = core.build_water_staging_prompt(style_desc, m_request)
                        elif tr == "enhance":
                            p = core.build_enhance_prompt()
                        else:
                            p = core.build_staging_prompt(style_desc, "", m_request)
                        data, err = core.generate_from_image_bytes(
                            client, it["bytes"], p, model=model, aspect="4:5", size="1K")
                        if err:
                            st.error(f"{disp}（{suffix}）生成失敗: {err}")
                        else:
                            results.append((f"{disp}（{suffix}）", data))
                        done += 1
                        prog.progress(done / total, text=f"生成中… {done}/{total}")
                    # ② 写真の無い部屋を、実写真のトーンに合わせて生成（間取り図を土台に）
                    base = floor_plan if floor_plan is not None else input_png
                    for g in gaps:
                        p = core.build_room_tour_prompt(
                            style_desc, g, core.ROOM_TOUR_PRESETS.get(g, ""),
                            with_ref=(anchor is not None), user_request=m_request)
                        imgs = [(base, "image/png")]
                        if anchor is not None:
                            imgs.append((anchor, "image/png"))
                        data, err = core.generate_from_images(
                            client, imgs, p, model=model, aspect="4:5", size="1K")
                        if err:
                            st.error(f"{g}（生成）失敗: {err}")
                        else:
                            results.append((f"{g}（生成）", data))
                        done += 1
                        prog.progress(done / total, text=f"生成中… {done}/{total}")
                    prog.empty()
        elif mode.startswith("複数部屋"):
            ref_bytes = ref_photo.getvalue() if ref_photo is not None else None
            ref_mime = (ref_photo.type or "image/png") if ref_photo is not None else "image/png"
            # 優先順位: ①手動アップの参照写真 → ②生成済み3Dパース → ③マイソク内写真の自動抽出 → ④最初のカット
            if ref_bytes is None and st.session_state.get("m_persp_anchor") \
                    and st.session_state.get("maisoku_perspective") is not None:
                ref_bytes, ref_mime = st.session_state["maisoku_perspective"], "image/png"
                st.caption("※生成済みの3Dパースを全体の配色アンカーに使用しています。")
            # 手動指定も3Dパースもなければ、マイソク（PDF）内の室内写真を参照トーンに自動使用
            if ref_bytes is None and up is not None:
                _raw = up.getvalue()
                _is_pdf = (up.type == "application/pdf") or up.name.lower().endswith(".pdf")
                if _is_pdf:
                    with st.spinner("マイソク内の写真から雰囲気の参照を自動取得中…"):
                        auto_ref = core.pick_reference_photo(client, _raw)
                    if auto_ref is not None:
                        ref_bytes, ref_mime = auto_ref, "image/png"
                        st.caption("※マイソク内の室内写真を参照トーンに使用しています。")
                    else:
                        st.caption("※マイソク内に参照できる室内写真が見つからず、"
                                   "最初のカット基準でトーンを揃えます。")
            sel = list(rooms)
            # 参照写真がない時のみ、LDKを基準カットに先頭化
            if keep_style and ref_bytes is None and "LDK" in sel:
                sel = ["LDK"] + [r for r in sel if r != "LDK"]
            anchor = ref_bytes            # 参照写真があれば最初からトーン基準に
            anchor_mime = ref_mime
            prog = st.progress(0.0, text="複数部屋の画像を生成中…")
            for i, r in enumerate(sel, 1):
                use_ref = keep_style and anchor is not None
                prompt = core.build_room_tour_prompt(
                    style_desc, r, core.ROOM_TOUR_PRESETS[r], with_ref=use_ref,
                    user_request=m_request)
                imgs = [(img_bytes, mime)]
                if use_ref:
                    imgs.append((anchor, anchor_mime))
                data, err = core.generate_from_images(
                    client, imgs, prompt, model=model, aspect="4:5", size="1K")
                if err:
                    st.error(f"{r} 生成失敗: {err}")
                else:
                    results.append((r, data))
                    if keep_style and anchor is None:
                        anchor = data
                        anchor_mime = "image/png"
                prog.progress(i / len(sel), text=f"生成中… {i}/{len(sel)}（{r}）")
            prog.empty()
        elif mode.startswith("3Dパース"):
            prog = st.progress(0.0, text="3Dパースを生成中…")
            prompt = core.build_3d_perspective_prompt(style_desc, user_request=m_request)
            data, err = core.generate_from_image_bytes(
                client, img_bytes, prompt, model=model,
                aspect="4:5", size="1K", mime_type=mime)
            if err:
                st.error(f"3Dパース生成失敗: {err}")
            else:
                results.append(("3Dパース", data))
                # ルームツアーの全体配色アンカーとして再利用できるよう保存
                st.session_state.maisoku_perspective = data
                st.caption("※この3Dパースは、次にルームツアーを生成する際の"
                           "『全体の配色アンカー』として自動で使えます。")
            prog.progress(1.0)
            prog.empty()
        else:
            want = [("after", True)]
            if mode.startswith("ビフォーアフター"):
                want = [("before（空室）", False), ("after（家具あり）", True)]
            prog = st.progress(0.0, text="内観を生成中…")
            for i, (label, staged) in enumerate(want, 1):
                prompt = core.build_interior_prompt(style_desc, room, staged=staged,
                                                    user_request=m_request)
                data, err = core.generate_from_image_bytes(
                    client, img_bytes, prompt, model=model,
                    aspect="4:5", size="1K", mime_type=mime)
                if err:
                    st.error(f"{label} 生成失敗: {err}")
                else:
                    results.append((label, data))
                prog.progress(i / len(want), text=f"内観を生成中… {i}/{len(want)}")
            prog.empty()

        if results:
            st.session_state.maisoku_results = results
            st.success(f"{len(results)}枚 生成しました。")

    mres = st.session_state.get("maisoku_results")
    if mres:
        st.divider()
        st.subheader(f"生成結果（{len(mres)}枚）")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (label, data) in enumerate(mres, 1):
                zf.writestr(f"{i:02d}_{label}.png", data)
        st.download_button("⬇️ まとめてZIPでダウンロード", zbuf.getvalue(),
                           "roomtour.zip", "application/zip",
                           use_container_width=True, key="m_zip")
        cols = st.columns(3)
        for idx, (label, data) in enumerate(mres):
            with cols[idx % 3]:
                st.image(data, caption=label, use_container_width=True)
                st.download_button("⬇️", data, f"{idx+1:02d}_{label}.png", "image/png",
                                   key=f"m_dl_{idx}", use_container_width=True)
        if st.button("→ この結果を動画化", key="pl_to_video_m", type="primary",
                     use_container_width=True):
            st.session_state["pl_handoff_images"] = list(mres)
            st.switch_page(page_video)
        st.caption("※SNS投稿時は運用ルールに従い『※AI加工のイメージです』の注記を焼き込み、"
                   "エリアは市区・駅ぼかしまで。")


# ======================================================================
# 実写真ステージング（旧タブ4: 実室内写真→高解像度化＋家具）
# ======================================================================
def render_stage():
    st.caption("マイソク/写真の実際の室内写真を抽出して加工。"
               "賃貸モード＝設備・構造を変えず家具のみ（優良誤認回避）。"
               "リノベ提案モード＝事業B向け、床・壁・照明・水回りまで刷新した完成イメージ。")

    up2 = st.file_uploader("マイソク（PDF）または室内写真（PNG/JPG）",
                           type=["pdf", "png", "jpg", "jpeg", "webp"], key="stg_upload")

    photos = []  # [(png_bytes, w, h), ...]
    if up2 is not None:
        raw2 = up2.getvalue()
        if (up2.type == "application/pdf") or up2.name.lower().endswith(".pdf"):
            try:
                photos = core.extract_pdf_photos(raw2, min_px=250)
            except Exception as e:  # noqa: BLE001
                st.error(f"PDFからの画像抽出に失敗: {e}")
        else:
            photos = [(raw2, 0, 0)]

    if photos:
        stg_mode = st.radio(
            "モード", ["賃貸（現況に家具を置く）", "リノベ提案（リノベ後のイメージ）"],
            horizontal=True, key="stg_mode",
            help="賃貸=設備・構造を変えず家具のみ。リノベ提案=事業B向け、"
                 "床・壁・照明・水回りまで刷新した完成イメージを生成")
        reno_mode = stg_mode.startswith("リノベ")

        gc1, gc2, gc3 = st.columns(3)
        style_name2 = gc1.selectbox("スタイル",
                                    list(core.INTERIOR_STYLES.keys()), key="stg_style")
        model2 = gc2.selectbox("モデル", core.MODELS, index=0, key="stg_model",
                               help="品質重視ならNano Banana 2 (3.1) を試す")
        aspect2 = gc3.radio("出力比率", ["4:5", "1:1", "3:4"], horizontal=True, key="stg_aspect")

        stg_request = st.text_area(
            "要望（任意・全ステージングに共通で反映）", key="stg_request",
            placeholder="例：ソファはグレー系、木目を強めに、観葉植物多め、生活感控えめ など。"
                        "※実際にない設備・広さは足しません",
            help="色味・家具・雰囲気などの希望を自由に書けます")

        if reno_mode:
            TREAT = ["使わない", "リノベ後イメージにする"]
            default_treat = "リノベ後イメージにする"
        else:
            TREAT = ["使わない", "リビングとしてステージング", "寝室としてステージング",
                     "おまかせステージング", "水回り・玄関を演出", "高解像度化のみ"]
            default_treat = "おまかせステージング"

        # アップロード内容・モードが変わったら初期選択を再設定
        import hashlib as _hashlib
        sig = _hashlib.md5(
            b"".join(p[0][:4000] for p in photos)
            + str(len(photos)).encode() + stg_mode.encode()
        ).hexdigest()
        if st.session_state.get("stg_sig") != sig:
            imgs_bytes = [p[0] for p in photos]
            blanks = [core.is_blank_image(b) for b in imgs_bytes]
            ai = ["おまかせステージング"] * len(photos)
            try:
                with st.spinner("AIが各写真を判定中"
                                "（白紙・ロゴ・地図など不要な画像は自動で『使わない』に）…"):
                    _c = make_client()
                    ai = core.classify_rooms(_c, imgs_bytes)
            except Exception:  # noqa: BLE001
                pass
            suggestions = []
            for i in range(len(photos)):
                if blanks[i] or ai[i] == "使わない":      # 白紙 or SKIP判定
                    suggestions.append("使わない")
                elif reno_mode:
                    suggestions.append("リノベ後イメージにする")
                else:
                    suggestions.append(ai[i] if ai[i] in TREAT else "おまかせステージング")
            for k in [k for k in st.session_state.keys() if k.startswith("stg_treat_")]:
                del st.session_state[k]
            for i, s in enumerate(suggestions):
                st.session_state[f"stg_treat_{i}"] = s if s in TREAT else default_treat
            st.session_state["stg_sig"] = sig

        if reno_mode:
            st.write("各写真を「リノベ後イメージにする／使わない」で選択。"
                     "図面や外観など不要なカットは「使わない」に。")
        else:
            st.write("各写真の処理（AIの推測を初期選択にしています。違う場合は選び直してください）"
                     "／大きい洋室→リビング・小さい洋室→寝室・キッチン/玄関→小物を演出・"
                     "浴室/トイレ/洗面→高解像度化のみ・不要→使わない")
        gcols = st.columns(4)
        for i, (b, w, h) in enumerate(photos):
            with gcols[i % 4]:
                st.image(b, use_container_width=True)
                st.selectbox(f"#{i}", TREAT, key=f"stg_treat_{i}")

        jobs = [(i, st.session_state.get(f"stg_treat_{i}", "使わない"))
                for i in range(len(photos))]
        jobs = [(i, t) for i, t in jobs if t != "使わない"]

        if st.button(f"🛋 選択した{len(jobs)}枚を一括生成（並行）", type="primary",
                     disabled=(len(jobs) == 0), key="stg_gen", use_container_width=True):
            try:
                client = make_client()
            except RuntimeError as e:
                st.error(str(e)); st.stop()
            import concurrent.futures as _cf
            style_desc = core.INTERIOR_STYLES[style_name2]

            ROOM_USE = {"リビングとしてステージング": "リビング",
                        "寝室としてステージング": "寝室",
                        "おまかせステージング": ""}

            def _run(job):
                i, t = job
                src = photos[i][0]
                is_reno = (t == "リノベ後イメージにする")
                is_stage = t in ROOM_USE
                is_water = (t == "水回り・玄関を演出")
                if is_reno:
                    pr = core.build_renovation_prompt(style_desc,
                                                      user_request=stg_request)
                elif is_stage:
                    pr = core.build_staging_prompt(style_desc, ROOM_USE[t],
                                                   user_request=stg_request)
                elif is_water:
                    pr = core.build_water_staging_prompt(style_desc,
                                                         user_request=stg_request)
                else:
                    pr = core.build_enhance_prompt()
                data, err = core.generate_from_images(
                    client, [(src, "image/png")], pr,
                    model=model2, aspect=aspect2, size="2K", add_safety=False)
                disc = ("※リノベ後のイメージ（仕上がりは設計により異なります）"
                        if is_reno else "※AI加工のイメージ")
                if not err and (is_reno or is_stage or is_water):  # 画像を変える処理は注記
                    try:
                        data = core.add_disclaimer(data, disc)
                    except Exception:  # noqa: BLE001
                        pass
                return (i, t, data, err)

            results, done = [], 0
            prog = st.progress(0.0, text=f"並行生成中… 0/{len(jobs)}")
            with _cf.ThreadPoolExecutor(max_workers=4) as ex:
                futs = [ex.submit(_run, j) for j in jobs]
                for fut in _cf.as_completed(futs):
                    i, t, data, err = fut.result()
                    done += 1
                    if err:
                        st.error(f"#{i} 生成失敗: {err}")
                    else:
                        results.append((i, f"#{i} {t}", data))
                    prog.progress(done / len(jobs), text=f"並行生成中… {done}/{len(jobs)}")
            prog.empty()
            if results:
                results.sort(key=lambda r: r[0])
                st.session_state.stage_results = [(lbl, d) for _, lbl, d in results]
                st.success(f"{len(results)}枚 生成しました。")

    sres = st.session_state.get("stage_results")
    if sres:
        st.divider()
        st.subheader(f"生成結果（{len(sres)}枚）")
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (label, data) in enumerate(sres, 1):
                zf.writestr(f"{i:02d}_{label.replace(' ', '_')}.png", data)
        st.download_button("⬇️ まとめてZIPでダウンロード", zbuf.getvalue(),
                           "staged_set.zip", "application/zip",
                           use_container_width=True, key="stg_zip")
        cols = st.columns(3)
        for idx, (label, data) in enumerate(sres):
            with cols[idx % 3]:
                st.image(data, caption=label, use_container_width=True)
                st.download_button("⬇️", data, f"{idx+1:02d}_{label.replace(' ', '_')}.png",
                                   "image/png", key=f"stg_dl_{idx}", use_container_width=True)
        if st.button("→ この結果を動画化", key="pl_to_video_stg", type="primary",
                     use_container_width=True):
            st.session_state["pl_handoff_images"] = list(sres)
            st.switch_page(page_video)
        st.caption("※SNS投稿時は『※AI加工のイメージです』の注記を焼き込み、"
                   "エリアは市区・駅ぼかしまで。設備・広さは実物基準を崩さないこと。")


# ======================================================================
# 動画をつくる（旧タブ5: ルームツアー動画化・画像→動画→連結）
# ======================================================================
def render_video():
    import os as _os
    import room_tour_video as rtv
    _os.environ["FAL_KEY"] = get_secret("FAL_KEY", _os.environ.get("FAL_KEY", ""))

    st.caption("部屋画像をアップ → 各部屋をカメラの動く動画に → キャプション/BGMで1本のツアーに連結")
    if not get_secret("FAL_KEY", ""):
        st.warning("FAL_KEY 未設定。Secrets に fal.ai の APIキーを追加してください。")

    ROOM_LABELS = {"generic": "指定なし", "entrance": "玄関", "ldk": "LDK",
                   "bedroom": "洋室/寝室", "bathroom": "浴室", "toilet": "トイレ"}

    def _guess_room_type(label):
        s = str(label)
        if "玄関" in s:
            return "entrance"
        if "LDK" in s or "リビング" in s:
            return "ldk"
        if "洋室" in s or "寝室" in s:
            return "bedroom"
        if "浴室" in s:
            return "bathroom"
        if "トイレ" in s:
            return "toilet"
        return "generic"

    # 内観／ステージング結果からの直渡し（pl_handoff_images）を優先。DL・再アップ不要。
    handoff = st.session_state.get("pl_handoff_images")
    if handoff:
        st.info(f"内観で生成した {len(handoff)}枚を読み込みました。（ダウンロード・再アップ不要）")
        if st.button("読み込みを解除（手動アップに戻す）", key="pl_handoff_clear"):
            del st.session_state["pl_handoff_images"]
            st.rerun()
        # (label, bytes)。表示・生成の両方に bytes を使う
        src_items = [(str(label), data, data) for label, data in handoff]
    else:
        v_files = st.file_uploader(
            "部屋画像（再生順・複数可。JPG/PNG）", type=["png", "jpg", "jpeg"],
            accept_multiple_files=True, key="v_files")
        src_items = [(f.name, f, f.getvalue()) for f in v_files] if v_files else []

    room_types, captions, imgs = [], [], []
    if src_items:
        st.markdown("**各画像の部屋種別とキャプション**")
        for i, (label, disp, data) in enumerate(src_items):
            c0, c1, c2 = st.columns([1, 1, 2])
            c0.image(disp, width=90)
            # handoff時はlabelから種別を推定。キーはモード別（手動=v_/直渡し=pl_）
            _kp = "pl" if handoff else "v"
            _def_rt = _guess_room_type(label) if handoff else "generic"
            rt = c1.selectbox(f"種別{i+1}", list(ROOM_LABELS),
                              index=list(ROOM_LABELS).index(_def_rt),
                              format_func=lambda k: ROOM_LABELS[k], key=f"{_kp}_rt_{i}")
            # 種別generic（指定なし）はキャプション初期値を空に（「指定なし」焼き込み防止）
            cap_default = "" if rt == "generic" else ROOM_LABELS.get(rt, "")
            cap = c2.text_input(f"キャプション{i+1}", value=cap_default, key=f"{_kp}_cap_{i}")
            room_types.append(rt)
            captions.append(cap)
            imgs.append((label, data))

    st.markdown("**オプション**")
    o1, o2, o3 = st.columns(3)
    v_model = o1.selectbox("モデル", list(rtv.FAL_MODELS),
                           index=0, key="v_model",
                           help="日常運用=kling2.6_pro（安い）／見せ場=kling3.0_pro")
    v_dur = o2.selectbox("1本の長さ(秒)", [5, 10], index=0, key="v_dur")
    v_bgm = o3.checkbox("BGMを付ける", value=True, key="v_bgm")
    v_caps = st.checkbox("キャプションを焼く", value=True, key="v_caps")
    v_tag = st.text_input("上部タグ（物件名・間取り等／空欄で非表示）", key="v_tag",
                          placeholder="例: ニューモート204 ｜ 2LDK 57.07㎡")

    # マイソクPDFから上部タグを自動生成（任意）
    v_pdf = st.file_uploader("マイソクPDF（任意・上部タグ自動補完）", type=["pdf"], key="v_pdf")
    if v_pdf is not None and not v_tag:
        try:
            specs = rtv.parse_maisoku_specs(v_pdf.getvalue())
            auto = " ｜ ".join(x for x in [specs.get("madori"), specs.get("area"),
                                          specs.get("built")] if x)
            if auto:
                st.info(f"マイソクから自動タグ候補: {auto}（上のタグ欄に貼り付け可）")
        except Exception as e:  # noqa: BLE001
            st.caption(f"マイソク解析スキップ: {e}")

    v_note = st.text_input("画面注記（右下・景表法配慮／空欄で非表示）", key="v_note",
                           placeholder="例: ※画像はイメージです")

    n_imgs = len(src_items)
    est_usd = {"kling2.6_pro": 0.35, "kling2.1_pro": 0.49, "kling3.0_pro": 0.84}\
        .get(v_model, 0.35) * n_imgs * (v_dur / 5)
    mcol1, mcol2 = st.columns(2)
    mcol1.metric("推定コスト", f"約 ${est_usd:.2f}", f"≈{est_usd*150:.0f}円 / {n_imgs}本")
    if n_imgs:
        mcol2.metric("推定所要時間", f"約 {round(n_imgs * 1.0)}〜{round(n_imgs * 1.5)}分")
        st.caption(f"目安：{n_imgs}枚 × 約1〜1.5分／枚（連結・BGM含む）。fal生成は枚数に比例します。")

    if st.button("🎬 ルームツアーを生成", type="primary", key="v_gen",
                 disabled=(n_imgs == 0), use_container_width=True):
        if not get_secret("FAL_KEY", ""):
            st.error("FAL_KEY が未設定です。")
        else:
            bar = st.progress(0.0)
            status = st.empty()

            def _pg(step, total, msg):
                bar.progress(min((step + 1) / (total + 1), 1.0))
                if step < total:
                    status.write(f"全{total}枚中 {step+1}枚目を生成中…"
                                 "（1枚あたり約1〜1.5分）")
                else:
                    status.write("連結中…（クロスフェード＋BGM）")

            try:
                out = rtv.build_tour(
                    imgs, captions=captions if v_caps else [""] * n_imgs,
                    top_tag=v_tag, with_captions=v_caps, with_bgm=v_bgm,
                    also_silent=True, model_key=v_model, duration=v_dur,
                    room_types=room_types, image_note=v_note,
                    taste="pop", progress=_pg)   # 旧ツールは従来の座布団テロップを維持
                bar.progress(1.0)
                status.write("完成")
                st.success("ルームツアーを生成しました。")
                if out.get("silent"):
                    st.video(out["silent"])
                    st.download_button("⬇️ 無音版 mp4", out["silent"],
                                       file_name="room_tour_silent.mp4",
                                       mime="video/mp4", key="v_dl_silent")
                if out.get("bgm"):
                    st.video(out["bgm"])
                    st.download_button("⬇️ BGM版 mp4", out["bgm"],
                                       file_name="room_tour_bgm.mp4",
                                       mime="video/mp4", key="v_dl_bgm")
            except Exception as e:  # noqa: BLE001
                st.error(f"生成に失敗しました: {e}")


# ======================================================================
# 物件から動画をつくる（B2b-1: 一気通貫パイプライン）
#   入口2（PDF/写真）→ ①取り込み・種別 → ②画像化 → ★確認(Before/After) → ③動画
#   ※ core.*/build_tour/既存キーは不変。新規は pl_ 接頭辞。旧3ツールは残置。
# ======================================================================
PL_ROOMS = ["外観", "玄関", "LDK", "キッチン", "洋室", "寝室", "クローゼット",
            "浴室", "トイレ", "洗面", "バルコニー", "その他"]
PL_TREATMENTS = ["家具ステージング", "リノベ後イメージ", "水回り・玄関を演出",
                 "高解像度化のみ", "使わない"]
_PL_ROOM_TO_VIDEO = {"外観": "exterior", "玄関": "entrance", "LDK": "ldk", "キッチン": "ldk",
                     "洋室": "bedroom", "寝室": "bedroom", "クローゼット": "generic",
                     "浴室": "bathroom", "トイレ": "toilet", "洗面": "generic",
                     "バルコニー": "generic", "その他": "generic"}


def _pl_video_room_type(room):
    return _PL_ROOM_TO_VIDEO.get(room, "generic")


# シーンテロップ用：部屋種別 → 英名 / 和名
_PL_ROOM_EN = {"外観": "exterior", "玄関": "entrance", "LDK": "living room", "キッチン": "kitchen",
               "洋室": "bedroom", "寝室": "bedroom", "クローゼット": "closet", "浴室": "bathroom",
               "トイレ": "toilet", "洗面": "washroom", "バルコニー": "balcony", "その他": "room"}
_PL_ROOM_JP = {"外観": "外観", "玄関": "玄関", "LDK": "リビング", "キッチン": "キッチン",
               "洋室": "洋室", "寝室": "寝室", "クローゼット": "クローゼット", "浴室": "浴室",
               "トイレ": "トイレ", "洗面": "洗面", "バルコニー": "バルコニー", "その他": "その他"}
# 情感2行の下書きテンプレ（P1a・LLMなし）。無い種別は帖数入り汎用文
_PL_SUB_TEMPLATE = {
    "外観": ["街並みになじむ佇まい", "帰るのが楽しみになる外観"],
    "玄関": ["おかえりを迎える明るい玄関", "余裕のある土間で身支度もスムーズ"],
    "LDK": ["朝は光が差し込む窓辺で", "夜は家族で並んでくつろぐ時間"],
    "キッチン": ["料理がはかどる使いやすい設え", "家族と会話しながら過ごせる"],
    "洋室": ["自然光が心地よいプライベート空間", "一日の終わりにゆっくり休める"],
    "寝室": ["静かに眠りにつける落ち着き", "朝は柔らかな光で目覚める"],
    "浴室": ["一日の疲れをゆっくり流せる", "清潔感のあるくつろぎのバス"],
    "洗面": ["朝の身支度がはかどる洗面", "清潔で使いやすい水まわり"],
    "トイレ": ["清潔感のある落ち着いた空間", "毎日を気持ちよく過ごせる"],
    "バルコニー": ["風が抜ける開放的なバルコニー", "洗濯物も気持ちよく干せる"],
    "クローゼット": ["たっぷり収納で部屋すっきり", "衣類も小物もきれいに片づく"],
}


def _pl_caption_main(it, lang):
    """メインライン（部屋名＋帖）。lang='en'→'living room 10.9J' / 'ja'→'リビング 10.9帖'。"""
    room = it.get("room", "その他")
    jo = it.get("jo")
    if lang == "en":
        name = _PL_ROOM_EN.get(room, "room")
        return f"{name} {jo:g}J" if jo else name
    name = _PL_ROOM_JP.get(room, room)
    return f"{name} {jo:g}帖" if jo else name


def _pl_caption_sub(it):
    """情感2行の自動下書き（改行区切り）。種別テンプレ→無ければ帖数入り汎用。"""
    room = it.get("room", "その他")
    t = _PL_SUB_TEMPLATE.get(room)
    if t:
        return "\n".join(t)
    name = _PL_ROOM_JP.get(room, room)
    jo = it.get("jo")
    line1 = f"{jo:g}帖の広々とした{name}" if jo else f"ゆとりのある{name}"
    return line1 + "\n自然光が心地よい空間"


# テロップのスタイル自動既定：水回りは座布団(pop)で可読性、居室・その他は clean
_PL_TELOP_WATER = ("浴室", "洗面", "トイレ", "キッチン")
_PL_TELOP_LIVING = ("LDK", "洋室", "寝室")
_PL_TELOP_POSITIONS = ["下中央", "下左", "上中央", "中央"]


def _pl_resolve_taste(it, global_taste):
    """クリップのテロップ見た目を決定：明示 → 部屋種別自動 → 種別不明は全体既定。"""
    v = st.session_state.get(f"pl_taste_{it['id']}", "auto")
    if v in ("clean", "pop"):
        return v
    room = it.get("room")
    if room in _PL_TELOP_WATER:
        return "pop"
    if room in _PL_TELOP_LIVING or room in ("玄関", "バルコニー", "クローゼット", "外観", "その他"):
        return "clean"
    return global_taste                       # 種別不明→全体既定


def _pl_resolve_pos(it, global_pos):
    """クリップのテロップ配置を決定：明示 → auto は全体既定。"""
    v = st.session_state.get(f"pl_pos_{it['id']}", "auto")
    return v if v in _PL_TELOP_POSITIONS else global_pos


def _pl_set_flash_title(title):
    """『冒頭フラッシュに設定』の on_click（ウィジェット生成前に実行されるため代入が安全）。
    表紙(pl_cover_title/sub)も同じ値へ同期し、表紙とフラッシュのタイトルずれを防ぐ。"""
    st.session_state["pl_open_title"] = "flash"
    st.session_state["pl_flash_text"] = title
    st.session_state["pl_cover_title"] = title
    st.session_state["pl_cover_sub"] = st.session_state.get("pl_sub_edit", "")


def _pl_apply_title_choice():
    """タイトル案ラジオの on_change：選択案から編集欄・表紙タイトルを直接同期する。
    ※コールバックはウィジェット生成前に走るため、キーへの代入がフロント値より優先され確実に反映される
      （script本体での del/pop はフロントが旧値を再送するため効かない＝この方式が必須）。"""
    _draft = st.session_state.get("pl_prcopy") or {}
    _titles = _draft.get("titles", [])
    idx = st.session_state.get("pl_title_idx", 0)
    if not (isinstance(idx, int) and 0 <= idx < len(_titles)):
        return
    sel = _titles[idx]
    st.session_state["pl_title_edit"] = sel.get("title", "")
    st.session_state["pl_sub_edit"] = sel.get("subtitle", "")
    st.session_state["pl_cover_title"] = sel.get("title", "")
    st.session_state["pl_cover_sub"] = sel.get("subtitle", "")


def _pl_reset_title_choice():
    """『PRコピーを下書き』の on_click：新しい候補に備え選択・編集欄をリセット。
    ※コールバック内の pop はウィジェット再初期化として有効（script本体の pop は効かない）。
      下書き生成後に生成前seedが新案 titles[0] を入れ直す。"""
    for k in ("pl_title_idx", "pl_title_edit", "pl_sub_edit",
              "pl_cover_title", "pl_cover_sub"):
        st.session_state.pop(k, None)


def _pl_room_use(room):
    """build_staging_prompt の room_use（room指定を生成に効かせる＝痛み#3対策）。"""
    if room == "LDK":
        return "リビング"
    if room == "寝室":
        return "寝室"
    return ""


def _pl_sel_index(options, value, default=0):
    return options.index(value) if value in options else default


def _pl_guess_room_treat(ai_label, blank):
    """classify_rooms のラベル＋白紙判定 → (room, treatment) 初期値。"""
    if blank or ai_label == "使わない":
        return ("その他", "使わない")
    return {
        "リビングとしてステージング": ("LDK", "家具ステージング"),
        "寝室としてステージング": ("寝室", "家具ステージング"),
        "おまかせステージング": ("洋室", "家具ステージング"),
        "水回り・玄関を演出": ("玄関", "水回り・玄関を演出"),
        "高解像度化のみ": ("浴室", "高解像度化のみ"),
    }.get(ai_label, ("洋室", "家具ステージング"))


def _pl_reset():
    for k in [k for k in list(st.session_state)
              if k.startswith("pl_") and k != "pl_handoff_images"]:
        del st.session_state[k]


# 用途モード（度合いだけを切り替える。機能＝部屋種別は不変）
PL_MODES = ["賃貸ステージング", "リノベ提案（事業B）"]


def _pl_default_treatment(room, mode):
    """部屋種別 × 用途モード → 既定の処理（treatment）。処理は部屋から自動決定。"""
    if room == "外観":
        return "高解像度化のみ"          # 建物外観はステージング/リノベしない
    if mode == "リノベ提案（事業B）":
        if room in ("クローゼット", "バルコニー", "その他"):
            return "高解像度化のみ"      # 大改変しない
        return "リノベ後イメージ"
    # 賃貸ステージング
    if room in ("LDK", "洋室", "寝室"):
        return "家具ステージング"
    if room in ("キッチン", "玄関"):
        return "水回り・玄関を演出"
    return "高解像度化のみ"              # 浴室・洗面・トイレ・クローゼット・バルコニー・その他


def _pl_apply_mode_defaults():
    """用途モード変更時：全itemの処理を 部屋種別×モード で再導出（手動選択より優先）。"""
    mode = st.session_state.get("pl_mode", PL_MODES[0])
    for it in st.session_state.get("pl_items", []):
        # item["room"]（種別）はグリッド描画で常時同期される単一の真実
        st.session_state[f"pl_treat_{it['id']}"] = _pl_default_treatment(
            it.get("room", "その他"), mode)


def _pl_apply_room_default(i):
    """部屋種別変更時（汎用ドロップダウン）：そのitemの処理を 部屋×モード で再導出。"""
    mode = st.session_state.get("pl_mode", PL_MODES[0])
    r = st.session_state.get(f"pl_room_{i}", "その他")
    st.session_state[f"pl_treat_{i}"] = _pl_default_treatment(r, mode)


def _pl_apply_roomlink(i):
    """名前付き部屋リンク変更時：リンク先から room(種別)/jo を取得し処理を再導出。"""
    rid = st.session_state.get(f"pl_roomid_{i}")
    rooms = st.session_state.get("pl_rooms", [])
    r = next((x for x in rooms if x["id"] == rid), None)
    mode = st.session_state.get("pl_mode", PL_MODES[0])
    for it in st.session_state.get("pl_items", []):
        if it["id"] == i:
            it["room_id"] = rid if r else None
            it["room"] = r["type"] if r else "その他"
            it["jo"] = r.get("jo") if r else None
            st.session_state[f"pl_treat_{i}"] = _pl_default_treatment(it["room"], mode)
            return


def _pl_img_stats(img_bytes):
    """画像の (白地率, 黒線率, 平均彩度) をローカル計算。160pxに縮小。失敗時 (0,0,0)。"""
    try:
        import numpy as _np
        from io import BytesIO as _BytesIO
        from PIL import Image as _Image
        im = _Image.open(_BytesIO(img_bytes)).convert("RGB")
        im.thumbnail((160, 160))
        a = _np.asarray(im, dtype="float32")
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 0.0)
    if a.size == 0:
        return (0.0, 0.0, 0.0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    white_ratio = float((lum > 235).mean())
    black_ratio = float((lum < 60).mean())
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat_mean = float(_np.where(mx > 0, (mx - mn) / _np.maximum(mx, 1e-6), 0.0).mean())
    return (white_ratio, black_ratio, sat_mean)


def _pl_score_floorplan(img_bytes):
    """間取り図らしさをローカル画像判定。→ (gate:bool, score:float)。
    間取り図＝白地が多い＋黒い線がある＋ほぼ無彩色。写真/地図/外観/白紙枠と物理的に区別。"""
    white_ratio, black_ratio, sat_mean = _pl_img_stats(img_bytes)
    gate = (white_ratio > 0.6 and 0.02 < black_ratio < 0.20 and sat_mean < 0.15)
    return (gate, white_ratio * (1.0 - sat_mean))


def _pl_is_blank_frame(img_bytes):
    """マイソクの白い枠・白紙（中身ゼロ）＝白地率>0.9 かつ 黒線率<0.01。抽出から除外する。
    （間取り図は黒線率0.04〜、写真は白地率が低いので誤除外しない）"""
    white_ratio, black_ratio, _ = _pl_img_stats(img_bytes)
    return white_ratio > 0.9 and black_ratio < 0.01


def _pl_choose_floorplan(pdf_imgs, codes):
    """PDF抽出画像から間取り図を1枚選ぶ。ローカル判定（決定的）→ LLMフォールバック→ None。"""
    best_b, best_score = None, -1.0
    for b in pdf_imgs:
        gate, score = _pl_score_floorplan(b)
        if gate and score > best_score:
            best_b, best_score = b, score
    if best_b is not None:
        return best_b
    # フォールバック：classify_maisoku_images が FLOORPLAN とタグした最初の画像
    # （codes[i] はマルチラベル＝コードのリスト）
    for i, b in enumerate(pdf_imgs):
        if i < len(codes) and "FLOORPLAN" in (codes[i] or []):
            return b
    return None


def _pl_pick_floorplan():
    """サイドバーの手動上書き：選んだitem画像を間取り図に差し替える。"""
    x = st.session_state.get("pl_fp_pick")
    if x is None or x == -1:
        return
    for it in st.session_state.get("pl_items", []):
        if it["id"] == x:
            st.session_state["pl_floorplan"] = it["src_bytes"]
            return


def _pl_render_floorplan_sidebar():
    """間取り図を STEP1〜関所まで サイドバーに常時ピン留め（手動上書き付き）。"""
    fp = st.session_state.get("pl_floorplan")
    items = st.session_state.get("pl_items", [])
    if not fp and not items:
        return  # 取り込み前は非表示
    with st.sidebar:
        st.divider()
        st.markdown("### 間取り図")
        if fp:
            st.image(fp, caption="間取り図（種別選択の参照用）", use_container_width=True)
        else:
            st.caption("間取り図は未検出（手持ち写真のみ／未対応レイアウト等）。"
                       "下で手動指定できます。")
        if items:
            def _fmt(x):
                if x == -1:
                    return "（自動検出のまま）"
                it = next((t for t in items if t["id"] == x), None)
                return f"#{x + 1} {it['room'] if it else ''}"
            # ドロップダウンは選択肢が多いと画面外に溢れて選べないため、
            # expander内のradio（インライン・サイドバーごとスクロール可）にする
            with st.expander("これは間取り図（手動指定）", expanded=not fp):
                st.radio("抽出画像から選択", [-1] + [it["id"] for it in items],
                         format_func=_fmt, key="pl_fp_pick",
                         on_change=_pl_pick_floorplan, label_visibility="collapsed")


# classify_maisoku_images のコード → 部屋種別（PL_ROOMS）
_PL_CODE_TO_ROOM = {
    "LIVING": "LDK", "BEDROOM": "洋室", "KITCHEN": "キッチン", "BATH": "浴室",
    "WASH": "洗面", "TOILET": "トイレ", "ENTRANCE": "玄関", "STORAGE": "クローゼット",
    "BALCONY": "バルコニー", "EXTERIOR": "外観", "HALLWAY": "その他", "OTHER": "その他",
}
# 部屋種別ではなく「設備痕跡フラグ」＝主種別・coverage の判定から除外し _raw_codes にのみ残す。
# （WASHER_PAN を部屋にすると キッチン+洗面の写真で主種別が洗面へ流れ、処理が水回り演出→
#   高解像度化に落ちる回帰が起きるため。防水パン検出は _raw_codes で拾う）
_PL_FEATURE_CODES = ("WASHER_PAN",)
# 生成対象から除外するコード（間取り図・地図・白紙）。外観はツアーの掴みに使うため除外しない
_PL_EXCLUDE_CODES = ("FLOORPLAN", "MAP", "BLANK")


def _pl_parse_maisoku(pdf_bytes):
    """間取タイプ欄・面積をパース。→ {"rooms":[(部屋種別, 帖float)], "summary": 表示用文字列}。取れなければ空。"""
    import re
    rooms, tokens, type_str, area = [], [], "", ""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
    except Exception:  # noqa: BLE001
        return {"rooms": [], "summary": ""}
    m = re.search(r"間取タイプ\s*([0-9A-Za-z]+)\[([^\]]*)\]", text)
    if m:
        type_str = m.group(1)
        for tok in m.group(2).split("x"):          # 半角 ' x ' 区切り
            tok = tok.strip()
            mm = re.match(r"([^\d.]+)([\d.]+)", tok)
            if mm:
                sym, jo = mm.group(1).strip(), mm.group(2)
                tokens.append(f"{sym}{jo}")
                try:
                    rooms.append(("洋室" if sym == "洋" else sym, float(jo)))
                except ValueError:  # noqa: PERF203
                    pass
    ma = re.search(r"(\d+(?:\.\d+)?)\s*㎡", text)
    if ma:
        area = ma.group(1) + "㎡"
    summary = " ／ ".join(x for x in [type_str, "・".join(tokens), area] if x)
    return {"rooms": rooms, "summary": summary}


def _pl_assign_jo(items, madori_rooms):
    """間取タイプの帖数を居室アイテムへ順に割当（item['jo']）。複数洋室は取り込み順で仮割当。"""
    yo = [jo for t, jo in madori_rooms if t == "洋室"]
    ldk = [jo for t, jo in madori_rooms if t in ("LDK", "DK")]
    yi = li = 0
    for it in items:
        r = it.get("room")
        if r == "洋室" and yi < len(yo):
            it["jo"] = yo[yi]; yi += 1
        elif r in ("LDK", "キッチン") and li < len(ldk):
            it["jo"] = ldk[li]; li += 1


# 間取タイプの室記号 → 部屋種別（PL_ROOMS）
_PL_SYM_TO_TYPE = {"洋": "洋室", "洋室": "洋室", "LDK": "LDK", "DK": "LDK"}
# 全物件に存在するため、写真の有無に関わらず常に部屋リストへ入れる標準部屋
_PL_STANDARD_TYPES = ["外観", "玄関", "キッチン", "浴室", "洗面", "トイレ", "バルコニー", "クローゼット"]

# 新規取り込み時に残す＝ユーザー設定（物件非依存）。接頭辞削除の巻き込み防止に使う
_PL_KEEP_ON_IMPORT = {"pl_telop_taste", "pl_telop_pos", "pl_room_lang",
                      "pl_open_title", "pl_v_note", "pl_mode", "pl_cover_aspect",
                      "pl_include_fp", "pl_make_persp"}


def _pl_build_rooms(madori_rooms, items, vision_rooms=None):
    """物件固有の名前付き部屋リスト。→ [{id, name, type, jo}]。
    優先：間取り図読み取り(vision_rooms) ∪ 間取タイプ居室 ∪ 標準部屋。帖数は間取タイプ文字列を優先。"""
    from collections import Counter, deque
    vision_rooms = vision_rooms or []
    rooms, rid = [], 0

    def add(name, rtype, jo=None):
        nonlocal rid
        rooms.append({"id": rid, "name": name, "type": rtype, "jo": jo})
        rid += 1

    # 居室の帖数は間取タイプ文字列を優先（テキスト値が正確）→ 種別ごとのキュー
    yo_q = deque(jo for t, jo in madori_rooms if _PL_SYM_TO_TYPE.get(t) == "洋室")
    ldk_q = deque(jo for t, jo in madori_rooms if _PL_SYM_TO_TYPE.get(t) == "LDK")

    # 1) 居室（vision優先＝位置つき。無ければ間取タイプ）。帖数はテキスト優先で上書き
    vis_living = [v for v in vision_rooms if v.get("type") in ("洋室", "LDK")]
    if vis_living:
        living = []
        for v in vis_living:
            t = v["type"]
            jo = (yo_q.popleft() if t == "洋室" and yo_q
                  else ldk_q.popleft() if t == "LDK" and ldk_q else v.get("jo"))
            living.append((t, jo, v.get("position", "")))
    else:
        living = [(_PL_SYM_TO_TYPE.get(t, "その他"), jo, "") for t, jo in madori_rooms]

    total = Counter(t for t, _, _ in living)
    seen = {}
    for t, jo, pos in living:
        seen[t] = seen.get(t, 0) + 1
        suffix = chr(ord("A") + seen[t] - 1) if total[t] > 1 else ""
        detail = "・".join(p for p in [pos, (f"{jo:g}帖" if jo else "")] if p)
        add(f"{t}{suffix}" + (f"（{detail}）" if detail else ""), t, jo)

    # 2) vision の非居室（WIC/納戸/水回り/玄関/バルコニー等）。クローゼットは記載名を活かす
    for v in vision_rooms:
        t = v.get("type", "その他")
        if t in ("洋室", "LDK", "その他"):
            continue
        label = (v.get("label") or "").strip()
        if t == "クローゼット" and label:
            name = label + (f"（{v['position']}）" if v.get("position") else "")
        else:
            name = t
        if any(r["name"] == name for r in rooms):
            continue
        add(name, t, None)

    # 3) 標準部屋を必ず含める（未登録 type のみ）＝「玄関等が選べない」バグの解消
    for t in _PL_STANDARD_TYPES:
        if not any(r["type"] == t for r in rooms):
            add(t, t, None)

    return rooms


def _pl_link_items(items, pl_rooms):
    """各itemをbest-guessで部屋にリンク（room_id）。room(種別)/jo をリンク先から取得。
    同種複数（洋室A/B）は未使用スロットへ順に割当（外れても人が入替）。"""
    from collections import defaultdict, deque
    queue = defaultdict(deque)
    for r in pl_rooms:
        queue[r["type"]].append(r["id"])
    byid = {r["id"]: r for r in pl_rooms}
    for it in items:
        if it.get("treatment") == "使わない":
            it["room_id"] = None
            continue
        t = it.get("room")
        rid = queue[t].popleft() if queue.get(t) else None   # 未使用の同種スロット
        if rid is None:                                       # スロット無し→同種の先頭を再利用
            rid = next((r["id"] for r in pl_rooms if r["type"] == t), None)
        it["room_id"] = rid
        if rid is not None:
            it["room"] = byid[rid]["type"]
            it["jo"] = byid[rid].get("jo")


# 動線順（ツアーらしい並び）。同種内は現orderを維持して安定ソート
_PL_ROOM_RANK = {"外観": -1, "玄関": 0, "LDK": 1, "キッチン": 2, "洋室": 3, "寝室": 4,
                 "クローゼット": 5, "洗面": 6, "浴室": 7, "トイレ": 8,
                 "バルコニー": 9, "その他": 10}


def _pl_gen_sorted():
    """生成済アイテムを order 昇順で返す。"""
    return sorted([it for it in st.session_state.get("pl_items", [])
                   if it.get("gen_bytes")], key=lambda it: it.get("order", 0))


def _pl_auto_reorder():
    """生成済アイテムの order を部屋種別の動線順で振り直す（同種は現order維持＝安定）。"""
    gen = sorted(_pl_gen_sorted(),
                 key=lambda it: (_PL_ROOM_RANK.get(it.get("room"), 10), it.get("order", 0)))
    for i, it in enumerate(gen):
        it["order"] = i


def _pl_move(iid, delta):
    """order 順で iid のアイテムを delta 方向の隣と order を入れ替える。"""
    gen = _pl_gen_sorted()
    idx = next((k for k, it in enumerate(gen) if it["id"] == iid), None)
    if idx is None:
        return
    j = idx + delta
    if 0 <= j < len(gen):
        gen[idx]["order"], gen[j]["order"] = gen[j]["order"], gen[idx]["order"]


# 家具ステージングを許すのは居室のみ（非居室は窓・別室の捏造事故を防ぐ）
PL_RESIDENTIAL = ("LDK", "洋室", "寝室")


def _pl_generate_one(client, it, style_desc, model, aspect, req):
    """1itemを treatment/room に従い生成。(bytes|None, err|None, disc|None) を返す。
    ※注記(disc)は gen_bytes に焼き込まない（増加+cropで頭が切れ二重注記になるため）。
      文言だけ返し、画像ZIP/DL出力時に add_disclaimer で1回だけ焼く。動画/表紙は各レンダラが描く。"""
    t = it["treatment"]
    room = it.get("room", "")
    # 帖数ヒントを先頭に付加（帖数→全体要望→個別メモ の順で効かせる。core署名は変えない）
    jo = it.get("jo")
    if jo:
        _hint = (f"約{jo:g}帖の部屋。家具の大きさ・量を部屋の広さに合わせる"
                 "（実際より広く見せない）。")
        req = "\n".join(x for x in [_hint, req] if x)
    if t == "補完生成":
        # 写真の無い部屋：間取り図を土台に、実写真アンカーでトーンを合わせて内観を生成
        # （旧render_maisokuと同一経路。関所の「この画像だけ再生成」＋メモもここを通る）
        room_label = _PL_GAP_LABEL.get(room, room)
        base = it.get("_gap_base") or it.get("src_bytes")
        anchor = it.get("_gap_anchor")
        # 事実ガード（設備・築年）を前置＝記載外設備を描かせない（優良誤認防止）
        _req = "\n".join(x for x in [it.get("_gap_facts", ""), req] if x)
        pr = core.build_room_tour_prompt(
            style_desc, room_label, core.ROOM_TOUR_PRESETS.get(room_label, ""),
            with_ref=anchor is not None, user_request=_req)
        imgs = [(base, "image/png")]
        if anchor:
            imgs.append((anchor, "image/png"))
        data, err = core.generate_from_images(
            client, imgs, pr, model=model, aspect=aspect, size="2K", add_safety=False)
        return data, err, _PL_GAP_DISC
    if t == "3Dパース（試験）":
        # 間取り図（src_bytes）を土台に俯瞰パースを生成。関所の再生成＋メモもここを通る
        base = it.get("src_bytes")
        _req = "\n".join(x for x in [it.get("_gap_facts", ""), req] if x)  # 事実ガード
        pr = core.build_3d_perspective_prompt(style_desc, user_request=_req)
        data, err = core.generate_from_images(
            client, [(base, "image/png")], pr, model=model, aspect=aspect,
            size="2K", add_safety=False)
        return data, err, _PL_PERSP_DISC
    # 事実ガード（3条件）を req 先頭に前置＝記載外設備を描かせない（帖数ヒントと同方式）
    _sreq = "\n".join(x for x in [it.get("_stage_facts", ""), req] if x)
    if t == "リノベ後イメージ":
        # room-aware（部屋の機能を保ったまま刷新）
        pr = core.build_renovation_prompt(style_desc, user_request=_sreq, room=room)
        disc = "※リノベ後のイメージ（仕上がりは設計により異なります）"
    elif t == "家具ステージング" and room in PL_RESIDENTIAL:
        pr = core.build_staging_prompt(style_desc, _pl_room_use(room), user_request=_sreq)
        disc = "※AI加工のイメージ"
    elif t == "家具ステージング":
        # 非居室に家具ステージングが来た場合の最終防波堤（居室用ステージングは流さない）
        if room in ("キッチン", "玄関", "廊下", "バルコニー"):
            pr = core.build_water_staging_prompt(style_desc, user_request=_sreq)
            disc = "※AI加工のイメージ"
        else:  # 浴室・洗面・トイレ・クローゼット・その他
            pr = core.build_enhance_prompt()
            disc = None
    elif t == "水回り・玄関を演出":
        pr = core.build_water_staging_prompt(style_desc, user_request=_sreq)
        disc = "※AI加工のイメージ"
    else:  # 高解像度化のみ
        pr = core.build_enhance_prompt()
        disc = None
    data, err = core.generate_from_images(
        client, [(it["src_bytes"], "image/png")], pr,
        model=model, aspect=aspect, size="2K", add_safety=False)
    # 注記は焼き込まず disc として返す（gen_bytesは注記なしのクリーンな画像のまま保持）。
    return data, err, disc


def _pl_run_generation(jobs, style_name, model, aspect, req):
    import concurrent.futures as _cf
    try:
        client = make_client()
    except RuntimeError as e:
        st.error(str(e)); return
    style_desc = core.INTERIOR_STYLES[style_name]
    prog = st.progress(0.0, text=f"画像化中… 0/{len(jobs)}")
    done = 0
    # 事実ガードはメインスレッドで算出して item に載せる（worker threadでsession_stateを触らない）
    for it in jobs:
        it["_stage_facts"] = _pl_stage_facts_for(it, req)
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_pl_generate_one, client, it, style_desc, model, aspect, req): it["id"]
                for it in jobs}
        for fut in _cf.as_completed(futs):
            iid = futs[fut]
            try:
                data, err, disc = fut.result()
            except Exception as e:  # noqa: BLE001
                data, err, disc = None, str(e), None
            done += 1
            if err:
                st.warning(f"#{iid+1} 生成失敗: {err}")
            else:
                for it in st.session_state.get("pl_items", []):
                    if it["id"] == iid:
                        it["gen_bytes"] = _pl_crop_gen(data, it)  # 白帯除去（3Dパースは除外）
                        it["disc"] = disc      # 注記文言（ZIP/DL出力時に焼く）
            prog.progress(done / len(jobs), text=f"画像化中… {done}/{len(jobs)}")
    prog.empty()
    if any(it.get("gen_bytes") for it in st.session_state.get("pl_items", [])):
        st.session_state["pl_reordered"] = False   # 新バッチは関所で動線順を初回自動適用
        st.session_state["pl_stage"] = "review"
        st.rerun()
    else:
        st.error("生成できた画像がありませんでした。処理やアップ画像を見直してください。")


# ── B2b-3a：旧render_maisoku機能の吸収（補完生成 / 間取り図カット / 3Dパース）──
# 補完生成の対象外＝居室と外観。居室は間取り図からの生成では品質が不安定なため実写真が必要
# （旧render_maisokuの gap_rooms が非居室固定だったのと同じ安全域）。
_PL_GAP_EXCLUDE_TYPES = {"LDK", "洋室", "寝室", "外観"}
# ※補完生成の既定チェックは全OFF（マルチラベル分類でも検出漏れは残るため、実写真に
#   写り込んでいないか人が確認して選ぶ設計）。
# pl_room の type → ROOM_TOUR_PRESETS のラベル（表記差の吸収）
_PL_GAP_LABEL = {"洗面": "洗面所"}
_PL_GEN_UNIT_USD = 0.039   # Gemini 画像生成の単価/枚（コスト表示用）
# 注記（法令）：実写真でないことを明示。gen_bytesには焼かず出力段で1回付与。
_PL_GAP_DISC = "※AI生成のイメージ（実際の写真ではありません。設備・仕様は現況と異なる場合があります）"
_PL_PERSP_DISC = "※AI生成のイメージ（3Dパース・試験／実際の写真ではありません）"


def _pl_next_item_id(items):
    """既存itemと衝突しない新規item id（ウィジェットキー衝突回避）。"""
    return max((it["id"] for it in items), default=-1) + 1


def _pl_gap_candidate_rooms(pl_rooms):
    """補完生成の候補＝居室(LDK/洋室/寝室)・外観 以外の全部屋。写り込みでハード除外はしない
    （実写真に写り込む部屋も候補に出し、選択肢を奪わず警告ラベルで判断材料として見せる）。
    居室・外観のハード除外だけは維持（居室は間取り図からの生成品質が不安定）。"""
    return [r for r in (pl_rooms or []) if r["type"] not in _PL_GAP_EXCLUDE_TYPES]


def _pl_room_coverage(items):
    """部屋種別 → その種別が写り込んでいる実写真の記述 ['#N ラベル', ...]（マルチラベル和集合）。
    『ちらっと写っている』の判断材料（ハードフィルタしない）。#N は実写真の表示順。"""
    cov = {}
    photos = [x for x in items if x.get("_origin", "photo") == "photo"
              and x.get("treatment") != "使わない"]
    for k, it in enumerate(photos, 1):
        codes = it.get("_codes") or [it.get("room")]
        label = "・".join(dict.fromkeys(c for c in codes if c)) or it.get("room", "")
        for typ in codes:
            if typ:
                cov.setdefault(typ, []).append(f"#{k} {label}")
    return cov


def _pl_crop_gen(data, it):
    """生成結果の白帯レターボックスを除去。ただし3Dパース（_origin=="persp"）は俯瞰図が
    単色背景に浮く構図で上下の余白が意図的なため、トリミングしない。"""
    if it.get("_origin") == "persp":
        return data
    return core.crop_uniform_borders(data)


def _pl_pick_anchor(items):
    """補完生成のトーン参照＝実写真（優先：生成済み/実写真 LDK→洋室→任意）。無ければ None。"""
    def _b(it):
        return it.get("gen_bytes") or it.get("src_bytes")
    photos = [it for it in items if it.get("_origin", "photo") == "photo"
              and _b(it) and it.get("treatment") != "使わない"]
    for want in ("LDK", "洋室", "寝室"):
        for it in photos:
            if it.get("room") == want:
                return _b(it)
    return _b(photos[0]) if photos else None


def _pl_stage_input():
    import hashlib as _hashlib
    st.markdown("#### ① 取り込み・種別わけ")
    c1, c2 = st.columns(2)
    pdf = c1.file_uploader("マイソクPDF（埋め込み写真を抽出）", type=["pdf"], key="pl_pdf")
    photos_up = c2.file_uploader("手持ち写真（複数可）",
                                 type=["png", "jpg", "jpeg", "webp"],
                                 accept_multiple_files=True, key="pl_photos")
    raw_srcs = []
    pdf_imgs = []
    if pdf is not None:
        try:
            pdf_imgs = [b for (b, _w, _h) in core.extract_pdf_photos(pdf.getvalue(), min_px=250)]
        except Exception as e:  # noqa: BLE001
            st.error(f"PDF抽出に失敗: {e}")
        # 中身ゼロの白い枠（マイソク枠）を除外＝空行防止＋classify配列ズレ防止
        pdf_imgs = [b for b in pdf_imgs if not _pl_is_blank_frame(b)]
        raw_srcs += pdf_imgs
        if not raw_srcs:
            st.warning("PDFから使える室内写真が見つかりませんでした（手持ち写真をお使いください）。")
    if photos_up:
        raw_srcs += [f.getvalue() for f in photos_up]

    if not raw_srcs:
        st.info("マイソクPDF か 手持ち写真をアップしてください。")
        return

    sig = _hashlib.md5(b"".join(s[:2000] for s in raw_srcs)
                       + str(len(raw_srcs)).encode()).hexdigest()
    if st.session_state.get("pl_src_sig") != sig:
        # 細粒度分類を取り込み時1回だけ（部屋種別＋間取り図/外観/地図/白紙の判定を兼ねる）
        codes = [["OTHER"] for _ in raw_srcs]   # マルチラベル：各画像＝コードのリスト
        try:
            with st.spinner("AIが各写真の部屋種別を判定中…"):
                codes = core.classify_maisoku_images(make_client(), raw_srcs)
        except Exception:  # noqa: BLE001
            pass
        parsed = _pl_parse_maisoku(pdf.getvalue()) if pdf is not None else {"rooms": [], "summary": ""}
        _mode = st.session_state.get("pl_mode", PL_MODES[0])
        # 間取り図はローカル画像判定で選ぶ（LLM誤タグ対策・決定的）。候補はPDF抽出画像のみ
        floor_plan = _pl_choose_floorplan(pdf_imgs, codes[:len(pdf_imgs)])
        items = []
        for i, b in enumerate(raw_srcs):
            code_list = codes[i] if i < len(codes) else ["OTHER"]   # マルチラベル（生コード）
            # 設備痕跡フラグ（WASHER_PAN等）は部屋種別の判定から除外。生コードは _raw_codes に残す
            room_codes = [c for c in code_list if c not in _PL_FEATURE_CODES] or ["OTHER"]
            primary = room_codes[0]                                  # 主種別＝先頭の部屋コード
            room = _PL_CODE_TO_ROOM.get(primary, "その他")
            # 写っている部屋種別（除外コードを除く・coverage判定用）
            seen_types = [_PL_CODE_TO_ROOM[c] for c in room_codes
                          if c not in _PL_EXCLUDE_CODES and c in _PL_CODE_TO_ROOM]
            if primary in _PL_EXCLUDE_CODES or core.is_blank_image(b) or b is floor_plan:
                treat = "使わない"     # 間取り図・外観・地図・白紙は生成対象から除外
            else:
                treat = _pl_default_treatment(room, _mode)
            items.append({"id": i, "order": i, "src_bytes": b, "room": room,
                          "treatment": treat, "gen_bytes": None, "caption": "",
                          "jo": None, "room_id": None, "_codes": seen_types,
                          "_raw_codes": code_list})   # 生コード（防水パン検出用）
        # 間取り図をvisionで読んで部屋を列挙（取り込み時1回・floor_planがある時のみ）
        vision_rooms = []
        if floor_plan is not None:
            try:
                with st.spinner("間取り図から部屋を読み取り中…"):
                    vision_rooms = core.read_floorplan_rooms(make_client(), floor_plan)
            except Exception:  # noqa: BLE001
                pass
        # 名前付き部屋リスト（間取り図読取∪間取タイプ居室∪標準部屋）。マイソク文脈がある時のみ
        if floor_plan is not None or parsed["rooms"] or vision_rooms:
            pl_rooms = _pl_build_rooms(parsed["rooms"], items, vision_rooms)
            _pl_link_items(items, pl_rooms)
        else:
            pl_rooms = []                            # 手持ち写真のみ→汎用ドロップダウン
            _pl_assign_jo(items, parsed["rooms"])
        st.session_state["pl_items"] = items
        st.session_state["pl_rooms"] = pl_rooms
        st.session_state["pl_src_sig"] = sig
        st.session_state["pl_floorplan"] = floor_plan
        st.session_state["pl_summary"] = parsed["summary"]
        # 事実抽出（PRコピー下書き用・Geminiは呼ばない）。取り込み時1回
        st.session_state["pl_facts"] = (
            core.parse_maisoku_facts(pdf.getvalue()) if pdf is not None else {})
        # 新規取り込みで物件固有の値を一掃（別物件の建物名・帖数・コピーの焼き込み防止）
        # 完全一致（物件固有）：下書き・フラッシュ文言・上部タグ・タイトル/サブ編集・選択・表紙
        for k in ("pl_prcopy", "pl_flash_text", "pl_v_tag",
                  "pl_title_edit", "pl_sub_edit", "pl_title_idx",
                  "pl_cover_title", "pl_cover_sub", "pl_cover_src", "pl_cover_png",
                  "pl_gap_targets"):   # 補完生成の対象選択（物件固有）。生成結果はpl_items再構築で自動リセット
            st.session_state.pop(k, None)
        # 接頭辞（物件固有・写真ごと）：部屋/処理/間取り図選択＋テロップ本文・個別スタイル
        # ※ pl_room_ は pl_room_lang（ユーザー設定）と前方一致するため残すキーは除外
        for k in [k for k in list(st.session_state)
                  if k.startswith(("pl_room_", "pl_roomid_", "pl_treat_", "pl_fp_pick",
                                   "pl_capmain_", "pl_capsub_", "pl_taste_", "pl_pos_"))
                  and k not in _PL_KEEP_ON_IMPORT]:
            del st.session_state[k]
        # ウィジェットの値は session_state で管理（変更コールバックが上書きするため）
        for it in items:
            if pl_rooms:
                st.session_state[f"pl_roomid_{it['id']}"] = it["room_id"]
            else:
                st.session_state[f"pl_room_{it['id']}"] = it["room"]
            st.session_state[f"pl_treat_{it['id']}"] = it["treatment"]

    items = st.session_state.get("pl_items", [])

    if st.session_state.get("pl_summary"):
        st.info(f"この物件：{st.session_state['pl_summary']}")

    st.markdown("**何をつくる？**（用途を選ぶと各画像の処理が部屋種別から自動で決まります）")
    st.radio("用途", PL_MODES, horizontal=True, key="pl_mode",
             on_change=_pl_apply_mode_defaults)
    st.caption("賃貸ステージング＝家具を置いて魅せる（構造は維持）／"
               "リノベ提案（事業B）＝内装ごと刷新した完成イメージ（機能と骨格は維持）。"
               "処理は部屋種別ごとに自動設定され、必要なら個別に変更できます。")
    _IMG_ASPECT_LABEL = {"4:5": "4:5（Instagram投稿）", "1:1": "1:1（正方形）", "3:4": "3:4（縦）"}
    gc1, gc2, gc3 = st.columns(3)
    style_name = gc1.selectbox("スタイル", list(core.INTERIOR_STYLES.keys()), key="pl_style")
    model = gc2.selectbox("モデル", core.MODELS, index=0, key="pl_model")
    aspect = gc3.radio("画像の比率", ["4:5", "1:1", "3:4"], horizontal=True, key="pl_aspect",
                       format_func=lambda a: _IMG_ASPECT_LABEL.get(a, a))
    req = st.text_area("要望（任意・全体に反映）", key="pl_request",
                       placeholder="例：木目強め、観葉植物多め、生活感控えめ など")

    pl_rooms = st.session_state.get("pl_rooms")
    st.markdown("**各画像：部屋と処理**"
                "（AI初期値を編集可。部屋は動画の連結・字幕・帖数にも使われます）")
    if pl_rooms:
        st.caption("部屋は物件の間取りから自動リンク。サイドバーの間取り図を見て "
                   "洋室A/B 等を入れ替えできます。")

    def _fmt_room(rid):
        if rid is None:
            return "その他（リンクなし）"
        r = next((x for x in (pl_rooms or []) if x["id"] == rid), None)
        return r["name"] if r else "その他"

    # 1画像=1行の横並び（画像 / 部屋 / 処理）。補完生成・3Dパースは既に生成済みなので
    # ここ（取り込み写真の設定）には出さない（処理selectboxで treatment が壊れるのを防ぐ）。
    _photo_items = [it for it in items if it.get("_origin", "photo") == "photo"]
    hc1, hc2, hc3 = st.columns([1, 2, 2])
    hc1.caption("画像")
    hc2.caption("部屋")
    hc3.caption("処理")
    for it in _photo_items:
        i = it["id"]
        rc1, rc2, rc3 = st.columns([1, 2, 2])
        rc1.image(it["src_bytes"], width=110)
        # 値は session_state（pl_roomid_/pl_room_/pl_treat_）で管理するため index は渡さない
        if pl_rooms:
            sel = rc2.selectbox(
                "部屋", [None] + [r["id"] for r in pl_rooms], format_func=_fmt_room,
                key=f"pl_roomid_{i}", label_visibility="collapsed",
                on_change=_pl_apply_roomlink, args=(i,))
            r = next((x for x in pl_rooms if x["id"] == sel), None)
            it["room_id"] = sel
            it["room"] = r["type"] if r else "その他"
            it["jo"] = r.get("jo") if r else None
        else:
            it["room"] = rc2.selectbox(
                "部屋種別", PL_ROOMS, key=f"pl_room_{i}", label_visibility="collapsed",
                on_change=_pl_apply_room_default, args=(i,))
        it["treatment"] = rc3.selectbox(
            "処理", PL_TREATMENTS, key=f"pl_treat_{i}", label_visibility="collapsed")

    _photo_jobs = [it for it in _photo_items if it["treatment"] != "使わない"]
    st.divider()

    # カットを増やす（選択のみ・生成は下の「画像化する」で写真とまとめて実行）
    _pl_render_absorb_select(pl_rooms, items)

    # 生成内訳：写真＋補完＋3Dパース（間取り図stillは生成対象外なので含めない）
    _n_photo = len(_photo_jobs)
    _n_gap = len(st.session_state.get("pl_gap_targets") or [])
    _n_persp = 1 if (st.session_state.get("pl_make_persp")
                     and st.session_state.get("pl_floorplan") is not None) else 0
    _n_gen = _n_photo + _n_gap + _n_persp
    _cost = _n_gen * _PL_GEN_UNIT_USD
    _parts = [f"写真{_n_photo}枚"]
    if _n_gap:
        _parts.append(f"補完{_n_gap}枚")
    if _n_persp:
        _parts.append("3Dパース1枚")
    st.warning("⚠️ 次の「画像化」で Gemini の生成コストが発生します。")
    if st.button(f"② {'＋'.join(_parts)} を画像化する（約${_cost:.2f}・並行生成）",
                 type="primary", disabled=(_n_gen == 0), key="pl_gen_btn",
                 use_container_width=True):
        _pl_run_all_generation(style_name, model, aspect, req)


def _pl_gap_facts_block(facts):
    """補完/3D生成プロンプトへ前置する事実ブロック（設備・築年）＋記載外設備の禁止（優良誤認防止）。"""
    eq = (facts.get("equipment") or "").strip()
    built = (facts.get("built") or "").strip()
    parts = ["【この住戸の確定事実（マイソク記載）】"]
    parts.append(f"・設備は次の記載のみ：{eq[:300]}" if eq else "・設備の特記なし。")
    if built:
        parts.append(f"・{built}。築年相当の年式感を保ち、新築同様には描かない。")
    parts.append(
        "【厳守】マイソクに記載の無い設備を絶対に描かない（創作＝優良誤認）。"
        "記載が無ければ 追い焚き（リモコン）・浴室乾燥・浴室の窓・"
        "温水洗浄便座・トイレの窓・手洗いカウンター・室内洗濯機置場・"
        "ウッドデッキ・造作棚 などは描かない。"
        "実在しない設備・広さ・眺望を足して誇張しない。")
    return "\n".join(parts)


def _pl_staging_facts_block(facts, washer_ok=False, wreason="", reno=False):
    """ステージング/水回り/リノベ生成へ前置する『3条件の事実ガード』ブロック。
    足してよいのは ①持ち込み小物・家具 ②設備欄記載の設備 ③元画像に痕跡のある設備 の3つだけ。
    washer_ok: 洗濯機を置いてよいか（防水パン写り込み or ご要望）。wreason: その理由文字列。
    reno=True はリノベモード（既存設備の"更新"は可・"新設"は不可）。"""
    eq = (facts.get("equipment") or "").strip()
    built = (facts.get("built") or "").strip()
    parts = ["【この住戸の確定事実（マイソク記載）】"]
    parts.append(f"・設備欄の記載：{eq[:300]}" if eq else "・設備欄に特記なし。")
    if built:
        parts.append(f"・{built}。築年相当の年式感を保ち、新築同様には描かない。")
    if washer_ok:
        parts.append(f"・室内洗濯機置場：{wreason}。"
                     "防水パンの上に生活感のある洗濯機を1台だけ置いてよい。")
    else:
        parts.append("・この写真には防水パンが写っていないため、洗濯機は描かない"
                     "（洗濯機は防水パンが写る写真にのみ、その上に1台だけ置く）。")
    # ①は建物側の前提が不要な持ち込み物のみ（カーテン＝窓の存在を含意するので①から外し③へ）
    _bring_in = ("ソファ・テーブル・ラグ・照明スタンド・観葉植物・タオル・"
                 "スリッパ・カゴ・アート等（建物側の前提が要らない物のみ）")
    # ③の痕跡条件：洗濯機↔防水パン と同じ構造で、カーテン↔窓・下駄箱↔玄関造作 を条件化
    _traces = ("とくに 洗濯機は防水パンが写る写真にのみパンの上に1台／"
               "カーテンは窓が写っている場合のみその窓に掛ける／"
               "下駄箱・靴箱は玄関の造作が写っている場合のみ。"
               "痕跡（防水パン・窓・玄関造作）が無ければ足さない")
    # 開口部の捏造禁止＋逃げ道（明るさは照明・採光で。窓を足して明るくしない）
    _opening = ("入力画像で壁になっている面に、窓・扉・開口部・別室への抜けを一切新設しない"
                "（壁は壁のまま維持）。窓の無い壁にカーテンを描かない"
                "（カーテンは窓の存在を含意するため＝窓の捏造につながる）。"
                "明るさは照明・採光の演出で表現し、窓を足して明るくしない。")
    if reno:
        parts.append(
            "【リノベ後イメージで反映してよいのは次だけ】"
            f"①持ち込みの小物・家具（{_bring_in}）。"
            "②上記『設備欄の記載』にある設備、および既存設備の更新（グレードアップ・刷新）。"
            f"③元画像に痕跡が写っている設備の更新（{_traces}）。"
            "【厳守】記載にも写真にも無い設備・開口部を新設しない"
            "（リノベでも設備・窓の“新設”は不可・“更新”のみ＝優良誤認の防止）。"
            f"{_opening}"
            "食洗機・下駄箱・追い焚き・浴室乾燥・独立洗面台・造作棚・収納を新たに足さない。")
    else:
        parts.append(
            "【画像に足してよいのは次の3つだけ】"
            f"①持ち込みの小物・家具（{_bring_in}）＝常にOK。"
            "②上記『設備欄の記載』にある設備。"
            f"③元画像に痕跡が写っている設備（{_traces}）。"
            "【厳守】上記①〜③以外の設備・開口部を新設しない（＝優良誤認の防止）。"
            f"{_opening}"
            "食洗機・下駄箱・追い焚き・浴室乾燥・独立洗面台・造作棚・収納などを新たに描き足さない。")
    parts.append("※このあと（↓）にユーザーの個別のご要望が続く場合、"
                 "設備に関する指定はそちらを優先する（人の補足を尊重する）。")
    return "\n".join(parts)


def _pl_stage_facts_for(it, req):
    """reno/家具ステージング/水回り の item に前置する事実ブロックを返す（該当外は ""）。
    ※session_state を読むためメインスレッドから呼ぶこと（worker threadでは呼ばない）。"""
    import re
    t = it.get("treatment")
    if t not in ("リノベ後イメージ", "家具ステージング", "水回り・玄関を演出"):
        return ""
    facts = st.session_state.get("pl_facts", {})
    raw = it.get("_raw_codes") or []
    pan = "WASHER_PAN" in raw
    user_washer = bool(re.search(r"洗濯機|室内洗濯", req or ""))
    washer_ok = pan or user_washer
    wreason = ("写真で確認（防水パンあり）" if pan
               else ("ご要望のため" if user_washer else ""))
    return _pl_staging_facts_block(facts, washer_ok, wreason,
                                   reno=(t == "リノベ後イメージ"))


def _pl_pending_absorb_items(items, pl_rooms):
    """選択に基づく補完/3Dパースの pending item（gen_bytes=None）を作る。生成はしない。"""
    pending = []
    floorplan = st.session_state.get("pl_floorplan")
    facts_block = _pl_gap_facts_block(st.session_state.get("pl_facts", {}))  # 事実ガード
    base = floorplan or next(
        (it.get("src_bytes") for it in items if it.get("_origin", "photo") == "photo"), None)
    if base is not None:                      # 補完生成（間取り図/実写真を土台）
        anchor = _pl_pick_anchor(items)
        byid = {r["id"]: r for r in (pl_rooms or [])}
        for rid in (st.session_state.get("pl_gap_targets") or []):
            r = byid.get(rid)
            if not r:
                continue
            nid = _pl_next_item_id(items + pending)
            pending.append({"id": nid, "order": 10000 + nid, "src_bytes": base,
                            "room": r["type"], "treatment": "補完生成", "gen_bytes": None,
                            "caption": "", "jo": r.get("jo"), "room_id": rid,
                            "disc": _PL_GAP_DISC, "_origin": "gap",
                            "_gap_base": base, "_gap_anchor": anchor,
                            "_gap_facts": facts_block})
    if st.session_state.get("pl_make_persp") and floorplan is not None:  # 3Dパース
        nid = _pl_next_item_id(items + pending)
        pending.append({"id": nid, "order": 20000 + nid, "src_bytes": floorplan,
                        "room": "その他", "treatment": "3Dパース（試験）", "gen_bytes": None,
                        "caption": "", "jo": None, "room_id": None,
                        "disc": _PL_PERSP_DISC, "_origin": "persp",
                        "_gap_facts": facts_block})
    return pending


def _pl_run_all_generation(style_name, model, aspect, req):
    """写真item＋補完生成＋3Dパースを1回の並行生成でまとめて実行→関所へ。"""
    photos = [it for it in st.session_state.get("pl_items", [])
              if it.get("_origin", "photo") == "photo"]   # 既存gap/perspは作り直す
    pending = _pl_pending_absorb_items(photos, st.session_state.get("pl_rooms", []))
    items = photos + pending
    st.session_state["pl_items"] = items
    jobs = [it for it in items
            if (it.get("_origin", "photo") == "photo" and it["treatment"] != "使わない")
            or it.get("_origin") in ("gap", "persp")]
    _pl_run_generation(jobs, style_name, model, aspect, req)


def _pl_render_absorb_select(pl_rooms, items):
    """カットを増やす＝選択のみ（補完対象部屋・3D ON/OFF・間取り図 ON/OFF）。生成は「画像化」で。"""
    floorplan = st.session_state.get("pl_floorplan")
    with st.expander("🧩 カットを増やす（写真の無い部屋・間取り図・3Dパース）", expanded=False):
        st.caption("ここでは選ぶだけ。下の「画像化する」で写真とまとめて生成されます。")
        # B: 間取り図（生成しない静止カット）
        if "pl_include_fp" not in st.session_state:
            st.session_state["pl_include_fp"] = True
        st.checkbox("間取り図を1カットとして含める（動画は末尾／画像ZIPにも追加）",
                    key="pl_include_fp", disabled=(floorplan is None),
                    help="生成AIを通さず実物のまま。動画では静止クリップ（morphしない・fal課金なし）。")
        if floorplan is None:
            st.caption("※間取り図が未検出のため無効（サイドバーで手動指定できます）。")
        st.divider()
        # A: 補完生成の選択（生成は「画像化」でまとめて）
        _cands = _pl_gap_candidate_rooms(pl_rooms)
        _cov = _pl_room_coverage(items)   # 部屋種別→写り込み写真（判断材料・ハード除外しない）
        st.markdown("**写真の無い部屋を生成して補う**")
        st.caption("居室（LDK・洋室・寝室）は実写真が必要です（補完生成の対象外）。"
                   "水回り・玄関・バルコニー等を間取り図から補います。")
        if _cands:
            # ★安全側：既定は全てOFF。写り込みの可能性はラベルに付記し、人が見て選ぶ
            st.warning("⚠️ 選んだ部屋を間取り図からAI生成します。**下の実写真に写り込んでいないか"
                       "確認**し、写っていない部屋だけ選んでください（実写とAI生成の併存を防ぐ）。")
            _optids = [r["id"] for r in _cands]

            def _lbl_of(rid):
                r = next((x for x in _cands if x["id"] == rid), None)
                if not r:
                    return str(rid)
                hit = _cov.get(r["type"])
                return (f"{r['name']}  ⚠️ 実写真に写り込みの可能性（{'・'.join(hit)}）"
                        if hit else r["name"])

            cur = st.session_state.get("pl_gap_targets")
            if cur is None:   # 生成前seed＝全OFF（人が選ぶ）
                st.session_state["pl_gap_targets"] = []
            else:             # stale id を除外して例外回避
                st.session_state["pl_gap_targets"] = [i for i in cur if i in _optids]
            st.multiselect("補う部屋（既定OFF・写り込みを確認して選ぶ）", _optids,
                           format_func=_lbl_of, key="pl_gap_targets")
            # 実写真サムネイル一覧（写り込み確認用・#Nは上のラベルと対応）
            _photos = [it for it in items if it.get("_origin", "photo") == "photo"
                       and it.get("treatment") != "使わない"]
            if _photos:
                st.caption("実写真（写り込み確認用・番号は上の⚠️と対応）：")
                _cols = st.columns(min(len(_photos), 6))
                for _k, _it in enumerate(_photos, 1):
                    with _cols[(_k - 1) % len(_cols)]:
                        st.image(_it["src_bytes"], use_container_width=True)
                        _tags = "・".join(dict.fromkeys(_it.get("_codes") or [_it.get("room", "")]))
                        st.caption(f"#{_k} {_tags or _it.get('room', '')}")
        elif pl_rooms:
            st.caption("補完候補の部屋がありません（居室・外観のみ）。")
        else:
            st.caption("※マイソク（間取り）が無いため候補を出せません（手持ち写真のみ）。")
        st.divider()
        # C: 3Dパース（試験）
        st.markdown("**3Dパース（間取り俯瞰イメージ・試験）**")
        if "pl_make_persp" not in st.session_state:
            st.session_state["pl_make_persp"] = False
        st.checkbox("3Dパース（試験）を作る", key="pl_make_persp", disabled=(floorplan is None),
                    help="間取り図から俯瞰イメージを生成（品質は不安定＝試験）。動画では静止クリップ。")


def _pl_stage_review():
    st.markdown("#### ③ 確認（Before / After）")
    items = st.session_state.get("pl_items", [])
    gen_items = [it for it in items if it.get("gen_bytes")]
    if not gen_items:
        st.warning("生成画像がありません。取り込みに戻ってやり直してください。")
        if st.button("← 取り込みに戻る", key="pl_back_input0"):
            st.session_state["pl_stage"] = "input"; st.rerun()
        return
    st.caption("各画像を Before/After で確認。採用／除外／この画像だけ再生成／並べ替え が選べます。"
               "この並び順で動画が連結されます。動画化は下のボタンから（falコスト発生）。")
    # D: 初回入場時に動線順を自動適用（以降は手動並べ替えを尊重して再適用しない）
    if not st.session_state.get("pl_reordered"):
        _pl_auto_reorder()
        st.session_state["pl_reordered"] = True
    # C: いつでも動線順に一発整列
    if st.button("↕ 動線順に整列（玄関→LDK→洋室→…→水回り→バルコニー）",
                 key="pl_reorder_btn", use_container_width=True):
        _pl_auto_reorder(); st.rerun()
    try:
        client = make_client()
    except RuntimeError:
        client = None

    ordered = _pl_gen_sorted()     # A: order 昇順で表示
    n = len(ordered)
    for pos, it in enumerate(ordered):
        i = it["id"]
        with st.container(border=True):
            # B: ↑/↓ で隣と並べ替え（端は無効）
            mv1, mv2, mv3 = st.columns([1, 1, 6])
            if mv1.button("↑ 上へ", key=f"pl_up_{i}", disabled=(pos == 0),
                          use_container_width=True):
                _pl_move(i, -1); st.rerun()
            if mv2.button("↓ 下へ", key=f"pl_down_{i}", disabled=(pos == n - 1),
                          use_container_width=True):
                _pl_move(i, +1); st.rerun()
            mv3.caption(f"{pos + 1}番目 / {n}枚　・　{it.get('room', '')}")
            bc, ac = st.columns(2)
            bc.caption("Before（元）")
            bc.image(it["src_bytes"], use_container_width=True)
            ac.caption("After（生成）")
            ac.image(it["gen_bytes"], use_container_width=True)
            if it.get("disc"):   # gen_bytesは注記なし。文言を明示（保存/動画/表紙の出力時に付与）
                ac.caption(f"{it['disc']}（保存・動画・表紙の出力時に付与されます）")
            o1, o2, o3 = st.columns([1, 1, 2])
            it["_adopt"] = o1.checkbox("採用", value=it.get("_adopt", True),
                                       key=f"pl_adopt_{i}")
            it["room"] = o2.selectbox(
                "部屋", PL_ROOMS,
                index=_pl_sel_index(PL_ROOMS, it["room"], len(PL_ROOMS) - 1),
                key=f"pl_rv_room_{i}")
            it["caption"] = o3.text_input("字幕（動画・空で無し）",
                                          value=it.get("caption", ""), key=f"pl_rvcap_{i}")
            it["regen_note"] = st.text_input(
                "追加指示（この画像だけ・任意）", value=it.get("regen_note", ""),
                key=f"pl_note_{i}",
                placeholder="例：家族で暮らす感じで玄関に靴を足して／奥の開口と右手前の扉はそのまま残して")
            _origin = it.get("_origin", "photo")
            if _origin == "photo":
                rc1, rc2 = st.columns([1, 1])
                it["treatment"] = rc1.selectbox(
                    "処理（再生成用）", PL_TREATMENTS[:-1],
                    index=_pl_sel_index(PL_TREATMENTS[:-1], it["treatment"], 0),
                    key=f"pl_rv_treat_{i}")
                _do_regen = rc2.button("この画像だけ再生成", key=f"pl_regen_{i}",
                                       use_container_width=True)
            elif _origin == "gap":
                st.caption("補完生成（写真の無い部屋）。処理は固定。部屋・追加指示を変えて再生成できます。")
                _do_regen = st.button("この画像だけ再生成", key=f"pl_regen_{i}",
                                      use_container_width=True)
            else:  # persp（3Dパース・試験）＝間取り図から再生成できる
                st.caption("3Dパース（試験）。間取り図を土台に、追加指示を変えて再生成できます。")
                _do_regen = st.button("この画像だけ再生成", key=f"pl_regen_{i}",
                                      use_container_width=True)
            if _do_regen:
                if client is None:
                    st.error("APIキーが未設定です（設定ページで確認）。")
                else:
                    style_desc = core.INTERIOR_STYLES[
                        st.session_state.get("pl_style", list(core.INTERIOR_STYLES)[0])]
                    # 全体要望＋この画像の追加指示を併結（個別メモを後置＝優先的に効く）
                    _req = "\n".join(x for x in [
                        st.session_state.get("pl_request", ""),
                        it.get("regen_note", "")] if x)
                    it["_stage_facts"] = _pl_stage_facts_for(it, _req)  # 事実ガード（メイン側）
                    with st.spinner(f"#{i+1} を再生成中…"):
                        data, err, disc = _pl_generate_one(
                            client, it, style_desc,
                            st.session_state.get("pl_model", core.MODELS[0]),
                            st.session_state.get("pl_aspect", "4:5"),
                            _req)
                    if err:
                        st.error(f"再生成失敗: {err}")
                    else:
                        it["gen_bytes"] = _pl_crop_gen(data, it)  # 白帯除去（3Dパースは除外）
                        it["disc"] = disc
                        st.rerun()

    adopted = [it for it in ordered if it.get("_adopt", True)]   # 動線順で採用
    st.divider()
    st.write(f"採用 **{len(adopted)}**枚 / 生成 {len(ordered)}枚（上の並び順で連結）")
    e1, e2, e3 = st.columns(3)
    if e1.button("← 取り込みに戻る", key="pl_back_input", use_container_width=True):
        st.session_state["pl_stage"] = "input"; st.rerun()
    if adopted:
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            for k, it in enumerate(adopted, 1):
                _b = it["gen_bytes"]
                if it.get("disc"):   # 画像ZIPには注記を焼く（法令要件の維持）
                    try:
                        _b = core.add_disclaimer(_b, it["disc"])
                    except Exception:  # noqa: BLE001
                        pass
                zf.writestr(f"{k:02d}_{it['room']}.png", _b)
            # 間取り図カット：実物のまま（生成AI非通過・注記なし）で末尾に添付
            _fp = st.session_state.get("pl_floorplan")
            if st.session_state.get("pl_include_fp") and _fp is not None:
                zf.writestr(f"{len(adopted)+1:02d}_間取り図.png", _fp)
        e2.download_button("画像だけ保存（ZIP）", zbuf.getvalue(), "naikan_set.zip",
                           "application/zip", key="pl_zip", use_container_width=True)
    if e3.button(f"→ 採用{len(adopted)}枚で動画化へ", type="primary",
                 disabled=(len(adopted) == 0), key="pl_to_video_next",
                 use_container_width=True):
        st.session_state["pl_stage"] = "video"; st.rerun()


# ── 表紙特大（P1b-2）用ヘルパー：数値は必ず facts 由来（LLM出力から持ち込まない）──
def _pl_cover_access_band(access):
    """access から『駅への直接徒歩』が最短のエントリを1つ返す（バス便は除外・事実そのまま）。無ければ ""。"""
    import re
    best, best_min = "", None
    for a in (access or []):
        if "バス" in a:                       # バス便の徒歩は駅からの直接徒歩ではない
            continue
        ms = [int(x) for x in re.findall(r"徒歩\s*(\d+)\s*分", a)]
        if ms and (best_min is None or min(ms) < best_min):
            best_min, best = min(ms), a.strip()
    return best


def _pl_cover_madori_area(facts):
    """間取り／面積の特大行（例 '2LDK 57.64㎡'）。間取タイプは [ ] の前まで。facts由来のみ。"""
    madori = (facts.get("madori", "") or "").split("[")[0].strip()
    area = (facts.get("area", "") or "").strip()
    return " ".join(x for x in (madori, area) if x)


def _pl_cover_default_src(adopted):
    """表紙素材の既定：最初のLDK→無ければ先頭の居室→無ければ先頭。"""
    for it in adopted:
        if it.get("room") == "LDK":
            return it["id"]
    for it in adopted:
        if it.get("room") in ("洋室", "寝室"):
            return it["id"]
    return adopted[0]["id"] if adopted else None


def _pl_stage_video():
    import os as _os
    import room_tour_video as rtv
    _os.environ["FAL_KEY"] = get_secret("FAL_KEY", _os.environ.get("FAL_KEY", ""))
    st.markdown("#### ④ 動画化（ルームツアー）")
    items = st.session_state.get("pl_items", [])
    adopted = [it for it in items if it.get("gen_bytes") and it.get("_adopt", True)]
    adopted.sort(key=lambda it: it.get("order", 0))
    if not adopted:
        st.warning("採用画像がありません。確認に戻ってください。")
        if st.button("← 確認に戻る", key="pl_back_review0"):
            st.session_state["pl_stage"] = "review"; st.rerun()
        return
    if not get_secret("FAL_KEY", ""):
        st.warning("FAL_KEY 未設定。Secrets に fal.ai の APIキーを追加してください。")

    st.caption(f"採用 {len(adopted)}枚 を順番に動画化して1本に連結します。（DL・再アップ不要）")
    _VID_ASPECT_LABEL = {"9:16": "9:16（リール/TikTok/ショート）", "1:1": "1:1（正方形）",
                         "16:9": "16:9（横）"}
    o1, o2, o3 = st.columns(3)
    v_model = o1.selectbox("モデル", list(rtv.FAL_MODELS), index=0, key="pl_v_model")
    v_dur = o2.selectbox("1本の長さ(秒)", [5, 10], index=0, key="pl_v_dur")
    v_bgm = o3.checkbox("BGMを付ける", value=True, key="pl_v_bgm")
    v_aspect = st.selectbox("動画の向き", ["9:16", "1:1", "16:9"], index=0, key="pl_v_aspect",
                            format_func=lambda a: _VID_ASPECT_LABEL.get(a, a))
    _FIT_LABEL = {"fill": "埋める（余白なし・端が少し切れる）",
                  "contain": "全体を見せる（上下に余白）"}
    v_fit = st.radio("余白の扱い", ["fill", "contain"], index=0, horizontal=True,
                     key="pl_v_fit", format_func=lambda m: _FIT_LABEL.get(m, m))
    st.caption("埋める＝余白ゼロですが、写真の端が少し切れます"
               "（正方形素材を9:16にすると左右が大きめに切れます）。")
    st.caption("正方形素材は 1:1 動画が最も無駄なし。横長できれいに見せたい場合は、"
               "元写真（横長の撮影原本）を『手持ち写真』の入口で取り込むと余白・トリミングが減ります。")
    v_caps = st.checkbox("シーンテロップ（部屋名＋情感2行）を焼く", value=True, key="pl_v_caps")
    st.caption("下は「全体既定」。個別に変えたい写真だけ、下の各シーンで上書きできます。")
    t1, t2 = st.columns(2)
    v_taste = t1.radio("テロップの見た目（全体既定）", ["clean", "pop"], index=0, key="pl_telop_taste",
                       format_func=lambda x: {"clean": "clean（白・影・すっきり）",
                                              "pop": "pop（座布団box）"}.get(x, x))
    v_pos = t2.selectbox("テロップの配置（全体既定）", _PL_TELOP_POSITIONS, index=0,
                         key="pl_telop_pos")
    t3, t4 = st.columns(2)
    v_lang = t3.radio("部屋名の表記", ["en", "ja"], index=0, key="pl_room_lang",
                      format_func=lambda x: {"en": "英（living room 10.9J）",
                                             "ja": "和（リビング 10.9帖）"}.get(x, x))
    v_open = t4.radio("冒頭タイトル", ["none", "flash"], index=0, key="pl_open_title",
                      format_func=lambda x: {"none": "無し（映像から開始・離脱防止）",
                                             "flash": "極短フラッシュ（0.5秒）"}.get(x, x))
    v_flash = ""
    if v_open == "flash":
        _f = st.session_state.get("pl_facts", {})
        _madori = (_f.get("madori", "").split("[")[0]).strip()
        _fdef = (f"{_f['name']} ｜ {_madori}" if _f.get("name") and _madori
                 else f"{_madori} ｜ {_f['area']}" if _madori and _f.get("area") else "")
        if "pl_flash_text" not in st.session_state:   # 生成前seed（未設定時のみ・既定=物件名｜間取り）
            st.session_state["pl_flash_text"] = _fdef
        v_flash = st.text_input("フラッシュ文言（先頭に0.5秒だけ重畳・短く）",
                                key="pl_flash_text", placeholder="例: ニューモート204 ｜ 2LDK")
    v_tag = st.text_input("上部タグ（物件名・間取り等／空欄で非表示）", key="pl_v_tag",
                          placeholder="例: ニューモート204 ｜ 2LDK 57.07㎡")
    v_note = st.text_input("画面注記（右下・景表法配慮／空欄で非表示）", key="pl_v_note",
                           placeholder="例: ※画像はイメージです")

    # ── PRコピーをAIで下書き（Gemini 1回・押下時のみ）────────────────────────
    with st.expander("✍️ PRコピーをAIで下書き（タイトル3案・情感2行）", expanded=False):
        st.caption("マイソクの事実だけを根拠に下書きします。誇大語・事実外の数値は自動除去。"
                   "Gemini未設定/失敗でも簡易テンプレで続行します。")
        if st.button("PRコピーを下書き（AI・1回）", key="pl_prcopy_btn",
                     on_click=_pl_reset_title_choice):
            _facts = st.session_state.get("pl_facts", {})
            try:
                _client = make_client()
            except RuntimeError:
                _client = None
            if _client is None:
                st.warning("Gemini APIキーが未設定です。簡易テンプレのまま続行します。")
            else:
                _rooms = sorted({it["room"] for it in adopted})
                with st.spinner("PRコピーを下書き中…"):
                    _draft = core.draft_pr_copy(
                        _client, _facts.get("full_text", ""),
                        {k: v for k, v in _facts.items() if k != "full_text"}, _rooms)
                if not _draft:
                    st.warning("AI下書きに失敗しました。簡易テンプレのまま続行します。")
                else:
                    st.session_state["pl_prcopy"] = _draft
                    # 情感2行を room_subs で初期化（ユーザー編集済み＝テンプレ差分は尊重）
                    for it in adopted:
                        sub = _draft.get("room_subs", {}).get(it["room"])
                        if not sub:
                            continue
                        cur = st.session_state.get(f"pl_capsub_{it['id']}")
                        if cur is None or not cur.strip() or cur == _pl_caption_sub(it):
                            st.session_state[f"pl_capsub_{it['id']}"] = sub
                    st.rerun()
        _draft = st.session_state.get("pl_prcopy")
        if _draft:
            if _draft.get("fallback"):
                st.warning("AI候補が作れませんでした（事実に合う短い案が無し）。"
                           "簡易テンプレ（物件名 ｜ 間取り）を入れています。"
                           "手入力するか、もう一度お試しください。")
            if _draft.get("highlights"):
                st.markdown("**魅力ポイント**：" + "　".join(_draft["highlights"]))
            _titles = _draft.get("titles", [])
            if _titles:
                _labels = [f"[{t.get('direction', '')}] {t['title']}"
                           + (f" — {t['subtitle']}" if t.get("subtitle") else "")
                           for t in _titles]
                _idx = st.radio("タイトル案（方向性つき・クリック前に中身が見えます）",
                                list(range(len(_titles))),
                                format_func=lambda i: _labels[i], key="pl_title_idx",
                                on_change=_pl_apply_title_choice)   # 案切替→編集欄/表紙を即同期
                _sel = _titles[_idx]
                # 生成前seed（未設定時のみ＝初回render用。案切替時は on_change が上書きする）。
                # value= は key= と併用すると session_state 存在時に衝突するため使わない。
                if "pl_title_edit" not in st.session_state:
                    st.session_state["pl_title_edit"] = _sel.get("title", "")
                if "pl_sub_edit" not in st.session_state:
                    st.session_state["pl_sub_edit"] = _sel.get("subtitle", "")
                tc1, tc2 = st.columns(2)
                _t_title = tc1.text_input("タイトル（編集可）", key="pl_title_edit")
                tc2.text_input("サブタイトル（編集可）", key="pl_sub_edit")
                st.button("このタイトルを冒頭フラッシュに設定", key="pl_title_to_flash",
                          on_click=_pl_set_flash_title, args=(_t_title,))
                st.caption("表紙特大（P1b-2）＝タイトル大見出し＋サブ補足。冒頭フラッシュ＝短いタイトルのみ"
                           "（0.5秒では読めないためサブは載せません）。情感2行は各シーンに反映済み。")

    # ── 表紙特大（P1b-2）：リールカバー/カルーセル1枚目のPNG（動画本編には挿入しない）──
    with st.expander("🖼️ 表紙特大（リールカバー / カルーセル1枚目）を生成", expanded=False):
        st.caption("タイトル大見出し＋サブ＋◎魅力ポイント＋駅徒歩＋間取り/面積の1枚。"
                   "数値（徒歩分・㎡・間取り）はマイソクの事実のみ使用。"
                   "ffmpegのみ・fal課金なし・Gemini不要。動画本編には挿入しません（冒頭離脱を防ぐ設計）。")
        _cfacts = st.session_state.get("pl_facts", {})
        # タイトル/サブ：PRコピーで選んだ値を既定に（生成前seed・未設定時のみ＝地雷1回避）
        if "pl_cover_title" not in st.session_state:
            st.session_state["pl_cover_title"] = st.session_state.get("pl_title_edit", "")
        if "pl_cover_sub" not in st.session_state:
            st.session_state["pl_cover_sub"] = st.session_state.get("pl_sub_edit", "")
        cc1, cc2 = st.columns(2)
        cc1.text_input("タイトル（特大）", key="pl_cover_title",
                       placeholder="PRコピー下書きで選ぶと自動で入ります")
        cc2.text_input("サブタイトル（小）", key="pl_cover_sub")
        # 素材画像：既定=最初のLDK→居室→先頭（生成前seed＋stale idガード）
        _copts = [it["id"] for it in adopted]
        if st.session_state.get("pl_cover_src") not in _copts:
            st.session_state["pl_cover_src"] = _pl_cover_default_src(adopted)
        _clbl = {it["id"]: f"{p + 1}. {_PL_ROOM_JP.get(it['room'], it['room'])}"
                 for p, it in enumerate(adopted)}
        cs1, cs2 = st.columns([2, 1])
        cs1.selectbox("表紙の素材画像", _copts, key="pl_cover_src",
                      format_func=lambda i: _clbl.get(i, str(i)))
        cs2.radio("比率", ["9:16", "4:5"], key="pl_cover_aspect", horizontal=True,
                  format_func=lambda a: {"9:16": "9:16（カバー）",
                                         "4:5": "4:5（1枚目）"}.get(a, a))
        _chl = ((st.session_state.get("pl_prcopy") or {}).get("highlights") or [])[:3]
        _cband = _pl_cover_access_band(_cfacts.get("access"))
        _cma = _pl_cover_madori_area(_cfacts)
        if _chl:
            st.caption("◎ " + "　".join(_chl))
        st.caption(f"駅徒歩：{_cband or '（直接徒歩が取れず・省略）'}　／　間取り・面積：{_cma or '（取れず）'}")
        # 文字数チェック（超過は表紙で … 切り詰めになるため、短縮を促す。生成は止めない）
        _ctitle = st.session_state.get("pl_cover_title", "").strip()
        _csub = st.session_state.get("pl_cover_sub", "").strip()
        _clen = []
        if len(_ctitle) > core._PR_MAX_TITLE:
            _clen.append(f"タイトル {len(_ctitle)}字/上限{core._PR_MAX_TITLE}字")
        if len(_csub) > core._PR_MAX_SUBTITLE:
            _clen.append(f"サブ {len(_csub)}字/上限{core._PR_MAX_SUBTITLE}字")
        for _h in _chl:
            if len(str(_h).strip()) > core._PR_MAX_HIGHLIGHT:
                _clen.append(f"◎「{str(_h).strip()}」{len(str(_h).strip())}字/上限{core._PR_MAX_HIGHLIGHT}字")
        if _clen:
            st.warning("⚠️ 長すぎます（このままだと表紙で … に切り詰められます）："
                       + "／".join(_clen) + "。短くすると文意が保てます。")
        # 誇大・断定語の簡易チェック（編集後テキストにも念のため・警告のみで生成は止めない）
        _ctext = _ctitle + " " + _csub
        _cbad = [w for w in core._PR_BANNED if w in _ctext]
        if _cbad:
            st.warning(f"⚠️ 誇大・断定の可能性がある語：{'、'.join(_cbad)}"
                       "（景表法・掲載前に見直しを）")
        if st.button("表紙を生成（ffmpegのみ・課金なし）", key="pl_cover_gen"):
            _csrc = next((it for it in adopted
                          if it["id"] == st.session_state.get("pl_cover_src")), None)
            if not _csrc or not _csrc.get("gen_bytes"):
                st.error("素材画像が見つかりません。確認ステージで採用画像を用意してください。")
            else:
                _cfields = {
                    "title": st.session_state.get("pl_cover_title", ""),
                    "subtitle": st.session_state.get("pl_cover_sub", ""),
                    "highlights": _chl,
                    "access_band": _cband,
                    "madori_area": _cma,
                    "note": st.session_state.get("pl_v_note", "") or "※AI加工のイメージ",
                }
                _casp = st.session_state.get("pl_cover_aspect", "9:16")
                try:
                    with st.spinner("表紙を生成中…（ffmpeg）"):
                        _cpng = rtv.build_cover(_csrc["gen_bytes"], _cfields, aspect=_casp)
                    # 生成結果は非ウィジェットキーへ（地雷1回避）。取り込み時に削除される物件固有キー
                    st.session_state["pl_cover_png"] = {"aspect": _casp, "bytes": _cpng}
                except Exception as e:  # noqa: BLE001
                    st.error(f"表紙の生成に失敗しました: {e}")
        _cov = st.session_state.get("pl_cover_png")
        if _cov and _cov.get("bytes"):
            st.image(_cov["bytes"], caption=f"表紙プレビュー（{_cov.get('aspect', '')}）",
                     use_container_width=True)
            st.download_button(
                "⬇️ 表紙PNGをダウンロード", _cov["bytes"],
                file_name=f"cover_{_cov.get('aspect', '9:16').replace(':', 'x')}.png",
                mime="image/png", key="pl_cover_dl")
            st.caption("※タイトル/素材/比率を変えたら、再度「表紙を生成」を押すと更新されます。")

    # 部屋名表記を切り替えたら、各シーンのメイン文の自動下書きをリセットして追従させる
    if st.session_state.get("_pl_lang_sig") != v_lang:
        for it in adopted:
            st.session_state.pop(f"pl_capmain_{it['id']}", None)
        st.session_state["_pl_lang_sig"] = v_lang

    if v_caps:
        with st.expander(f"各シーンのテロップを編集（{len(adopted)}シーン・自動下書き）", expanded=False):
            st.caption("メイン＝部屋名＋帖（自動）。情感2行＝下書き。どちらも自由に編集できます。")
            st.caption("スタイル/配置の『既定に従う』＝部屋種別（居室clean／水回りpop）→全体既定 の順で自動決定。")
            _TASTE_LABEL = {"auto": "既定に従う", "clean": "clean（白・影）", "pop": "pop（座布団）"}
            _POS_LABEL = {"auto": "既定に従う", "下中央": "下中央", "下左": "下左",
                          "上中央": "上中央", "中央": "中央"}
            for pos, it in enumerate(adopted):
                st.markdown(f"**{pos + 1}. {_PL_ROOM_JP.get(it['room'], it['room'])}**")
                st.text_input("メイン", value=_pl_caption_main(it, v_lang),
                              key=f"pl_capmain_{it['id']}")
                st.text_area("情感2行（1行ずつ改行）", value=_pl_caption_sub(it),
                             key=f"pl_capsub_{it['id']}", height=70)
                sc1, sc2 = st.columns(2)
                sc1.selectbox("スタイル", ["auto", "clean", "pop"], index=0,
                              key=f"pl_taste_{it['id']}",
                              format_func=lambda x: _TASTE_LABEL.get(x, x))
                sc2.selectbox("配置", ["auto"] + _PL_TELOP_POSITIONS, index=0,
                              key=f"pl_pos_{it['id']}",
                              format_func=lambda x: _POS_LABEL.get(x, x))

    n = len(adopted)
    n_still = sum(1 for it in adopted if it.get("_origin") == "persp")  # 3Dパース＝fal非通過
    _fp_cut = bool(st.session_state.get("pl_include_fp")
                   and st.session_state.get("pl_floorplan") is not None)
    n_fal = n - n_still                          # fal課金は静止クリップを除いた本数
    est_usd = {"kling2.6_pro": 0.35, "kling2.1_pro": 0.49, "kling3.0_pro": 0.84}\
        .get(v_model, 0.35) * n_fal * (v_dur / 5)
    n_total = n + (1 if _fp_cut else 0)          # 間取り図stillを含む総カット数
    m1, m2 = st.columns(2)
    m1.metric("推定コスト", f"約 ${est_usd:.2f}", f"≈{est_usd*150:.0f}円 / fal {n_fal}本")
    m2.metric("推定所要時間", f"約 {round(n_fal * 1.0)}〜{round(n_fal * 1.5)}分")
    _still_msg = []
    if n_still:
        _still_msg.append(f"3Dパース{n_still}本")
    if _fp_cut:
        _still_msg.append("間取り図")
    st.caption(f"目安：全{n_total}カット。fal課金は{n_fal}本のみ"
               + (f"（{'・'.join(_still_msg)}は静止クリップ＝fal課金なし）" if _still_msg else "")
               + "。")

    bcol, gcol = st.columns([1, 2])
    if bcol.button("← 確認に戻る", key="pl_back_review", use_container_width=True):
        st.session_state["pl_stage"] = "review"; st.rerun()
    if gcol.button("🎬 ルームツアーを生成", type="primary", key="pl_v_gen",
                   use_container_width=True):
        if not get_secret("FAL_KEY", ""):
            st.error("FAL_KEY が未設定です。")
            return
        imgs = [(it.get("caption") or it["room"], it["gen_bytes"]) for it in adopted]
        room_types = [_pl_video_room_type(it["room"]) for it in adopted]
        # テロップ：編集済みがあれば優先、無ければ自動下書き
        captions = [st.session_state.get(f"pl_capmain_{it['id']}") or _pl_caption_main(it, v_lang)
                    for it in adopted]
        sub_captions = [st.session_state.get(f"pl_capsub_{it['id']}", "") for it in adopted]
        # スタイル/配置：画像ごと上書き→部屋種別自動→全体既定 の順で解決
        tastes = [_pl_resolve_taste(it, v_taste) for it in adopted]
        positions = [_pl_resolve_pos(it, v_pos) for it in adopted]
        # 注記：画面注記(v_note)が空でも必ず注記を焼く（法令）。個別は it["disc"]（リノベ/
        # ステージングで文言が異なる）→ 無ければ既定。build_tourで v_note があれば全体優先。
        notes = [it.get("disc") or "※AI加工のイメージ" for it in adopted]
        # 3Dパースは fal/Klingを通さず静止クリップ（俯瞰画像はmorphするため）
        still_flags = [it.get("_origin") == "persp" for it in adopted]
        # 間取り図カット：実物のまま末尾に静止クリップで追加（morphなし・fal課金なし）
        _fp = st.session_state.get("pl_floorplan")
        if st.session_state.get("pl_include_fp") and _fp is not None:
            _f = st.session_state.get("pl_facts", {})
            _mad = (_f.get("madori", "") or "").split("[")[0].strip()
            _ar = (_f.get("area", "") or "").strip()
            _fp_cap = " ".join(x for x in (_mad, _ar) if x) or "間取り図"
            imgs.append((_fp_cap, _fp)); captions.append(_fp_cap); sub_captions.append("")
            room_types.append("generic"); tastes.append(v_taste); positions.append("下中央")
            notes.append(""); still_flags.append(True)   # 実物＝注記なし・静止
        bar = st.progress(0.0)
        status = st.empty()

        def _pg(step, total, msg):
            bar.progress(min((step + 1) / (total + 1), 1.0))
            if step < total:
                status.write(f"全{total}枚中 {step+1}枚目を生成中…（1枚あたり約1〜1.5分）")
            else:
                status.write("連結中…（クロスフェード＋BGM）")

        try:
            out = rtv.build_tour(
                imgs, captions=captions if v_caps else [""] * len(imgs),
                sub_captions=sub_captions if v_caps else None,
                top_tag=v_tag, with_captions=v_caps, with_bgm=v_bgm,
                also_silent=True, model_key=v_model, duration=v_dur,
                room_types=room_types, image_note=v_note, notes=notes,
                still_flags=still_flags,
                taste=v_taste, tastes=tastes if v_caps else None,
                positions=positions if v_caps else None,
                flash_text=v_flash, aspect=v_aspect,
                fit_mode=v_fit, progress=_pg)
            bar.progress(1.0)
            status.write("完成")
            st.success("ルームツアーを生成しました。")
            if out.get("silent"):
                st.video(out["silent"])
                st.download_button("⬇️ 無音版 mp4", out["silent"],
                                   file_name="room_tour_silent.mp4",
                                   mime="video/mp4", key="pl_dl_silent")
            if out.get("bgm"):
                st.video(out["bgm"])
                st.download_button("⬇️ BGM版 mp4", out["bgm"],
                                   file_name="room_tour_bgm.mp4",
                                   mime="video/mp4", key="pl_dl_bgm")
        except Exception as e:  # noqa: BLE001
            st.error(f"生成に失敗しました: {e}")


def render_pipeline():
    st.subheader("物件から動画をつくる")
    st.caption("マイソクPDF や 手持ち写真 → 内観画像 → ルームツアー動画 までを一気通貫で。"
               "（途中のダウンロード・再アップは不要）")
    stage = st.session_state.get("pl_stage", "input")
    steps = ["① 取り込み・種別", "② 画像化", "③ 確認", "④ 動画化"]
    cur = {"input": 0, "review": 2, "video": 3}.get(stage, 0)
    st.caption("　→　".join(f"**{s}**" if i == cur else s for i, s in enumerate(steps)))
    if st.button("最初からやり直す", key="pl_reset_btn"):
        _pl_reset(); st.rerun()
    st.divider()
    if stage == "review":
        _pl_stage_review()
    elif stage == "video":
        _pl_stage_video()
    else:
        _pl_stage_input()
    # 間取り図をサイドバーに常時ピン留め（取り込み〜関所まで見ながら選べる）。
    # ※ ステージ処理の「後」に描く：取り込み直後の実行で _pl_stage_input() が
    #    pl_floorplan/pl_items をセットしてから読むため、追加操作なしで即表示される。
    #    st.sidebar はスクリプト内のどこで呼んでもサイドバーに反映されるので位置移動だけで解決。
    #    分岐内で pl_stage が変わり得るため読み直してガードする（遷移時は st.rerun 済みで通常未到達）。
    if st.session_state.get("pl_stage", "input") in ("input", "review", "video"):
        _pl_render_floorplan_sidebar()


# ======================================================================
# ナビゲーション（サイドバー常設メニュー）
#   ※ B2b-1: 「物件から動画をつくる」(render_pipeline) を追加。
#     旧「動画/内観/実写真ステージング」は残置（並存）。撤去はB2b-2。
# ======================================================================
page_home = st.Page(render_home, title="ホーム", icon=":material/home:", default=True)
page_pipeline = st.Page(render_pipeline, title="物件から動画をつくる",
                        icon=":material/auto_awesome_motion:")
page_video = st.Page(render_video, title="動画をつくる（画像から）", icon=":material/movie:")
page_maisoku = st.Page(render_maisoku, title="内観画像をつくる（マイソク→内観）",
                       icon=":material/apartment:")
page_stage = st.Page(render_stage, title="実写真ステージング", icon=":material/chair:")
page_carousel = st.Page(render_carousel, title="カルーセルをつくる",
                        icon=":material/view_carousel:")
page_background = st.Page(render_background, title="背景素材をつくる",
                         icon=":material/image:")
page_settings = st.Page(render_settings, title="設定", icon=":material/settings:")

nav = st.navigation({
    "": [page_home],
    "つくる": [page_pipeline, page_video, page_maisoku, page_stage,
             page_carousel, page_background],
    "その他": [page_settings],
})
nav.run()
# 脚注は nav.run() の後に置き、ページがサイドバーに足す内容（間取り図ピン留め等）を
# ナビ直下＝上部に寄せる（下部に置くと selectbox 等が画面外に溢れるため）
with st.sidebar:
    st.caption("生成画像にはSynthIDの不可視透かしが入ります。"
               "商用利用可否はGoogleの利用規約を最終確認してください。")
    st.caption("build: crop-fix-v53 (白帯crop判定を実20枚で再設計・bright/jump境界で頑健化)")
