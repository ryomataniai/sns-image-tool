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
import re
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
    st.info("マイソク／写真から、内観画像 → ルームツアー動画までを "
            "**「物件から動画をつくる」** の1本に集約しました。")
    st.markdown("#### 何をしますか？")

    # (page, icon, 名称, 1行説明, よく使う)
    cards = [
        (page_pipeline, ":material/auto_awesome_motion:", "物件から動画をつくる",
         "マイソク/写真 → 内観画像 → ルームツアー動画までを一気通貫で。", True),
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

    _render_home_disclaimer()


def _render_home_disclaimer():
    """掲載事故防止のための注意事項（ホーム常時表示）。"""
    st.warning(
        "**⚠️ ご利用上の注意（SNS・広告に掲載する前に必ず確認）**\n\n"
        "- **AI生成画像は実物写真ではありません。** 補完生成・3Dパース・ステージング画像には"
        "「AI生成イメージ」等の注記が自動で入ります。**注記を削除・トリミングして掲載しないでください。**\n"
        "- **掲載前に宅建業法・不動産広告規約（公取協）の専門家確認が必須です。** "
        "本ツールの事実性ガードは掲載事故を減らすためのもので、適法性を保証するものではありません。\n"
        "- **おとり広告の禁止**：成約済み・取引できない物件、実在しない条件での掲載はできません。"
        "掲載時点で募集中であることを必ず確認してください。\n"
        "- **取引態様の明示**：広告には取引態様（仲介・貸主 等）の表示が必要です。\n"
        "- **徒歩時間の表記**：道路距離80m＝1分（端数切り上げ）で算出してください。"
        "駅名と徒歩分数の組み合わせはマイソク原本と照合してください。",
        icon=":material/gavel:",
    )


def render_settings():
    st.title("設定")
    if GEMINI_KEY:
        st.success("APIキー: 検出済み（Secrets/環境変数）")
    else:
        st.text_input("Gemini APIキー", type="password", key="manual_gemini_key",
                      help="https://aistudio.google.com/apikey で取得")
    st.caption("生成画像にはSynthIDの不可視透かしが入ります。"
               "商用利用可否はGoogleの利用規約を最終確認してください。")
    _render_caption_template_editor()
    _render_video_env_diagnostics()
    st.caption("build: v79-feature-reach2 (★②自分を整える部屋の【厳守】1行を経路非依存の書き方へ訂正。旧文『独立洗面台・浴室乾燥…等を、写真に無いのに描き加えない』は、補完生成（写真の無い部屋を間取り図から起こす経路）では『写真そのものが存在しない』ため『独立洗面台を描くな』と読まれうる。②は独立洗面台が訴求の芯で、洗面は写真が付いていないことが多い＝②の主力カットがまさにこの経路に乗る。③の『物件事実に無いのに』と同じ書き方へ揃え『この住戸に実在しないのに描き加えない（入力画像に写っておらず、物件事実にも記載が無いもの）』へ。staging（実写真）経路の意味は変わらない（写真に写っておらず物件事実にも無い＝従来の写真に無いと実質同義）。補完生成には _gap_facts（設備・築年の事実ガード）が前置されているので記載の有無はプロンプト内で判定できる。★受入(0円): normal / mote_heya / hobby は staging・補完生成・3Dパースとも変更前とバイト一致（②の定義しか触っていない証明）。②のみ staging 15ブロック・補完生成17ブロックでこの1行だけが差し替わることを diff で確認。新文言が②のban語（安心/安全/防犯等）を踏まないことも確認。既存テスト（scrub-clause 22件 / back-direction 22件 / feature-reach(b) 全件）通過。以下 v79-feature-reach: ★特集の到達範囲を揃えた（依頼文v1.3 §8）。(a) 補完生成 build_room_tour_prompt と 3Dパース build_3d_perspective_prompt に concept_staging 引数を追加しapp 側から feature_staging(pl_feature) を渡す＝写真の無い部屋（間取り図から起こす内観）だけ特集が1バイトも届いていなかった穴を塞ぐ。★本丸=ROOM_TOUR_FURNITURE の競合解消: 洋室=ベッドを主役に と③趣味部屋=デスク+本棚 が1枚のプロンプト内で正面衝突していた。furni_line 直後に優先順の1行を足して解く（特集の方向づけがあれば家具の種類はそちらを優先／用途に合わない家具＝トイレのソファ・水回りのベッドは特集に関わらず厳守）。ROOM_TOUR_FURNITURE の中身は1文字も変えていない。★concept_staging が空のときは改行すら足さない実装にした＝特集なし(normal)は変更前とバイト一致。（最初の実装で空行が1本入り normal のバイト一致が崩れたのを自己テストで検出して修正）(b) STORY_SITUATIONS の各エントリに feature を追加し story_situations_for(rooms, feature_id) へ。従来は検出部屋でしか絞らず特集を見ないため、②③を選んでもモテ部屋の世界観6件しか出なかった。A系/B3 の文面・style・need は1文字も変えず、②に C1-C4・③に D1-D4 を追加。B1 は全特集共通に置き候補ゼロを防ぐ。★引数の既定は normal（None＝絞らない、ではない）＝渡し忘れたときに全特集の世界観が混ざったリストを返すと、モテ部屋の選択肢に②③が紛れても気づけない。既定 normal なら候補が共通1本だけという目に見える壊れ方になる。★受入(0円): (a) 補完生成8部屋×ref有無16通り＋3Dパースの全文をファイルへ落とし normal は変更前とバイト一致・③洋室に③の【厳守】(自立式ラック/防音・楽器可を想起させない)が届くことを確認。(b) mote_heya の返り値が変更前と完全一致(A1-A4,B1,B3)・②③でA系B3の漏れゼロ・最小構成(LDKのみ)でも全特集で候補が1本以上・C/D全8件が ban/needs_review/fact_scrub/数字のいずれも踏まないことを実関数で確認。既存テスト(v79-scrub-clause 22件・v79-back-direction 22件)とstaging/ban集合の回帰も再確認。★注意: v79-back-direction は依頼文が「実機目視＋工程5の後」としていたが、その指示が届く前に実装・push済（e5c70d4）。仕様差1件を報告に記載。以下 v79-back-direction: ★fact_scrub の『日当たり・方角』グループを『方角の断定』と『日当たり・採光』の2つに分割＝マイソクに日当たり良好の1語があるだけで南向きという方角の断定まで通っていた穴を塞ぐ（実測で再現）。方角の裏付けは 南向き/南面 の明示のみ。日当たりの裏付けは 日当たり/陽当たり/採光/日照 ＋ 南向き/南面（南向きの明示があれば日当たりも通る・逆は通らない）。★back から 方角・向き を削除＝向き が短すぎて 北向き / バルコニー南東向き にヒットし、北向き物件で南向きと書けていた（実測）。谷合さんの実マイソク16件の全数調査で 方角・向き の出現0件と確認できたため、正規表現化の小改修をせず単純削除で足りる（過剰除去に倒れる心配が実データ上ない）。★私の当初の推奨案（[東西南北]向き の正規表現）は本命を直さない＝北向きにもマッチするため北向き物件で南向きと書ける状態が残る。谷合さんの指摘どおり誤りだった。★claims は1語も削っていない（旧20語→新20語・2グループに分配しただけ）。back は10→8語（減＝厳しくなる側のみ・増えた語ゼロ）。★受入(0円): 記載×主張の15通り＋他グループ7通りの計22ケースを全数実測し期待と全一致。他グループ（眺望/角部屋/静けさ/通風）は無改変で回帰なし。ban集合66/66/71 不変・staging は normal/mote ともバイト一致。★残る粗さ: グループ単位の裏付けという機構上、南向きの明示があると同グループの 西日 / 朝日が差し（東西の含意）も通る。方角ごとの対応表が要る＝今回の範囲外として別途。以下 v79-scrub-clause: ★ban ヒット時の断片化を止めた＝②③で動画を出す前の必修(依頼文v1.2 §8)。ban は語を置換して消す方式だったため『安心の、オートロック。』→『の、オートロック。』のように助詞始まりの断片が動画に焼かれていた（警告は出るが止まらない）。covercopy-v1 で数字の機械削除をやめて検出のみに切り替えたのと同じ型の沈黙破損。core.ban_scrub＝節ごと除去へ切替（fact_scrub と同じ思想）。★1源化: 旧 _drop_neg_clauses を drop_clauses_containing へ汎用化し、否定設備ガードと ban 除去が同じ節分割を共有する（節の切り方を2箇所に書かない）。配線4経路＝magtext の _clean(big_text/comment/表紙3案)・polish_narration(ナレ)・_scrub_cover_copy(表紙)・draft_sns_captions の _clean(投稿文/ハッシュタグ)。★自己テストで回帰を1件検出して修正: 旧 _drop_neg_clauses は無条件 strip(。) していたため、ban 経路をここへ寄せた時点で ヒットの有無に関わらず末尾の句点が消えていた（帰りたくなる、1LDK。の句点は意図的な演出）。→ ①1語も当たらなければ1文字も触らない ②当たった場合の後始末は fact_scrub と同一 に修正。★§5の訂正: claims の単独『あさひ』を撤回し『あさひが差す』『あさひが入る』へ置換。単独だと大阪に頻出する物件名（朝日プラザ/あさひ荘）に巻き添えで正当な節が丸ごと落ちる（実測: あさひプラザの、エントランス。→空）。_NEEDS_REVIEW の「『極』単文字は入れない」と同型の失敗だった。★受入(0円・偽クライアントで magtext/polish_narration を実走): ②で安心を含む comment/big_text/表紙3案/ナレの全経路に助詞始まりの断片ゼロ・ban ヒット無しのテキストは1文字も変わらない・fact_scrub 既存12語は全て除去継続・ban 集合の語数は工程4から不変(66/66/71)・staging は normal/mote とも変更前とバイト一致。★私の誤報を訂正: 前回報告の「物件名にあさひを含むと full_text が裏付けになり光の主張が残る」は再現しない（back に あさひ にヒットする語が無い）。実測せず書いた推測だった。★別途起票が要る実バグを発見: back語『向き』が広すぎ、full_text に 北向き/バルコニー向き があるだけで『南向き』の主張が裏付けありと判定され残る（実測）＝景表法直撃。今回は範囲外なので触っていない。以下 feat-ban-1: ★ban の適用範囲を意図どおりにした。旧 _MOTE_HARD_NG を _COMMON_HARD_NG へ改名し全特集共通のハードNGへ正式昇格＝_story_ban_words() が concept_ban_extra(mote) を引数ハードコードしていたためmagtext/story_narration は特集に関係なく除去する一方 polish_narration/draft_sns_captions は特集依存だった（同じ語が経路によって消えたり消えなかったりする状態）を解消。_story_ban_words(feature_id) へ変更しstory_narration にも feature_id を追加、呼出2箇所（app 🎙️物語生成 / magtext）を配線。新関数 feature_ng(fid)=共通＋特集固有 を下流の post-filter が全部参照する。★『かわいい／可愛い』の2語だけ ban → _NEEDS_REVIEW へ降格（谷合さん判断 2026-08-03）＝止めずに人が見る。★副次で穴を1つ塞いだ: _scrub_cover_copy が concept 既定 normal 固定で、②の安心/安全/防犯 が表紙コピーに素通りしていた。feature 引数を通して magtext から特集を渡す。★併せて、ban が語を置換して消す方式のため『の、オートロック。』のような壊れた断片が「選べる表紙案」として残る問題に対処＝禁止語を除去した案は案ごと落とす（全滅時は既存のfeature_fallback が受け皿なので表紙コピーが空にならない）。★受入(0円・偽クライアントで magtext/story_narration/polish_narration を実走): _story_ban_words は 68→66語＝差分は かわいい/可愛い の2語のみ（依頼文の受入どおり）。polish/sns は normal/②/③ で 44→66・49→71・48→70 語＝共通22語の昇格分だけ増加（緩めた語はゼロ）。_NEEDS_REVIEW は 13→15語。②③に mote 固有語が漏れていないこと（mote固有は空・共通へ移動）を一覧で確認。『かわいい、洗面台。』が全特集で除去されず needs_review に出ること／『安心』が②でのみ除去されることを実走で確認。★残課題: ビートの comment / ナレは ban 置換で断片化しうる（のオートロック付き。）。警告は出るが文は残る＝表紙案と違い代替がないため今回は挙動を変えていない。以下 feat-merge-3: ★UI統合＝テイストのセレクタを1つにした。旧コンセプトセレクタ(_pl_concept_selector・pl_concept)を廃止し、ページ最上部の『特集（テイスト）』(pl_feature)1箇所に統合。表紙expander内にあった2つ目の特集セレクタは撤去し現在の選択の表示のみに（同じ状態を2ウィジェットで編集できる状態を解消）。key は pl_feature を維持＝sticky が飛ばない。★既定を mote_heya → normal(特集なし)へ切替＝feat-merge-2 で staging が特集駆動になったため、ここを切り替えないと『人が選んでいないのに既定でモテのダークトーン』になる。★下流の情報源も特集へ: polish_narration(concept=→feature=) / draft_sns_captions(concept=→feature=) / _concept_caption_line→_feature_caption_line / concept_voice_id→feature_voice_id / _pl_follow_concept_style→_pl_follow_feature_style(スタイル既定)。★スタイル既定を同時に移したのは、staging だけ特集駆動にすると『stagingはモテのダークトーン／styleは北欧』と1枚のプロンプト内で方向が割れるため。★準備中(status:wip / concept_is_wip)の表示は廃止＝②③が準備中でなくなった。★feature_label→feature_display_name へ改名（旧名は『焼く文字』に読めて label と役割が逆に伝わるため）。★受入(0円): 新規セッション(session_state空)の初期選択が normal＝表示『特集なし（標準）』で staging が変更前の normal と一致／特集を normal→②→③ と切り替えて staging・magtextトーン・表紙accent の3要素の組が全特集で相異なることを確認／ナレtone・投稿文tone・hashtags・ban・voice_id が旧 concept 経路と全一致(回帰ゼロ)。★残: pl_concept を読む箇所が3つ残る(_pl_caption_sub / _pl_follow_concept_cover_style / draft_pr_copy)。いずれも feat-dead-1 で丸ごと削除する対象で、セレクタが無い今は normal に固定＝実害なし。以下 v79-note: ★景表法注記(※家具・小物はAI生成のイメージ)を v78 テロップ層から v79 overlay 側へ移設＝feat-dead-1 でテロップ層を削除しても注記が道連れで消えない状態にした（工程5の前提条件）。★1源化: core.AI_IMAGE_NOTE ＋ core.ai_note_line(facts, 生成日) を新設し、表紙(_pl_cover_v79_fields)・本編(build_note_overlay)・DATA面(_data_notes) の3面が同じ定数から文言を作る。年月は data_note_date と同じ規則（マイソク記載を優先→無ければ生成日）＝3面で年月がずれない。★全カット保証: 注記はビート文字面に相乗りさせず build_note_overlay の独立PNGを本編全長の時間窓で焼く。ビート文字面は先頭sceneにしか載らず、間取り図カットのように room_label も big_text も無いカットはoverlay ループで skip されるため、相乗りさせるとそこだけ注記が消える（実測で確認）。★コントラスト修正: 旧テロップ層の note は v79 下部グラデ(alpha235)の下敷きで 212→40 まで落ちていた。overlay 側はグラデの上に描くので沈まない。実測=ビートカット210 / 間取り図カット169(グリフ輝度206) / 旧モード212＝同水準。★seg側の注記は glob の v79_note があるときだけ止める（旧 job state や旧経路では残す＝注記がゼロになる瞬間を作らない）。★文字面の焼込みが失敗した場合の警告文を変更: 『文字なしで生成しました』では注記欠落が伝わらないため『この動画にはAI生成イメージの注記が入っていません。投稿せず再生成してください』と明示（silent drop 禁止）。★注意: glob に v79_note を追加したため job_id が変わる＝この版をデプロイすると生成途中のジョブは別ジョブ扱いになる（再開すると fal 再課金）。デプロイ前に④動画化の途中ジョブを完了/破棄すること。以下 feat-merge-2: ★内観staging の情報源を pl_concept → pl_feature(特集) へ差替え＝本件の主目的。これ以前は特集を切り替えても内観画像のプロンプトが1バイトも変わらず、FEATURES[*] の staging_prompt は参照ゼロの死にデータだった。app.py:1297 を core.feature_staging(pl_feature) へ。★fallback は normal（mote_heya ではない）: ②画像化は④動画化 expander より前に走るため新規セッションで④を一度も開かないと pl_feature は未設定＝mote_heya に倒すと人が選んでいないのにダークトーン staging が入る。★build_staging_prompt / build_water_staging_prompt のシグネチャ(concept_staging=)は据え置き（改名は差分を広げるだけ・feat-dead-1 で整理）。v79-3 当時の実装と食い違ったコメント（1源=staging が参照）を実態へ修正。★受入(0円・fal/Gemini 不使用): 種別2×部屋9=18ブロックのプロンプト全文をファイルへ落として diff。normal は変更前と sha256 一致(3059c02b028b0cb7)・mote_heya は変更前の pl_concept=mote と sha256 一致(4026a6561fd92159)＝回帰ゼロを機械証明。②③は concept_staging を載せる15ブロック全部に【厳守】行(②収納の扉/③自立式ラック・防音楽器可ペット可)が届いていることを grep で確認。★併せて fact_scrub の日当たりグループにひらがな異表記4語を追加(やわらかい光/あたたかい光/ひかりが差す/あさひ)＝従来は漢字表記のみ検出で『やわらかい光の、洗面台。』がガードを素通りしていた。既存語は1語も削っていない。裏付けなし=節ごと除去/裏付けあり(南向き記載)=残存 を新旧9語で実測。★既知の弱点: 物件名に『あさひ』を含む物件は full_text が裏付けになり誤って残りうる。以下 feat-merge-1.5: ★v79「動く雑誌」モードで v78 テロップ層（メイン＋情感2行＋上部タグ）を焼かないようにゲート＝run_tour_job._make_seg の _normalize_clip 引数を _telop_off で落とす。理由=v79 は big_text/comment/room_pill/マストヘッドが同じ役割を担い、両方焼くと同役割の文字が2系統重なる（実測: 情感2行が big_text 直下に沈み、上部タグが OSAKA ROOMS マストヘッドと y172-208 で交差）。subtitle_beats の焼込みは既に not _v79_mag でスキップ済みだったが seg 段のテロップ層だけゲートが漏れていた＝v79-5b の設計意図と実装の食い違いを解消。★判定は ordered（描画完了分）でなく scenes（ジョブ全体）で行う: _make_seg は描画の最初に走るため ordered はこの時点で必ず空／seg は冪等キャッシュなので再開時に判定が揺れるとテロップ有無の混ざった seg ができる。★note（※画像はイメージです）と冒頭 flash は落とさない: ビート面の情報バー note_line は scene に載らず常に空＝景表法の注記はこのテロップ層の note が唯一の出口。★UIの「シーンテロップを焼く」は挙動を変えず注記だけ追加（黙って無効化するとチェックが嘘になるため。撤去/内部フラグ化は feat-dead-1 で判断）。★受入(fal課金ゼロ・still のみで run_tour_job を実走): A) v79モード=情感2行/メイン/上部タグが消えマストヘッド交差も解消・注記は残る・v79文字面(big_text/accent/タグ/room_pill/masthead)は従来どおり B) 旧モード(big_text/comment なし)=従来どおりテロップが焼かれる＝回帰なし。★既存の再開中ジョブは status=done の seg をそのまま再利用するため古いテロップ入り seg が残る（fal 再課金を避ける既存仕様・新規ジョブから適用）。★別途報告(この変更とは無関係の既存事象): 景表法注記が v79 下部グラデで沈む（コントラスト 212→40・変更前後で同値42/40）。以下 feat-merge-1: ★特集マスタ FEATURES をテイストの唯一の情報源へ拡張。CONCEPT_PRESETS の実消費7キー(style_default/staging_prompt/narration.tone/narration.voice_id/ban_words/caption.tone/caption.hashtags)を移植し、②自分を整える部屋/③趣味部屋の中身(staging長文＋【厳守】ブロック/comment_tone/cover_hooks 3案/固有ban/hashtags)を実装＝②③が実際に使える状態に。★normal(特集なし)を FEATURES へ追加＝統合後の既定を旧 pl_concept=normal と完全一致させ全体回帰を防ぐ(staging空・style_default ナチュラル/北欧)。空labelは『表紙に特集枠を描かない』合図であり、UI表示名は feature_display_name() に分離した(label を表示名で埋めた瞬間に枠が復活するため別物として扱う)。rtv._v79_feature_label は空labelで枠ごと描画しない分岐を追加(空文字のままだと『特集　』の空枠が残る)。FEATURE_ORDER でセレクタの並びを明示(dictキー順に依存しない)。★feature_of() の未知idフォールバックを None→normal へ(concept_of と同型)。実害確認済=旧実装は未知idのとき表紙が『特集　モテ部屋』を騙っていた(rtv:1479)＝issue-v1 の『取れないものを既定で埋めない』と同方針で normal=枠なしへ。呼出側の feature_of(x) or {} は dict が常に真で無害。アクセサ追加=feature_label/feature_style_default/feature_staging/feature_voice_id/feature_tone/feature_hashtags/feature_ban_extra/feature_ban。★移植しないキーと理由(工程0の実測で消費先が死んでいると確定): cover.default/cover.style=呼出元ゼロ／cover.tone=消費先が draft_pr_copy の title/subtitle だけで表紙PNGには一度も焼かれない(v79-3 c0a7b78 で切離・唯一残る冒頭フラッシュ経路も表紙挿入ON=既定で不発)／telop・sub_template=消費先が v78 テロップ層で feat-merge-1.5 で落とす。★受入(fal不要・ローカル実測): 旧 CONCEPT_PRESETS との7キー全一致を normal/mote で機械照合(staging_prompt はバイト一致)・②③の cover_hooks 全6案が _scrub_cover_copy 警告ゼロ/needs_review ゼロ(AI3案全滅時の fallback として機能する条件)・表紙4特集をローカル描画し normal は特集枠なし/mote GOLD/②ROSE/③SAGE の accent 追従を確認。★注意=この時点では内観staging の情報源はまだ pl_concept 側(feat-merge-2 で接続)。以下 covercopy-v1: ★表紙コピーを物件別の自由生成に切替＝特集固定文言(cover_hooks[0])の焼き回しを廃止。★併せて既存の受入8違反を解消: magtextが生成した表紙hookをUIは表示するのに _pl_cover_v79_fields が読まず、画面の文言と焼かれる文言が別物になりうる状態だった(旧コードで再現テスト済)。core.magtext=cover節のみ差替え(hook_candidates 3案・全角14字・読点1つ・facts根拠・切り口を変える)。ビート面(big_text/comment/narration_kana/タグ)の生成規則は不変=旧コードとbeats出力バイト一致で確認。ガードは全案に必ず通す(fact_scrub→ban→core._scrub_cover_copy〈物件名/字数14/装飾記号〉→needs_review)。空になった案は落とし全滅なら feature.cover_hooks[0] へ hook_source=feature_fallback で明示フォールバック。★数字は削除せず検出のみ(間取り表記1LDKを機械削除すると『帰りたくなる、LDK。』と壊れるため。間取り以外の数値主張はneeds_reviewで人力確認へ)。UI=📖内に3案ラジオ(全文表示・人が選ぶゲートは維持)＋確定コピー表示。★needs_reviewは案ごとに保持(needs_review_by_hook)＝ラジオ本文末尾に⚠️要確認を付け理由も案別に並べる(全案を1行にまとめると『選ぼうとしている案が安全か』が分からず、数値主張を人が弾く前提が崩れるため)。選択中の案が要確認なら生成前サマリーにも⚠️。配線は _pl_effective_hook が1源(人の選択>magtext既定>特集既定=📖未実行の回帰経路)。stale対策=autoカバー署名に表紙コピー/マストヘッド表記を含め自動追従(無課金)＋PNGに焼いたコピーを記録し手動カバーの食い違いを _pl_cover_stale で検知(📖/🖼️/生成前サマリーの3箇所に警告・サマリーではNG扱い)。死にコード削除=app._pl_cover_ai_cb/_pl_cover_clean_copy・core.draft_cover_copy・rtv.build_cover_magazine。fal不要でローカル検証済。以下 issue-v1: ★ISSUE番号のUI化＋エリア自動化＝マストヘッド2行目のハードコード『ISSUE 01 / OSAKA・FUKUSHIMA』を廃止。★主目的=エリア誤表示の事故対策(福島固定のため西区/九条/本町の物件で事実と異なるエリアが表紙・全ビート・DATA面の3面に焼き込まれていた)。生成ロジックは core.magazine_issue_line に一本化(_AREA_ROMAJI＝西区/ドーム前導線を追加・エリア決定順=手入力>マイソク代表駅(_sns_access_pick)のローマ字>空・★取れないときに既定エリアを騙らず ISSUE 03 単独へフォールバック・未知駅はローマ字を推測せず日本語のまま)。描画=_v79_masthead(issue_text)＋fit-to-width(30→22・長い駅名NISHI-NAGAHORIで左右見切れゼロ)、3面(表紙:1579/ビート:1514/DATA:1625)へ配線。UI=📖内にISSUE番号/エリア手動上書き＋確定文字列プレビュー(課金前に目で確定)＋生成前サマリーに1行。★build_data_pageの死に引数area(本体未参照)を削除しissue_textへ統合。★app._pl_cover_subline/_PL_AREA_ROMAJI(呼出元ゼロの死にコード)を削除＝2源化の温床を断つ。『最初からやり直す』は号数を保持(連番運用)・エリアはクリア(物件依存)。fal不要でローカルPNG検証済。★注意=ISSUE番号/エリアはjob_id(glob)に入るため生成後の変更は別ジョブ扱い＝fal再課金。以下 autosort-v1: ★🔀部屋順の整列をデフォルト化(押し忘れ→部屋バラバラ動画の$3.15無駄を防ぐ)。画像化直後に③で1回だけ自動整列。過去のsticky事故(整列が繰り返し適用され手動順を上書き)対策の3条件: 条件1(一回限り)=pl_autosort_doneで再発火しない・条件2(手動不上書き)=_pl_moveがpl_order_manualを立て以後自動整列しない・条件3(明示+取消)=🔀自動整列しました通知＋「元の順に戻す」(_pl_restore_order・元順pl_order_original復元・以後手動扱い)。生成前チェックの整列=表示は状態判定のまま(自動化しても実態反映)。★条件2/3の非再現テスト済(手動↓→再実行×2→手動順保持)。fal不要。以下v79-6-data2: ★DATA面の状態を生成前サマリー(pregen-guard)に追加＝課金前にDATA面の品質を判定。_pl_data_summary()=DATA面 ON/OFF・行数(build_data_rows充足率)・間取り図あり/なし(pl_floorplan)・行少なめ⚠️。生成前サマリー＋📖直後の両方に表示(情報源1箇所)。$3.15を払う前にDATA面がスカスカにならないか/間取り図がAで取れたかが分かる。以下v79-6-data: ★DATA面(動く雑誌の最終ページ)を本編末尾に追加=masthead＋DATA見出し＋間取り図(pl_floorplan)＋スペック表(金ラベル/白値・罫線・fit-to-width)＋注記。静止3.5s・ナレなし・BGM継続。core.build_data_rows(取れない行は省略・方角はマイソク明記時のみ「建物」行・否定facts除外(fact_negated)・生値寸法strip)＋data_note_date(マイソク日付優先→生成日)。rtv.build_data_page(間取り図無しは表を上に詰めてDATA面は必ず出す=silent drop禁止・取得ログ)。run_tour_jobが_cover_clip+_prepend_clipで末尾連結。fal不要。以下ui-stepgate-v1: ★📖文字面生成を④動画化の必須前提として強制(誤課金防止)。修正1=文字面(pl_mag/big_text)空だと🎬ルームツアー生成ボタンをdisabled＋理由caption。修正2=生成前サマリーの最上段に文字面状態(✅生成済Nビート/🔴⚠️未生成)を追加・未生成でボタン封鎖と連動(_pl_pregen_summaryが3-tuple:md,ng,mag_ready)。修正3=ステップバーに📖文字面を追加(①取込→②画像化→③整列・確認→📖文字面→④動画化・現在地太字/未完了グレー/📖未実行は赤⚠️)。修正4=📖expanderを未生成時に自動展開＋タイトル🔴【必須】/生成済✅・ボタンprimary。fal不要でUI検証完結。以下magfit-v79c: ★①②Klingモーション幻覚修正=外観/玄関のカメラワークを静止〜微パンに制約(外観motion=minimal・扉/窓/シャッター開閉変形禁止・人物出現禁止・扉の先の別空間生成禁止)＋_V79_NEGATIVE強化(no opening doors/no people appearing等)。③白サブ文言(comment/ナレ)を動画描画から非表示(build_beat_overlayがcomment描かない・音声TTSは別経路で残す)＋big_text中心≈1470に縦センタリング。④外観/トイレの金色見出しをfact_scrub空化時に方角非依存の安全フォールバックで復活(core._safe_big_fallback:外観=建物種別+階数/トイレ=独立/玄関=シューズ/洗面=独立洗面台/汎用=部屋名・空にしない)。fact_scrubは不変。以下magfit-v79b: ★magfit-v79のリグレッション修正=big_text空化で一部シーンのテキスト層(masthead+tag+金色見出し+サブ)がまるごと消える問題。真因=big_text空→overlay loopの旧スキップ(if not big_text:continue)でoverlay全体を捨てていた(既存機構・fact_scrubで方角等の全節除去→空化が引き金)。修正=①overlay loopはbig_text空でもmasthead/tag/comment描画(真に空のビートだけスキップ)②build_beat_overlay/_v79_fit_fontを例外安全化(getattr size・fit失敗は基準サイズ・非空lines)③per-beat try/except(1ビート失敗で全落ちしない)④magtextでbig_text空化を警告(元テキスト+理由)⑤_rewrite_unnatural空返し防御⑥発火ゲートをbig_text or commentに⑦reading_dict朝→あさ。以下magfit-v79: ★金色スペック行の横幅fit未実装による左右見切れ2件＋不自然文言1件を修正。修正1(描画)=表紙DATAストリップ(_v79_infobar spec行)にfit-to-width(38→26縮小・下限で｜折返し2行・max_w=W-120=左右60px)。修正2(描画)=beat big_text(金色見出し)を_v79_fit_font(96→56縮小・下限で幅折返し・動的y)＝物件により語数が変わっても見切れゼロ。修正3(生成)=magtextの不自然表現ガード『床が余る』系→『余裕がある』系(_rewrite_unnatural・big/comment両方・プロンプト明記・書換はcomment改変=kana stale不採用)。以下pregen-guard: ★$3.15誤爆・巻き戻り対策。①『最初からやり直す』を2段確認(1クリック目は武装のみ・はいで初めて全消し＝再描画ずれの誤爆で①巻き戻り+画像化再課金を防ぐ)。②生成ボタン直上に生成前チェック表示=特集／整列済否(room_tour_rank昇順か)／文字面(🈶kana採用N/M)／⚠️辞書読みK件(部屋名・kana-reasonと同一情報源=pl_magのnarration_kana有無)／カット数・概算$。NG項目は黄色警告。以下kana-reason: ★kana不採用/崩れの切り分けを per-beat 可視化＝magtext warningsに『🈚 部屋: 未出力／不採用(漢字残存N字/長さ乖離/comment改変)』を出す(core._kana_reject_reason)。どのビートがなぜkana不発かが実機で即判明(①採用/フォールバック ②Gemini未出力 ③ガード条件を1発切り分け)。reading_dictに帰宅→きたく追加(採用kanaの残存漢字もnormalize_readingで補正=二段の網)。以下narrkana-diag: ★narration_kana誤読残存(着く→とどく等)の原因特定＝📖生成メッセージにkana採用率『🈶採用 N/Mビート』を表示(0/N=kana不発→Gemini未出力/ガード過剰弾き/未デプロイを疑う)。副次=プロンプトのnarration_kana指示に助詞は→わ/へ→え・英字数字単位のひらがな展開(LDK→えるでぃーけー)を明記／reading_dictに着く/落ち着く/入浴剤(フォールバック用)。以下comment-wrap: commentの画面幅見切れ修正2点。描画側=build_beat_overlayが_v79_wrap_widthでcommentを描画幅(W-180)最大2行に折返し(句読点優先・フォント42固定)。生成側=magtextのcommentを全角24字以内・1文に(プロンプト＋後処理_first_sentenceで2文以上は第1文のみ・警告)。★1文化はcomment改変なのでnarration_kanaはstale→不採用(辞書フォールバック・narr-fix-d 4条件目と整合)。以下narrfix-d: ★漢字誤読クラスを根絶＝magtextが narration_kana（commentの全ひらがな読み・Geminiの文脈読み・数字/英字/単位も日本語読み展開）を1コール内で出力し、TTSはこれを読む。reading_dict辞書はフォールバックへ降格。★ガード=narration_kanaは①非空②ほぼ仮名(漢字1割以下)③commentと長さ乖離なし④commentが後処理(fact_scrub/ban/否定)で改変されていない、を満たすときだけ採用。外れたら黙らず警告＋normalize_reading(comment)=辞書経路へフォールバック(『かなを読んだつもりで漢字を読む』を構造的に防ぐ)。TTSは normalize_reading(kana or comment)＝かな採用時も辞書を通し残存漢字を補正。配線=magtext→pl_mag→scene→_pl_assign_story_beats(grp[0].beat_narration_kana)→run_tour_job(measure-first/fallback両方でkana優先)。measure-firstのタイミング機構は不変(narr_actualはkana音声から_adur測定)。実聴(漢字読みvsかな読みのイントネーション)はCOO実機→谷合さん判定。以下e2e-bugfix: ナレありE2E(被りゼロ=measure-first成功)後の修正3件。★bug①(景表法ブロッカー)=否定文脈付きfacts(駐輪場満車・駐輪厳禁)を全経路で除外=core.fact_negated(強マーカー満車/厳禁/不可等は近接8字・弱マーカーなしは近接3字・無料は否定にしない)＋_drop_neg_clauses、magtextのタグ/プロンプト/big_text/comment全経路でk not in _negated＋警告。★bug②=表紙情報バーの生寸法10x6再出現→_strip_raw_dimをmadori/area/tag全部に適用(1LDK 10x6/area/tag経由も塞ぐ)。★誤読7語をreading_dict.jsonに追記(靴くつ/広々ひろびろ/今日きょう/一日いちにち/洗ってあらって/湯船ゆぶね/浸かってつかって)＝narr-fix-d(narration_kana)までのフォールバック。以下narrfix-c: ★ふりがな辞書（誤読補正）をデータ駆動化＝reading_dict.json（{表記:読み}）を core._READ_TABLE へマージ（最長一致保持）。ElevenLabsの誤読を1行足すだけで直せる（コード変更不要・__で始まるキーは注記スキップ・無い/壊れ→空でフェイルセーフ）。初期語=v78実績誤読（来たか→きたか・洗面台）＋部屋名/設備名の音読み事故（給湯・洗面所・玄関・納戸・独立洗面台）。読みの実効はCOO実機。以下narr-fix-b: ★ナレ音声の実尺を測ってから映像尺を決める（予測係数5.26に依存しない）＝measure-first。run_tour_job順序変更(narration ON＋beatモードのみ): フル正規化seg(無trim)→全comment TTS→実尺測定→d_i=max(MIN_BEAT4.0, narr+TAIL0.5)→segfitへtrim/末尾フリーズ延長→組立→overlay窓/ナレ開始/総尺は Σd_i を共有(逐次=被り0)。ナレ>素材尺はフリーズ延長(>2.5s警告)。★フォールバック=measure-first失敗時は予測trim旧経路へ+警告『旧経路で生成』を明示(黙って落ちない)。★_dur は動画v:0選択で音声を測れず5.0s固定を返す穴を発見→_adur(format=duration)追加(既存CPSログの穴も解消)。narration OFF/非beatは完全回帰。検証: 実run_tour_job(still+モックサイン波)で silencedetect 重なり0ms/はみ出し0ms/総尺一致/検証表/フリーズ警告/フォールバック明示/回帰。実TTS実尺はCOO実機。以下magtext: ★ナレは comment のみ読む（big_textは特大文字で視聴者が読む・声はコメントを添えるだけ）＝magtext narration_text=comment。発話量半減で音声被りの主因が消える。comment空＝ナレ無ビート。★注意: これ単体だとbeat_narr_sec(字数由来の映像尺)が短くなる＝MIN_BEATフロアはnarr-fix-bで入る（a↔b間は本番E2Eを回さない）。以下v79-5b本体: ★ナレOFF回で文字面を生成できない配線ミスを修正＝📖動く雑誌の文字生成を if v_narr_on 外の独立expanderへ移動＋ElevenLabsゲート(disabled=not _narr_ok)除去(文字面はGemini生成でナレ非依存)。ナレOFF経路検証済(narration空でもbig_text注入・ビート割当・overlay成立)＝1本目BGMのみE2Eが回る。物件名自動挿入監査済(既定で挿入なし・冒頭フラッシュは既定OFF・表紙コピーは物件名を明示除去)。以下v79-5b本体: magtext配線+文字面overlay合成。①build_beat_overlay=big_textをaccent_wordで白/accent2行分割+comment+タグ最大3ピル(左余白・金バー)+room_pill(表示名)+マストヘッド+情報バー(透明PNG)。②run_tour_job=big_text保持時に各ビートの文字面PNGを時間窓overlay合成(_burn_beat_overlays・1パス・ビート開始=cover_off+Σbeat_narr_sec)＝v78字幕焼きの代替(背景Kling+文字主役)。③app=📖動く雑誌の文字を生成(特集ベース・core.magtext)→pl_mag_先頭id(room_label/big_text/accent/comment/tags)+pl_narr=narration_text(画面の文字を読む)→scene注入→glob v79_accent。needs_review=型承認ゲート集約表示。★_pl_assign_story_beats堅牢化(短big_text×同室多枚でlen(cuts)<stock→全画像を背景B-rollとしてnsec内均等配置・crash防止・描画尺==nsec維持)。ローカルframe/overlay時間窓/統合seam検証済。実Gemini品質+フルE2E(fal)はCOO実機。前=v79-5a)")


def _render_video_env_diagnostics():
    """動画エンジンの描画環境（ffmpegのdrawtext対応・日本語フォント解決）を自己診断。
    Cloud Reboot後、谷合さん操作なしで容疑者(c)フォント/drawtext欠如を即確認できる。"""
    with st.expander("🩺 動画エンジン診断（フォント・drawtext）", expanded=False):
        try:
            import room_tour_video as rtv          # rtv はモジュール全体では未import（関数内ローカル運用）
            d = rtv.env_diagnostics()
        except Exception as e:  # noqa: BLE001
            st.error(f"診断に失敗: {type(e).__name__}: {str(e)[:120]}")
            return
        st.write(f"- ffmpeg: `{d['ffmpeg']}`")
        (st.success if d["drawtext"] else st.error)(
            f"drawtext フィルタ: {'利用可' if d['drawtext'] else '★利用不可（テロップ・冒頭タイトルが焼けません）'}")
        (st.success if d["font_ok"] else st.error)(
            f"日本語フォント: {d['font'] or '(未解決＝fontconfig名フォールバック)'}"
            f"{'（存在）' if d['font_ok'] else ' ★存在せず（同梱fonts/やfonts-noto-cjkを確認）'}")
        if not (d["drawtext"] and d["font_ok"]):
            st.warning("⚠ 冒頭タイトル・テロップが本番で出ない場合、上記の赤項目が原因の可能性が高いです。")
        # 明朝（雑誌型カバーのマスト/コピー用）
        (st.success if d.get("serif_ok") else st.info)(
            f"明朝フォント（雑誌型カバー）: {d.get('serif') or '(未解決)'}"
            f"{'（存在）' if d.get('serif_ok') else ' ※未解決＝ゴシックで代替表示になります'}")
        # ナレーション（ElevenLabs）— ★キー値は表示しない（存在有無のみ）
        _ek, _ev = d.get("eleven_key"), d.get("eleven_voice")
        (st.success if (_ek and _ev) else st.info)(
            f"AIナレーション（ElevenLabs）: APIキー{'✓' if _ek else '未設定'} / "
            f"ボイスID{'✓' if _ev else '未設定'}"
            + ("" if (_ek and _ev) else "（Secretsに ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID を追加で有効化）"))


def _tpl_tags_to_text(tags):
    """area_hashtags(list) → 編集用テキスト（1行1タグ）。"""
    return "\n".join(str(t) for t in (tags or []))


def _tpl_text_to_tags(text):
    """編集テキスト → area_hashtags(list)。改行/カンマ/空白区切り・先頭#補完・重複除去。"""
    import re as _re
    out = []
    for t in _re.split(r"[\s,、　]+", str(text or "")):
        t = t.strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        if t not in out:
            out.append(t)
    return out


def _tpl_current_draft():
    """編集欄（widget key）から現在のテンプレ dict を組み立てる。
    ※on_click コールバックから呼ぶこと＝全widget値がsession_stateにコミット済みの状態で読める。"""
    return {
        "footer": st.session_state.get("tpl_footer_in", ""),
        "cta": st.session_state.get("tpl_cta_in", ""),
        "area_hashtags": _tpl_text_to_tags(st.session_state.get("tpl_tags_in", "")),
        "reply": st.session_state.get("tpl_reply_in", ""),
        "dm": st.session_state.get("tpl_dm_in", ""),
    }


def _tpl_save_cb():
    """保存ボタンの on_click。必須要素validateを通し、不合格なら保存せずエラーメッセージを残す。
    ★body-flow（if st.button():）では実ブラウザでwidget commitと競合し、旧値のまま保存が通る
      不具合があった（guardfix-v67）。on_click は全widget値コミット後に実行される保証があり競合しない。"""
    draft = _tpl_current_draft()
    errs, warns = core.validate_caption_templates(draft)
    st.session_state["_tpl_kept_open"] = True          # 保存操作後はエディタを開いたまま結果を見せる
    if errs:
        st.session_state["_tpl_save_msg"] = ("error", "保存できません — " + "／".join(errs))
    else:
        st.session_state["pl_caption_tpl"] = draft
        _note = ("（注意：" + "／".join(warns) + "）") if warns else ""
        st.session_state["_tpl_save_msg"] = ("success", "保存しました（このセッション内で有効）。" + _note)


def _render_caption_template_editor():
    """投稿文テンプレ（フッター/CTA/エリア大ハッシュタグ/返信・DM）を設定画面で編集。
    永続化は session_state ＋ JSONエクスポート/インポート（Cloud再起動で消える点をUI明記）。
    フッター必須要素（AI生成/取引態様/{date}）が欠けたら保存拒否＝法務注記の消失を構造的に防ぐ。"""
    import json as _json
    with st.expander("📝 投稿文テンプレ（フッター・CTA・ハッシュタグ）を編集",
                     expanded=bool(st.session_state.get("_tpl_kept_open", False))):
        st.caption("④動画化ページの「投稿文生成」が使うテンプレです。数値・設備・徒歩分は"
                   "マイソク事実から自動固定され、ここでは編集できません（誤記防止）。")
        st.warning("⚠ この時点注記は成約後の掲載継続を許容するものではありません。"
                   "成約判明後は投稿のアーカイブ/削除が必要です（おとり広告規制）。", icon="⚠️")
        st.info("編集値はブラウザセッション内のみ保持され、Streamlit Cloud再起動で既定に戻ります。"
                "恒久運用は下部の「エクスポート」でJSON保存→次回「インポート」してください。", icon="💾")

        # ── インポート（widget生成前に処理し、各入力欄へ反映）──
        up = st.file_uploader("インポート（JSON）", type="json", key="tpl_import_file")
        if up is not None and st.session_state.get("_tpl_import_id") != up.file_id:
            try:
                d = _json.loads(up.getvalue().decode("utf-8"))
                if not isinstance(d, dict):
                    raise ValueError("JSONオブジェクトではありません")
                # 取込前に必須要素を検査。欠けていれば取込拒否（編集欄に反映しない＝法務注記の消失防止）。
                # 未指定キーは既定で補完した候補を検査し、部分importでも既定が保たれることを保証。
                _keys = ("footer", "cta", "area_hashtags", "reply", "dm")
                _cand = {**core.default_caption_templates(),
                         **{k: d[k] for k in _keys if k in d}}
                _imp_errs, _ = core.validate_caption_templates(_cand)
                if _imp_errs:
                    # _tpl_import_id は更新しない＝取込拒否の理由をrerun後も表示し続ける
                    st.error("インポートを拒否しました（不正テンプレ・必須要素不足）：\n- "
                             + "\n- ".join(_imp_errs))
                else:
                    if "footer" in d:
                        st.session_state["tpl_footer_in"] = str(d["footer"])
                    if "cta" in d:
                        st.session_state["tpl_cta_in"] = str(d["cta"])
                    if "area_hashtags" in d:
                        st.session_state["tpl_tags_in"] = _tpl_tags_to_text(d["area_hashtags"])
                    if "reply" in d:
                        st.session_state["tpl_reply_in"] = str(d["reply"])
                    if "dm" in d:
                        st.session_state["tpl_dm_in"] = str(d["dm"])
                    st.session_state["_tpl_import_id"] = up.file_id
                    st.success("インポートしました。内容を確認し「保存」を押してください。")
            except Exception as e:  # noqa: BLE001
                st.error(f"インポート失敗: {type(e).__name__}: {str(e)[:120]}")

        # ── 入力欄の初期値（既定 or 保存済み編集値）を一度だけ種付け ──
        eff = _pl_effective_templates()
        st.session_state.setdefault("tpl_footer_in", eff["footer"])
        st.session_state.setdefault("tpl_cta_in", eff["cta"])
        st.session_state.setdefault("tpl_tags_in", _tpl_tags_to_text(eff["area_hashtags"]))
        st.session_state.setdefault("tpl_reply_in", eff["reply"])
        st.session_state.setdefault("tpl_dm_in", eff["dm"])

        st.text_area("固定フッター（必須：『AI生成』注記・『取引態様』・時点注記 {date} を含めること）",
                     key="tpl_footer_in", height=120,
                     help="{date} は投稿文生成日のJST日付（例: 2026年7月14日）に自動置換されます。")
        st.text_input("CTA行", key="tpl_cta_in")
        st.text_area("エリア大ハッシュタグ（5個目安・1行1タグ／カンマ可・#は自動補完）",
                     key="tpl_tags_in", height=90,
                     help="ここはGeminiに書かせず固定します。エリア小・属性タグはGeminiが自動生成。")
        st.text_input("コメント返信テンプレ", key="tpl_reply_in")
        st.text_area("DM誘導テンプレ（{LINE_URL} プレースホルダは残すこと）",
                     key="tpl_dm_in", height=120)

        # 保存前プレビュー検証（赤=保存不可 / 黄=警告）。表示用（enforcementは on_click 側）。
        _draft = _tpl_current_draft()
        _errs, _warns = core.validate_caption_templates(_draft)
        if "{LINE_URL}" not in _draft["dm"]:
            _warns.append("DMに {LINE_URL} が無い（誘導リンクが入りません）。")
        for w in _warns:
            st.warning(w)

        # 直近の保存操作の結果（on_click コールバックが session_state に残す）を表示
        _msg = st.session_state.get("_tpl_save_msg")
        if _msg:
            (st.error if _msg[0] == "error" else st.success)(_msg[1])

        c1, c2 = st.columns(2)
        with c1:
            # ★enforcementは on_click コールバックで実施（body-flowは実ブラウザでwidget commitと
            #   競合し旧値のまま保存が通る不具合があった＝guardfix-v67）。コールバックは全widget値
            #   コミット後に走る保証があり、必ず最新の編集値でvalidateする。
            st.button("💾 保存", key="tpl_save", type="primary", use_container_width=True,
                      on_click=_tpl_save_cb)
        with c2:
            st.download_button(
                "⬇ エクスポート（JSON）",
                data=_json.dumps(_draft, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="caption_templates.json", mime="application/json",
                key="tpl_export", use_container_width=True)
        if _errs:
            st.error("現在の入力は必須要素が不足しています（このままでは保存不可）：\n- " + "\n- ".join(_errs))


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
# 物件から動画をつくる（B2b-1: 一気通貫パイプライン）
#   入口2（PDF/写真）→ ①取り込み・種別 → ②画像化 → ★確認(Before/After) → ③動画
#   ※ core.*/build_tour/既存キーは不変。新規は pl_ 接頭辞。
#     （旧3ツール render_maisoku/render_stage/render_video は cleanup-v54 で撤去済み）
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
    """情感2行の自動下書き（改行区切り）。種別テンプレ→無ければ帖数入り汎用。
    ★factguard-v74: 既定テンプレはfacts無関係の固定文＝『光が差し込む/静か/自然光』等の属性を毎回主張し、
      PRコピー下書きを押さないと そのままテロップに焼かれる。facts に裏付けの無い属性は事実照合で除去
      （禁止でなく照合＝南向き等がfactsにあれば残る）。全節が事実外なら帖数＋室名の安全既定へ。"""
    room = it.get("room", "その他")
    name = _PL_ROOM_JP.get(room, room)
    jo = it.get("jo")
    # ★静的既定もコンセプトに追従（factguard-v75）。コンセプト別→汎用テンプレ→帖数汎用 の順。
    _concept = st.session_state.get("pl_concept", "normal")
    base = core.concept_sub_template(_concept, room)
    if base is None:
        t = _PL_SUB_TEMPLATE.get(room)
        if t:
            base = "\n".join(t)
        else:
            line1 = f"{jo:g}帖の広々とした{name}" if jo else f"ゆとりのある{name}"
            base = line1 + "\n自然光が心地よい空間"
    clean, _ = core.fact_scrub(base, _pl_effective_facts())   # ★事実外属性を節単位で除去（照合）
    if clean.strip():
        return clean
    return f"{jo:g}帖の{name}" if jo else name               # facts安全な最小既定（帖数＋室名・主張なし）


def _pl_scene_main_text(it, lang):
    """シーンのメイン行のみ（編集後優先）。★ナレ自動下書きの元＝短く上限内に収まり緑スタート。
    目（メイン＋情感2行を並列表示）と耳（直列・尺に縛られる）で予算が違うため、ナレ既定は
    メインのみ純コピー。情感2行は『AIで整える』で素材として畳み込む（情報は失わない）。"""
    return str(st.session_state.get(f"pl_capmain_{it['id']}") or _pl_caption_main(it, lang)).strip()


def _pl_scene_telop_text(it, lang):
    """シーンのテロップ全文（メイン＋情感2行・編集後を優先）。★『AIで整える』/『全下書き』の素材。
    ナレ自動下書きには使わない（丸ごとは耳の尺を超えるため）。"""
    main = st.session_state.get(f"pl_capmain_{it['id']}") or _pl_caption_main(it, lang)
    subs = st.session_state.get(f"pl_capsub_{it['id']}")
    if subs is None:
        subs = _pl_caption_sub(it)
    lines = [str(main)] + [s for s in str(subs).split("\n") if s.strip()]
    return "、".join(x.strip() for x in lines if x.strip())


def _pl_narr_polish_cb(nid, tel, dur):
    """『AIで整える』on_click。テロップ全文(メイン＋情感2行=tel)を素材に口語＋読み正規化し
    上限内の1文へ畳んで narr のみ更新（auto は触らない＝以後追従せず人の編集扱い）。
    コールバック内なのでウィジェットキーへの代入が安全（地雷①回避）。"""
    try:
        c = make_client()
    except RuntimeError:
        c = None
    if c is None:
        st.session_state[f"_pl_narr_msg_{nid}"] = "Gemini APIキーが未設定です（設定ページで確認）。"
        return
    # ★素材は常にテロップ全文(メイン＋情感2行)。情感を捨てず、上限内の耳向け1文に畳み込む。
    pr = core.polish_narration(c, tel, dur, _pl_effective_facts(),
                               feature=st.session_state.get("pl_feature", "normal"))
    st.session_state[f"pl_narr_{nid}"] = pr["text"]
    st.session_state[f"_pl_narr_msg_{nid}"] = ("🎙️ " + "／".join(pr["warnings"])
                                               if pr.get("warnings") else "")


def _pl_story_generate_cb(situation, style, dur):
    """★story-v78 A-3『物語を生成』on_click。採用画像を部屋でビート化し、全ビートを1コールで物語生成。
    各ビート先頭sceneの pl_narr に格納（★pl_narr_auto は更新しない＝編集済み判定でテロップ追従を止める＝意図）。
    継続シーン（同ビートの2枚目以降）は空にする（1枚目にまとまる）。コールバック内＝ウィジェットキー代入が安全。"""
    if not (situation or "").strip():
        st.session_state["_pl_story_msg"] = "シチュエーションを選ぶか、自由入力してください。"
        return
    items = st.session_state.get("pl_items", [])
    adopted = sorted([it for it in items if it.get("gen_bytes") and it.get("_adopt", True)],
                     key=lambda it: it.get("order", 0))
    if not adopted:
        st.session_state["_pl_story_msg"] = "採用画像がありません。"
        return
    beats = []                                    # 連続する同roomを1ビートに（🔀整列後の順を尊重）
    for it in adopted:
        if beats and beats[-1]["room"] == it.get("room"):
            beats[-1]["stock"] += 1
            beats[-1]["ids"].append(it["id"])
        else:
            beats.append({"room": it.get("room"), "stock": 1, "ids": [it["id"]]})
    try:
        client = make_client()
    except RuntimeError:
        client = None
    res = core.story_narration(
        client, [{"room": b["room"], "stock": b["stock"]} for b in beats],
        _pl_effective_facts(), situation.strip(), style=style, budget_sec=33,
        feature_id=st.session_state.get("pl_feature", "normal"))   # ★feat-ban-1：ban を特集に追従
    if not res:
        st.session_state["_pl_story_msg"] = "生成できませんでした（Gemini未設定など）。"
        return
    for b, line in zip(beats, res["lines"]):
        st.session_state[f"pl_narr_{b['ids'][0]}"] = line["text"]   # ★auto更新しない＝追従停止
        for mid in b["ids"][1:]:
            st.session_state[f"pl_narr_{mid}"] = ""                 # 継続シーン＝空（1枚目にまとまる）
    st.session_state["_pl_story_msg"] = ("🎙️ " + "／".join(res["warnings"]) if res["warnings"]
                                         else "物語を生成しました（各ビート先頭にナレをまとめました）。")
    st.session_state["_pl_story_prompt"] = res["prompt"]            # 実機検証で提出（後でFで撤去）
    st.session_state["_pl_story_raw"] = res["raw"]


def _pl_mag_generate_cb(feature_id):
    """★v79-5b『雑誌の文字を生成』on_click。採用画像を部屋でビート化し、magtextで文字面を1コール生成。
    各ビート先頭sceneに pl_mag_{id}（big_text/accent_word/comment/tags/needs_review）＋pl_narr_{id}（narration_text＝
    画面の文字を読み上げる）。継続シーンは pl_narr 空・pl_mag 消去。cover/data_rows は pl_mag_cover/pl_mag_data へ。
    ★pl_narr_auto は更新しない（テロップ追従を止める＝story-v78と同じ意図）。needs_review は型承認ゲート用に集約表示。"""
    items = st.session_state.get("pl_items", [])
    adopted = sorted([it for it in items if it.get("gen_bytes") and it.get("_adopt", True)],
                     key=lambda it: it.get("order", 0))
    if not adopted:
        st.session_state["_pl_mag_msg"] = "採用画像がありません。"
        return
    beats = []                                    # 連続する同roomを1ビートに（🔀整列後の順を尊重）
    for it in adopted:
        if beats and beats[-1]["room"] == it.get("room"):
            beats[-1]["stock"] += 1
            beats[-1]["ids"].append(it["id"])
        else:
            beats.append({"room": it.get("room"), "stock": 1, "ids": [it["id"]]})
    try:
        client = make_client()
    except RuntimeError:
        client = None
    res = core.magtext(
        client, [{"room": b["room"], "stock": b["stock"]} for b in beats],
        _pl_effective_facts(), feature_id, budget_sec=33)
    if not res:
        st.session_state["_pl_mag_msg"] = "生成できませんでした（Gemini未設定など）。"
        return
    _flags = []                                    # needs_review（型承認・人力採用）を集約
    for b, beat in zip(beats, res["beats"]):
        fid0 = b["ids"][0]
        st.session_state[f"pl_mag_{fid0}"] = {
            "room_label": beat.get("room_label", ""),
            "big_text": beat["big_text"], "accent_word": beat["accent_word"],
            "comment": beat["comment"], "tags": beat["tags"],
            "narration_kana": beat.get("narration_kana", ""),   # ★narr-fix-d：TTSが読む全ひらがな読み
            "needs_review": beat["needs_review"]}
        st.session_state[f"pl_narr_{fid0}"] = beat["narration_text"]   # ★ナレ本文（=comment・字幕/参照/フォールバック用）
        for mid in b["ids"][1:]:
            st.session_state[f"pl_narr_{mid}"] = ""                    # 継続シーン＝空（1枚目にまとまる）
            st.session_state.pop(f"pl_mag_{mid}", None)
        if beat.get("needs_review"):
            _flags.append(f"{beat['room_label']}『{beat['big_text']}』")
    st.session_state["pl_mag_cover"] = res["cover"]
    st.session_state["pl_mag_data"] = res.get("data_rows", [])
    _cov = res["cover"]
    # ★covercopy-v1：📖再生成で候補が入れ替わるので、前回の選択（3案ラジオ）は破棄して1案目に戻す。
    #   残すと『前物件のコピーが選択されたまま新物件の表紙に焼かれる』事故になる（候補外の値はradioも落ちる）。
    st.session_state.pop("pl_cover_hook_pick", None)
    st.session_state.pop("_keep_pl_cover_hook_pick", None)
    if _cov.get("needs_review"):
        _flags.append("表紙コピー（" + " / ".join(_cov["needs_review"]) + "）")
    # ★narr-fix-d診断：kana採用率（誤読残存の切り分け＝kana不発なら辞書追加は無意味・まず原因特定）。
    #   commentありビートのうち narration_kana が採用された数。0/N なら Gemini未出力 or ガード過剰弾き or 未デプロイを疑う。
    _wc = [b for b in res["beats"] if (b.get("comment") or "").strip()]
    _wk = [b for b in _wc if (b.get("narration_kana") or "").strip()]
    _kana_diag = f"🈶 読み仮名(narration_kana)採用 {len(_wk)}/{len(_wc)}ビート" + (
        "（0＝kana不発：Geminiレスポンスに narration_kana があるか／ガード不採用理由を下の🧪と警告で確認）"
        if _wc and not _wk else "（不採用は辞書読みにフォールバック）" if len(_wk) < len(_wc) else "")
    _msg = "📖 文字面を生成しました（各ビート先頭に big_text/comment/タグをまとめました）。　" + _kana_diag
    if _flags:
        _msg += "　⚠️要確認(型承認・人力採用)：" + " / ".join(_flags)
    if res.get("warnings"):
        _msg += "　／　" + "／".join(res["warnings"])
    st.session_state["_pl_mag_msg"] = _msg
    st.session_state["_pl_mag_prompt"] = res.get("prompt", "")
    st.session_state["_pl_mag_raw"] = res.get("raw", "")


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


def _pl_follow_feature_style():
    """特集→スタイル既定の sticky 追従（v70a pl_narr_auto / v70b pl_capmain_auto と同型の3例目）。
    ★見た目の源は特集＝単一の情報源。人がスタイルを明示変更したら停止（pl_style != pl_style_auto）。
    widget生成前に代入（pop禁止＝v70bの教訓）。スタイル記述に生活感を持たせない＝二重情報源の再発防止。
    ★スタイル selectbox(key=pl_style) の生成より前に必ず呼ぶこと。
    ★feat-merge-3：情報源を pl_concept → pl_feature へ。staging（feat-merge-2）と揃えないと
      『stagingはモテのダークトーン／styleは北欧』のように1枚のプロンプトの中で方向が割れる。"""
    sd = core.feature_style_default(st.session_state.get("pl_feature", "normal"))
    if sd in core.INTERIOR_STYLES:
        prev_auto = st.session_state.get("pl_style_auto")
        if st.session_state.get("pl_style") in (None, prev_auto):   # 未設定 or 前回自動値のまま＝未上書き
            st.session_state["pl_style"] = sd                       # ★スタイルwidget生成前に代入
        st.session_state["pl_style_auto"] = sd                      # 追跡値を更新（次回の追従判定用）


def _pl_follow_concept_cover_style():
    """コンセプト→表紙スタイル の sticky 追従（pl_style と同型・pl_cover_style_auto を影キーに）。
    ★setdefault は静的既定で2回目以降に追従しない。人が明示変更したら停止。radio生成前に代入。"""
    cs = core.concept_cover_style(st.session_state.get("pl_concept", "normal"))
    if cs in ("simple", "magazine"):
        prev_auto = st.session_state.get("pl_cover_style_auto")
        if st.session_state.get("pl_cover_style") in (None, prev_auto):   # 未設定/未上書き→追従
            st.session_state["pl_cover_style"] = cs
        st.session_state["pl_cover_style_auto"] = cs                      # 追跡値を更新


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
    # on_click コールバック（地雷①）。uploaderの nonce を進めてから全 pl_ キーを消し、
    # 消えた nonce を新値で復活させる → 次回 file_uploader が別キーで作り直され、
    # 同じファイルの再アップが載るようになる（del はウィジェットをリセットしない＝地雷②回避）。
    n = st.session_state.get("pl_upload_nonce", 0) + 1
    for k in [k for k in list(st.session_state) if k.startswith("pl_")]:
        del st.session_state[k]
    st.session_state["pl_upload_nonce"] = n
    # ★issue-v1：影キー(_keep_*)は先頭 "_" なので上のループで消えない＝号数は次の物件へ持ち越す（連番運用で毎回入力し直さない）。
    #   一方エリアは物件依存なので明示クリア（前の物件のエリアを次の物件に焼き込む事故を防ぐ）。
    st.session_state.pop("_keep_pl_issue_area", None)
    # ★covercopy-v1：表紙コピーの選択も物件依存＝同じ理由で影キーごとクリア（issue-v1のエリアと同型の事故）。
    st.session_state.pop("_keep_pl_cover_hook_pick", None)


def _pl_mag_beats(adopted):
    """★文字面(pl_mag)の生成済みビート（big_text保持）を返す。📖未実行なら空＝雑誌レイアウトが付かない。"""
    _m = [st.session_state.get(f"pl_mag_{it['id']}") for it in adopted]
    return [m for m in _m if isinstance(m, dict) and m.get("big_text")]


def _pl_data_summary():
    """★DATA面の生成前サマリー行（build_data_rows と同一情報源）：ON/OFF・行数・間取り図有無。
    $を払う前に『DATA面がスカスカにならないか（rows充足率）』『間取り図がAで取れたか』を判定できる。"""
    if not bool(st.session_state.get("pl_v_data", True)):
        return "DATA面＝OFF（末尾ページなし）"
    _rows = core.build_data_rows(_pl_effective_facts())
    _fp = st.session_state.get("pl_floorplan") is not None
    _s = f"DATA面＝✅ON（{len(_rows)}行・間取り図{'あり' if _fp else '⚠️なし→表上詰め'}）"
    if len(_rows) < 4:
        _s += "　⚠️行が少なめ（マイソクから取れる項目が少）"
    return _s


def _pl_pregen_summary(adopted):
    """★生成前チェック（$3.15投下前に状態を可視化）：★文字面有無を最上段（誤課金の主因）→特集／整列／🈶採用／辞書読み。
    kana採用/不採用は kana-reason と同一情報源＝pl_mag_{id} の narration_kana 有無。返り値 (markdown, ng_flags[], mag_ready)。"""
    _fid = st.session_state.get("pl_feature", "normal")
    _parts, _ng = [], []
    # ★最上段＝文字面（未生成だと masthead/タグ/金色見出しが一切付かない＝誤課金の主因）
    _mags = _pl_mag_beats(adopted)
    _withc = [m for m in _mags if (m.get("comment") or "").strip()]
    _fb = [m for m in _withc if not (m.get("narration_kana") or "").strip()]
    _mag_ready = bool(_mags)
    if not _mag_ready:
        _parts.append("**文字面＝⚠️未生成**（📖『動く雑誌の文字を生成』を先に実行）")
        _ng.append("文字面未")
    else:
        _parts.append(f"文字面＝✅生成済（{len(_mags)}ビート・🈶kana採用{len(_withc) - len(_fb)}/{len(_withc)}）")
        if _fb:
            _names = "・".join(m.get("room_label", "") for m in _fb)
            _parts.append(f"⚠️辞書読み{len(_fb)}件（{_names}）")
    # ★feat-merge-1：label は表紙の枠に焼く文字（normalは空）なので、人が読む欄は feature_display_name を使う。
    _parts.append(f"特集={core.feature_display_name(_fid)}")
    # 整列済否：現在の並びが room_tour_rank 昇順か（🔀部屋順に整列 相当・状態で判定）
    _ranks = [core.room_tour_rank(it.get("room")) for it in adopted]
    if _ranks == sorted(_ranks):
        _parts.append("整列=済")
    else:
        _parts.append("整列=⚠️未（🔀部屋順に整列を推奨）")
        _ng.append("整列未")
    _parts.append(_pl_data_summary())        # ★DATA面（ON/OFF・行数・間取り図有無）＝課金前に品質判定
    # ★issue-v1：3面に焼かれるマストヘッド文字列を課金前に確定表示。エリアが取れないと号数のみ＝誤エリアは出ないが
    #   「エリアを出したい回」は手入力を促す（黙って地名なしで焼かない）。
    _iss = _pl_issue_text()
    _parts.append(f"雑誌表記={_iss}" + ("" if "/" in _iss else "　⚠️エリア未取得（手入力で補えます）"))
    # ★covercopy-v1：表紙に焼かれるコピーを課金前に確定表示。表紙PNGが古い（選択変更後に作り直していない）ならNG扱い。
    _mcv = st.session_state.get("pl_mag_cover") or {}
    _hk = _pl_effective_hook()
    _hk_nr = (_mcv.get("needs_review_by_hook") or {}).get(_hk)
    _parts.append(f"表紙コピー=『{_hk}』"
                  + ("　⚠️特集の既定（物件別になっていない）" if _mcv.get("hook_source") == "feature_fallback" else "")
                  # ★選択中の案に要確認表現があるなら課金前に出す（どの案かは📖側で本文つきに表示）
                  + (f"　⚠️要確認（{' / '.join(_hk_nr)}）" if _hk_nr else ""))
    _cstale, _cbaked, _ccur = _pl_cover_stale()
    if _cstale:
        _parts.append(f"⚠️表紙PNGが古い（焼込『{_cbaked}』≠現在『{_ccur}』・🖼️表紙特大で作り直し）")
        _ng.append("表紙PNG古い")
    return "　／　".join(_parts), _ng, _mag_ready


def _pl_reset_arm_cb():
    """★誤爆対策：『最初からやり直す』1クリック目＝武装のみ（消さない）。2段確認で巻き戻り＋画像化再課金を防ぐ。"""
    st.session_state["_pl_reset_armed"] = True


def _pl_reset_cancel_cb():
    st.session_state["_pl_reset_armed"] = False


def _pl_reset_confirm_cb():
    """『はい、最初からやり直す』＝実行。_pl_reset は pl_ キーを消すが _pl_reset_armed(先頭_)は残るので明示解除。"""
    st.session_state["_pl_reset_armed"] = False
    _pl_reset()


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


def _pl_gen_sorted():
    """生成済アイテムを order 昇順で返す。"""
    return sorted([it for it in st.session_state.get("pl_items", [])
                   if it.get("gen_bytes")], key=lambda it: it.get("order", 0))


def _pl_auto_reorder():
    """★roomsort-v78：生成済アイテムの order を標準ツアー順（core._ROOM_TOUR_ORDER・1箇所）に振り直す。
    同ランクは現order維持＝安定ソート（LDK1枚目/2枚目の相対順を壊さない）。★ワンショット：ボタン押下時のみ
    呼ぶ（初回自動整列は廃止＝押さなければPDF順・機械が勝手に動かさない・状態を持たない＝sticky事故のクラスを断つ）。"""
    gen = sorted(_pl_gen_sorted(),
                 key=lambda it: (core.room_tour_rank(it.get("room")), it.get("order", 0)))
    for i, it in enumerate(gen):
        it["order"] = i


def _pl_move(iid, delta):
    """order 順で iid のアイテムを delta 方向の隣と order を入れ替える。
    ★autosort-v1 条件2：手動で並べ替えたら pl_order_manual を立て、以後の自動整列を発火させない（手動順を絶対に上書きしない）。"""
    gen = _pl_gen_sorted()
    idx = next((k for k, it in enumerate(gen) if it["id"] == iid), None)
    if idx is None:
        return
    j = idx + delta
    if 0 <= j < len(gen):
        gen[idx]["order"], gen[j]["order"] = gen[j]["order"], gen[idx]["order"]
        st.session_state["pl_order_manual"] = True   # ★手動操作＝以後 autosort 発火しない


def _pl_restore_order():
    """★autosort-v1 条件3：自動整列前の元順（PDF順）に戻す。以後は手動扱い＝再度の自動整列をしない（逃げ道）。"""
    _orig = dict(st.session_state.get("pl_order_original") or [])
    for it in st.session_state.get("pl_items", []):
        if it["id"] in _orig:
            it["order"] = _orig[it["id"]]
    st.session_state["pl_order_manual"] = True
    st.session_state.pop("pl_autosorted_shown", None)   # 自動整列通知を消す（もう自動整列状態でない）


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
        # ★v79-feature-reach(a)：補完生成にも特集を届ける（従来ここだけテイストが素通りしていた）。
        pr = core.build_room_tour_prompt(
            style_desc, room_label, core.ROOM_TOUR_PRESETS.get(room_label, ""),
            with_ref=anchor is not None, user_request=_req,
            concept_staging=core.feature_staging(st.session_state.get("pl_feature", "normal")))
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
        # ★v79-feature-reach(a)：3Dパースにも特集を届ける。
        pr = core.build_3d_perspective_prompt(
            style_desc, user_request=_req,
            concept_staging=core.feature_staging(st.session_state.get("pl_feature", "normal")))
        data, err = core.generate_from_images(
            client, [(base, "image/png")], pr, model=model, aspect=aspect,
            size="2K", add_safety=False)
        return data, err, _PL_PERSP_DISC
    # 事実ガード（3条件）を req 先頭に前置＝記載外設備を描かせない（帖数ヒントと同方式）
    _sreq = "\n".join(x for x in [it.get("_stage_facts", ""), req] if x)
    # ★feat-merge-2：内観stagingの方向づけを『特集』(pl_feature) から取る＝テイストの単一情報源。
    #   これ以前は pl_concept 側を読んでいたため、特集を切り替えても内観画像のプロンプトは1バイトも
    #   変わらなかった（FEATURES[*]["staging_prompt"] は参照ゼロの死にデータだった）。
    #   ★引数名 concept_staging= は据え置き（改名は差分を無駄に広げるだけ・feat-dead-1 で整理する）。
    #   ★fallback は "normal"（"mote_heya" ではない）。②画像化は④動画化の expander より前に走るため、
    #     新規セッションで④を一度も開かないと pl_feature は未設定。ここで mote_heya に倒すと
    #     人が特集を選んでいないのにダークトーンの staging が入る＝回帰。未選択は「追加なし」に倒す。
    _cst = core.feature_staging(st.session_state.get("pl_feature", "normal"))
    if t == "リノベ後イメージ":
        # room-aware（部屋の機能を保ったまま刷新）
        pr = core.build_renovation_prompt(style_desc, user_request=_sreq, room=room)
        disc = "※リノベ後のイメージ（仕上がりは設計により異なります）"
    elif t == "家具ステージング" and room in PL_RESIDENTIAL:
        pr = core.build_staging_prompt(style_desc, _pl_room_use(room), user_request=_sreq,
                                       concept_staging=_cst)
        disc = "※AI加工のイメージ"
    elif t == "家具ステージング":
        # 非居室に家具ステージングが来た場合の最終防波堤（居室用ステージングは流さない）
        if room in ("キッチン", "玄関", "廊下", "バルコニー"):
            pr = core.build_water_staging_prompt(style_desc, user_request=_sreq, concept_staging=_cst)
            disc = "※AI加工のイメージ"
        else:  # 浴室・洗面・トイレ・クローゼット・その他
            pr = core.build_enhance_prompt()
            disc = None
    elif t == "水回り・玄関を演出":
        pr = core.build_water_staging_prompt(style_desc, user_request=_sreq, concept_staging=_cst)
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
        # ★roomsort-v78：初回自動整列を廃止（PDF順のまま③へ）。並び替えは③の「🔀 部屋順に整列」で人が1クリック。
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


# 取り込み解除／再取り込みで一掃する物件固有キー（ユーザー設定 _PL_KEEP_ON_IMPORT は保持）
_PL_PROPERTY_EXACT = (
    "pl_src_sig", "pl_items", "pl_rooms", "pl_floorplan", "pl_summary",
    "pl_facts", "pl_prcopy", "pl_flash_text", "pl_v_tag", "pl_title_edit",
    "pl_sub_edit", "pl_title_idx", "pl_cover_title", "pl_cover_sub",
    "pl_cover_src", "pl_cover_png", "pl_cover_copy", "pl_gap_targets", "pl_persp_png",
    # 売買マイソク対応（物件固有）：種別・facts抽出ゲート・確認状態・物件選択
    "pl_ptype", "pl_ptype_sig", "pl_facts_key", "pl_facts_confirmed",
    "pl_facts_confirm_chk", "pl_prop_pick", "pl_prop_manual_start",
    "pl_prop_manual_end", "pl_prop_manual_on",
    "pl_video_out", "pl_video_err",   # 生成済み動画のパス/失敗表示（物件固有・新規取り込みでクリア）
    "pl_sns")                          # SNS投稿文（物件固有）
_PL_PROPERTY_PREFIX = ("pl_room_", "pl_roomid_", "pl_treat_", "pl_fp_pick",
                       "pl_capmain_", "pl_capsub_", "pl_taste_", "pl_pos_",
                       "pl_narr_", "_pl_narr_msg_",
                       "pl_facts_edit_")


def _pl_clear_property_keys():
    """物件固有キーを一掃（取り込み解除／再取り込み用）。ユーザー設定(_PL_KEEP_ON_IMPORT)は保持。
    ※ pl_room_ 接頭辞は pl_room_lang（ユーザー設定）を巻き込むため除外リストで守る（地雷③）。
    ウィジェットキー(pl_room_*/pl_treat_*/pl_roomid_*)を消すのは、それらを生成する前に呼ぶこと。"""
    for k in _PL_PROPERTY_EXACT:
        st.session_state.pop(k, None)
    for k in [k for k in list(st.session_state)
              if k.startswith(_PL_PROPERTY_PREFIX) and k not in _PL_KEEP_ON_IMPORT]:
        del st.session_state[k]


def _pl_all_other(codes):
    """分類結果が全画像『その他』のみか（＝分類失敗の可能性）。設備痕跡コードは除外して判定。"""
    if not codes:
        return True
    for cl in codes:
        room = [c for c in (cl or []) if c not in _PL_FEATURE_CODES]
        if any(c != "OTHER" for c in room):
            return False
    return True


def _pl_classify_with_retry(raw_srcs):
    """部屋種別分類を実行。失敗（例外 or 全『その他』）なら2.5秒待って1回だけ再試行する
    （レート制限は一過性のため）。返り値 (codes, warn|None)。落とさず必ず codes を返す。
    warn は '{例外型}: {先頭120字}' か '全画像が『その他』判定になりました'。"""
    import time as _time
    default = [["OTHER"] for _ in raw_srcs]

    def _run():
        return core.classify_maisoku_images(make_client(), raw_srcs)

    warn = None
    try:
        codes = _run()
    except Exception as e:  # noqa: BLE001  make_client失敗・想定外例外（黙って握り潰さない）
        codes, warn = None, f"{type(e).__name__}: {str(e)[:120]}"
    if codes is None or _pl_all_other(codes):          # 失敗の可能性→1回だけ再試行
        _time.sleep(2.5)
        try:
            retry = _run()
            if _pl_all_other(retry):
                codes = retry
                warn = warn or "全画像が『その他』判定になりました"
            else:
                codes, warn = retry, None               # 再試行で成功
        except Exception as e:  # noqa: BLE001
            codes = codes if codes is not None else default
            warn = f"{type(e).__name__}: {str(e)[:120]}"
    return (codes or default), warn


# 売買factsの編集フィールド（賃貸/売買共通＋売買固有）。key接頭辞は pl_facts_edit_
_PL_FACTS_COMMON = ("name", "address", "madori", "area", "built", "fee", "equipment")
_PL_FACTS_SALE_EXTRA = ("shuzen", "floor", "genkyo", "note")
_PL_FACTS_LABEL = {
    "name": "建物名", "address": "所在地", "madori": "間取り", "area": "専有面積",
    "built": "築年月", "fee": "管理費", "equipment": "設備（設備欄記載のみ）",
    "rent": "賃料", "price": "価格", "shuzen": "修繕積立金", "floor": "所在階・向き",
    "genkyo": "現況", "note": "備考・特記",
}


def _pl_extract_sale_facts_with_retry(pdf_bytes):
    """売買factsを Gemini vision で抽出。例外は握り潰さず、失敗時は2.5秒待って1回だけ再試行。
    返り値 (facts, warn|None)。落とさず必ず facts を返す（失敗時は {}）。"""
    import time as _time

    def _run():
        return core.extract_sale_facts_vision(make_client(), pdf_bytes)

    try:
        return _run(), None
    except Exception as e:  # noqa: BLE001  黙って握り潰さない
        warn = f"{type(e).__name__}: {str(e)[:120]}"
    _time.sleep(2.5)
    try:
        return _run(), None
    except Exception as e:  # noqa: BLE001
        return {}, f"{type(e).__name__}: {str(e)[:120]}"


def _pl_effective_facts():
    """事実ガード・PRコピーが使う『有効facts』。売買で未確認なら設備を空に倒す（フェイルセーフ）。
    ＝未確認の売買では equipment を事実として使わず、持ち込み小物のみ許可に寄せる。"""
    f = dict(st.session_state.get("pl_facts", {}))
    if f.get("_ptype") == "sale" and not st.session_state.get("pl_facts_confirmed"):
        f["equipment"] = ""
    return f


def _pl_effective_templates():
    """投稿文の有効テンプレ＝リポジトリ既定（caption_templates.json）に設定画面の編集値を上書き。
    編集値は session_state（Cloud再起動で消える＝設定画面でエクスポート推奨をUI明記）。"""
    return {**core.default_caption_templates(), **st.session_state.get("pl_caption_tpl", {})}


def _pl_apply_facts_edit():
    """facts編集フォームの確定（on_click）。編集値を pl_facts に反映し確認状態も更新する。
    ※session_state書き換えはコールバック内で行う（地雷①）。"""
    f = dict(st.session_state.get("pl_facts", {}))
    ptype = f.get("_ptype", "rent")
    fields = list(_PL_FACTS_COMMON) + [("price" if ptype == "sale" else "rent")]
    if ptype == "sale":
        fields += list(_PL_FACTS_SALE_EXTRA)
    for k in fields:
        v = st.session_state.get(f"pl_facts_edit_{k}", "")
        v = v.strip() if isinstance(v, str) else v
        if v:
            f[k] = v
        else:
            f.pop(k, None)
    acc = [a.strip() for a in st.session_state.get("pl_facts_edit_access", "").split("\n")
           if a.strip()]
    if acc:
        f["access"] = acc
    else:
        f.pop("access", None)
    f["_ptype"] = ptype
    st.session_state["pl_facts"] = f
    # 賃貸は常に確認済み扱い。売買は確認チェックの値に従う
    st.session_state["pl_facts_confirmed"] = (
        True if ptype == "rent" else bool(st.session_state.get("pl_facts_confirm_chk")))


def _pl_seed_facts_edit(facts):
    """抽出/再抽出したfactsを編集フォームの各キーに種付け（取り込み時1回）。"""
    ptype = facts.get("_ptype", "rent")
    fields = list(_PL_FACTS_COMMON) + [("price" if ptype == "sale" else "rent")]
    if ptype == "sale":
        fields += list(_PL_FACTS_SALE_EXTRA)
    for k in fields:
        st.session_state[f"pl_facts_edit_{k}"] = str(facts.get(k, "") or "")
    acc = facts.get("access") or []
    st.session_state["pl_facts_edit_access"] = "\n".join(acc) if isinstance(acc, list) else str(acc)


def _pl_render_facts_form(items):
    """B3: factsの確認・修正UI（賃貸/売買共通）。売買は『確認済み』にするまで設備を事実に使わない
    （フェイルセーフ＝持ち込み小物のみ許可）。種別ラジオは form の外＝変更で即再抽出される。"""
    if not st.session_state.get("pl_items"):
        return
    ptype = st.session_state.get("pl_ptype", "rent")
    with st.expander("📋 物件情報（マイソク/AI抽出）を確認・修正", expanded=(ptype == "sale")):
        st.radio("種別", ["rent", "sale"], horizontal=True, key="pl_ptype",
                 format_func=lambda x: {"rent": "賃貸", "sale": "売買"}.get(x, x))
        if ptype == "sale":
            st.caption("売買図面はAI抽出のため誤りが混じり得ます。**内容を確認し、必要なら修正**して"
                       "『確認済み』にチェック→『この内容で確定』を押してください。未確認の間は"
                       "設備を事実として使いません（持ち込み小物のみ許可のフェイルセーフ）。")
        _money = "price" if ptype == "sale" else "rent"
        with st.form("pl_facts_form"):
            f1, f2 = st.columns(2)
            f1.text_input(_PL_FACTS_LABEL["name"], key="pl_facts_edit_name")
            f2.text_input(_PL_FACTS_LABEL["address"], key="pl_facts_edit_address")
            f3, f4 = st.columns(2)
            f3.text_input(_PL_FACTS_LABEL["madori"], key="pl_facts_edit_madori")
            f4.text_input(_PL_FACTS_LABEL["area"], key="pl_facts_edit_area")
            f5, f6 = st.columns(2)
            f5.text_input(_PL_FACTS_LABEL["built"], key="pl_facts_edit_built")
            f6.text_input(_PL_FACTS_LABEL[_money], key=f"pl_facts_edit_{_money}")
            st.text_area("交通（1行に1つ・例『◯◯線◯◯駅 徒歩5分』）",
                         key="pl_facts_edit_access", height=70)
            f7, f8 = st.columns(2)
            f7.text_input(_PL_FACTS_LABEL["fee"], key="pl_facts_edit_fee")
            if ptype == "sale":
                f8.text_input(_PL_FACTS_LABEL["shuzen"], key="pl_facts_edit_shuzen")
                s1, s2 = st.columns(2)
                s1.text_input(_PL_FACTS_LABEL["floor"], key="pl_facts_edit_floor")
                s2.text_input(_PL_FACTS_LABEL["genkyo"], key="pl_facts_edit_genkyo")
                st.text_input(_PL_FACTS_LABEL["note"], key="pl_facts_edit_note")
            st.text_input(_PL_FACTS_LABEL["equipment"], key="pl_facts_edit_equipment",
                          help="設備欄に明記された設備のみ。広告文・リフォーム説明からの推測は入れない。")
            if ptype == "sale":
                st.checkbox("この内容を確認済みにする（STEP2以降の事実ガード・PRコピーに使う）",
                            key="pl_facts_confirm_chk")
            st.form_submit_button("この内容で確定", on_click=_pl_apply_facts_edit)
        if ptype == "sale" and not st.session_state.get("pl_facts_confirmed"):
            st.info("未確認：設備は事実ガードに使いません（持ち込み小物のみ許可）。"
                    "確認できたら上のチェックを入れて『この内容で確定』を押してください。")


def _pl_stage_input():
    import hashlib as _hashlib
    st.markdown("#### ① 取り込み・種別わけ")
    c1, c2 = st.columns(2)
    # uploaderキーは nonce 付き＝リセット/取り込み解除で作り直し、同一ファイルの再アップを可能に
    _un = st.session_state.setdefault("pl_upload_nonce", 0)
    pdf = c1.file_uploader("マイソクPDF（埋め込み写真を抽出）", type=["pdf"],
                           key=f"pl_pdf_{_un}")
    photos_up = c2.file_uploader("手持ち写真（複数可）",
                                 type=["png", "jpg", "jpeg", "webp"],
                                 accept_multiple_files=True, key=f"pl_photos_{_un}")
    # B1: 複数物件が連結された一括PDF（レインズDL）を物件ごとに分割→1件だけ選んで取り込む。
    # 自動分割が合わない場合の手動ページ範囲指定フォールバックを必ず用意する。
    active_pdf_bytes = None
    if pdf is not None:
        _full = pdf.getvalue()
        _npg = core.pdf_page_count(_full)
        _s, _e = 0, _npg
        # 賃貸の複数物件一括DLは運用上存在しないため、賃貸/不明は分割せず全ページ=1物件とする。
        # （2ページ構成の賃貸マイソクが2物件に誤分割される回帰を防ぐ。判定はPDF全体テキストで。）
        # 分割UI（物件選択・手動範囲）は売買判定時のみ表示する。
        if core.detect_property_type(core.pdf_full_text(_full)) == "sale":
            _props = core.split_pdf_properties(_full)
            if len(_props) > 1:
                st.caption(f"この一括PDFには **{len(_props)}物件** が含まれます。取り込む1件を選んでください"
                           "（複数物件の同時処理はできません）。")
                _labels = [f"{i+1}. {p['label']}（p{p['start']+1}〜{p['end']}）"
                           for i, p in enumerate(_props)]
                _sel = st.selectbox("取り込む物件", list(range(len(_props))),
                                    format_func=lambda i: _labels[i], key="pl_prop_pick")
                _s, _e = _props[_sel]["start"], _props[_sel]["end"]
            with st.expander("ページ範囲を手動指定（自動分割が合わない場合）"):
                mc1, mc2 = st.columns(2)
                _ms = mc1.number_input("開始ページ", 1, _npg, _s + 1, key="pl_prop_manual_start")
                _me = mc2.number_input("終了ページ", 1, _npg, min(_e, _npg), key="pl_prop_manual_end")
                if st.checkbox("手動範囲を使う", key="pl_prop_manual_on"):
                    _s, _e = int(_ms) - 1, int(_me)
        active_pdf_bytes = core.subpdf_bytes(_full, _s, _e) if (_s, _e) != (0, _npg) else _full
    raw_srcs = []
    pdf_imgs = []
    if pdf is not None:
        try:
            pdf_imgs = [b for (b, _w, _h) in core.extract_pdf_photos(active_pdf_bytes, min_px=250)]
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
        # 取り込み解除：以前に取り込んだ物件があるなら状態を一掃して input へ戻す
        # （✕で消した後に同じファイルを再アップ→sig一致で旧itemsが使われ続ける事故を防ぐ）
        if st.session_state.get("pl_src_sig"):
            _pl_clear_property_keys()
            st.session_state["pl_stage"] = "input"
            st.session_state["pl_upload_nonce"] = st.session_state.get("pl_upload_nonce", 0) + 1
            st.info("取り込みを解除しました。マイソクPDF か 手持ち写真をアップしてください。")
        else:
            st.info("マイソクPDF か 手持ち写真をアップしてください。")
        return

    sig = _hashlib.md5(b"".join(s[:2000] for s in raw_srcs)
                       + str(len(raw_srcs)).encode()).hexdigest()
    _classify_warn = None
    if st.session_state.get("pl_src_sig") != sig:
        # 細粒度分類を取り込み時1回だけ（部屋種別＋間取り図/外観/地図/白紙の判定を兼ねる）。
        # 失敗（例外/全その他）は握り潰さず警告＋1回リトライ。パイプラインは続行する。
        with st.spinner("AIが各写真の部屋種別を判定中…"):
            codes, _classify_warn = _pl_classify_with_retry(raw_srcs)
        if _classify_warn:
            st.warning(f"AIによる部屋種別の判定に失敗しました（{_classify_warn}）。"
                       "部屋種別を手動で選んでください。")
        parsed = _pl_parse_maisoku(active_pdf_bytes) if pdf is not None else {"rooms": [], "summary": ""}
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
            except Exception as e:  # noqa: BLE001  握り潰さず知らせる（続行はする）
                st.warning(f"間取り図の読み取りに失敗しました（{type(e).__name__}: "
                           f"{str(e)[:120]}）。部屋の自動リンクが減る場合があります。")
        # 名前付き部屋リスト（間取り図読取∪間取タイプ居室∪標準部屋）。マイソク文脈がある時のみ
        if floor_plan is not None or parsed["rooms"] or vision_rooms:
            pl_rooms = _pl_build_rooms(parsed["rooms"], items, vision_rooms)
            _pl_link_items(items, pl_rooms)
            # 名前付き部屋はあるのに1件もリンクできない＝自動リンク全滅（分類が弱い等）。
            # 分類警告を既に出していなければ、原因が見えるよう知らせる。
            if (pl_rooms and not _classify_warn
                    and not any(it.get("room_id") for it in items)):
                st.warning("部屋の自動リンクができませんでした"
                           "（AI判定が不十分な可能性）。部屋種別を手動で選んでください。")
        else:
            pl_rooms = []                            # 手持ち写真のみ→汎用ドロップダウン
            _pl_assign_jo(items, parsed["rooms"])
        st.session_state["pl_items"] = items
        st.session_state["pl_rooms"] = pl_rooms
        st.session_state["pl_src_sig"] = sig
        st.session_state["pl_floorplan"] = floor_plan
        st.session_state["pl_summary"] = parsed["summary"]
        # ※ 事実抽出（pl_facts）は種別（賃貸/売買）に依存するため、この直後の
        #   (sig, 種別) ゲートの独立ブロックで行う（種別切替で再抽出できるようにするため）。
        # 新規取り込みで物件固有の値を一掃（別物件の建物名・帖数・コピーの焼き込み防止）
        # 完全一致（物件固有）：下書き・フラッシュ文言・上部タグ・タイトル/サブ編集・選択・表紙
        for k in ("pl_prcopy", "pl_flash_text", "pl_v_tag",
                  "pl_title_edit", "pl_sub_edit", "pl_title_idx",
                  "pl_cover_title", "pl_cover_sub", "pl_cover_src", "pl_cover_png",
                  "pl_gap_targets"):   # 補完生成の対象選択（物件固有）。生成結果はpl_items再構築で自動リセット
            st.session_state.pop(k, None)
        # 接頭辞（物件固有・写真ごと）：部屋/処理/間取り図選択＋テロップ本文・個別スタイル
        #   ＋売買facts編集フォーム（pl_facts_edit_）。
        # ※ pl_room_ は pl_room_lang（ユーザー設定）と前方一致するため残すキーは除外
        for k in [k for k in list(st.session_state)
                  if k.startswith(("pl_room_", "pl_roomid_", "pl_treat_", "pl_fp_pick",
                                   "pl_capmain_", "pl_capsub_", "pl_taste_", "pl_pos_",
                                   "pl_narr_", "_pl_narr_msg_",
                                   "pl_facts_edit_"))
                  and k not in _PL_KEEP_ON_IMPORT]:
            del st.session_state[k]
        # ウィジェットの値は session_state で管理（変更コールバックが上書きするため）
        for it in items:
            if pl_rooms:
                st.session_state[f"pl_roomid_{it['id']}"] = it["room_id"]
            else:
                st.session_state[f"pl_room_{it['id']}"] = it["room"]
            st.session_state[f"pl_treat_{it['id']}"] = it["treatment"]

    # B2: 種別（賃貸/売買）判定＋facts抽出。種別に依存するため (sig, 種別) でゲート。
    #   賃貸 → 既存 parse_maisoku_facts（不変）。売買 → Gemini vision（握り潰さず警告＋リトライ）。
    if pdf is not None:
        # 新規PDF：種別を自動検出して既定に（前物件の上書きは破棄）。売買は用途既定を事業Bに。
        if st.session_state.get("pl_ptype_sig") != sig:
            _det = core.detect_property_type(core.pdf_full_text(active_pdf_bytes))
            if _det == "unknown":     # 物件単体で不明なら一括PDF全体で補強（バッチは同種）
                _det = core.detect_property_type(core.pdf_full_text(pdf.getvalue()))
            st.session_state["pl_ptype"] = _det if _det in ("rent", "sale") else "rent"
            if st.session_state["pl_ptype"] == "sale":
                st.session_state["pl_mode"] = "リノベ提案（事業B）"   # 変更は可能
            st.session_state["pl_ptype_sig"] = sig
        _ptype = st.session_state.get("pl_ptype", "rent")
        _facts_key = f"{sig}:{_ptype}"
        if st.session_state.get("pl_facts_key") != _facts_key:
            if _ptype == "sale":
                with st.spinner("売買図面から情報をAIで抽出中…（Gemini vision）"):
                    _facts, _sale_warn = _pl_extract_sale_facts_with_retry(active_pdf_bytes)
                if _sale_warn:
                    st.warning(f"売買図面のAI抽出に失敗しました（{_sale_warn}）。"
                               "下のフォームに手入力で補ってください。")
                st.session_state["pl_facts_confirmed"] = False   # 売買は要確認（フェイルセーフ）
                st.session_state["pl_facts_confirm_chk"] = False  # 確認チェックの初期値
            else:
                _facts = core.parse_maisoku_facts(active_pdf_bytes)   # 賃貸は従来どおり
                st.session_state["pl_facts_confirmed"] = True
            _facts["_ptype"] = _ptype
            st.session_state["pl_facts"] = _facts
            _pl_seed_facts_edit(_facts)
            st.session_state["pl_facts_key"] = _facts_key
    else:
        st.session_state.setdefault("pl_facts", {})

    items = st.session_state.get("pl_items", [])

    # 物件サマリ：賃貸は間取タイプparse由来（従来）。売買は同parseが誤検出するので
    #   vision抽出facts（間取り/面積）から組み立てる（誤った面積の誇大表示＝優良誤認を防ぐ）。
    if st.session_state.get("pl_ptype") == "sale":
        _sf = st.session_state.get("pl_facts", {})
        _ssum = " ／ ".join(x for x in (_sf.get("madori", ""), _sf.get("area", ""),
                                        _sf.get("price", "")) if x)
        if _ssum:
            st.info(f"この物件（売買）：{_ssum}")
    elif st.session_state.get("pl_summary"):
        st.info(f"この物件：{st.session_state['pl_summary']}")

    _pl_render_facts_form(items)

    st.markdown("**何をつくる？**（用途を選ぶと各画像の処理が部屋種別から自動で決まります）")
    st.radio("用途", PL_MODES, horizontal=True, key="pl_mode",
             on_change=_pl_apply_mode_defaults)
    st.caption("賃貸ステージング＝家具を置いて魅せる（構造は維持）／"
               "リノベ提案（事業B）＝内装ごと刷新した完成イメージ（機能と骨格は維持）。"
               "処理は部屋種別ごとに自動設定され、必要なら個別に変更できます。")
    _IMG_ASPECT_LABEL = {"4:5": "4:5（Instagram投稿）", "1:1": "1:1（正方形）", "3:4": "3:4（縦）"}
    _pl_follow_feature_style()          # ★スタイルwidget生成前に 特集→スタイル を追従（sticky）
    gc1, gc2, gc3 = st.columns(3)
    style_name = gc1.selectbox("スタイル", list(core.INTERIOR_STYLES.keys()), key="pl_style")
    # ★人がスタイルを明示上書き中は一行明示＝「追従停止（正常）」を「追従漏れ（バグ）」と誤認させない。
    if st.session_state.get("pl_style") != st.session_state.get("pl_style_auto"):
        gc1.caption("✋ スタイルを手動で選択中（コンセプトに追従しません）")
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
    facts = _pl_effective_facts()   # 売買未確認は設備を空に倒す（フェイルセーフ）
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
    facts_block = _pl_gap_facts_block(_pl_effective_facts())  # 事実ガード（売買未確認は設備空）
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
    # ★autosort-v1：画像化直後に部屋順を『1回だけ』自動整列（sticky事故対策の3条件）。
    #   条件1(一回限り)=pl_autosort_done で再発火しない。条件2(手動不上書き)=pl_order_manual が立っていたら自動整列しない。
    #   条件3(明示+取消)=自動整列したら _pl_autosorted で通知＋「元の順に戻す」を出す。★in-place で order を振り直す＝rerun不要。
    if not st.session_state.get("pl_autosort_done") and not st.session_state.get("pl_order_manual"):
        st.session_state["pl_order_original"] = [(it["id"], it.get("order", 0)) for it in gen_items]
        _pl_auto_reorder()
        st.session_state["pl_autosort_done"] = True
        st.session_state["pl_autosorted_shown"] = True
    if st.session_state.get("pl_autosorted_shown"):
        _ac1, _ac2 = st.columns([3, 1])
        _ac1.success("🔀 部屋順に自動整列しました（外観→玄関→LDK→…→洋室）。意図と違えば右の「元の順に戻す」で。")
        _ac2.button("元の順に戻す", key="pl_restore_order_btn", on_click=_pl_restore_order,
                    use_container_width=True)
    # ★roomsort-v78/autosort-v1：「🔀 部屋順に整列」はワンショット手動（自動整列後の再整列・元に戻した後の再整列に使える）。
    #   押した後に ↑上へ/↓下へ で直せる（その修正を機械が上書きしない＝pl_order_manual を立てる）。
    if st.button("🔀 部屋順に整列（外観→玄関→LDK→キッチン→水回り→…→洋室）",
                 key="pl_reorder_btn", use_container_width=True,
                 help="採用画像をこの1回だけ標準ツアー順に並べ替えます。押さなければ元の順のまま。"
                      "同じ部屋（LDK2枚など）は隣り合い、後で ↑上へ/↓下へ で微調整できます。"):
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


# ★issue-v1：旧 _PL_AREA_ROMAJI / _pl_cover_subline（v78の方向転換で呼び出し元ゼロの死にコード）は
#   core._AREA_ROMAJI / core.magazine_issue_line へ移設した（2源化の温床を断つ＝生成ロジックは core 1源）。


def _pl_issue_text(facts=None):
    """★issue-v1：マストヘッド2行目の確定文字列を返す唯一の入口。
    UIプレビュー／表紙／ビート面／DATA面の4消費すべてがこれを見る＝『見えている文字列＝焼かれる文字列』を機械保証。
    facts 省略時は _pl_effective_facts()（表紙は呼出側の facts をそのまま使う）。"""
    return core.magazine_issue_line(
        _pl_effective_facts() if facts is None else facts,
        st.session_state.get("pl_issue_no", 1),
        st.session_state.get("pl_issue_area", ""))


# ★covercopy-v1：_pl_cover_clean_copy（呼出元ゼロ）と _pl_cover_ai_cb（on_click登録先ゼロ＝ボタン本体が無い）を削除。
#   両者が使っていた core.draft_cover_copy も併せて削除し、表紙コピーの生成経路を magtext の3案に一本化した。
#   コピーの機械ガードは core._scrub_cover_copy() に集約。
#   ※下の _pl_cover_default_src は生きている（autoカバーと表紙UIが使う）。死にコード削除の巻き添えで一度消して
#     NameError を作り込んだため、範囲を絞って復元した経緯あり。


def _pl_cover_default_src(adopted):
    """表紙素材の既定：最初のLDK→無ければ先頭の居室→無ければ先頭。"""
    for it in adopted:
        if it.get("room") == "LDK":
            return it["id"]
    for it in adopted:
        if it.get("room") in ("洋室", "寝室"):
            return it["id"]
    return adopted[0]["id"] if adopted else None


def _pl_effective_hook(feature_id=None):
    """★covercopy-v1：表紙コピーの確定文字列を返す唯一の入口（issue-v1 の _pl_issue_text と同じ思想）。
    優先順＝①人が選んだ案（3案ラジオ）＞②magtextの既定案（1案目）＞③特集の既定コピー（📖未実行時の回帰経路）。
    ★①は現在の候補に含まれるときだけ有効。📖再生成で候補が入れ替わったのに旧選択が残ると
      『画面に出ている案と焼かれる案が別物』になるため、候補外なら②へ落とす（受入8と同型の担保）。"""
    _mc = st.session_state.get("pl_mag_cover") or {}
    _cands = [c for c in (_mc.get("hook_candidates") or []) if str(c).strip()]
    _pick = str(st.session_state.get("pl_cover_hook_pick", "") or "").strip()
    if _cands and _pick not in _cands:
        _pick = ""
    _fid = feature_id or st.session_state.get("pl_feature", "normal")
    _fallback = ((core.FEATURES.get(_fid) or {}).get("cover_hooks") or [""])[0]
    return _pick or str(_mc.get("hook", "") or "").strip() or _fallback


def _pl_cover_stale():
    """★covercopy-v1：表紙PNGに焼かれたコピーと、現在の確定コピーが食い違っていないかを返す (stale, baked, current)。
    autoカバーは _feat_sig に hook を含めたので自動追従する（ffmpegのみ＝無課金）。
    手動生成カバーは追従を止める仕様（_pl_cover_auto_sig=None）なので、ここだけ古いまま残りうる＝黙って使わない。
    ★hookキーを持たない旧PNG（covercopy-v1以前に作られたもの）は判定不能なので stale としない（誤警告を出さない）。"""
    _cov = st.session_state.get("pl_cover_png") or {}
    if not _cov.get("bytes"):
        return False, "", ""
    baked = str(_cov.get("hook", "") or "")
    cur = _pl_effective_hook()
    return (bool(baked) and baked != cur), baked, cur


def _pl_v79_area_line(facts):
    """★v79 表紙エリア行（暫定・正式文面はv79-5 magtext）。access最短徒歩から『{駅}、駅{n}分。』。無ければ間取り・面積。"""
    import re
    band = _pl_cover_access_band(facts.get("access"))
    m_st = re.search(r"([^\s　]+?)駅", band or "")
    m_wk = re.search(r"徒歩\s*(\d+)\s*分", band or "")
    if m_st and m_wk:
        return f"{m_st.group(1)}、駅{m_wk.group(1)}分。"
    return _pl_cover_madori_area(facts) or "OSAKA ROOMS"


# ★v79-3.1/v79-4：情報バー設備行のカテゴリ別優先（セキュリティ→水回り2件→通信→残り）。訴求力順（ネット無料を確保）。
#   各カテゴリ (語リスト, 上限件数)。上から順に、facts に含まれる語を上限まで拾い、合計 max_items で打ち切り。
_PL_EQUIP_CATEGORIES = [
    (["オートロック", "モニター付インターホン", "カメラ付きインターホン", "TVモニターホン", "宅配ボックス"], 1),   # セキュリティ
    (["バス・トイレ別", "バストイレ別", "独立洗面台", "追焚", "追い焚き", "浴室乾燥",
      "室内洗濯機置場", "温水洗浄便座", "ウォシュレット"], 2),                                                # 水回り（最大2）
    (["インターネット無料", "ネット無料", "光ファイバー", "光配線"], 1),                                       # 通信（ネット無料）
    (["エアコン", "システムキッチン", "都市ガス", "フローリング", "収納", "宅配ボックス"], 4),                  # 残り
]


def _strip_raw_dim(s):
    """★v79-4b(バグ②)：『10x6』『10.5×6.0』等の生寸法トークン（整形前の帖寸法）を表示文字列から除去。
    madori/area/tag のどのフィールドに紛れても情報バーに出さない（v79-3.1のmadori単体ガードの穴を塞ぐ）。"""
    return re.sub(r"\s*\d+(?:\.\d+)?\s*[xX×✕]\s*\d+(?:\.\d+)?\s*", " ", str(s or "")).strip()


def _pl_cover_equip_line(facts, max_items=4, max_w=1000, font_size=30):
    """★v79-3.1/v79-4：情報バーの設備行を『カテゴリ別優先（セキュリティ→水回り2件→通信→残り）で最大max_items
    →描画幅(max_w)超なら件数を減らす』で組む。★文字サイズは固定（雑誌の質感維持）＝縮小せず件数で収める。"""
    raw = facts.get("equipment")
    text = "／".join(raw) if isinstance(raw, list) else str(raw or "")
    picked = []
    for terms, cap in _PL_EQUIP_CATEGORIES:         # カテゴリ順・各カテゴリは上限まで
        _n = 0
        for term in terms:
            if len(picked) >= max_items:
                break
            if term in text and not any((term in p or p in term) for p in picked):
                picked.append(term)
                _n += 1
            if _n >= cap:
                break
        if len(picked) >= max_items:
            break
    if not picked and isinstance(raw, list):        # カテゴリ外でもリストなら先頭から
        picked = [p for p in raw if p][:max_items]
    if not picked:
        return ""
    import room_tour_video as rtv
    from PIL import ImageDraw, Image
    f = rtv._v79_sans_r(font_size)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    while picked:                                   # 幅フィット：超えたら末尾から1件ずつ減らす（サイズ固定）
        line = "／".join(picked)
        if d.textlength(line, font=f) <= max_w:
            return line
        picked.pop()
    return ""


def _pl_jst_ym():
    """JST の『YYYY年M月』（生成日）。注記の年月フォールバックに使う。"""
    from datetime import datetime, timezone, timedelta
    _j = datetime.now(timezone(timedelta(hours=9)))
    return f"{_j.year}年{_j.month}月"


def _pl_ai_note_line(facts=None):
    """★v79-note：AI生成イメージの注記（表紙・ビート面が共有する唯一の入口）。
    文言は core.ai_note_line＝DATA面の『※家具・小物はAI生成のイメージ』とも同じ定数から作る。
    年月は マイソク記載を優先し、無ければ生成日（core.data_note_date と同じ規則＝3面で年月がずれない）。"""
    return core.ai_note_line(facts or st.session_state.get("pl_facts", {}), _pl_jst_ym())


def _pl_cover_v79_fields(facts, feature_id, layout):
    """★v79-3：build_cover_v79 に渡す fields を facts＋特集から組む1源（auto/手動が共有＝ピクセル同一の担保）。
    ★copy は magtext の物件別3案から人が選んだ hook（読点『、』優先で2行分割・covercopy-v1）。area_lineは駅アクセス由来。
    ★家賃には管理費を必ず併記（rentguard資産）。返り値=build_cover_v79 の kwargs dict。"""
    feat = core.feature_of(feature_id) or {}
    rent = (facts.get("rent", "") or "").strip()
    fee = (facts.get("fee", "") or "").strip()
    _madori_raw = facts.get("madori", "") or ""
    madori = _madori_raw.split("[")[0].strip()
    # ★v79-3.1：間取タイプでない生値（例『10x6』＝居室帖数のraw）は非表示（生値を出さない・整形はv79-5）
    if madori and not re.search(r"[LDKRＬＤＫＲ]|ワンルーム|ルーム", madori):
        madori = ""
    _tag = re.search(r"\[(.+?)\]", _madori_raw)   # madoriの[角部屋]等のタグを情報バーの1節へ
    tag = _tag.group(1).strip() if _tag else ""
    area = (facts.get("area", "") or "").strip()
    # ★v79-4b(バグ②)：どのフィールドに紛れても生寸法『10x6』を情報バーに出さない（『1LDK 10x6』/area/tag経由も塞ぐ）
    madori, area, tag = _strip_raw_dim(madori), _strip_raw_dim(area), _strip_raw_dim(tag)
    equip_line = _pl_cover_equip_line(facts)      # ★主要最大4件＋幅フィット（垂れ流し防止）
    note_line = _pl_ai_note_line(facts)           # ★v79-note：表紙／ビート面／DATA面で同じ文言（1源）
    # 家賃管理費併記（数字形式の生値をそのまま・混入検知は呼出側 warning）
    _rentfee = (f"賃料{rent}" + (f"＋管理費{fee}/月" if fee else "")) if rent else ""
    spec_line = " ｜ ".join(x for x in (f"{madori} {area}".strip(), tag, _rentfee) if x.strip())
    price = ("¥" + rent.replace("円", "").strip()) if rent else ""
    # ★covercopy-v1：hook は物件別の3案から人が選んだもの（_pl_effective_hook が唯一の入口）。
    #   旧実装は feature.cover_hooks[0] 固定＝全物件で同じコピーが焼かれ、かつ📖が表示するhookと食い違っていた。
    hook = _pl_effective_hook(feature_id)
    area_line = _pl_v79_area_line(facts)
    # ★issue-v1：マストヘッド2行目（号数＋エリア）も fields 経由で渡す＝auto/手動が同一文字列（1源2消費の担保を維持）。
    kw = {"price": price, "spec_line": spec_line, "equip_line": equip_line, "note_line": note_line,
          "issue_text": _pl_issue_text(facts)}
    if layout == "price_hero":
        kw.update({"area_line": area_line, "hook": hook,
                   "price_sub": (f"管理費 {fee}/月" if fee else "")})
    else:                                    # copy_hero（標準）：hookを読点で2行分割
        parts = [p for p in re.split(r"(?<=、)", hook) if p.strip()]   # 『、』の後ろで割る（読点は前行に残す）
        copy1 = parts[0] if parts else hook
        copy2 = "".join(parts[1:]) if len(parts) > 1 else ""
        kw.update({"copy1": copy1, "copy2": copy2,
                   "price_sub": (f"管理費 {fee}/月 ／ {area_line}" if fee else area_line)})
    return kw


def _pl_build_cover_v79(src_bytes, facts, feature_id, layout, aspect="9:16"):
    """★v79-3 共有カバービルダー（auto も手動も これ1本）＝1源2消費。build_cover_v79 に一元委譲。"""
    import room_tour_video as rtv
    kw = _pl_cover_v79_fields(facts, feature_id, layout)
    return rtv.build_cover_v79(src_bytes, feature_id=feature_id, layout=layout, aspect=aspect, **kw)


def _pl_cover_layout():
    """★v79-3 表紙レイアウト（新キー pl_cover_layout＝旧 pl_cover_style の options差替え地雷を回避）。
    既定 copy_hero。旧値/未知値は copy_hero へサニタイズ。"""
    v = st.session_state.get("pl_cover_layout")
    return v if v in ("copy_hero", "price_hero") else "copy_hero"


def _pl_auto_cover_bytes(adopted):
    """★v79-3：表紙を既定素材＋facts＋選択特集で自動生成（build_cover_v79・ffmpegのみ課金なし）。
    ★手動『表紙特大』と同一ビルダー・同一fields＝1源2消費（ピクセル同一）。作れなければ None。"""
    import re  # noqa: F401
    _sid = _pl_cover_default_src(adopted)
    _src = next((it for it in adopted if it["id"] == _sid), None)
    if not _src or not _src.get("gen_bytes"):
        return None
    _f = _pl_effective_facts()
    _fid = st.session_state.get("pl_feature", "normal")
    return _pl_build_cover_v79(_src["gen_bytes"], _f, _fid, _pl_cover_layout(), aspect="9:16")


def _pl_v79_focal_probe(rtv):
    """★一時デバッグ（v79-4c あり/なし実測・後で外す=F）: 同一画像で Klingプロンプト
    『あり（新・focal主語指向）』『なし（旧・無目的push-in）』の2本を生成し、focal着地/パララックス/破綻を見比べる。
    ★fal実課金（2本）。生成前後の課金メモ（推定＋時刻）を記録。実残高は fal ダッシュボードで確認。"""
    import os as _os, tempfile as _tf, datetime as _dt   # ★関数スコープでimport（表示部の_os NameError修正）
    with st.expander("🧪 一時デバッグ: Klingプロンプト あり/なし実測（v79-4c・後で外す）", expanded=False):
        _items = [it for it in st.session_state.get("pl_items", []) if it.get("gen_bytes")]
        if not _items:
            st.caption("採用画像がありません（確認ステージで用意）。")
            return
        _opts = {it["id"]: f"{_PL_ROOM_JP.get(it['room'], it['room'])}" for it in _items}
        _sid = st.selectbox("素材（LDK推奨）", list(_opts), format_func=lambda i: _opts[i],
                            key="_v79_probe_src")
        _it = next((it for it in _items if it["id"] == _sid), None)
        if not _it:
            return
        _rt = _pl_video_room_type(_it["room"])
        _m = core.room_facts_map(_it["room"])
        _yes = rtv.build_kling_prompt(_rt, _m.get("focal"), _m.get("motion", "normal"))  # あり（新）
        _no = rtv.ROOM_PROMPTS.get(_rt, rtv.ROOM_PROMPTS["generic"])                     # なし（旧・無目的）
        # ★表示は st.code（key無し・毎rerun再描画）＝素材を変えると表示も更新（text_areaの固定key stale地雷を回避）。
        st.markdown(f"**あり（新・focal主語指向）** ｜ 素材={_PL_ROOM_JP.get(_it['room'], _it['room'])}"
                    f"（video_type={_rt} / focal={_m.get('focal')} / motion={_m.get('motion')}）")
        st.code(_yes, language=None)
        st.markdown("**なし（旧・無目的push-in）**")
        st.code(_no, language=None)
        _unit = 0.35   # kling2.6_pro 5s の実績単価/本
        # ★安定パス（素材ごと・毎回同じ）＝再run/クラッシュ後に既存mp4を再利用＝二重課金回避。
        _dir = _os.path.join(_tf.gettempdir(), "v79probe")
        _os.makedirs(_dir, exist_ok=True)
        _p_yes = _os.path.join(_dir, f"focal_yes_{_sid}.mp4")
        _p_no = _os.path.join(_dir, f"focal_no_{_sid}.mp4")
        _have = _os.path.exists(_p_yes) and _os.path.exists(_p_no)
        st.caption(f"想定fal課金：未生成分のみ × ${_unit:.2f}（5秒）。"
                   + ("★この素材は生成済み＝再課金なしで表示。" if _have
                      else f"★2本で最大 ${_unit * 2:.2f}。生成前に fal 残高を控えてください。"))
        _req_yes = _os.path.join(_dir, f"req_{_sid}_yes.json")   # ★request_id＋status_url/response_urlをJSON保存
        _req_no = _os.path.join(_dir, f"req_{_sid}_no.json")
        # ★手動 request_id 再fetch（Rebootで req file が消えた課金済みクリップの回収用・fal Usage/前回表示から貼る）。
        #   保存URLが無い→root形式でpoll（fal_poll_clipのフォールバック）。
        _mrid = st.text_input("（任意）課金済み request_id を貼って『あり』側を再fetch（追加課金なし・root形式）",
                              key="_v79_manual_rid", placeholder="fal の request_id")
        if st.button("🧪 あり/なし 生成（submit/poll・URL保存＝再fetchで二重課金回避）", key="_v79_probe_gen",
                     disabled=not get_secret("FAL_KEY", "")):
            import time as _time, json as _json
            _os.environ["FAL_KEY"] = get_secret("FAL_KEY", _os.environ.get("FAL_KEY", ""))
            _rec = {"started": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).strftime("%H:%M:%S"),
                    "unit_usd": _unit, "room": _it["room"], "src_id": _sid, "billed_clips": 0, "req": {}}

            def _one(_prompt, _out, _req, _manual=""):
                """1本: mp4あれば再利用／保存JSON(request_id+URL)あれば保存URLで再fetch／手入力idはroot形式で再fetch／
                無ければsubmit(課金)→JSON保存→保存URLでpoll。★pollは推測でURLを組まず fal返却URLを使う。"""
                if _os.path.exists(_out):
                    return
                _rid, _su, _ru = "", None, None
                if (_manual or "").strip():
                    _rid = _manual.strip()                     # 手入力＝request_idのみ→root形式フォールバック
                elif _os.path.exists(_req):
                    _d = _json.load(open(_req)); _rid = _d.get("request_id", ""); _su = _d.get("status_url"); _ru = _d.get("response_url")
                if not _rid:
                    _sub = rtv.fal_submit_clip(_it["gen_bytes"], _prompt, 5, "kling2.6_pro", rtv._V79_NEGATIVE)
                    _rid, _su, _ru = _sub["request_id"], _sub["status_url"], _sub["response_url"]
                    _json.dump(_sub, open(_req, "w"))          # ★submit直後にrequest_id＋URLを永続化（死んでも回収可）
                    _rec["billed_clips"] += 1
                _rec["req"][_os.path.basename(_out)] = _rid    # 表示（Reboot後の手動再fetch用に控える）
                _w = 0
                while _w < 720:
                    if rtv.fal_poll_clip("kling2.6_pro", _rid, _out, _su, _ru) == "done":
                        return
                    _time.sleep(6); _w += 6
                raise TimeoutError(f"生成タイムアウト（request_id={_rid}・次回このrequest_idで再fetch可）")
            try:
                with st.spinner("生成中…（fal queue submit/poll・request_id保存＝クラッシュしても再fetchで拾える）"):
                    _one(_yes, _p_yes, _req_yes, _mrid)        # 手入力ridは『あり』側に使う
                    _one(_no, _p_no, _req_no)
                _rec["ended"] = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).strftime("%H:%M:%S")
                _rec["est_usd"] = round(_unit * _rec["billed_clips"], 2)
                st.session_state["_v79_probe_rec"] = _rec
            except Exception as e:  # noqa: BLE001  ★鍵は含めない（型/HTTPステータス/URL＝fal_poll_clipが付与・デバッグ用）
                _sc = getattr(getattr(e, "response", None), "status_code", None)
                _detail = f"{type(e).__name__}" + (f" / HTTP {_sc}" if _sc else "")
                st.error(f"生成に失敗/中断：{_detail}\n{str(e)[:400]}\n"
                         f"request_id控え：{_rec.get('req')}\n"
                         "★request_id保存済＝もう一度押すと再fetchで拾い再課金しません。鍵はこの表示に含めていません。")
        if _os.path.exists(_p_yes) and _os.path.exists(_p_no):
            _rec = st.session_state.get("_v79_probe_rec")
            if _rec:
                st.success(f"fal課金メモ：{_rec}（billed_clips=今回実課金した本数・0なら再利用のみ）")
            oc1, oc2 = st.columns(2)
            oc1.caption("あり（新・focal）"); oc1.video(_p_yes)
            oc2.caption("なし（旧・無目的）"); oc2.video(_p_no)
            with open(_p_yes, "rb") as _f:
                oc1.download_button("⬇️ あり.mp4", _f.read(), file_name=f"v79-4c_focal_yes_{_sid}.mp4",
                                    mime="video/mp4", key="_v79_dl_yes")
            with open(_p_no, "rb") as _f:
                oc2.download_button("⬇️ なし.mp4", _f.read(), file_name=f"v79-4c_focal_no_{_sid}.mp4",
                                    mime="video/mp4", key="_v79_dl_no")


def _pl_v78_timestamp_probe(rtv):
    """★一時デバッグ（v78字幕同期の疎通・後で外す）: ElevenLabs with-timestamps の生JSONを画面に出す。
    本番の鍵/ボイス(HIRO)/合成設定を使用。谷合さんは Reboot→ボタン1回→スクショ で完結。
    確認: 文字単位か／要素数==入力長か／句読点・数字の扱い／『。』1つのms（=字数不足 vs 句読点ポーズ の答え）。"""
    with st.expander("🧪 一時デバッグ: ElevenLabs with-timestamps 疎通（v78字幕同期・後で外す）"):
        _txt = "終電まで、あと30分。その話は、まだしない。"
        st.caption("本番の鍵/ボイス(HIRO)/合成設定で1回叩き、生JSONを表示。鍵は画面にもログにも出しません。"
                   "『返るか』でなく『日本語字幕に使えるか（文字単位・要素数一致・句読点/数字）』を実レスポンスで確定。")
        st.code(_txt, language=None)
        if st.button("🧪 疎通を1回叩く（with-timestamps）", key="_v78_probe_btn"):
            try:
                st.session_state["_v78_probe"] = rtv.tts_timestamps_probe(_txt)
                st.session_state["_v78_probe_err"] = ""
            except Exception as e:  # noqa: BLE001  ★キーを含みうる詳細は型のみ
                st.session_state["_v78_probe"] = None
                st.session_state["_v78_probe_err"] = f"{type(e).__name__}: {str(e)[:120]}"
        if st.session_state.get("_v78_probe_err"):
            st.error("疎通失敗: " + st.session_state["_v78_probe_err"])
        _pr = st.session_state.get("_v78_probe")
        if _pr:
            _al = _pr.get("alignment") or _pr.get("normalized_alignment") or {}
            _c = _al.get("characters") or []
            _s = _al.get("character_start_times_seconds") or []
            _e = _al.get("character_end_times_seconds") or []
            st.markdown("**自動チェック（生JSONから計算・下に生JSON全文）**")
            _chk = {
                "粒度": "文字単位(charactersあり)" if _c else "characters無し=単語単位/非対応の疑い",
                "要素数": len(_c), "入力文字列長": len(_txt), "要素数==入力長": len(_c) == len(_txt),
                "『、』を含む": "、" in _c, "『。』を含む": "。" in _c,
                "数字3の要素数": _c.count("3"), "数字0の要素数": _c.count("0"),
            }
            if _c and _e and _s and len(_c) == len(_e) == len(_s):
                _chk["『。』1つのms"] = [round((_e[i] - _s[i]) * 1000)
                                        for i, ch in enumerate(_c) if ch == "。"]
            st.write(_chk)
            st.markdown("**生JSON（audio_base64は長さ表記済み・要約なし）**")
            st.json(_pr)


# ④の設定は往復(③↔④)で消える：Streamlitは『描画されなかったwidgetのstate』を破棄する。
# ③滞在中に④のトグルwidgetが生成されない→keyが消える→④再訪でvalue=既定に戻る（無言・毎回踏む）。
# ★影キー(_keep_*・非widget=往復で消えない)に人の選択を保存し、cleanupで消えたkeyだけ復元。
# ★sticky(pl_style_auto)と同じ「widget生成前に代入」規律。pl_styleが効いたのは②で毎回描画されるから。
_PL_V_KEEP_KEYS = ("pl_v_model", "pl_v_dur", "pl_v_bgm", "pl_v_aspect", "pl_v_caps",
                   "pl_v_tag", "pl_v_note", "pl_v_flashcut", "pl_v_cover_on",
                   "pl_v_cover_sec", "pl_v_narr_on", "pl_v_story", "pl_v_data",
                   "pl_issue_no", "pl_issue_area",   # ★issue-v1：往復で号数/エリアが消えないように影キー保存
                   "pl_cover_hook_pick")             # ★covercopy-v1：選んだ表紙コピーを往復で保持


def _pl_v_keep(key, default):
    """④設定の既定値を『影キー(_keep_*・往復で消えない) → 無ければdefault』で返す。
    ★widgetの value=/index= にこれを渡す＝session_stateを先に代入しない＝『default値＋SessionState』警告を出さない。
    往復でwidget stateがcleanupされても value=影キー で人の選択が復元される。"""
    return st.session_state.get("_keep_" + key, default)


def _pl_v_keep_idx(options, key, default):
    """selectbox/radio 用：影キーの値の index を返す（無効値は0）。"""
    v = _pl_v_keep(key, default)
    return options.index(v) if v in options else 0


def _pl_v_save_settings():
    """④の設定widget生成後、現在値を影キーへ保存（往復で消えても value=影キー で復元できるように）。"""
    for _k in _PL_V_KEEP_KEYS:
        if _k in st.session_state:
            st.session_state["_keep_" + _k] = st.session_state[_k]


def _pl_assign_story_beats(scenes, v_dur):
    """★story-v78 A0 part2：連続する同 room を1ビートにまとめ、各scene に beat_id/gen_dur/trim を注入。
    ナレ有ビート → core.allocate_beat_cuts(chars, stock)（描画尺==ナレ秒・パディングでビート内xfade相殺）。
    ナレ無/still ビート → 固定 v_dur（描画尺=v_dur×stock−0.6×(stock−1)＝_assemble_beats実描画と一致）。
    ビート先頭sceneに beat_narration/beat_narr_sec を載せる（run_tour_jobの単純化ナレ配置が読む）。
    返り値: 予定総尺（Σ 各ビート描画尺）。★実尺==この値 が受入。scenes を in-place 変更。
    ★normalは呼ばれない（story OFF）＝完全回帰。"""
    if not scenes:
        return 0.0
    # 連続する同 room を1ビートに（room 未設定は個別ビート＝間取り図等の still を独立させる）
    groups, cur, cur_key = [], [], object()
    for sc in scenes:
        k = sc.get("room") if sc.get("room") else id(sc)   # room無し=単独ビート
        if k != cur_key and cur:
            groups.append(cur); cur = []
        cur.append(sc); cur_key = k
    if cur:
        groups.append(cur)
    total = 0.0
    for bid, grp in enumerate(groups):
        stock = len(grp)
        narr = "\n".join(s.get("narration", "").strip() for s in grp
                         if (s.get("narration") or "").strip())
        chars = len(re.sub(r"\s+", "", narr))
        if chars > 0:
            a = core.allocate_beat_cuts(chars, stock)
            # ★rendered_sec（実描画尺）を使う＝非overflow時は narr_sec と一致。overflow（在庫に対しナレ過長で
            #   物理的に描画尺<ナレ秒）でも 📐予定==実尺 とナレ配置の累積が実タイムラインと一致（ドリフトしない）。
            #   ナレ音声が描画尺を超える分は run_tour_job の over>0.3 警告で原稿短縮を促す。
            cuts, trims, nsec = a["cuts"], a["trims"], a["rendered_sec"]
            # ★v79-5b：allocateは短ナレ×多枚で len(cuts)<stock を返す（余剰画像はカット不要）。
            #   だが全画像は背景B-rollとして活かす＝nsec内に均等配置し直す（描画尺==nsec維持・画像を捨てない・crash防止）。
            #   v79のbig_textは簡潔なので同室2枚以上で頻発。v78は長ナレで len(cuts)==stock ＝このガードは不発（不変）。
            if len(cuts) < stock:
                _padded = nsec + 0.6 * max(0, stock - 1)
                _per = _padded / stock
                cuts = [10 if _per > 5 else 5] * stock
                trims = [round(_per, 2)] * stock
        else:                                    # ナレ無/still：固定 v_dur・実 _assemble_beats 描画尺に一致
            cuts = [int(v_dur)] * stock
            trims = [float(v_dur)] * stock
            nsec = round(v_dur * stock - 0.6 * max(0, stock - 1), 2)
        for j, sc in enumerate(grp):
            sc["beat_id"] = bid
            sc["gen_dur"] = int(cuts[j])
            sc["trim"] = float(trims[j])
        grp[0]["beat_narration"] = narr          # 先頭のみ（空でも run 側は尺だけ加算）
        grp[0]["beat_narr_sec"] = float(nsec)
        grp[0]["beat_narration_kana"] = grp[0].get("narration_kana", "")   # ★narr-fix-d：TTS読み仮名（先頭sceneのmagtext由来）
        total += nsec
    return round(total, 2)


def _pl_stage_video():
    import os as _os
    import room_tour_video as rtv
    _os.environ["FAL_KEY"] = get_secret("FAL_KEY", _os.environ.get("FAL_KEY", ""))
    # ナレーション用（★キー値はここで env に載せるのみ・UI/ログには一切出さない）
    _os.environ["ELEVENLABS_API_KEY"] = get_secret("ELEVENLABS_API_KEY",
                                                   _os.environ.get("ELEVENLABS_API_KEY", ""))
    # 既定ボイス（Secrets）→ 特集が voice_id を持てばそれを env に載せ替え（データ駆動）。
    # ★現状は全特集 voice_id=None → 既定 ELEVENLABS_VOICE_ID(=HIRO) にフォールバック＝no-op。
    #   ②自分を整える部屋の女性ボイスIDが決まったら FEATURES["totonoeru"]["narration"]["voice_id"] に
    #   直書きするだけで効く（鍵はSecrets・設定は表）。
    _default_voice = get_secret("ELEVENLABS_VOICE_ID", _os.environ.get("ELEVENLABS_VOICE_ID", ""))
    _os.environ["ELEVENLABS_VOICE_ID"] = core.feature_voice_id(
        st.session_state.get("pl_feature", "normal"), default_voice=_default_voice) or ""
    _pl_v78_timestamp_probe(rtv)   # ★一時デバッグ: with-timestamps 疎通（v78字幕同期・後で外す）
    _pl_v79_focal_probe(rtv)       # ★一時デバッグ: Klingプロンプト あり/なし実測（v79-4c・後で外す）
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
    # ★tabkeep-v78：value=/index= を影キーから読む＝③↔④往復でstateがcleanupされても人の選択を復元
    o1, o2, o3 = st.columns(3)
    _fal_models = list(rtv.FAL_MODELS)
    v_model = o1.selectbox("モデル", _fal_models, key="pl_v_model",
                           index=_pl_v_keep_idx(_fal_models, "pl_v_model", _fal_models[0]))
    v_dur = o2.selectbox("1本の長さ(秒)", [5, 10], key="pl_v_dur",
                         index=_pl_v_keep_idx([5, 10], "pl_v_dur", 5))
    v_bgm = o3.checkbox("BGMを付ける", value=_pl_v_keep("pl_v_bgm", True), key="pl_v_bgm")
    # ★v79-6：DATA面（動く雑誌の最終ページ・物件スペック一覧）。既定ON＝雑誌の締めとして常に付ける・必要なら外せる。
    v_data = st.checkbox("📄 DATA面（最終ページ・物件スペック一覧）を末尾に付ける",
                         value=_pl_v_keep("pl_v_data", True), key="pl_v_data")
    v_aspect = st.selectbox("動画の向き", ["9:16", "1:1", "16:9"], key="pl_v_aspect",
                            index=_pl_v_keep_idx(["9:16", "1:1", "16:9"], "pl_v_aspect", "9:16"),
                            format_func=lambda a: _VID_ASPECT_LABEL.get(a, a))
    _FIT_LABEL = {"fill": "埋める（余白なし・端が少し切れる）",
                  "contain": "全体を見せる（上下に余白）"}
    v_fit = st.radio("余白の扱い", ["fill", "contain"], index=0, horizontal=True,
                     key="pl_v_fit", format_func=lambda m: _FIT_LABEL.get(m, m))
    st.caption("埋める＝余白ゼロですが、写真の端が少し切れます"
               "（正方形素材を9:16にすると左右が大きめに切れます）。")
    st.caption("正方形素材は 1:1 動画が最も無駄なし。横長できれいに見せたい場合は、"
               "元写真（横長の撮影原本）を『手持ち写真』の入口で取り込むと余白・トリミングが減ります。")
    v_caps = st.checkbox("シーンテロップ（部屋名＋情感2行）を焼く",
                         value=_pl_v_keep("pl_v_caps", True), key="pl_v_caps")
    # ★feat-merge-1.5：v79「動く雑誌」（📖文字面あり）ではこのテロップ層を焼かない＝ONでも出ない。
    #   黙って無効化するとチェックが嘘になるので明示する（撤去するか内部フラグ化するかは feat-dead-1 で判断）。
    st.caption("※📖動く雑誌の文字面を生成している回は、この層は焼かれません"
               "（big_text・コメント・部屋タグと役割が重なり、画面で二重になるため）。"
               "※画像はイメージです の注記は、このチェックに関係なく常に焼かれます。")
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
        # 生成前seed。未設定 or 空（facts未ロード時に空でstickyになる導線）のとき既定を再投入。
        #   ※widget生成前なので session_state 代入は安全。ユーザが別文言を入れれば非空で上書きされない。
        if _fdef and not str(st.session_state.get("pl_flash_text", "")).strip():
            st.session_state["pl_flash_text"] = _fdef
        v_flash = st.text_input("フラッシュ文言（先頭に0.5秒だけ重畳・短く）",
                                key="pl_flash_text", placeholder="例: ニューモート204 ｜ 2LDK")
    v_tag = st.text_input("上部タグ（物件名・間取り等／空欄で非表示）", key="pl_v_tag",
                          value=_pl_v_keep("pl_v_tag", ""),
                          placeholder="例: ニューモート204 ｜ 2LDK 57.07㎡")
    v_note = st.text_input("画面注記（右下・景表法配慮／空欄で非表示）", key="pl_v_note",
                           value=_pl_v_keep("pl_v_note", ""),
                           placeholder="例: ※画像はイメージです")
    v_flashcut = st.checkbox("カット境界に白フラッシュ（極短・0.2秒）", key="pl_v_flashcut",
                             value=_pl_v_keep("pl_v_flashcut", False),
                             help="OFF＝従来のクロスフェード0.6秒。ON＝各カットの境界を0.2秒の"
                                  "白フラッシュに（メモリ特性は従来と同一・全結合には戻しません）。")

    # ── 冒頭に表紙を挿入（narration-v68 / v70b 4-1：毎回自動生成＋既定ON）──
    # 谷合さんが「表紙が追加されない」を繰返し踏んだため、表紙を自動生成しトグル既定ONに。
    # 表紙生成は ffmpeg のみ・課金なし＝毎回作ってもコスト影響なし。失敗しても止めない（付加価値）。
    # ★v79-3：特集/レイアウトが変わったら auto カバーを再生成（accent/ラベル追従）。手動生成後は追従停止（sig=None）。
    # ★covercopy-v1：署名に『焼かれる文字列』（表紙コピー＋マストヘッド表記）も含める＝📖再生成やコピー選択の変更で
    #   autoカバーが自動的に作り直される（ffmpegのみ＝無課金なので毎回作ってよい）。stale PNG対策の主経路。
    _feat_sig = (st.session_state.get("pl_feature", "normal"), _pl_cover_layout(),
                 _pl_effective_hook(), _pl_issue_text())
    _need_auto = ("pl_cover_png" not in st.session_state) or (
        st.session_state.get("_pl_cover_auto_sig") is not None
        and st.session_state.get("_pl_cover_auto_sig") != _feat_sig)
    if _need_auto and adopted:
        try:
            _acov = _pl_auto_cover_bytes(adopted)
            if _acov:
                # ★焼いたコピーをPNGと一緒に記録＝あとから「この画は古い」を機械判定できる（_pl_cover_stale）
                st.session_state["pl_cover_png"] = {"aspect": "9:16", "bytes": _acov,
                                                    "hook": _pl_effective_hook()}
                st.session_state["_pl_cover_auto_sig"] = _feat_sig   # ★autoの追従判定用（手動でNoneにする）
        except Exception as e:  # noqa: BLE001  自動生成失敗は止めない（手動ボタンで作れる）
            st.session_state["_pl_cover_auto_err"] = f"{type(e).__name__}"
    _cov_ready = bool(st.session_state.get("pl_cover_png"))
    v_cover_on = st.checkbox("表紙を冒頭に挿入", value=_pl_v_keep("pl_v_cover_on", True),
                             key="pl_v_cover_on", disabled=not _cov_ready,
                             help="表紙は自動生成され既定ONです。素材/文言を変えたいときは"
                                  "下の「🖼️ 表紙特大」で作り直せます。")
    if not _cov_ready:
        st.caption("↑ 表紙を自動生成できませんでした。下の「🖼️ 表紙特大」で作成すると有効になります"
                   "（表紙なしでも動画は生成できます）。")
    v_cover_sec = 1.5
    if v_cover_on and _cov_ready:
        v_cover_sec = st.slider("表紙の表示秒数", 1.0, 3.0, _pl_v_keep("pl_v_cover_sec", 1.5),
                                0.5, key="pl_v_cover_sec")
        if st.session_state.get("pl_open_title") == "flash":
            st.caption("※ 表紙挿入中は冒頭の極短フラッシュ（タイトル文字）を自動でOFFにします（冒頭の重複回避）。")

    # ── 🎙️ AIナレーション（narsync-v70a：シーン単位・欄は下のテロップ編集UIに並べる）──
    _narr_env = rtv.narration_env_ready()
    _narr_ok = _narr_env["key"] and _narr_env["voice"]
    v_narr_on = False
    with st.expander("🎙️ AIナレーション（男性ナレーター・シーン同期）", expanded=False):
        _nlimit = core.narration_char_limit(v_dur)
        st.caption(f"各シーン1文・**{_nlimit}字以内（読み正規化後）**（{v_dur}秒尺に収める・速度変更なし）。"
                   "ナレ欄は下の『各シーンのテロップを編集』にテロップと並べて表示します。"
                   "未編集ならテロップに追従／一度直したら追従しません。空欄のシーンは無音。"
                   "英字・㎡・金額は日本語読みへ自動正規化。コスト目安：約60〜80字/本。")
        if not _narr_ok:
            st.info("ElevenLabs の APIキー／ボイスIDが未設定です。Secrets に "
                    "ELEVENLABS_API_KEY と ELEVENLABS_VOICE_ID を追加すると有効化されます。", icon="🔒")
        v_narr_on = st.checkbox("ナレーションを付ける", value=_pl_v_keep("pl_v_narr_on", True),
                                key="pl_v_narr_on", disabled=not _narr_ok)   # ★既定ON（v78前提2）
        if v_narr_on:
            # ★story-v78 A-3：シーン独立生成を廃止し、全ビートを1コールで物語生成。検出部屋でシチュを絞る（§4）。
            _arooms = [it.get("room") for it in adopted]
            # ★v79-feature-reach(b)：特集でも絞る（②③でモテ部屋の世界観が出続けるのを止める）。
            _sits = core.story_situations_for(
                _arooms, st.session_state.get("pl_feature", "normal"))
            _opts = [s["id"] for s in _sits] + ["free"]
            _labels = {s["id"]: s["label"] for s in _sits}
            _labels["free"] = "自由入力（下の欄に書く）"
            _sid = st.radio("シチュエーション（物語の設定・検出部屋で絞り込み）", _opts,
                            key="pl_story_sit", format_func=lambda x: _labels.get(x, x))
            if _sid == "free":
                _sfree = st.text_input(
                    "自由入力のシチュエーション", key="pl_story_sit_free",
                    placeholder="例：週末、友達を呼んでたこ焼きパーティー")
                _sstyle = st.radio("語りの立ち位置", ["独白", "語りかけ"], key="pl_story_style",
                                   horizontal=True)
                _situation, _style = _sfree.strip(), _sstyle
            else:
                _s = next((s for s in _sits if s["id"] == _sid), None)
                _situation, _style = (_s["text"], _s["style"]) if _s else ("", "独白")
                if _s:
                    st.caption(f"「{_situation}」／{_style}")
            st.caption("★『🎬 ストーリー割り当て』をONにすると、この物語が部屋=ビートで正しく配置されます。")
            st.button("🎬 物語を生成（1コール・全ビートを1つの物語で）", key="pl_story_gen",
                      on_click=_pl_story_generate_cb, args=(_situation, _style, v_dur),
                      disabled=not _narr_ok, use_container_width=True)
            _smsg = st.session_state.get("_pl_story_msg")
            if _smsg:
                st.warning(_smsg)
            if st.session_state.get("_pl_story_raw"):   # 実機検証：プロンプト＋生レスポンス提出（Fで撤去）
                with st.expander("🧪 生成プロンプト＋生レスポンス（実機検証用・後で外す）", expanded=False):
                    st.text_area("プロンプト", st.session_state.get("_pl_story_prompt", ""),
                                 height=200, key="_pl_story_prompt_view")
                    st.text_area("生レスポンス", st.session_state.get("_pl_story_raw", ""),
                                 height=150, key="_pl_story_raw_view")

    # ── 📖 動く雑誌の文字面（v79-5b・特集ベース・★ナレ非依存）────────────────────
    #   big_text/comment/タグを1コール生成（Gemini）。『🎬 ストーリー割り当て』ONで映像にoverlay合成される。
    #   ★ナレOFFでも生成・表示できる（文字面が主役・ナレは画面の文字を読むだけ）＝ElevenLabs鍵は不要。
    #   ★以前は if v_narr_on 内＋ElevenLabsで無効化していた＝ナレOFF回で文字面が作れない配線ミスを修正。
    # ★誤課金防止：文字面が動画化の前提。未生成なら expander を自動展開＋タイトルに【必須】、生成済なら✅。
    _mag_done = bool(_pl_mag_beats(adopted))
    _mag_title = ("📖 動く雑誌の文字面　✅ 生成済" if _mag_done
                  else "📖 動く雑誌の文字面　🔴【必須】未生成（先にこれを実行）")
    with st.expander(_mag_title, expanded=not _mag_done):
        _mag_fid = st.session_state.get("pl_feature", "normal")
        st.caption(f"特集『{core.feature_display_name(_mag_fid)}』の文字面を生成"
                   "（big_text＝映るカットの数字/角部屋・comment・タグ最大3）。"
                   "★これを実行しないと masthead・部屋タグ・金色見出しが一切付きません（④動画化の必須前提）。"
                   "表紙hookは特集の定型から選ばれ、独自案は⚠️要確認（型承認・人力採用）。")
        # ── ★issue-v1：ISSUE番号／エリア表記（表紙・全ビート・DATA面のマストヘッドに焼かれる）──
        #   既定値は _pl_v_keep（影キー）で与える＝session_stateへ先に代入しない（『default値＋SessionState』警告と
        #   body-flow代入の StreamlitAPIException を回避・v78前提1の地雷①）。
        _ic1, _ic2 = st.columns([1, 2])
        with _ic1:
            st.number_input("ISSUE番号", min_value=1, max_value=99, step=1,
                            value=int(_pl_v_keep("pl_issue_no", 1)), key="pl_issue_no")
        with _ic2:
            st.text_input("エリア表記（空欄＝マイソクの駅名から自動）",
                          value=_pl_v_keep("pl_issue_area", ""), key="pl_issue_area",
                          placeholder="例: NISHIKUJO")
        # ★$3.15を払う前に『実際に焼かれる文字列』を目で確定できる（pregen-guardと同じ思想）。
        st.caption(f"マストヘッド表記：**{_pl_issue_text()}**"
                   "（表紙・全ビート・DATA面の3面に同じ文字列が入ります）")
        st.button(("📖 動く雑誌の文字を生成（1コール・特集ベース）" if not _mag_done
                   else "📖 文字面を再生成（特集変更時など）"), key="pl_mag_gen",
                  type=("secondary" if _mag_done else "primary"),
                  on_click=_pl_mag_generate_cb, args=(_mag_fid,),
                  use_container_width=True)   # ★Gemini生成＝ElevenLabsで無効化しない
        _mmsg = st.session_state.get("_pl_mag_msg")
        if _mmsg:
            st.warning(_mmsg)
        _mcov = st.session_state.get("pl_mag_cover")
        if _mcov:
            st.caption(f"表紙：{_mcov.get('area_line', '')}／{_mcov.get('price', '')}"
                       f"（{_mcov.get('price_sub', '')}）")
            # ── ★covercopy-v1：表紙コピーを物件別3案から人が選ぶ（自動採用しない＝型承認ゲートは維持）──
            _cands = [c for c in (_mcov.get("hook_candidates") or []) if str(c).strip()]
            if _mcov.get("hook_source") == "feature_fallback":
                st.warning("⚠️ 表紙コピーをAIで生成できませんでした（または全案がガードで除去）。"
                           "特集の既定コピーを使用しています＝物件別になっていません。📖再生成で作り直せます。")
            # ★どの案が引っかかったのかを『選ぶ前に』見せる。全案ぶんを1行にまとめると、選ぼうとしている案が
            #   安全かを人が判断できず「数値主張は人が弾く」という設計の前提が崩れる（ゲートが機能する条件）。
            _nrmap = _mcov.get("needs_review_by_hook") or {}
            if len(_cands) > 1:
                # ★options差替え地雷：候補が入れ替わると旧選択がoptions外になり radio が落ちる。
                #   widget生成『前』に session_state を正規化する（pl_cover_src と同じ既存パターン）。
                #   value=/index= は渡さない（session_state駆動に一本化＝『default値＋SessionState』警告を出さない）。
                _prev = _pl_v_keep("pl_cover_hook_pick", "")
                if st.session_state.get("pl_cover_hook_pick") not in _cands:
                    st.session_state["pl_cover_hook_pick"] = _prev if _prev in _cands else _cands[0]
                st.radio(f"表紙コピー（この物件のための{len(_cands)}案から選ぶ）", _cands,
                         key="pl_cover_hook_pick",
                         format_func=lambda c: c + ("　⚠️要確認" if _nrmap.get(c) else ""))
            elif _cands:
                st.caption(f"表紙コピー：**{_cands[0]}**"
                           + ("　⚠️要確認" if _nrmap.get(_cands[0]) else "") + "（候補1案のみ）")
            for _hc in _cands:                       # 案ごとの理由（⚠️が付いた案だけ・本文つきで並べる）
                if _nrmap.get(_hc):
                    st.caption(f"　⚠️ 『{_hc}』：" + " / ".join(_nrmap[_hc]))
            _sel_nr = _nrmap.get(_pl_effective_hook())
            if _sel_nr:                              # 選択中の案が引っかかっている＝いま焼かれる文言の警告
                st.warning(f"⚠️ 選択中の『{_pl_effective_hook()}』に人力確認が要る表現："
                           + " / ".join(_sel_nr) + "（別案に変えるか、このまま使うかを判断してください）")
            _cstale, _cbaked, _ccur = _pl_cover_stale()
            if _cstale:
                st.warning(f"⚠️ 生成済みの表紙PNGは古いコピー『{_cbaked}』のままです"
                           f"（現在の選択は『{_ccur}』）。🖼️ 表紙特大 で作り直してください。")
        # ★v79-6：DATA面（最終ページ）の状態を📖直後にも表示（生成前サマリーと同一情報源＝build_data_rows）。
        st.caption("📄 " + _pl_data_summary())
        if st.session_state.get("_pl_mag_raw"):   # 実機検証：st.code（key無し＝stale回避）
            with st.expander("🧪 magtextプロンプト＋生レスポンス（実機検証用・後で外す）", expanded=False):
                st.code(st.session_state.get("_pl_mag_prompt", ""))
                st.code(st.session_state.get("_pl_mag_raw", ""))

    # ── 🎬 ストーリー割り当て（story-v78 A0・検証中）──────────────────────────
    #   ONで「部屋=ビート／画像=カット」割り当て：連続する同室を1ビートにまとめ、ナレ字数から尺を配分。
    #   ビート境界=ハードカット・ビート内=0.6xfade（パディングで相殺）→ 実尺==Σ(予定ビート尺)。
    #   ★既定OFF＝現行の一律尺＋全境界xfade（完全回帰）。谷合さんの実尺検証用ゲート。
    v_story = st.checkbox(
        "🎬 ストーリー割り当て（部屋=ビート／画像=カット・検証中）",
        value=_pl_v_keep("pl_v_story", False), key="pl_v_story")
    if v_story:
        st.caption("実尺が『予定Σビート尺』に一致するかを、完成後の 📏／📐 で確認できます（A0検証）。"
                   "OFFにすると現行どおり（完全回帰）。")

    _pl_v_save_settings()   # ★④設定widget生成後：現在の人の選択を影キーへ保存（tabkeep-v78）

    # ── PRコピーをAIで下書き（Gemini 1回・押下時のみ）────────────────────────
    with st.expander("✍️ PRコピーをAIで下書き（タイトル3案・情感2行）", expanded=False):
        st.caption("マイソクの事実だけを根拠に下書きします。誇大語・事実外の数値・"
                   "事実外の属性（眺望/方角/日当たり/静けさ/周辺）は自動除去。"
                   "Gemini未設定/失敗でも簡易テンプレで続行します。")
        if st.button("PRコピーを下書き（AI・1回）", key="pl_prcopy_btn",
                     on_click=_pl_reset_title_choice):
            _facts = _pl_effective_facts()   # 売買未確認は設備を空に倒す（フェイルセーフ）
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
                        {k: v for k, v in _facts.items() if k != "full_text"}, _rooms,
                        concept=st.session_state.get("pl_concept", "normal"))
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
            for _kw in _draft.get("key_warnings", []):   # ★キー不一致＝バグ（赤・要確認・黙って捨てない）
                st.error("⚠️ " + _kw + "（部屋の対応が取れませんでした＝要確認）")
            for _fw in _draft.get("warnings", []):       # 事実外の属性を除去＝正常動作（黄・人に返す）
                st.warning("🛡️ " + _fw)
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

    # ── SNS投稿文（IG/TikTok）を生成（Gemini flash 1回・押下時のみ）─────────────
    with st.expander("📝 投稿文（Instagram / TikTok）を生成", expanded=False):
        st.caption("マイソクの事実から、そのまま貼れる投稿文を生成します。数値（家賃・管理費・㎡・徒歩分）と"
                   "設備は事実そのまま・固定フッター（AI生成イメージ／取引態様）はテンプレ。誇大・ban語は自動除去。"
                   "⚠️ 投稿前に宅建・広告の型承認（専門家確認）を通してください。")
        if st.button("📝 投稿文を生成（AI・1回）", key="pl_sns_btn"):
            _sfacts = _pl_effective_facts()
            try:
                _sclient = make_client()
            except RuntimeError:
                _sclient = None
            if _sclient is None:
                st.warning("Gemini APIキーが未設定です（設定ページで確認）。")
            else:
                with st.spinner("投稿文を生成中…"):
                    try:
                        _sns = core.draft_sns_captions(_sclient, _sfacts,
                                                       templates=_pl_effective_templates(),
                                                       feature=st.session_state.get("pl_feature", "normal"))
                    except Exception as e:  # noqa: BLE001
                        _sns = None
                        st.error(f"投稿文の生成に失敗しました: {type(e).__name__}: {str(e)[:120]}")
                if _sns is None:
                    st.warning("投稿文を作れませんでした（マイソクの事実が不足している可能性）。")
                else:
                    st.session_state["pl_sns"] = _sns
                    st.rerun()
        _sns = st.session_state.get("pl_sns")
        if _sns:
            if _sns.get("warnings"):
                st.warning("⚠️ 誇大・ban語を自動除去しました（要確認）：" + "、".join(_sns["warnings"]))
            st.markdown("**Instagram — フックA（数字/コスパ訴求）**")
            st.code(_sns["ig_a"], language=None)
            st.markdown("**Instagram — フックB（特徴/内装訴求）**")
            st.code(_sns["ig_b"], language=None)
            st.markdown("**TikTok — フックA**")
            st.code(_sns["tt_a"], language=None)
            st.markdown("**TikTok — フックB**")
            st.code(_sns["tt_b"], language=None)
            st.markdown("**コメント返信 ＋ DM本文テンプレ**（`{LINE_URL}` を差し替え）")
            st.code(_sns["reply"] + "\n\n" + _sns["dm"], language=None)
            st.caption("各ブロック右上のコピーボタンでそのまま貼れます。"
                       "※投稿は型承認（宅建・広告専門家の事前確認）後に。")

    # ── 表紙特大（P1b-2）：リールカバー/カルーセル1枚目のPNG。★v79-3以降は動画冒頭1.5sにも同一ソースで使う（1源2消費）──
    with st.expander("🖼️ 表紙特大（リールカバー / カルーセル1枚目）を生成", expanded=False):
        st.caption("素材＋事実から表紙1枚を生成。数値（徒歩分・㎡・間取り）はマイソクの事実のみ使用。"
                   "ffmpegのみ・fal課金なし。★このカバーは**動画の冒頭1.5秒にも同じデザインで使われます**（1源2消費）。")
        _cfacts = st.session_state.get("pl_facts", {})
        # ★feat-merge-3：ここにあった2つ目の特集セレクタは撤去した（選ぶ場所はページ最上部の1箇所だけ）。
        #   同じ状態を2つのウィジェットで編集できると「どちらで選んだか」で挙動が違って見える温床になる。
        #   ここは現在の選択の表示のみ（変えたいときは上へ戻る）。
        _fid = st.session_state.get("pl_feature", "normal")
        st.caption(f"特集（テイスト）＝**{core.feature_display_name(_fid)}**　"
                   "…変更はページ最上部の「特集（テイスト）」から（表紙の色・特集枠もそれに追従します）。")
        # ★表紙レイアウト（新キー pl_cover_layout＝旧 pl_cover_style の options差替え地雷を回避）。既定 copy_hero。
        _clayout = st.radio("表紙レイアウト", ["copy_hero", "price_hero"], horizontal=True,
                            key="pl_cover_layout",
                            format_func=lambda s: {"copy_hero": "コピー主役（標準）",
                                                   "price_hero": "価格主役（数字特大）"}.get(s, s))
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
        # ★covercopy-v1：ここに出る文字列＝実際に焼かれる文字列（_pl_effective_hook が1源）。
        _hook_now = _pl_effective_hook(_fid)
        _hsrc = (st.session_state.get("pl_mag_cover") or {}).get("hook_source")
        st.caption(f"コピー：**{_hook_now}**　"
                   + ("（📖の3案から選択中）" if _hsrc == "ai"
                      else "（⚠️特集の既定＝物件別になっていません。📖でコピーを生成してください）")
                   + "　※家賃には管理費を必ず併記します。")
        _cs, _cb, _cc2 = _pl_cover_stale()
        if _cs:
            st.warning(f"⚠️ 表示中の表紙PNGは古いコピー『{_cb}』のままです（現在の選択は『{_cc2}』）。"
                       "下のボタンで作り直してください。")

        if st.button("表紙を生成（ffmpegのみ・課金なし）", key="pl_cover_gen"):
            _csrc = next((it for it in adopted
                          if it["id"] == st.session_state.get("pl_cover_src")), None)
            if not _csrc or not _csrc.get("gen_bytes"):
                st.error("素材画像が見つかりません。確認ステージで採用画像を用意してください。")
            else:
                _casp = st.session_state.get("pl_cover_aspect", "9:16")
                try:
                    with st.spinner("表紙を生成中…（ffmpeg/PIL）"):
                        # ★賃料ガード（rentguard・景表法）：数字以外(漢数字等)混入で描画を止め人に返す。
                        #   自動での数値化はしない＝機械が金額を勝手に作らない（沈黙破損の防止）。
                        _bad = [lbl for lbl, v in (("賃料", _cfacts.get("rent", "")),
                                                   ("管理費", _cfacts.get("fee", "")))
                                if v and not core.money_is_clean(v)]
                        if _bad:
                            raise ValueError(
                                f"{' / '.join(_bad)}に数字以外の文字が含まれます"
                                f"（抽出値『{_cfacts.get('rent','')} / {_cfacts.get('fee','')}』）。"
                                "金額が正しく表示できないため表紙生成を中止しました（自動での数値化はしません）。")
                        # ★v79-3：auto と同一の共有ビルダー（1源2消費＝ピクセル同一）。特集/レイアウトを渡す。
                        _cpng = _pl_build_cover_v79(_csrc["gen_bytes"], _cfacts, _fid, _clayout, aspect=_casp)
                    # 生成結果は非ウィジェットキーへ（地雷1回避）。取り込み時に削除される物件固有キー
                    st.session_state["pl_cover_png"] = {"aspect": _casp, "bytes": _cpng,
                                                        "hook": _pl_effective_hook(_fid)}
                    st.session_state["_pl_cover_auto_sig"] = None   # ★手動生成＝以後 auto で上書きしない
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

    if v_caps:
        with st.expander(f"各シーンのテロップを編集（{len(adopted)}シーン・自動下書き）", expanded=False):
            st.caption("メイン＝部屋名＋帖（自動・表記切替に追従）。情感2行＝下書き。どちらも自由に編集できます。")
            st.caption("スタイル/配置の『既定に従う』＝部屋種別（居室clean／水回りpop）→全体既定 の順で自動決定。")
            _TASTE_LABEL = {"auto": "既定に従う", "clean": "clean（白・影）", "pop": "pop（座布団）"}
            _POS_LABEL = {"auto": "既定に従う", "下中央": "下中央", "下左": "下左",
                          "上中央": "上中央", "中央": "中央"}
            for pos, it in enumerate(adopted):
                st.markdown(f"**{pos + 1}. {_PL_ROOM_JP.get(it['room'], it['room'])}**")
                # メイン欄も v70a ナレ欄と同じ sticky（未編集→表記切替に追従／手編集→追従停止）。
                #   ★popでは Streamlit が widget内部値を保持し value= が無視される（英→和が残る真因）。
                #   widget生成前に session_state へ代入する方式なら確実に反映される（地雷①回避）。
                _mid = it["id"]
                _mkey, _amkey = f"pl_capmain_{_mid}", f"pl_capmain_auto_{_mid}"
                _mdraft = _pl_caption_main(it, v_lang)
                if _mkey not in st.session_state:
                    st.session_state[_mkey] = _mdraft
                    st.session_state[_amkey] = _mdraft
                elif (st.session_state.get(_amkey) == st.session_state.get(_mkey)
                      and st.session_state[_mkey] != _mdraft):
                    st.session_state[_mkey] = _mdraft           # 未編集→表記切替に追従
                    st.session_state[_amkey] = _mdraft
                st.text_input("メイン", key=_mkey)
                st.text_area("情感2行（1行ずつ改行）", value=_pl_caption_sub(it),
                             key=f"pl_capsub_{it['id']}", height=70)
                sc1, sc2 = st.columns(2)
                sc1.selectbox("スタイル", ["auto", "clean", "pop"], index=0,
                              key=f"pl_taste_{it['id']}",
                              format_func=lambda x: _TASTE_LABEL.get(x, x))
                sc2.selectbox("配置", ["auto"] + _PL_TELOP_POSITIONS, index=0,
                              key=f"pl_pos_{it['id']}",
                              format_func=lambda x: _POS_LABEL.get(x, x))
                # 🎙️ ナレ欄（テロップと並べる）。未編集ならテロップに追従／編集済みなら追従しない。
                if v_narr_on:
                    _nid = it["id"]
                    _nkey, _akey = f"pl_narr_{_nid}", f"pl_narr_auto_{_nid}"
                    _draft = core.normalize_reading(_pl_scene_main_text(it, v_lang))  # メインのみ＝緑スタート
                    if _nkey not in st.session_state:                       # 初回：自動下書き＋追従基準
                        st.session_state[_nkey] = _draft
                        st.session_state[_akey] = _draft
                    elif (st.session_state.get(_akey) == st.session_state.get(_nkey)
                          and st.session_state[_nkey] != _draft):           # 未編集→テロップに追従
                        st.session_state[_nkey] = _draft
                        st.session_state[_akey] = _draft
                    st.text_area("🎙️ ナレーション（読み上げ内容・空欄＝このシーンは無音）",
                                 key=_nkey, height=68)
                    # ★story-v78 A-3：継続シーン（同ビート2枚目以降）で空欄なら『1枚目にまとまる』を明示
                    #   ＝空欄が「無音」なのか「まとめた」のか画面から分かるようにする（沈黙で変に見せない）。
                    if (pos > 0 and adopted[pos - 1].get("room") == it.get("room")
                            and not (st.session_state.get(_nkey) or "").strip()):
                        st.caption("🎙️ このビートのナレは1枚目の画像にまとまっています"
                                   "（この画像は無音で続けて再生されます）。個別に付けたいときはこの欄に入力できます。")
                    _nv = st.session_state.get(_nkey, "")
                    _ncnt = len(core.normalize_reading(_nv))               # ★字数は正規化後で数える
                    if _ncnt > _nlimit:
                        st.error(f"🎙️ {_ncnt}字（上限{_nlimit}字を超過）。短縮してください（尺からはみ出します）。")
                    elif 0 < _ncnt <= _nlimit // 2:
                        # 既定(メインのみ)がスカスカのとき、情感2行を素材に上限近くまで埋める導線を控えめに示唆
                        st.caption(f"🎙️ {_ncnt}/{_nlimit}字（正規化後）— 「AIで整える」で情感を織り込むとより自然になります")
                    else:
                        st.caption(f"🎙️ {_ncnt}/{_nlimit}字（正規化後）")
                    if core.narration_has_ascii(_nv):
                        st.warning("🎙️ 英字が残っています（TTSが英語読みする可能性）。"
                                   "日本語表記に直すか『AIで整える』を押してください。")
                    st.button("AIで整える", key=f"pl_narr_polish_{_nid}",
                              on_click=_pl_narr_polish_cb, args=(_nid, _pl_scene_telop_text(it, v_lang), v_dur))
                    _pmsg = st.session_state.get(f"_pl_narr_msg_{_nid}")
                    if _pmsg:
                        st.warning(_pmsg)

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

    if n_fal >= 5:   # #4 ガード：カット数が多いと時間がかかる（メモリ超過はv58で解消済）
        st.warning("⚠️ 採用カットが多いほど生成に時間がかかります（1本あたり約1〜1.5分）。"
                   "急ぐ場合や不安なときは3〜4カットに絞るのがおすすめです。")

    # ── ジョブ組み立て（接続断に強いqueue+state.json）。job_idは入力＋設定から決定的に導出 ──
    #    毎render組み立て→同一入力なら同一job_dir＝再入場時に「続きから再開」を検出できる。
    _scenes, _images = [], []
    for _k, it in enumerate(adopted):
        _nm = it.get("caption") or it["room"]
        _note = v_note or (it.get("disc") or "※AI加工のイメージ")   # 全体注記優先→個別→既定
        _sub_raw = st.session_state.get(f"pl_capsub_{it['id']}", "") if v_caps else ""
        _scenes.append({
            "name": _nm, "still": it.get("_origin") == "persp",
            "caption": (st.session_state.get(f"pl_capmain_{it['id']}")
                        or _pl_caption_main(it, v_lang)) if v_caps else "",
            "subs": [s for s in _sub_raw.split("\n") if s.strip()][:2],
            "note": _note, "taste": _pl_resolve_taste(it, v_taste),
            "pos": _pl_resolve_pos(it, v_pos), "top_tag": v_tag if v_caps else "",
            "room_type": _pl_video_room_type(it["room"]), "flash": "", "fit": v_fit,
            "room": it["room"],   # ★story-v78ビート化キー（連続する同roomを1ビートに）
            # ★v79-4：focal主語/動き量（room_facts_map由来・狭室minimal）。focalはv79-5 magtextがビート毎に上書き。
            "focal": core.room_facts_map(it["room"]).get("focal"),
            "motion": core.room_facts_map(it["room"]).get("motion", "normal"),
            # ★シーン単位（ID基準）でナレを持たせる＝index基準の噛み合わせズレ(v68真因)を断つ
            "narration": (st.session_state.get(f"pl_narr_{it['id']}", "") if v_narr_on else "")})
        # ★v79-5b：ビート先頭画像に magtext の文字面（big_text/accent/comment/タグ）を注入。
        #   pl_mag_{id} は生成CBがビート先頭idにのみ格納＝この画像が_pl_assign_story_beatsのgrp[0]に一致。
        _mg = st.session_state.get(f"pl_mag_{it['id']}")
        if isinstance(_mg, dict) and _mg.get("big_text"):
            _scenes[-1]["room_label"] = _mg.get("room_label", "")   # 表示名（LDK→リビング等・magtext由来）
            _scenes[-1]["big_text"] = _mg.get("big_text", "")
            _scenes[-1]["accent_word"] = _mg.get("accent_word", "")
            _scenes[-1]["comment"] = _mg.get("comment", "")
            _scenes[-1]["beat_tags"] = _mg.get("tags", [])
            _scenes[-1]["narration_kana"] = _mg.get("narration_kana", "")   # ★narr-fix-d：TTS読み仮名
        _images.append((_nm, it["gen_bytes"]))
    # 表紙挿入ON時は冒頭極短フラッシュを自動OFF（冒頭の重複回避）
    if _scenes and st.session_state.get("pl_open_title") == "flash" and not v_cover_on:
        # 冒頭タイトルON。文言が空なら facts から既定を再導出（＝空のまま無タイトルになる嘘UIを撲滅）。
        _title = (v_flash or "").strip()
        if not _title:
            _ff = st.session_state.get("pl_facts", {})
            _mad = (_ff.get("madori", "") or "").split("[")[0].strip()
            _title = (f"{_ff['name']} ｜ {_mad}" if _ff.get("name") and _mad
                      else f"{_mad} ｜ {_ff['area']}" if _mad and _ff.get("area") else "")
        if _title:
            _scenes[0]["flash"] = _title                    # 冒頭フラッシュは先頭のみ
        else:
            st.warning("⚠ 冒頭タイトルがONですが文言が空で、物件名・間取りからも作れませんでした。"
                       "タイトルは付きません（『フラッシュ文言』を入力してください）。")
    _fp = st.session_state.get("pl_floorplan")               # 間取り図カット（実物・静止・fal課金なし）
    if st.session_state.get("pl_include_fp") and _fp is not None:
        _f = st.session_state.get("pl_facts", {})
        _mad = (_f.get("madori", "") or "").split("[")[0].strip()
        _ar = (_f.get("area", "") or "").strip()
        _fp_cap = " ".join(x for x in (_mad, _ar) if x) or "間取り図"
        _scenes.append({"name": _fp_cap, "still": True, "caption": _fp_cap if v_caps else "",
                        "subs": [], "note": v_note, "taste": v_taste, "pos": "下中央",
                        "top_tag": v_tag if v_caps else "", "room_type": "generic",
                        "flash": "", "fit": "contain"})
        _images.append((_fp_cap, _fp))
    # ★story-v78 A0：ONのときだけビート割り当てを注入（scenesに beat_id/gen_dur/trim/beat_narration）。
    #   OFF＝一切注入しない＝run_tour_jobで beat_id無し＝_xfade_concat（完全回帰）。
    if v_story and _scenes:
        _predicted = _pl_assign_story_beats(_scenes, v_dur)
        st.session_state["_pl_v_predicted_sec"] = _predicted
    else:
        st.session_state.pop("_pl_v_predicted_sec", None)
    # ★story-v78 B（テキスト一本化）：ナレを字幕へ。ビート単位・③字数比の1行切替（動画レベルで焼く）。
    #   ビートのナレ(=TTSと同一text)を prepare_subtitle(焼込前fact_scrub=手編集の穴＋3行折返し)→{lines,dur}。
    #   dur=beat_narr_sec（A0の描画秒＝ナレ音声と同一の時間軸）。subtitle_beats があれば unified＝メイン/情感を焼かない。
    _sub_beats, _sub_warn = None, []
    if v_story and v_narr_on and _scenes:
        _facts_sub = _pl_effective_facts()
        _sub_beats = []
        _prev = object()
        for _sc in _scenes:
            _bid = _sc.get("beat_id")
            if _bid == _prev:
                continue                                  # ビート先頭のみ（beat_narration/secは先頭sceneに載る）
            _prev = _bid
            _bn = (_sc.get("beat_narration") or "").strip()
            _bdur = float(_sc.get("beat_narr_sec") or 0.0)
            if _bn:
                _slines, _sremoved = core.prepare_subtitle(_bn, _facts_sub)
                _sub_beats.append({"lines": _slines, "dur": _bdur})
                _room = _sc.get("room", "")
                for _r in _sremoved:
                    _sub_warn.append(f"{_room}: 字幕から事実外属性『{_r}』を除去しました（焼込前ゲート）。")
                if _slines and _slines[-1].endswith("…"):
                    _sub_warn.append(f"{_room}: 字幕を3行に収めるため末尾を省略しました"
                                     "（★音声は全文読みます／D＝同期実装後は不要になります）。")
            else:
                _sub_beats.append({"lines": [], "dur": _bdur})   # ナレ無ビート＝尺だけ進める（字幕なし）
    for _w in _sub_warn:
        st.warning("💬 " + _w)
    _ow, _oh = rtv.ASPECT_DIMS.get(v_aspect, (1080, 1920))
    _cov_bytes = None
    if v_cover_on and st.session_state.get("pl_cover_png"):
        _cov_bytes = st.session_state["pl_cover_png"].get("bytes")
    # ★v79-5b：文字面overlayのaccent色（選択中の特集）。run_tour_jobがbig_text保持時に build_beat_overlay へ渡す。
    _v79_accent = (core.feature_of(st.session_state.get("pl_feature", "normal")) or {}).get("accent")
    # ★v79-6 DATA面（動く雑誌の最終ページ）：facts から安全にスペック行を組む（取れない行は省略・方角は明記時のみ・否定除外・寸法strip）。
    _data_facts = _pl_effective_facts()
    _data_rows = core.build_data_rows(_data_facts)
    _data_ym = core.data_note_date(_data_facts, _pl_jst_ym())   # ★注記年月＝マイソク日付優先→生成日
    # ★v79-note：AI生成イメージの文言は core.AI_IMAGE_NOTE が1源（表紙・ビート面・DATA面で書き分けない）。
    _data_notes = ["※保証会社利用必須ほか諸費用あり。詳細はお問い合わせください。",
                   core.AI_IMAGE_NOTE + "　※間取り図は資料に基づく",
                   f"※{_data_ym}時点の情報・現況優先"]
    # ★v79-note：本編（ビート面）に全長で焼く注記。表紙 _pl_cover_v79_fields と同じ _pl_ai_note_line 由来。
    #   これが glob に入っている＝run_tour_job が seg 側（テロップ層）の注記を止めて overlay 側で焼く合図。
    _v79_note = _pl_ai_note_line(_data_facts)
    # ★issue-v1：マストヘッド2行目（号数＋エリア）。表紙(_pl_cover_v79_fields)と同一の _pl_issue_text 由来＝3面一致。
    _issue_text = _pl_issue_text(_data_facts)
    _glob = {"out_w": _ow, "out_h": _oh, "duration": v_dur, "model_key": v_model,
             "with_bgm": v_bgm, "also_silent": True, "flash_cut": bool(v_flashcut),
             "narration_on": bool(v_narr_on), "aspect": v_aspect, "v79_accent": _v79_accent,
             "v79_issue": _issue_text, "v79_note": _v79_note,   # ★v79-note：景表法注記（本編全長・1源）
             "cover_on": bool(v_cover_on and _cov_bytes), "cover_sec": float(v_cover_sec),
             "negative_prompt": rtv._V79_NEGATIVE, "cfg_scale": None,   # ★v79-4：動く雑誌のnegative
             # ★v79-6：DATA面（スペック行があれば末尾に静止ページ連結・ナレなし・BGM継続・間取り図はdata_floorplan.png）
             "data_on": bool(v_data and _data_rows),   # ★既定ON・チェックで外せる（行が無ければ自動OFF）
             # ★issue-v1：旧 "area": "OSAKA"（build_data_page が一度も参照しない死に引数）を廃止し issue_text へ統合。
             "data_page": {"feature_id": st.session_state.get("pl_feature", "normal"),
                           "issue_text": _issue_text, "rows": _data_rows,
                           "notes": _data_notes, "sec": 3.5},
             # ★story-v78 B：ナレ字幕の一本化。subtitle_beats=③焼込用[{lines,dur}]／unified=メイン/情感を焼かない。
             #   ★v79文字面(big_text)がある場合はrun_tour_jobが字幕焼きをスキップ（画面の主役はbig_text）。
             "subtitle_beats": _sub_beats, "unified_subtitle": bool(_sub_beats)}
    import tempfile as _tf
    _job_id = rtv.job_id_for(_images, {"glob": _glob, "scenes": _scenes})
    _job_dir = _os.path.join(_tf.gettempdir(), f"tour_{_job_id}")
    _state = rtv.read_job_state(_job_dir)
    _scst = _state.get("scenes", []) if _state else []
    _n_notdone = sum(1 for s in _scst if s.get("status") != "done")        # 失敗+未submit+投入済み未回収
    _n_failed = sum(1 for s in _scst if s.get("status") == "failed")
    _n_charge = sum(1 for s in _scst if s.get("status") in ("pending", "failed"))  # 再開で新規fal課金する分
    # ★状態とUIの整合を機械保証：done でないシーンが残る／連結未完（phase!=done）なら再開可能。
    #   ＝「完成(phase=done)＋一部失敗」も拾う（v60で漏れていたセル）。
    _resumable = bool(_state) and (_n_notdone > 0 or _state.get("phase") != "done")
    _resume_cost = 0.35 * (v_dur / 5) * _n_charge

    def _run_video_job():
        if not get_secret("FAL_KEY", ""):
            st.error("FAL_KEY が未設定です。")
            return
        bar = st.progress(0.0)
        status = st.empty()

        def _pg(step, total, msg):
            bar.progress(min((step + 1) / (total + 1), 1.0))
            status.write(msg)
        try:
            if not rtv.read_job_state(_job_dir):   # 新規（または再起動で/tmp消失）→初期化
                rtv.init_job(_job_dir, _images, _scenes, _glob, cover=_cov_bytes)
            # ★covercopy-v1：表紙PNGは job_id のハッシュ対象（_glob/_scenes/_images）に入らないため、
            #   コピーを変えても job_id は同じ＝既存jobが見つかり init_job がスキップされ、job_dir の
            #   古い cover.png がそのまま動画に入る（＝セッション側PNGを直しても動画だけ旧コピーになる穴）。
            #   DATA面の素材と同じく毎回上書きして解決する（job_id_for は触らない＝fal再課金を起こさない）。
            if _cov_bytes:
                with open(_os.path.join(_job_dir, "cover.png"), "wb") as _f:
                    _f.write(_cov_bytes)
            # ★v79-6：DATA面の間取り図(pl_floorplan)と背景(直前シーン画像)を job_dir に保存（毎回＝再起動/resume耐性）。
            #   pl_floorplan が無ければ保存しない＝run_tour_job が表を上に詰めてDATA面を出す（silent dropしない）。
            _fp_b = st.session_state.get("pl_floorplan")
            if _fp_b:
                with open(_os.path.join(_job_dir, "data_floorplan.png"), "wb") as _f:
                    _f.write(_fp_b)
            if adopted:
                with open(_os.path.join(_job_dir, "data_bg.png"), "wb") as _f:
                    _f.write(adopted[-1]["gen_bytes"])   # 背景＝直前(最後の)採用シーンを暗くぼかす
            out = rtv.run_tour_job(_job_dir, progress=_pg)   # done skip/submitted回収/pendingのみ投入
            bar.progress(1.0); status.write("完成")
            st.session_state.pop("pl_video_err", None)        # 成功したので失敗表示を消す
            _st_done = rtv.read_job_state(_job_dir)
            st.session_state["pl_video_out"] = {
                "silent": out.get("silent"), "bgm": out.get("bgm"),
                "narrated": out.get("narrated"), "narrated_bgm": out.get("narrated_bgm"),
                "narr_warn": (_st_done or {}).get("narration_warnings", []),
                "outdir": out.get("outdir"), "job_dir": _job_dir}
            st.rerun()
        except Exception as e:  # noqa: BLE001  失敗理由をstate.jsonから拾いUIへ（三箇所目）＋rerun
            _st = rtv.read_job_state(_job_dir)
            _scene_errs = sorted({sc.get("error", "") for sc in (_st.get("scenes", []) if _st else [])
                                  if sc.get("error")})
            st.session_state["pl_video_err"] = {
                "base": f"{type(e).__name__}: {str(e)[:300]}", "scenes": _scene_errs}
            st.rerun()

    def _resume_button(key):   # 独立ボタン（ラベル切替でなく分離＝気づかれやすく）
        _cost = (f"・fal課金≈${_resume_cost:.2f}" if _n_charge else "・再課金なし")
        _lbl = (f"▶ 続きから再開（失敗{_n_failed}本を再試行{_cost}）" if _n_failed
                else f"▶ 続きから再開（残り{_n_notdone}本を回収{_cost}）")
        if st.button(_lbl, type="primary", key=key, use_container_width=True):
            _run_video_job()

    if _resumable:   # 再開バナー＋独立の再開ボタン（生成ボタンとは分離）
        _d, _t, _fl = rtv.job_progress(_state)
        st.info(f"前回の生成に未完了/失敗が残っています：**{_d}/{_t} 完了**"
                + (f"（{_fl}本失敗）" if _fl else "")
                + "。下の『続きから再開』で残り（失敗・未完了）分だけ生成します"
                "（**完了・投入済みは fal 再課金なし**で回収）。")
        _resume_button("pl_v_resume_top")

    # 直近の生成失敗を可視化（logger/state.jsonに加えUIの三箇所目）。rerun後も残す
    _err = st.session_state.get("pl_video_err")
    if _err:
        _b = _err.get("base", "")
        if "403" in _b or "insufficient" in _b.lower() or "credit" in _b.lower() or "balance" in _b.lower():
            st.error("生成に失敗しました：**falのクレジット残高を確認してください**（残高不足の可能性）。"
                     "チャージ後、上の『続きから再開』で投入済み分を再課金なしで回収できます。")
        elif "429" in _b:
            st.error("生成に失敗しました：falのレート上限（429）。時間をおいて『続きから再開』を。")
        else:
            st.error(f"生成に失敗しました: {_b}")
        if _err.get("scenes"):
            st.error("**失敗理由（シーン別）**：\n" + "\n".join(f"・{x}" for x in _err["scenes"]))

    # ★生成前チェック（$3.15投下前に状態を可視化・誤爆対策の情報クッション）。
    _mag_ready = True
    if not _resumable:
        _sum_md, _sum_ng, _mag_ready = _pl_pregen_summary(adopted)
        (st.warning if _sum_ng else st.info)(
            f"🔎 生成前チェック： {_sum_md}　／　カット{n_fal}本 ≈ ${est_usd:.2f}"
            + ("　←⚠️の項目を整えてから生成を推奨" if _sum_ng else ""))
    bcol, gcol = st.columns([1, 2])
    if bcol.button("← 確認に戻る", key="pl_back_review", use_container_width=True):
        st.session_state["pl_stage"] = "review"; st.rerun()
    if not _resumable:   # 新規生成（再開は上の独立ボタン。全成功/未生成はこちら）
        # ★誤課金防止：文字面(pl_mag)未生成だと雑誌レイアウト(masthead/タグ/金色見出し)が一切付かない→生成ボタン封鎖。
        if gcol.button("🎬 ルームツアーを生成", type="primary", key="pl_v_gen",
                       use_container_width=True, disabled=not _mag_ready):
            _run_video_job()
        if not _mag_ready:
            gcol.caption("⚠️ 先に 📖『動く雑誌の文字を生成』を実行してください"
                         "（未実行だと masthead・部屋タグ・金色見出しが付かない動画になります）。")

    # 生成済み動画（パス）を再rerun後も表示。download_button は open(path) で逐次＝mp4を変数に持たない
    _vout = st.session_state.get("pl_video_out")
    if _vout:
        _sp, _bp = _vout.get("silent"), _vout.get("bgm")
        _missing = not ((_sp and _os.path.exists(_sp)) or (_bp and _os.path.exists(_bp)))
        if _missing and not _resumable:
            # ②/tmp消失（再起動等）を明示＝黙って二重課金しない。再開できるなら上のバナーに従う
            st.warning("前回の動画データが見つかりません（アプリ再起動などで一時ファイルが消去された可能性）。"
                       "お手数ですが最初から生成してください。")
        if (_sp and _os.path.exists(_sp)) or (_bp and _os.path.exists(_bp)):
            st.success("ルームツアーが完成しました。")
            # ★実尺を表示（story-v78 A0疎通①：1カット×10秒指定で ≈10s か ≈5s か＝Klingが尺を丸めないかの実測）。
            #   「10秒で投げた」は「10秒返った」の証拠でない＝出力mp4の実尺を測る。
            _vpath = _sp if (_sp and _os.path.exists(_sp)) else _bp
            _real_sec = rtv._dur(_vpath)
            st.caption(f"📏 完成動画の実尺: {_real_sec:.2f}秒")
            # ★story-v78 A0受入：予定Σビート尺を隣に出す＝暗算せず「実尺==予定」を目で判定（疎通①と同じ規律）。
            _pred = st.session_state.get("_pl_v_predicted_sec")
            if _pred is not None:
                _diff_ms = (_real_sec - float(_pred)) * 1000
                st.caption(f"📐 予定 Σビート尺（A0割り当て）: {float(_pred):.2f}秒 ／ "
                           f"差: {_diff_ms:+.0f}ms")
        # #3 1シーン失敗の隔離：完成＋一部失敗でも警告バナー直下に独立の『続きから再開』を出す
        _jd = _vout.get("job_dir")
        _jst = rtv.read_job_state(_jd) if _jd else None
        if _jst and _jst.get("n_failed"):
            _dd, _tt, _ff = rtv.job_progress(_jst)
            st.warning(f"⚠️ {_tt - _ff}本成功 / {_ff}本失敗。下の『続きから再開』で失敗分のみ再試行できます"
                       "（成功分は再課金なし）。")
            if _resumable:
                _resume_button("pl_v_resume_bottom")
        if _sp and _os.path.exists(_sp):
            st.video(_sp)   # Streamlit 1.50 はローカルmp4パスをそのまま再生できる（bytes不要）
            st.download_button("⬇️ 無音版 mp4", data=open(_sp, "rb"),
                               file_name="room_tour_silent.mp4",
                               mime="video/mp4", key="pl_dl_silent")
        if _bp and _os.path.exists(_bp):
            st.video(_bp)
            st.download_button("⬇️ BGM版 mp4", data=open(_bp, "rb"),
                               file_name="room_tour_bgm.mp4",
                               mime="video/mp4", key="pl_dl_bgm")
        # ── ナレーション版（生成された場合のみ）──
        for _w in (_vout.get("narr_warn") or []):
            st.warning("🎙️ " + str(_w))
        _np, _nbp = _vout.get("narrated"), _vout.get("narrated_bgm")
        if _np and _os.path.exists(_np):
            st.caption("🎙️ ナレーション版（各シーン頭に音声・速度調整なし）")
            st.video(_np)
            st.download_button("⬇️ ナレ版 mp4", data=open(_np, "rb"),
                               file_name="room_tour_narrated.mp4",
                               mime="video/mp4", key="pl_dl_narr")
        if _nbp and _os.path.exists(_nbp):
            st.video(_nbp)
            st.download_button("⬇️ ナレ＋BGM版 mp4", data=open(_nbp, "rb"),
                               file_name="room_tour_narrated_bgm.mp4",
                               mime="video/mp4", key="pl_dl_narr_bgm")


def _pl_feature_selector():
    """★feat-merge-3：上流の特集選択（単一の情報源 pl_feature）。旧 _pl_concept_selector を置き換える。
    ここ1箇所で「画像のstaging・スタイル既定・文字面のトーン・ナレ・表紙のaccent/ラベル・投稿文」が決まる。
    ★特集名は内部語＝顧客向け出力には出さない（表紙の『特集　○○』枠は別で、normal は枠ごと描かない）。
    ★key は既存の pl_feature を維持（新キーを切ると sticky が飛ぶ）。既定は normal＝特集なし（標準）。"""
    st.session_state.setdefault("pl_feature", "normal")
    _f = st.selectbox("特集（テイスト）　※上流で1回・画像化〜投稿文の既定が追従",
                      core.FEATURE_ORDER, key="pl_feature",
                      format_func=core.feature_display_name)
    if _f == "normal":
        st.caption("特集なし（標準）。家具ステージングに方向づけを足さず、表紙にも特集枠を出しません。")
    else:
        st.caption(f"🏠 家具ステージング・スタイル既定・文字面のトーン・ナレ・表紙・投稿文の既定が"
                   f"「{core.feature_display_name(_f)}」に追従します（特集名は顧客向けに出しません）。")
    return _f


def render_pipeline():
    st.subheader("物件から動画をつくる")
    st.caption("マイソクPDF や 手持ち写真 → 内観画像 → ルームツアー動画 までを一気通貫で。"
               "（途中のダウンロード・再アップは不要）")
    stage = st.session_state.get("pl_stage", "input")
    # ★手順の常時表示（📖文字面を明示＝埋もれ解消）。現在地=太字／未完了=グレー＋⚠️。
    _sb_items = st.session_state.get("pl_items", [])
    _sb_ids = [it["id"] for it in _sb_items if it.get("gen_bytes") and it.get("_adopt", True)]
    _sb_mag = any(isinstance(st.session_state.get(f"pl_mag_{i}"), dict)
                  and st.session_state[f"pl_mag_{i}"].get("big_text") for i in _sb_ids)
    steps = ["① 取込", "② 画像化", "③ 整列・確認", "📖 文字面", "④ 動画化"]
    if stage == "video":
        cur = 4 if _sb_mag else 3               # video中：文字面未なら📖が現在地
    else:
        cur = {"input": 0, "review": 2}.get(stage, 0)
    _labels = []
    for i, s in enumerate(steps):
        if i == cur:
            _labels.append(f"**{s}**")          # 現在地
        elif i == 3 and not _sb_mag and stage == "video":
            _labels.append(f":red[{s}⚠️]")      # 📖文字面 未実行を赤で強調
        elif i > cur:
            _labels.append(f":gray[{s}]")       # 未完了はグレー
        else:
            _labels.append(s)                   # 完了
    st.caption("　→　".join(_labels))
    _pl_feature_selector()   # 上流1択（単一の情報源 pl_feature）＝画像化より前に置き下流の既定を追従させる
    # ★誤爆対策（$3.15巻き戻り）：『最初からやり直す』は2段確認。1クリック目は消さず、はいで初めて全消し。
    #   on_click で nonce を進めてから全消し（uploaderを作り直す＝地雷②回避）。rerunは自動。
    if st.session_state.get("_pl_reset_armed"):
        st.warning("⚠️ 最初からやり直すと、取り込んだ画像・生成した画像・動画がすべて消えます"
                   "（②画像化は再課金になります）。よろしいですか？")
        _rc1, _rc2 = st.columns(2)
        _rc1.button("はい、最初からやり直す", key="pl_reset_yes", type="primary",
                    on_click=_pl_reset_confirm_cb, use_container_width=True)
        _rc2.button("キャンセル", key="pl_reset_no",
                    on_click=_pl_reset_cancel_cb, use_container_width=True)
    else:
        st.button("最初からやり直す", key="pl_reset_btn", on_click=_pl_reset_arm_cb)
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
page_carousel = st.Page(render_carousel, title="カルーセルをつくる",
                        icon=":material/view_carousel:")
page_background = st.Page(render_background, title="背景素材をつくる",
                         icon=":material/image:")
page_settings = st.Page(render_settings, title="設定", icon=":material/settings:")

nav = st.navigation({
    "": [page_home],
    "つくる": [page_pipeline, page_carousel, page_background],
    "その他": [page_settings],
})
nav.run()
# 脚注は nav.run() の後に置き、ページがサイドバーに足す内容（間取り図ピン留め等）を
# ナビ直下＝上部に寄せる（下部に置くと selectbox 等が画面外に溢れるため）
with st.sidebar:
    st.caption("生成画像にはSynthIDの不可視透かしが入ります。"
               "商用利用可否はGoogleの利用規約を最終確認してください。")
    st.caption("build: v79-feature-reach2 (★②自分を整える部屋の【厳守】1行を経路非依存の書き方へ訂正。旧文『独立洗面台・浴室乾燥…等を、写真に無いのに描き加えない』は、補完生成（写真の無い部屋を間取り図から起こす経路）では『写真そのものが存在しない』ため『独立洗面台を描くな』と読まれうる。②は独立洗面台が訴求の芯で、洗面は写真が付いていないことが多い＝②の主力カットがまさにこの経路に乗る。③の『物件事実に無いのに』と同じ書き方へ揃え『この住戸に実在しないのに描き加えない（入力画像に写っておらず、物件事実にも記載が無いもの）』へ。staging（実写真）経路の意味は変わらない（写真に写っておらず物件事実にも無い＝従来の写真に無いと実質同義）。補完生成には _gap_facts（設備・築年の事実ガード）が前置されているので記載の有無はプロンプト内で判定できる。★受入(0円): normal / mote_heya / hobby は staging・補完生成・3Dパースとも変更前とバイト一致（②の定義しか触っていない証明）。②のみ staging 15ブロック・補完生成17ブロックでこの1行だけが差し替わることを diff で確認。新文言が②のban語（安心/安全/防犯等）を踏まないことも確認。既存テスト（scrub-clause 22件 / back-direction 22件 / feature-reach(b) 全件）通過。以下 v79-feature-reach: ★特集の到達範囲を揃えた（依頼文v1.3 §8）。(a) 補完生成 build_room_tour_prompt と 3Dパース build_3d_perspective_prompt に concept_staging 引数を追加しapp 側から feature_staging(pl_feature) を渡す＝写真の無い部屋（間取り図から起こす内観）だけ特集が1バイトも届いていなかった穴を塞ぐ。★本丸=ROOM_TOUR_FURNITURE の競合解消: 洋室=ベッドを主役に と③趣味部屋=デスク+本棚 が1枚のプロンプト内で正面衝突していた。furni_line 直後に優先順の1行を足して解く（特集の方向づけがあれば家具の種類はそちらを優先／用途に合わない家具＝トイレのソファ・水回りのベッドは特集に関わらず厳守）。ROOM_TOUR_FURNITURE の中身は1文字も変えていない。★concept_staging が空のときは改行すら足さない実装にした＝特集なし(normal)は変更前とバイト一致。（最初の実装で空行が1本入り normal のバイト一致が崩れたのを自己テストで検出して修正）(b) STORY_SITUATIONS の各エントリに feature を追加し story_situations_for(rooms, feature_id) へ。従来は検出部屋でしか絞らず特集を見ないため、②③を選んでもモテ部屋の世界観6件しか出なかった。A系/B3 の文面・style・need は1文字も変えず、②に C1-C4・③に D1-D4 を追加。B1 は全特集共通に置き候補ゼロを防ぐ。★引数の既定は normal（None＝絞らない、ではない）＝渡し忘れたときに全特集の世界観が混ざったリストを返すと、モテ部屋の選択肢に②③が紛れても気づけない。既定 normal なら候補が共通1本だけという目に見える壊れ方になる。★受入(0円): (a) 補完生成8部屋×ref有無16通り＋3Dパースの全文をファイルへ落とし normal は変更前とバイト一致・③洋室に③の【厳守】(自立式ラック/防音・楽器可を想起させない)が届くことを確認。(b) mote_heya の返り値が変更前と完全一致(A1-A4,B1,B3)・②③でA系B3の漏れゼロ・最小構成(LDKのみ)でも全特集で候補が1本以上・C/D全8件が ban/needs_review/fact_scrub/数字のいずれも踏まないことを実関数で確認。既存テスト(v79-scrub-clause 22件・v79-back-direction 22件)とstaging/ban集合の回帰も再確認。★注意: v79-back-direction は依頼文が「実機目視＋工程5の後」としていたが、その指示が届く前に実装・push済（e5c70d4）。仕様差1件を報告に記載。以下 v79-back-direction: ★fact_scrub の『日当たり・方角』グループを『方角の断定』と『日当たり・採光』の2つに分割＝マイソクに日当たり良好の1語があるだけで南向きという方角の断定まで通っていた穴を塞ぐ（実測で再現）。方角の裏付けは 南向き/南面 の明示のみ。日当たりの裏付けは 日当たり/陽当たり/採光/日照 ＋ 南向き/南面（南向きの明示があれば日当たりも通る・逆は通らない）。★back から 方角・向き を削除＝向き が短すぎて 北向き / バルコニー南東向き にヒットし、北向き物件で南向きと書けていた（実測）。谷合さんの実マイソク16件の全数調査で 方角・向き の出現0件と確認できたため、正規表現化の小改修をせず単純削除で足りる（過剰除去に倒れる心配が実データ上ない）。★私の当初の推奨案（[東西南北]向き の正規表現）は本命を直さない＝北向きにもマッチするため北向き物件で南向きと書ける状態が残る。谷合さんの指摘どおり誤りだった。★claims は1語も削っていない（旧20語→新20語・2グループに分配しただけ）。back は10→8語（減＝厳しくなる側のみ・増えた語ゼロ）。★受入(0円): 記載×主張の15通り＋他グループ7通りの計22ケースを全数実測し期待と全一致。他グループ（眺望/角部屋/静けさ/通風）は無改変で回帰なし。ban集合66/66/71 不変・staging は normal/mote ともバイト一致。★残る粗さ: グループ単位の裏付けという機構上、南向きの明示があると同グループの 西日 / 朝日が差し（東西の含意）も通る。方角ごとの対応表が要る＝今回の範囲外として別途。以下 v79-scrub-clause: ★ban ヒット時の断片化を止めた＝②③で動画を出す前の必修(依頼文v1.2 §8)。ban は語を置換して消す方式だったため『安心の、オートロック。』→『の、オートロック。』のように助詞始まりの断片が動画に焼かれていた（警告は出るが止まらない）。covercopy-v1 で数字の機械削除をやめて検出のみに切り替えたのと同じ型の沈黙破損。core.ban_scrub＝節ごと除去へ切替（fact_scrub と同じ思想）。★1源化: 旧 _drop_neg_clauses を drop_clauses_containing へ汎用化し、否定設備ガードと ban 除去が同じ節分割を共有する（節の切り方を2箇所に書かない）。配線4経路＝magtext の _clean(big_text/comment/表紙3案)・polish_narration(ナレ)・_scrub_cover_copy(表紙)・draft_sns_captions の _clean(投稿文/ハッシュタグ)。★自己テストで回帰を1件検出して修正: 旧 _drop_neg_clauses は無条件 strip(。) していたため、ban 経路をここへ寄せた時点で ヒットの有無に関わらず末尾の句点が消えていた（帰りたくなる、1LDK。の句点は意図的な演出）。→ ①1語も当たらなければ1文字も触らない ②当たった場合の後始末は fact_scrub と同一 に修正。★§5の訂正: claims の単独『あさひ』を撤回し『あさひが差す』『あさひが入る』へ置換。単独だと大阪に頻出する物件名（朝日プラザ/あさひ荘）に巻き添えで正当な節が丸ごと落ちる（実測: あさひプラザの、エントランス。→空）。_NEEDS_REVIEW の「『極』単文字は入れない」と同型の失敗だった。★受入(0円・偽クライアントで magtext/polish_narration を実走): ②で安心を含む comment/big_text/表紙3案/ナレの全経路に助詞始まりの断片ゼロ・ban ヒット無しのテキストは1文字も変わらない・fact_scrub 既存12語は全て除去継続・ban 集合の語数は工程4から不変(66/66/71)・staging は normal/mote とも変更前とバイト一致。★私の誤報を訂正: 前回報告の「物件名にあさひを含むと full_text が裏付けになり光の主張が残る」は再現しない（back に あさひ にヒットする語が無い）。実測せず書いた推測だった。★別途起票が要る実バグを発見: back語『向き』が広すぎ、full_text に 北向き/バルコニー向き があるだけで『南向き』の主張が裏付けありと判定され残る（実測）＝景表法直撃。今回は範囲外なので触っていない。以下 feat-ban-1: ★ban の適用範囲を意図どおりにした。旧 _MOTE_HARD_NG を _COMMON_HARD_NG へ改名し全特集共通のハードNGへ正式昇格＝_story_ban_words() が concept_ban_extra(mote) を引数ハードコードしていたためmagtext/story_narration は特集に関係なく除去する一方 polish_narration/draft_sns_captions は特集依存だった（同じ語が経路によって消えたり消えなかったりする状態）を解消。_story_ban_words(feature_id) へ変更しstory_narration にも feature_id を追加、呼出2箇所（app 🎙️物語生成 / magtext）を配線。新関数 feature_ng(fid)=共通＋特集固有 を下流の post-filter が全部参照する。★『かわいい／可愛い』の2語だけ ban → _NEEDS_REVIEW へ降格（谷合さん判断 2026-08-03）＝止めずに人が見る。★副次で穴を1つ塞いだ: _scrub_cover_copy が concept 既定 normal 固定で、②の安心/安全/防犯 が表紙コピーに素通りしていた。feature 引数を通して magtext から特集を渡す。★併せて、ban が語を置換して消す方式のため『の、オートロック。』のような壊れた断片が「選べる表紙案」として残る問題に対処＝禁止語を除去した案は案ごと落とす（全滅時は既存のfeature_fallback が受け皿なので表紙コピーが空にならない）。★受入(0円・偽クライアントで magtext/story_narration/polish_narration を実走): _story_ban_words は 68→66語＝差分は かわいい/可愛い の2語のみ（依頼文の受入どおり）。polish/sns は normal/②/③ で 44→66・49→71・48→70 語＝共通22語の昇格分だけ増加（緩めた語はゼロ）。_NEEDS_REVIEW は 13→15語。②③に mote 固有語が漏れていないこと（mote固有は空・共通へ移動）を一覧で確認。『かわいい、洗面台。』が全特集で除去されず needs_review に出ること／『安心』が②でのみ除去されることを実走で確認。★残課題: ビートの comment / ナレは ban 置換で断片化しうる（のオートロック付き。）。警告は出るが文は残る＝表紙案と違い代替がないため今回は挙動を変えていない。以下 feat-merge-3: ★UI統合＝テイストのセレクタを1つにした。旧コンセプトセレクタ(_pl_concept_selector・pl_concept)を廃止し、ページ最上部の『特集（テイスト）』(pl_feature)1箇所に統合。表紙expander内にあった2つ目の特集セレクタは撤去し現在の選択の表示のみに（同じ状態を2ウィジェットで編集できる状態を解消）。key は pl_feature を維持＝sticky が飛ばない。★既定を mote_heya → normal(特集なし)へ切替＝feat-merge-2 で staging が特集駆動になったため、ここを切り替えないと『人が選んでいないのに既定でモテのダークトーン』になる。★下流の情報源も特集へ: polish_narration(concept=→feature=) / draft_sns_captions(concept=→feature=) / _concept_caption_line→_feature_caption_line / concept_voice_id→feature_voice_id / _pl_follow_concept_style→_pl_follow_feature_style(スタイル既定)。★スタイル既定を同時に移したのは、staging だけ特集駆動にすると『stagingはモテのダークトーン／styleは北欧』と1枚のプロンプト内で方向が割れるため。★準備中(status:wip / concept_is_wip)の表示は廃止＝②③が準備中でなくなった。★feature_label→feature_display_name へ改名（旧名は『焼く文字』に読めて label と役割が逆に伝わるため）。★受入(0円): 新規セッション(session_state空)の初期選択が normal＝表示『特集なし（標準）』で staging が変更前の normal と一致／特集を normal→②→③ と切り替えて staging・magtextトーン・表紙accent の3要素の組が全特集で相異なることを確認／ナレtone・投稿文tone・hashtags・ban・voice_id が旧 concept 経路と全一致(回帰ゼロ)。★残: pl_concept を読む箇所が3つ残る(_pl_caption_sub / _pl_follow_concept_cover_style / draft_pr_copy)。いずれも feat-dead-1 で丸ごと削除する対象で、セレクタが無い今は normal に固定＝実害なし。以下 v79-note: ★景表法注記(※家具・小物はAI生成のイメージ)を v78 テロップ層から v79 overlay 側へ移設＝feat-dead-1 でテロップ層を削除しても注記が道連れで消えない状態にした（工程5の前提条件）。★1源化: core.AI_IMAGE_NOTE ＋ core.ai_note_line(facts, 生成日) を新設し、表紙(_pl_cover_v79_fields)・本編(build_note_overlay)・DATA面(_data_notes) の3面が同じ定数から文言を作る。年月は data_note_date と同じ規則（マイソク記載を優先→無ければ生成日）＝3面で年月がずれない。★全カット保証: 注記はビート文字面に相乗りさせず build_note_overlay の独立PNGを本編全長の時間窓で焼く。ビート文字面は先頭sceneにしか載らず、間取り図カットのように room_label も big_text も無いカットはoverlay ループで skip されるため、相乗りさせるとそこだけ注記が消える（実測で確認）。★コントラスト修正: 旧テロップ層の note は v79 下部グラデ(alpha235)の下敷きで 212→40 まで落ちていた。overlay 側はグラデの上に描くので沈まない。実測=ビートカット210 / 間取り図カット169(グリフ輝度206) / 旧モード212＝同水準。★seg側の注記は glob の v79_note があるときだけ止める（旧 job state や旧経路では残す＝注記がゼロになる瞬間を作らない）。★文字面の焼込みが失敗した場合の警告文を変更: 『文字なしで生成しました』では注記欠落が伝わらないため『この動画にはAI生成イメージの注記が入っていません。投稿せず再生成してください』と明示（silent drop 禁止）。★注意: glob に v79_note を追加したため job_id が変わる＝この版をデプロイすると生成途中のジョブは別ジョブ扱いになる（再開すると fal 再課金）。デプロイ前に④動画化の途中ジョブを完了/破棄すること。以下 feat-merge-2: ★内観staging の情報源を pl_concept → pl_feature(特集) へ差替え＝本件の主目的。これ以前は特集を切り替えても内観画像のプロンプトが1バイトも変わらず、FEATURES[*] の staging_prompt は参照ゼロの死にデータだった。app.py:1297 を core.feature_staging(pl_feature) へ。★fallback は normal（mote_heya ではない）: ②画像化は④動画化 expander より前に走るため新規セッションで④を一度も開かないと pl_feature は未設定＝mote_heya に倒すと人が選んでいないのにダークトーン staging が入る。★build_staging_prompt / build_water_staging_prompt のシグネチャ(concept_staging=)は据え置き（改名は差分を広げるだけ・feat-dead-1 で整理）。v79-3 当時の実装と食い違ったコメント（1源=staging が参照）を実態へ修正。★受入(0円・fal/Gemini 不使用): 種別2×部屋9=18ブロックのプロンプト全文をファイルへ落として diff。normal は変更前と sha256 一致(3059c02b028b0cb7)・mote_heya は変更前の pl_concept=mote と sha256 一致(4026a6561fd92159)＝回帰ゼロを機械証明。②③は concept_staging を載せる15ブロック全部に【厳守】行(②収納の扉/③自立式ラック・防音楽器可ペット可)が届いていることを grep で確認。★併せて fact_scrub の日当たりグループにひらがな異表記4語を追加(やわらかい光/あたたかい光/ひかりが差す/あさひ)＝従来は漢字表記のみ検出で『やわらかい光の、洗面台。』がガードを素通りしていた。既存語は1語も削っていない。裏付けなし=節ごと除去/裏付けあり(南向き記載)=残存 を新旧9語で実測。★既知の弱点: 物件名に『あさひ』を含む物件は full_text が裏付けになり誤って残りうる。以下 feat-merge-1.5: ★v79「動く雑誌」モードで v78 テロップ層（メイン＋情感2行＋上部タグ）を焼かないようにゲート＝run_tour_job._make_seg の _normalize_clip 引数を _telop_off で落とす。理由=v79 は big_text/comment/room_pill/マストヘッドが同じ役割を担い、両方焼くと同役割の文字が2系統重なる（実測: 情感2行が big_text 直下に沈み、上部タグが OSAKA ROOMS マストヘッドと y172-208 で交差）。subtitle_beats の焼込みは既に not _v79_mag でスキップ済みだったが seg 段のテロップ層だけゲートが漏れていた＝v79-5b の設計意図と実装の食い違いを解消。★判定は ordered（描画完了分）でなく scenes（ジョブ全体）で行う: _make_seg は描画の最初に走るため ordered はこの時点で必ず空／seg は冪等キャッシュなので再開時に判定が揺れるとテロップ有無の混ざった seg ができる。★note（※画像はイメージです）と冒頭 flash は落とさない: ビート面の情報バー note_line は scene に載らず常に空＝景表法の注記はこのテロップ層の note が唯一の出口。★UIの「シーンテロップを焼く」は挙動を変えず注記だけ追加（黙って無効化するとチェックが嘘になるため。撤去/内部フラグ化は feat-dead-1 で判断）。★受入(fal課金ゼロ・still のみで run_tour_job を実走): A) v79モード=情感2行/メイン/上部タグが消えマストヘッド交差も解消・注記は残る・v79文字面(big_text/accent/タグ/room_pill/masthead)は従来どおり B) 旧モード(big_text/comment なし)=従来どおりテロップが焼かれる＝回帰なし。★既存の再開中ジョブは status=done の seg をそのまま再利用するため古いテロップ入り seg が残る（fal 再課金を避ける既存仕様・新規ジョブから適用）。★別途報告(この変更とは無関係の既存事象): 景表法注記が v79 下部グラデで沈む（コントラスト 212→40・変更前後で同値42/40）。以下 feat-merge-1: ★特集マスタ FEATURES をテイストの唯一の情報源へ拡張。CONCEPT_PRESETS の実消費7キー(style_default/staging_prompt/narration.tone/narration.voice_id/ban_words/caption.tone/caption.hashtags)を移植し、②自分を整える部屋/③趣味部屋の中身(staging長文＋【厳守】ブロック/comment_tone/cover_hooks 3案/固有ban/hashtags)を実装＝②③が実際に使える状態に。★normal(特集なし)を FEATURES へ追加＝統合後の既定を旧 pl_concept=normal と完全一致させ全体回帰を防ぐ(staging空・style_default ナチュラル/北欧)。空labelは『表紙に特集枠を描かない』合図であり、UI表示名は feature_display_name() に分離した(label を表示名で埋めた瞬間に枠が復活するため別物として扱う)。rtv._v79_feature_label は空labelで枠ごと描画しない分岐を追加(空文字のままだと『特集　』の空枠が残る)。FEATURE_ORDER でセレクタの並びを明示(dictキー順に依存しない)。★feature_of() の未知idフォールバックを None→normal へ(concept_of と同型)。実害確認済=旧実装は未知idのとき表紙が『特集　モテ部屋』を騙っていた(rtv:1479)＝issue-v1 の『取れないものを既定で埋めない』と同方針で normal=枠なしへ。呼出側の feature_of(x) or {} は dict が常に真で無害。アクセサ追加=feature_label/feature_style_default/feature_staging/feature_voice_id/feature_tone/feature_hashtags/feature_ban_extra/feature_ban。★移植しないキーと理由(工程0の実測で消費先が死んでいると確定): cover.default/cover.style=呼出元ゼロ／cover.tone=消費先が draft_pr_copy の title/subtitle だけで表紙PNGには一度も焼かれない(v79-3 c0a7b78 で切離・唯一残る冒頭フラッシュ経路も表紙挿入ON=既定で不発)／telop・sub_template=消費先が v78 テロップ層で feat-merge-1.5 で落とす。★受入(fal不要・ローカル実測): 旧 CONCEPT_PRESETS との7キー全一致を normal/mote で機械照合(staging_prompt はバイト一致)・②③の cover_hooks 全6案が _scrub_cover_copy 警告ゼロ/needs_review ゼロ(AI3案全滅時の fallback として機能する条件)・表紙4特集をローカル描画し normal は特集枠なし/mote GOLD/②ROSE/③SAGE の accent 追従を確認。★注意=この時点では内観staging の情報源はまだ pl_concept 側(feat-merge-2 で接続)。以下 covercopy-v1: ★表紙コピーを物件別の自由生成に切替＝特集固定文言(cover_hooks[0])の焼き回しを廃止。★併せて既存の受入8違反を解消: magtextが生成した表紙hookをUIは表示するのに _pl_cover_v79_fields が読まず、画面の文言と焼かれる文言が別物になりうる状態だった(旧コードで再現テスト済)。core.magtext=cover節のみ差替え(hook_candidates 3案・全角14字・読点1つ・facts根拠・切り口を変える)。ビート面(big_text/comment/narration_kana/タグ)の生成規則は不変=旧コードとbeats出力バイト一致で確認。ガードは全案に必ず通す(fact_scrub→ban→core._scrub_cover_copy〈物件名/字数14/装飾記号〉→needs_review)。空になった案は落とし全滅なら feature.cover_hooks[0] へ hook_source=feature_fallback で明示フォールバック。★数字は削除せず検出のみ(間取り表記1LDKを機械削除すると『帰りたくなる、LDK。』と壊れるため。間取り以外の数値主張はneeds_reviewで人力確認へ)。UI=📖内に3案ラジオ(全文表示・人が選ぶゲートは維持)＋確定コピー表示。★needs_reviewは案ごとに保持(needs_review_by_hook)＝ラジオ本文末尾に⚠️要確認を付け理由も案別に並べる(全案を1行にまとめると『選ぼうとしている案が安全か』が分からず、数値主張を人が弾く前提が崩れるため)。選択中の案が要確認なら生成前サマリーにも⚠️。配線は _pl_effective_hook が1源(人の選択>magtext既定>特集既定=📖未実行の回帰経路)。stale対策=autoカバー署名に表紙コピー/マストヘッド表記を含め自動追従(無課金)＋PNGに焼いたコピーを記録し手動カバーの食い違いを _pl_cover_stale で検知(📖/🖼️/生成前サマリーの3箇所に警告・サマリーではNG扱い)。死にコード削除=app._pl_cover_ai_cb/_pl_cover_clean_copy・core.draft_cover_copy・rtv.build_cover_magazine。fal不要でローカル検証済。以下 issue-v1: ★ISSUE番号のUI化＋エリア自動化＝マストヘッド2行目のハードコード『ISSUE 01 / OSAKA・FUKUSHIMA』を廃止。★主目的=エリア誤表示の事故対策(福島固定のため西区/九条/本町の物件で事実と異なるエリアが表紙・全ビート・DATA面の3面に焼き込まれていた)。生成ロジックは core.magazine_issue_line に一本化(_AREA_ROMAJI＝西区/ドーム前導線を追加・エリア決定順=手入力>マイソク代表駅(_sns_access_pick)のローマ字>空・★取れないときに既定エリアを騙らず ISSUE 03 単独へフォールバック・未知駅はローマ字を推測せず日本語のまま)。描画=_v79_masthead(issue_text)＋fit-to-width(30→22・長い駅名NISHI-NAGAHORIで左右見切れゼロ)、3面(表紙:1579/ビート:1514/DATA:1625)へ配線。UI=📖内にISSUE番号/エリア手動上書き＋確定文字列プレビュー(課金前に目で確定)＋生成前サマリーに1行。★build_data_pageの死に引数area(本体未参照)を削除しissue_textへ統合。★app._pl_cover_subline/_PL_AREA_ROMAJI(呼出元ゼロの死にコード)を削除＝2源化の温床を断つ。『最初からやり直す』は号数を保持(連番運用)・エリアはクリア(物件依存)。fal不要でローカルPNG検証済。★注意=ISSUE番号/エリアはjob_id(glob)に入るため生成後の変更は別ジョブ扱い＝fal再課金。以下 autosort-v1: ★🔀部屋順の整列をデフォルト化(押し忘れ→部屋バラバラ動画の$3.15無駄を防ぐ)。画像化直後に③で1回だけ自動整列。過去のsticky事故(整列が繰り返し適用され手動順を上書き)対策の3条件: 条件1(一回限り)=pl_autosort_doneで再発火しない・条件2(手動不上書き)=_pl_moveがpl_order_manualを立て以後自動整列しない・条件3(明示+取消)=🔀自動整列しました通知＋「元の順に戻す」(_pl_restore_order・元順pl_order_original復元・以後手動扱い)。生成前チェックの整列=表示は状態判定のまま(自動化しても実態反映)。★条件2/3の非再現テスト済(手動↓→再実行×2→手動順保持)。fal不要。以下v79-6-data2: ★DATA面の状態を生成前サマリー(pregen-guard)に追加＝課金前にDATA面の品質を判定。_pl_data_summary()=DATA面 ON/OFF・行数(build_data_rows充足率)・間取り図あり/なし(pl_floorplan)・行少なめ⚠️。生成前サマリー＋📖直後の両方に表示(情報源1箇所)。$3.15を払う前にDATA面がスカスカにならないか/間取り図がAで取れたかが分かる。以下v79-6-data: ★DATA面(動く雑誌の最終ページ)を本編末尾に追加=masthead＋DATA見出し＋間取り図(pl_floorplan)＋スペック表(金ラベル/白値・罫線・fit-to-width)＋注記。静止3.5s・ナレなし・BGM継続。core.build_data_rows(取れない行は省略・方角はマイソク明記時のみ「建物」行・否定facts除外(fact_negated)・生値寸法strip)＋data_note_date(マイソク日付優先→生成日)。rtv.build_data_page(間取り図無しは表を上に詰めてDATA面は必ず出す=silent drop禁止・取得ログ)。run_tour_jobが_cover_clip+_prepend_clipで末尾連結。fal不要。以下ui-stepgate-v1: ★📖文字面生成を④動画化の必須前提として強制(誤課金防止)。修正1=文字面(pl_mag/big_text)空だと🎬ルームツアー生成ボタンをdisabled＋理由caption。修正2=生成前サマリーの最上段に文字面状態(✅生成済Nビート/🔴⚠️未生成)を追加・未生成でボタン封鎖と連動(_pl_pregen_summaryが3-tuple:md,ng,mag_ready)。修正3=ステップバーに📖文字面を追加(①取込→②画像化→③整列・確認→📖文字面→④動画化・現在地太字/未完了グレー/📖未実行は赤⚠️)。修正4=📖expanderを未生成時に自動展開＋タイトル🔴【必須】/生成済✅・ボタンprimary。fal不要でUI検証完結。以下magfit-v79c: ★①②Klingモーション幻覚修正=外観/玄関のカメラワークを静止〜微パンに制約(外観motion=minimal・扉/窓/シャッター開閉変形禁止・人物出現禁止・扉の先の別空間生成禁止)＋_V79_NEGATIVE強化(no opening doors/no people appearing等)。③白サブ文言(comment/ナレ)を動画描画から非表示(build_beat_overlayがcomment描かない・音声TTSは別経路で残す)＋big_text中心≈1470に縦センタリング。④外観/トイレの金色見出しをfact_scrub空化時に方角非依存の安全フォールバックで復活(core._safe_big_fallback:外観=建物種別+階数/トイレ=独立/玄関=シューズ/洗面=独立洗面台/汎用=部屋名・空にしない)。fact_scrubは不変。以下magfit-v79b: ★magfit-v79のリグレッション修正=big_text空化で一部シーンのテキスト層(masthead+tag+金色見出し+サブ)がまるごと消える問題。真因=big_text空→overlay loopの旧スキップ(if not big_text:continue)でoverlay全体を捨てていた(既存機構・fact_scrubで方角等の全節除去→空化が引き金)。修正=①overlay loopはbig_text空でもmasthead/tag/comment描画(真に空のビートだけスキップ)②build_beat_overlay/_v79_fit_fontを例外安全化(getattr size・fit失敗は基準サイズ・非空lines)③per-beat try/except(1ビート失敗で全落ちしない)④magtextでbig_text空化を警告(元テキスト+理由)⑤_rewrite_unnatural空返し防御⑥発火ゲートをbig_text or commentに⑦reading_dict朝→あさ。以下magfit-v79: ★金色スペック行の横幅fit未実装による左右見切れ2件＋不自然文言1件を修正。修正1(描画)=表紙DATAストリップ(_v79_infobar spec行)にfit-to-width(38→26縮小・下限で｜折返し2行・max_w=W-120=左右60px)。修正2(描画)=beat big_text(金色見出し)を_v79_fit_font(96→56縮小・下限で幅折返し・動的y)＝物件により語数が変わっても見切れゼロ。修正3(生成)=magtextの不自然表現ガード『床が余る』系→『余裕がある』系(_rewrite_unnatural・big/comment両方・プロンプト明記・書換はcomment改変=kana stale不採用)。以下pregen-guard: ★$3.15誤爆・巻き戻り対策。①『最初からやり直す』を2段確認(1クリック目は武装のみ・はいで初めて全消し＝再描画ずれの誤爆で①巻き戻り+画像化再課金を防ぐ)。②生成ボタン直上に生成前チェック表示=特集／整列済否(room_tour_rank昇順か)／文字面(🈶kana採用N/M)／⚠️辞書読みK件(部屋名・kana-reasonと同一情報源=pl_magのnarration_kana有無)／カット数・概算$。NG項目は黄色警告。以下kana-reason: ★kana不採用/崩れの切り分けを per-beat 可視化＝magtext warningsに『🈚 部屋: 未出力／不採用(漢字残存N字/長さ乖離/comment改変)』を出す(core._kana_reject_reason)。どのビートがなぜkana不発かが実機で即判明(①採用/フォールバック ②Gemini未出力 ③ガード条件を1発切り分け)。reading_dictに帰宅→きたく追加(採用kanaの残存漢字もnormalize_readingで補正=二段の網)。以下narrkana-diag: ★narration_kana誤読残存(着く→とどく等)の原因特定＝📖生成メッセージにkana採用率『🈶採用 N/Mビート』を表示(0/N=kana不発→Gemini未出力/ガード過剰弾き/未デプロイを疑う)。副次=プロンプトのnarration_kana指示に助詞は→わ/へ→え・英字数字単位のひらがな展開(LDK→えるでぃーけー)を明記／reading_dictに着く/落ち着く/入浴剤(フォールバック用)。以下comment-wrap: commentの画面幅見切れ修正2点。描画側=build_beat_overlayが_v79_wrap_widthでcommentを描画幅(W-180)最大2行に折返し(句読点優先・フォント42固定)。生成側=magtextのcommentを全角24字以内・1文に(プロンプト＋後処理_first_sentenceで2文以上は第1文のみ・警告)。★1文化はcomment改変なのでnarration_kanaはstale→不採用(辞書フォールバック・narr-fix-d 4条件目と整合)。以下narrfix-d: ★漢字誤読クラスを根絶＝magtextが narration_kana（commentの全ひらがな読み・Geminiの文脈読み・数字/英字/単位も日本語読み展開）を1コール内で出力し、TTSはこれを読む。reading_dict辞書はフォールバックへ降格。★ガード=narration_kanaは①非空②ほぼ仮名(漢字1割以下)③commentと長さ乖離なし④commentが後処理(fact_scrub/ban/否定)で改変されていない、を満たすときだけ採用。外れたら黙らず警告＋normalize_reading(comment)=辞書経路へフォールバック(『かなを読んだつもりで漢字を読む』を構造的に防ぐ)。TTSは normalize_reading(kana or comment)＝かな採用時も辞書を通し残存漢字を補正。配線=magtext→pl_mag→scene→_pl_assign_story_beats(grp[0].beat_narration_kana)→run_tour_job(measure-first/fallback両方でkana優先)。measure-firstのタイミング機構は不変(narr_actualはkana音声から_adur測定)。実聴(漢字読みvsかな読みのイントネーション)はCOO実機→谷合さん判定。以下e2e-bugfix: ナレありE2E(被りゼロ=measure-first成功)後の修正3件。★bug①(景表法ブロッカー)=否定文脈付きfacts(駐輪場満車・駐輪厳禁)を全経路で除外=core.fact_negated(強マーカー満車/厳禁/不可等は近接8字・弱マーカーなしは近接3字・無料は否定にしない)＋_drop_neg_clauses、magtextのタグ/プロンプト/big_text/comment全経路でk not in _negated＋警告。★bug②=表紙情報バーの生寸法10x6再出現→_strip_raw_dimをmadori/area/tag全部に適用(1LDK 10x6/area/tag経由も塞ぐ)。★誤読7語をreading_dict.jsonに追記(靴くつ/広々ひろびろ/今日きょう/一日いちにち/洗ってあらって/湯船ゆぶね/浸かってつかって)＝narr-fix-d(narration_kana)までのフォールバック。以下narrfix-c: ★ふりがな辞書（誤読補正）をデータ駆動化＝reading_dict.json（{表記:読み}）を core._READ_TABLE へマージ（最長一致保持）。ElevenLabsの誤読を1行足すだけで直せる（コード変更不要・__で始まるキーは注記スキップ・無い/壊れ→空でフェイルセーフ）。初期語=v78実績誤読（来たか→きたか・洗面台）＋部屋名/設備名の音読み事故（給湯・洗面所・玄関・納戸・独立洗面台）。読みの実効はCOO実機。以下narr-fix-b: ★ナレ音声の実尺を測ってから映像尺を決める（予測係数5.26に依存しない）＝measure-first。run_tour_job順序変更(narration ON＋beatモードのみ): フル正規化seg(無trim)→全comment TTS→実尺測定→d_i=max(MIN_BEAT4.0, narr+TAIL0.5)→segfitへtrim/末尾フリーズ延長→組立→overlay窓/ナレ開始/総尺は Σd_i を共有(逐次=被り0)。ナレ>素材尺はフリーズ延長(>2.5s警告)。★フォールバック=measure-first失敗時は予測trim旧経路へ+警告『旧経路で生成』を明示(黙って落ちない)。★_dur は動画v:0選択で音声を測れず5.0s固定を返す穴を発見→_adur(format=duration)追加(既存CPSログの穴も解消)。narration OFF/非beatは完全回帰。検証: 実run_tour_job(still+モックサイン波)で silencedetect 重なり0ms/はみ出し0ms/総尺一致/検証表/フリーズ警告/フォールバック明示/回帰。実TTS実尺はCOO実機。以下magtext: ★ナレは comment のみ読む（big_textは特大文字で視聴者が読む・声はコメントを添えるだけ）＝magtext narration_text=comment。発話量半減で音声被りの主因が消える。comment空＝ナレ無ビート。★注意: これ単体だとbeat_narr_sec(字数由来の映像尺)が短くなる＝MIN_BEATフロアはnarr-fix-bで入る（a↔b間は本番E2Eを回さない）。以下v79-5b本体: ★ナレOFF回で文字面を生成できない配線ミスを修正＝📖動く雑誌の文字生成を if v_narr_on 外の独立expanderへ移動＋ElevenLabsゲート(disabled=not _narr_ok)除去(文字面はGemini生成でナレ非依存)。ナレOFF経路検証済(narration空でもbig_text注入・ビート割当・overlay成立)＝1本目BGMのみE2Eが回る。物件名自動挿入監査済(既定で挿入なし・冒頭フラッシュは既定OFF・表紙コピーは物件名を明示除去)。以下v79-5b本体: magtext配線+文字面overlay合成。①build_beat_overlay=big_textをaccent_wordで白/accent2行分割+comment+タグ最大3ピル(左余白・金バー)+room_pill(表示名)+マストヘッド+情報バー(透明PNG)。②run_tour_job=big_text保持時に各ビートの文字面PNGを時間窓overlay合成(_burn_beat_overlays・1パス・ビート開始=cover_off+Σbeat_narr_sec)＝v78字幕焼きの代替(背景Kling+文字主役)。③app=📖動く雑誌の文字を生成(特集ベース・core.magtext)→pl_mag_先頭id(room_label/big_text/accent/comment/tags)+pl_narr=narration_text(画面の文字を読む)→scene注入→glob v79_accent。needs_review=型承認ゲート集約表示。★_pl_assign_story_beats堅牢化(短big_text×同室多枚でlen(cuts)<stock→全画像を背景B-rollとしてnsec内均等配置・crash防止・描画尺==nsec維持)。ローカルframe/overlay時間窓/統合seam検証済。実Gemini品質+フルE2E(fal)はCOO実機。前=v79-5a)")
