# SNS画像量産ツール v1.1

Gemini API（Nano Banana）でプロンプト一覧から画像を一括生成する社内ツール。
教育系SNS投稿の「暮らしのイメージ」背景を、撮影なし・低コストで量産する。

**2つの使い方があります：**

| 版 | 起動 | 向き |
|---|---|---|
| 🖥️ **Web版（推奨）** | `streamlit run app.py` | 非エンジニア・選別重視。ブラウザでポチポチ |
| ⌨️ CLI版 | `python3 generate_images.py` | 大量バッチ・自動化向け |

生成ロジックは `core.py` に集約（両版で共通／将来のNext.js化でも移植可）。

---

## 🖥️ Web版の使い方（いちばんかんたん）

```bash
pip install -r requirements.txt --break-system-packages
export GEMINI_API_KEY=取得したキー     # 任意。UIのキー欄でも入力可
python3 -m streamlit run app.py
```

ブラウザが自動で開く（`http://localhost:8501`）。あとは画面で：

1. 左サイドバーでモデル・比率（4:5など）・1案あたり枚数を設定
2. 「サンプルプロンプトを読み込む」or 自分で1行1案を入力
3. 生成予定枚数と推定コストを確認 →「🎨 画像を生成」
4. ギャラリーで良いものを選び、個別 or ZIP一括でダウンロード

> スタッフに使ってもらう時：同じLANなら表示の Network URL を共有すれば他PCからもアクセス可（谷合さんのMacが起動している間）。常時アクセスが必要になったらVercel等への本番デプロイを検討。

---

> 位置づけ：初期は谷合が運用。将来エンクススタッフ向けに本番デプロイする想定で、
> 生成ロジック（`core.py`）は再利用できる形に分離してある。

---

## ☁️ 常時稼働させる（Streamlit Community Cloud・社内限定）

スタッフがいつでもブラウザから使えるよう、無料の Streamlit Community Cloud に常時デプロイする手順。**社内限定（パスワード認証）込み**。

### 手順
1. **GitHubリポジトリを作成**し、このフォルダ一式を push する
   （`.gitignore` で `secrets.toml` と `output/` は除外済み。プライベートリポ推奨）
   ```bash
   cd SNS画像量産ツール
   git init && git add . && git commit -m "init sns image tool"
   # GitHubで空リポを作成してから
   git remote add origin <あなたのリポURL>
   git push -u origin main
   ```
2. **https://share.streamlit.io** にGitHubアカウントでログイン
3. 「New app」→ リポジトリ／ブランチ／`app.py` を指定
4. **Settings > Secrets** に下記を貼り付ける（`.streamlit/secrets.toml.example` 参照）
   ```toml
   GEMINI_API_KEY = "あなたのAPIキー"
   APP_PASSWORD   = "社内で共有する合言葉"
   ```
5. Deploy → `https://〇〇.streamlit.app` の常時URLが発行される

### 社内限定の二重ガード
- **アプリ内パスワード**：`APP_PASSWORD` を設定すると、開いた人にパスワードを要求（実装済み）。
- **Streamlit Cloud の Viewer 制限**（任意・推奨）：アプリを Private にして、Settings > Sharing で許可するGoogleアカウント（メール）を指定すると、その人しか開けなくなる。パスワードと併用で堅い。

> ⚠️ `APP_PASSWORD` を空にすると認証なしで誰でも使えてしまう（＝APIキーのタダ乗り＝課金リスク）。本番では必ず設定すること。

---

## ⌨️ CLI版のセットアップ

---

## 1. 初回セットアップ（1回だけ）

### ① APIキーを取得
1. https://aistudio.google.com/apikey にアクセス（t.ryoma@pot-luck.jp でログイン）
2. 「Create API key」でキーを発行
3. **課金を有効化**しないと無料枠を超えた時点で止まる。Google Cloud側で従量課金を有効に。

> ⚠️ キーは秘密情報。人に見せない・コミットしない・チャットに貼らない。

### ② ライブラリを入れる
```bash
pip install -r requirements.txt --break-system-packages
```

### ③ キーを環境変数にセット（ターミナルごとに必要）
```bash
export GEMINI_API_KEY=取得したキー
```

---

## 2. 使い方

### まず必ず dry-run（枚数とコスト確認）
```bash
python3 generate_images.py --prompts prompts_sample.csv --dry-run
```

### 本生成
```bash
python3 generate_images.py --prompts prompts_sample.csv --out output
```
`output/` に PNG が連番で保存される。

### よく使うオプション
| オプション | 意味 | 例 |
|---|---|---|
| `--prompts` | プロンプトCSV | `--prompts my.csv` |
| `--out` | 出力フォルダ | `--out 0701投稿` |
| `--count` | 1プロンプトあたり枚数（当たりを選ぶ用） | `--count 3` |
| `--aspect` | 比率 | `4:5`(縦) `1:1`(正方) `9:16`(リール) `16:9`(横) |
| `--size` | 解像度 | `512` `1K` `2K` `4K` |
| `--model` | モデル | `gemini-2.5-flash-image`(既定/最安) `gemini-3.1-flash-image`(高品質) |
| `--max` | 総枚数の安全上限 | `--max 100` |
| `--dry-run` | 生成せず見積もりだけ | — |

### 例：1案あたり3枚出して当たりを選ぶ
```bash
python3 generate_images.py --prompts prompts_sample.csv --count 3 --out 候補 --max 100
```

---

## 3. プロンプトCSVの書き方

| 列 | 必須 | 内容 |
|---|---|---|
| `prompt` | ◯ | 生成したい画像の説明 |
| `id` | 任意 | ファイル名。無ければ連番＋自動命名 |
| `count` | 任意 | この行を何枚生成するか（CLI `--count` と掛け算） |

- 全プロンプトに「文字・ロゴなし／特定実在物件でないイメージ」の安全文言が**自動付与**される（スクリプト内 `SAFETY_SUFFIX`）。
- 比率は CSV ではなく `--aspect` で一括指定。

---

## 4. 運用ルール（線引き・重要）

- ✅ 概念・暮らし・エリアの雰囲気を表す画像として使う（教育投稿の挿絵・背景）。
- ❌ AI画像を「特定物件」のように見せない（家賃・住所・「入居者募集」を付けない）。おとり・景表法リスク。
- 全画像に **SynthID（不可視の電子透かし）** が入る（Gemini仕様）。
- 商用SNS投稿での利用可否は **Google生成AIの利用規約を最終確認**すること。
- **生成は自動／採用は人間が選別**。雑な絵をそのまま出さない（宅建業者としての信頼を守る）。

---

## 5. コスト感（参考・実費は請求で確認）

| モデル | 1枚 | 150枚/月 |
|---|---|---|
| gemini-2.5-flash-image | 約$0.039（約6円） | 約$5.9（約900円） |

※価格は変動するため公式（ai.google.dev/gemini-api/docs/pricing）で要確認。

---

## 6. 将来のWeb化メモ

- `generate_one()` をそのままサーバ側関数として流用可能。
- フォーム入力 → 生成 → ギャラリーで採用/却下 → DL、の3画面でMVP。
- 物件提案くんの Vercel/Next.js 基盤に相乗りすれば認証・ホスティングを再利用できる。
