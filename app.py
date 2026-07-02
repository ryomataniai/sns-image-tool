# -*- coding: utf-8 -*-
"""
SNS画像量産ツール — 社内Web版 (app.py)
========================================
2モード:
  1) 単発画像量産  … プロンプト一覧から「暮らしのイメージ」を一括生成
  2) カルーセル自動生成 … トピック→コピー生成→背景生成→文字焼き込みで完成カルーセル

生成ロジックは core.py / carousel.py を共有。
"""

import io
import os
import zipfile
from pathlib import Path

import streamlit as st

import core
import carousel

st.set_page_config(page_title="SNS画像量産ツール", page_icon="🏠", layout="wide")


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

    st.title("🔒 SNS画像量産ツール")
    st.caption("エンクス社内ツール。パスワードを入力してください。")
    st.text_input("パスワード", type="password", key="pw_input", on_change=_verify)
    if st.session_state.get("auth_ok") is False:
        st.error("パスワードが違います。")
    st.stop()


check_password()
GEMINI_KEY = get_secret("GEMINI_API_KEY") or core.get_api_key()


def make_client():
    return core.get_client(GEMINI_KEY)


# ----------------------------------------------------------------------
# サイドバー（共通）
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 設定")
    if GEMINI_KEY:
        st.success("APIキー: 検出済み（Secrets/環境変数）")
        sidebar_key = ""
    else:
        sidebar_key = st.text_input(
            "Gemini APIキー", type="password",
            help="https://aistudio.google.com/apikey で取得",
        )
        if sidebar_key:
            GEMINI_KEY = sidebar_key
    st.caption("⚠️ 生成画像にはSynthIDの不可視透かしが入ります。"
               "商用利用可否はGoogleの利用規約を最終確認してください。")
    st.caption("build: stage-v17 (実写真ルームツアーに間取り図カットを追加)")

st.title("🏠 SNS画像量産ツール")

tab_single, tab_carousel, tab_maisoku, tab_stage = st.tabs(
    ["🖼️ 単発画像量産", "📚 カルーセル自動生成", "🏠 マイソク→内観", "🛋 実写真ステージング"])


# ======================================================================
# タブ1: 単発画像量産
# ======================================================================
with tab_single:
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
# タブ2: カルーセル自動生成
# ======================================================================
with tab_carousel:
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
# タブ3: マイソク／間取り図 → 内観シミュレーション（技術検証用）
# ======================================================================
with tab_maisoku:
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
                                   "ルームツアー（複数カット）",
                                   "3Dパース（間取り俯瞰イメージ・試験）"],
                    key="m_mode")

    # モード別オプション
    room = core.INTERIOR_ROOMS[0]
    rooms, keep_style, ref_photo = [], True, None
    gap_rooms = []
    if mode.startswith("マイソク丸ごと"):
        st.caption("マイソク内の実際の室内写真を土台に家具ステージングします。"
                   "実物ベースなので間取りと乖離しません。写真の無い部屋だけ、実写真のトーンに"
                   "合わせて生成で補います（間取り図も自動抽出して整合を取ります）。")
        gap_rooms = st.multiselect(
            "写真が無い部屋で生成して補うもの", ["玄関", "トイレ", "洗面所", "浴室", "バルコニー"],
            default=["トイレ"], key="m_gap",
            help="マイソクに写真が無い部屋だけをここから生成します。"
                 "実写真がある部屋は自動で除外されるので、重複はしません。")
        st.checkbox("間取り図もカットに含める（SNSツアー用・抽出した実物をそのまま添付）",
                    value=True, key="m_include_fp",
                    help="マイソクから抽出した間取り図を、ツアーの1カットとして出力に含めます。"
                         "生成AIは通さず実物をそのまま使うので正確です。")
    elif mode.startswith("ルームツアー"):
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

    gen_disabled = (input_png is None) or (mode.startswith("ルームツアー") and not rooms)
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
                    st.caption(
                        f"実写真 {len(real)}枚をステージング＋写真の無い部屋 {len(gaps)}件を生成します。"
                        + ("間取り図も1カットとして添付します。" if (
                            st.session_state.get("m_include_fp") and floor_plan is not None)
                           else ""))
                    prog = st.progress(0.0, text="実写真をステージング中…")
                    done = 0
                    seen = {}
                    # ① 実写真を構造維持で演出（実物ベース＝間取りと乖離しない）
                    for it in real:
                        lbl = it["label"]
                        seen[lbl] = seen.get(lbl, 0) + 1
                        disp = lbl if seen[lbl] == 1 else f"{lbl}{seen[lbl]}"
                        tr = it["treatment"]
                        if tr == "staging_living":
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
                            st.error(f"{disp}（実写真）生成失敗: {err}")
                        else:
                            results.append((f"{disp}（実写真）", data))
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
        elif mode.startswith("ルームツアー"):
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
            prog = st.progress(0.0, text="ルームツアーを生成中…")
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
        st.caption("※SNS投稿時は運用ルールに従い『※AI加工のイメージです』の注記を焼き込み、"
                   "エリアは市区・駅ぼかしまで。")


# ======================================================================
# タブ4: 実写真ステージング（マイソクの実室内写真→高解像度化＋家具）
# ======================================================================
with tab_stage:
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
        st.caption("※SNS投稿時は『※AI加工のイメージです』の注記を焼き込み、"
                   "エリアは市区・駅ぼかしまで。設備・広さは実物基準を崩さないこと。")
