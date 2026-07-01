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
    st.caption("build: topic-fix-v2")

st.title("🏠 SNS画像量産ツール")

tab_single, tab_carousel = st.tabs(["🖼️ 単発画像量産", "📚 カルーセル自動生成"])


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
