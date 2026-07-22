# 放課後等デイサービス比較ガイド

放課後等デイサービス・児童発達支援の事業所を探す保護者向けの紹介サイトです。
[kurashi-guide](../kurashi-guide) と同じ「毎晩AIがコラム記事を自動執筆する」仕組みをベースに、
事業所紹介ページを追加した構成になっています。

```
毎晩 深夜2時
  → GitHub Actions が自動起動（PC不要・無料）
  → Claude API が「選び方ガイド」コラムを数本"執筆"（有料・1記事 約0.06ドル）
  → docs/ に静的サイトを再生成（コラム記事 + 事業所ページ）
  → GitHub に自動コミット → GitHub Pages で公開
```

事業所ページは自動生成しません。実際にヒアリング・見学した事業所の情報を
`data/facilities.json` に人力で追記する運用です（AIに嘘の事業所情報を書かせないため）。

---

## 仕組み（ファイルの役割）

| 場所 | 役割 |
|------|------|
| `data/keywords.json` | コラムで書きたい検索キーワードのリスト |
| `data/facilities.json` | 掲載する事業所の情報（人力で追記・編集） |
| `data/config.json` | サイト名・URL・免責文などの設定 |
| `generate.py` | 本体。コラムをAIに書かせつつ、事業所ページも含めてサイトを生成 |
| `articles/` | 生成されたコラム記事データ（元データ） |
| `docs/` | 公開される静的サイト（GitHub Pagesがここを配信） |
| `.github/workflows/nightly.yml` | 毎晩の自動実行設定（コラムのみ。事業所ページは追記のたびに手動で `--build-only` を実行） |

---

## はじめての準備（順番にやればOK）

### 0. まず手元で"見た目"を確認（APIキー不要・無料）

`! python projects/houkago-day-guide/generate.py --demo`

→ サンプルのコラム記事が1本でき、`docs/` に事業所サンプル(【サンプル】〇〇放課後等デイサービス)も
含めてサイトが作られます。エクスプローラーで
`projects/houkago-day-guide/docs/index.html` をダブルクリックするとブラウザで確認できます。

> 確認できたら、`articles/post-001.json` を削除し、`data/keywords.json` の
> id:1 の `"status"` を `"todo"` に戻しておきましょう（サンプルを消して本番に備える）。
> `data/facilities.json` のサンプル事業所も、実際の事業所情報が集まったら置き換えてください。

### 1. Anthropic（Claude）のAPIキーを取得

kurashi-guide で取得済みのキーを使い回せます（新規取得は不要）。

### 2. GitHubにリポジトリを作って push

このフォルダ（`houkago-day-guide`）を新しいGitHubリポジトリとして公開します。
詳しいコマンドは別途ご案内します。

### 3. APIキーをGitHubに登録（コードに直接書かない）

GitHubのリポジトリ画面で:
`Settings` → `Secrets and variables` → `Actions` → `New repository secret`
- Name: `ANTHROPIC_API_KEY`
- Secret: `sk-ant-...`

### 4. GitHub Pages を有効化

`Settings` → `Pages` →
- Source: `Deploy from a branch`
- Branch: `main` / フォルダ `/docs` を選んで Save

数分後、`https://（ユーザー名）.github.io/houkago-day-guide/` で公開されます。

### 5. サイトURLを設定に反映

`data/config.json` の `site_url` を、上で決まった公開URLに書き換えて push。

### 6. 動作テスト

GitHubの `Actions` タブ → 「夜間に記事を自動生成」→ `Run workflow` を手動実行。
緑のチェックがつき、記事が増えれば成功です。あとは毎晩自動で動きます。

---

## 事業所を追加するには

`data/facilities.json` に1件分のオブジェクトを追記して、
`python generate.py --build-only` を実行するとサイトに反映されます（API不要・無料）。

```json
{
  "id": 2,
  "slug": "facility-002",
  "status": "published",
  "name": "事業所名",
  "area": "都道府県 市区町村",
  "address": "住所",
  "phone": "電話番号",
  "hours": "営業時間",
  "target_age": "対象年齢",
  "disability_types": ["発達障害"],
  "transport": true,
  "features": "事業所の特徴（ヒアリング内容をもとに記述。飾らず事実ベースで）",
  "website": "",
  "updated": "2026-07-22"
}
```

`status` を `"draft"` にすると、サイトには表示されず下書きのまま保持できます
（公開前の内容確認フローに使えます）。`id`・`slug` は既存と重複しない番号にしてください。

## コラムのネタを増やすには

`data/keywords.json` に追記するだけ:

```json
{ "id": 11, "query": "利用者負担が軽減される制度", "status": "todo" }
```

idは重複しない番号にしてください。`status` は必ず `"todo"`。

## コストの調整

`data/config.json` の `articles_per_run`（1晩の本数）で調整。
- `model` を `"claude-haiku-4-5"` にすると最安（品質はやや下がる）
- `"claude-opus-4-8"` にすると最高品質（コスト高め）
