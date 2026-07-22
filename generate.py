#!/usr/bin/env python3
"""
放課後等デイサービス比較ガイド ― 夜間自動記事生成 + 事業所ページ生成エンジン

kurashi-guide と同じ仕組みをベースに、以下を追加している:
  - 事業所紹介ページ(facilities.json を人力で編集 → 静的ページ化)
  - 事業所向けの無料掲載案内ページ(about-listing.html、固定文)

コラム記事は毎晩 GitHub Actions から呼び出され、未執筆のキーワードを
Claude に執筆させる。事業所データは自動生成しない(実地ヒアリングで集めた
生データを facilities.json に人力で追記する運用)。

使い方:
  python generate.py            # 本番(Claude APIでコラム執筆)。ANTHROPIC_API_KEY が必要
  python generate.py --demo     # APIを使わずサンプル記事を1本作る(動作確認用・無料)
  python generate.py --build-only  # 既存データからサイトだけ作り直す(コラムAPI呼び出しなし)
"""

import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
ARTICLES = BASE / "articles"
DOCS = BASE / "docs"

# ---- 記事の構造をAIに守らせるためのスキーマ ----
ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "body_html": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["name", "text"],
                "additionalProperties": False,
            },
        },
        "faqs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "a": {"type": "string"},
                },
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "description", "body_html", "tags", "steps", "faqs"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "あなたは、放課後等デイサービスや児童発達支援について書く日本の福祉情報メディアの"
    "編集ライターです。読者は『子どもの通所先を探している保護者』。"
    "検索して来た人が、記事を読み終えたら次に何をすればよいか分かるように、"
    "やさしく具体的に書きます。\n"
    "ルール:\n"
    "- 事実に基づき、断定的な医療・診断・支援方針の判断はしない。"
    "必要な場合は必ず「事業所や自治体・専門家に相談してください」と促す。\n"
    "- 制度や金額に触れる場合は「自治体や事業所によって異なる場合がある」と明記する。\n"
    "- 文章は丁寧語。1記事1500〜2500文字程度。\n"
    "- body_html は <h2><h3><p><ul><li><ol> のみで構成。"
    "導入→説明→チェックポイント→まとめ、の流れを意識する。\n"
    "- title は32文字以内で検索キーワードを含める。description は記事要約120文字程度。\n"
    "- steps には本文のチェックポイントや手順を3〜7個、name(短く)とtext(具体的な説明・1〜2文)で入れる。\n"
    "- faqs には保護者がよく検索する疑問を2〜4個、q(質問)とa(80〜150文字の回答)で入れる。"
)


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_with_claude(query, model):
    """Claude APIで1記事ぶんのデータを作って返す。"""
    import anthropic

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    user_prompt = (
        f"検索キーワード「{query}」で訪れた保護者に向けた、実用的な解説記事を書いてください。"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": {"type": "json_schema", "schema": ARTICLE_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def demo_article(query):
    """API無しの動作確認用。固定文のサンプル記事。"""
    return {
        "title": f"{query}【サンプル記事】",
        "description": f"これは {query} のサンプル記事です。動作確認のために自動生成されました。",
        "body_html": (
            "<h2>はじめに</h2>"
            "<p>これは Claude API を使わずに作成したサンプル記事です。"
            "サイトの見た目や仕組みを確認するために表示しています。</p>"
            "<h2>本番では</h2>"
            "<p>ANTHROPIC_API_KEY を設定して <code>python generate.py</code> を実行すると、"
            "ここに実際の解説記事が自動で書き込まれます。</p>"
            "<h2>まとめ</h2>"
            "<p>仕組みが動いていれば成功です。次は本番モードを試してみましょう。</p>"
        ),
        "tags": ["サンプル"],
        "steps": [
            {"name": "準備する", "text": "必要な情報をそろえます。"},
            {"name": "確認する", "text": "手順どおりに確認します。"},
            {"name": "相談する", "text": "不明点は事業所や自治体に相談します。"},
        ],
        "faqs": [
            {"q": "これはサンプルですか？", "a": "はい。動作確認用の固定サンプル記事です。"},
        ],
    }


def build_article_record(kw, content):
    today = datetime.date.today().isoformat()
    slug = f"post-{kw['id']:03d}"
    return {
        "id": kw["id"],
        "slug": slug,
        "query": kw["query"],
        "title": content["title"],
        "description": content["description"],
        "body_html": content["body_html"],
        "tags": content.get("tags", []),
        "steps": content.get("steps", []),
        "faqs": content.get("faqs", []),
        "date": today,
    }


# ----------------- サイト生成 -----------------

def jsonld(obj):
    """構造化データを<script>タグ文字列にして返す。"""
    return ('<script type="application/ld+json">'
            + json.dumps(obj, ensure_ascii=False)
            + "</script>\n")


def ga4_snippet(config):
    """GA4計測タグ。config.json の ga_measurement_id が空なら何も出さない。"""
    gid = config.get("ga_measurement_id", "")
    if not gid:
        return ""
    safe_gid = html.escape(gid)
    return (
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={safe_gid}"></script>\n'
        "<script>\n"
        "window.dataLayer = window.dataLayer || [];\n"
        "function gtag(){dataLayer.push(arguments);}\n"
        "gtag('js', new Date());\n"
        f"gtag('config', '{safe_gid}');\n"
        "</script>\n"
    )


def page_shell(config, title, description, inner, canonical, head_extra=""):
    site = config["site_title"]
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{html.escape(canonical)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{html.escape(site)}">
<link rel="stylesheet" href="style.css">
{ga4_snippet(config)}{head_extra}<!-- AdSense用: 審査通過後にここへ広告コードを貼る -->
</head>
<body>
<header class="site-header">
  <a class="site-title" href="index.html">{html.escape(site)}</a>
  <p class="site-tagline">{html.escape(config['site_description'])}</p>
  <nav class="site-nav">
    <a href="index.html">トップ</a>
    <a href="facilities.html">事業所を探す</a>
    <a href="articles.html">選び方ガイド</a>
    <a href="about-listing.html">無料掲載について</a>
  </nav>
</header>
<main class="container">
{inner}
</main>
<footer class="site-footer">
  <p class="disclaimer">{html.escape(config.get('disclaimer', ''))}</p>
  <p>&copy; {datetime.date.today().year} {html.escape(site)}</p>
</footer>
</body>
</html>
"""


def related_articles(art, arts, limit=4):
    """同じタグを多く共有する記事を優先し、足りなければ新着で補う。"""
    others = [a for a in arts if a["slug"] != art["slug"]]
    my_tags = set(art.get("tags", []))

    def score(a):
        return len(my_tags & set(a.get("tags", [])))

    others.sort(key=lambda a: (score(a), a["date"], a["id"]), reverse=True)
    return others[:limit]


def render_faq_section(faqs):
    if not faqs:
        return ""
    items = "".join(
        f"<details class='faq-item'><summary>{html.escape(f['q'])}</summary>"
        f"<p>{html.escape(f['a'])}</p></details>"
        for f in faqs
    )
    return f"<section class='faq'><h2>よくある質問</h2>{items}</section>"


def render_related_section(related):
    if not related:
        return ""
    links = "".join(
        f"<li><a href='{a['slug']}.html'>{html.escape(a['title'])}</a></li>"
        for a in related
    )
    return f"<nav class='related'><h2>あわせて読みたい</h2><ul>{links}</ul></nav>"


def article_structured_data(config, art, url):
    """記事ページに埋め込む構造化データ(Article/HowTo/FAQ)をまとめて返す。"""
    site = config["site_title"]
    blocks = [jsonld({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": art["title"],
        "description": art["description"],
        "datePublished": art["date"],
        "dateModified": art["date"],
        "author": {"@type": "Organization", "name": config.get("author", site)},
        "publisher": {"@type": "Organization", "name": site},
        "mainEntityOfPage": url,
        "inLanguage": "ja",
    })]
    steps = art.get("steps") or []
    if steps:
        blocks.append(jsonld({
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": art["title"],
            "description": art["description"],
            "step": [
                {"@type": "HowToStep", "position": i + 1,
                 "name": s["name"], "text": s["text"]}
                for i, s in enumerate(steps)
            ],
        }))
    faqs = art.get("faqs") or []
    if faqs:
        blocks.append(jsonld({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["q"],
                 "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                for f in faqs
            ],
        }))
    return "".join(blocks)


def render_article_page(config, art, arts):
    url = f"{config['site_url']}/{art['slug']}.html"
    tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in art["tags"])
    faq_html = render_faq_section(art.get("faqs"))
    related_html = render_related_section(related_articles(art, arts))
    inner = f"""
<article>
  <p class="crumb"><a href="articles.html">選び方ガイド</a> ＞ 記事</p>
  <h1>{html.escape(art['title'])}</h1>
  <p class="meta">公開日: {art['date']}</p>
  <div class="tags">{tags}</div>
  <div class="article-body">
  {art['body_html']}
  </div>
  {faq_html}
  {related_html}
  <p class="back"><a href="articles.html">← 選び方ガイド一覧へ戻る</a></p>
</article>
"""
    head_extra = article_structured_data(config, art, url)
    return page_shell(config, art["title"], art["description"], inner, url, head_extra)


def render_articles_index(config, arts):
    items = ""
    for a in sorted(arts, key=lambda x: (x["date"], x["id"]), reverse=True):
        items += f"""
  <li class="card">
    <a href="{a['slug']}.html">
      <span class="card-title">{html.escape(a['title'])}</span>
      <span class="card-desc">{html.escape(a['description'])}</span>
      <span class="card-date">{a['date']}</span>
    </a>
  </li>"""
    inner = f"""
<h1 class="index-h1">選び方ガイド</h1>
<p class="index-lead">放課後等デイサービス・児童発達支援を選ぶときに役立つ解説記事です。毎晩少しずつ増えていきます。</p>
<ul class="card-list">{items}
</ul>
<p class="count">現在 {len(arts)} 記事を公開中（毎晩自動更新）</p>
"""
    return page_shell(config, f"選び方ガイド | {config['site_title']}",
                      "放課後等デイサービスの選び方や制度について解説する記事の一覧です。",
                      inner, config["site_url"] + "/articles.html")


def facility_structured_data(config, fac, url):
    return jsonld({
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": fac["name"],
        "description": fac.get("features", ""),
        "address": fac.get("address", ""),
        "telephone": fac.get("phone", ""),
        "url": url,
    })


def render_facility_card(fac):
    types = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in fac.get("disability_types", []))
    transport = "送迎あり" if fac.get("transport") else "送迎なし"
    return f"""
  <li class="card">
    <a href="{fac['slug']}.html">
      <span class="card-title">{html.escape(fac['name'])}</span>
      <span class="card-desc">{html.escape(fac.get('area', ''))} ／ {html.escape(fac.get('target_age', ''))} ／ {transport}</span>
      <span class="card-date">更新日: {fac.get('updated', '')}</span>
    </a>
  </li>"""


def render_facilities_index(config, facs):
    items = "".join(render_facility_card(f) for f in sorted(facs, key=lambda x: x["id"]))
    inner = f"""
<h1 class="index-h1">事業所を探す</h1>
<p class="index-lead">掲載されている放課後等デイサービス・児童発達支援事業所の一覧です。詳細は各事業所のページをご覧ください。</p>
<ul class="card-list">{items}
</ul>
<p class="count">現在 {len(facs)} 事業所を掲載中</p>
<p class="count"><a href="about-listing.html">事業所の方はこちら（無料掲載のご案内）</a></p>
"""
    return page_shell(config, f"事業所を探す | {config['site_title']}",
                      "放課後等デイサービス・児童発達支援事業所の一覧ページです。",
                      inner, config["site_url"] + "/facilities.html")


def render_facility_page(config, fac):
    url = f"{config['site_url']}/{fac['slug']}.html"
    types = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in fac.get("disability_types", []))
    transport = "あり" if fac.get("transport") else "なし"
    website_row = (
        f"<tr><th>ウェブサイト</th><td><a href=\"{html.escape(fac['website'])}\">{html.escape(fac['website'])}</a></td></tr>"
        if fac.get("website") else ""
    )
    inner = f"""
<article>
  <p class="crumb"><a href="facilities.html">事業所を探す</a> ＞ 詳細</p>
  <h1>{html.escape(fac['name'])}</h1>
  <div class="tags">{types}</div>
  <table class="fac-table">
    <tr><th>エリア</th><td>{html.escape(fac.get('area', ''))}</td></tr>
    <tr><th>住所</th><td>{html.escape(fac.get('address', ''))}</td></tr>
    <tr><th>電話番号</th><td>{html.escape(fac.get('phone', ''))}</td></tr>
    <tr><th>営業時間</th><td>{html.escape(fac.get('hours', ''))}</td></tr>
    <tr><th>対象年齢</th><td>{html.escape(fac.get('target_age', ''))}</td></tr>
    <tr><th>送迎</th><td>{transport}</td></tr>
    {website_row}
  </table>
  <div class="article-body">
    <h2>事業所の特徴</h2>
    <p>{html.escape(fac.get('features', ''))}</p>
  </div>
  <p class="meta">情報更新日: {fac.get('updated', '')}</p>
  <p class="back"><a href="facilities.html">← 事業所一覧へ戻る</a></p>
</article>
"""
    head_extra = facility_structured_data(config, fac, url)
    return page_shell(config, f"{fac['name']} | {config['site_title']}",
                      fac.get("features", fac["name"]), inner, url, head_extra)


def render_index(config, arts, facs):
    fac_items = "".join(render_facility_card(f) for f in sorted(facs, key=lambda x: x["id"])[:6])
    art_items = ""
    for a in sorted(arts, key=lambda x: (x["date"], x["id"]), reverse=True)[:6]:
        art_items += f"""
  <li class="card">
    <a href="{a['slug']}.html">
      <span class="card-title">{html.escape(a['title'])}</span>
      <span class="card-desc">{html.escape(a['description'])}</span>
    </a>
  </li>"""
    inner = f"""
<h1 class="index-h1">{html.escape(config['site_title'])}</h1>
<p class="index-lead">{html.escape(config['site_description'])}</p>

<section>
  <h2 class="section-h2">事業所を探す（{len(facs)}件掲載中）</h2>
  <ul class="card-list">{fac_items}
  </ul>
  <p class="more"><a href="facilities.html">事業所一覧をすべて見る →</a></p>
</section>

<section>
  <h2 class="section-h2">選び方ガイド</h2>
  <ul class="card-list">{art_items}
  </ul>
  <p class="more"><a href="articles.html">記事をすべて見る →</a></p>
</section>
"""
    head_extra = jsonld({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": config["site_title"],
        "description": config["site_description"],
        "url": config["site_url"] + "/",
        "inLanguage": "ja",
    })
    return page_shell(config, config["site_title"], config["site_description"],
                      inner, config["site_url"] + "/", head_extra)


def render_about_listing(config):
    inner = """
<article>
  <h1>事業所の方へ ― 無料掲載のご案内</h1>
  <div class="article-body">
    <h2>掲載費用は無料です</h2>
    <p>放課後等デイサービス・児童発達支援事業所を運営されている方は、無料で掲載いただけます。
    保護者が事業所を探す際の選択肢の一つとして、事業所の情報をわかりやすく紹介します。</p>
    <h2>掲載までの流れ</h2>
    <ol>
      <li>下記の連絡先に、事業所名・エリア・特徴などをご連絡ください。</li>
      <li>内容を確認のうえ、紹介ページを作成します。</li>
      <li>公開前に内容をご確認いただき、問題なければ公開します。</li>
    </ol>
    <h2>お問い合わせ</h2>
    <p>お問い合わせ先は準備中です。決まり次第こちらに掲載します。</p>
  </div>
  <p class="back"><a href="index.html">← トップへ戻る</a></p>
</article>
"""
    return page_shell(config, f"無料掲載のご案内 | {config['site_title']}",
                      "放課後等デイサービス事業所向けの無料掲載案内ページです。",
                      inner, config["site_url"] + "/about-listing.html")


def render_sitemap(config, arts, facs):
    urls = [
        f"  <url><loc>{config['site_url']}/</loc></url>",
        f"  <url><loc>{config['site_url']}/facilities.html</loc></url>",
        f"  <url><loc>{config['site_url']}/articles.html</loc></url>",
        f"  <url><loc>{config['site_url']}/about-listing.html</loc></url>",
    ]
    for a in arts:
        urls.append(
            f"  <url><loc>{config['site_url']}/{a['slug']}.html</loc>"
            f"<lastmod>{a['date']}</lastmod></url>"
        )
    for f in facs:
        urls.append(
            f"  <url><loc>{config['site_url']}/{f['slug']}.html</loc>"
            f"<lastmod>{f.get('updated', '')}</lastmod></url>"
        )
    body = "\n".join(urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


STYLE = """:root{--bg:#f6f8fa;--ink:#2b2b2b;--accent:#2d7dc4;--card:#fff;--line:#e2e8ee}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;
background:var(--bg);color:var(--ink);line-height:1.8}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.site-header{background:#fff;border-bottom:1px solid var(--line);padding:18px 20px;text-align:center}
.site-title{font-size:1.3rem;font-weight:700;color:var(--ink)}
.site-tagline{margin:6px 0 0;font-size:.8rem;color:#777}
.site-nav{margin-top:12px;display:flex;gap:16px;justify-content:center;flex-wrap:wrap;font-size:.85rem}
.container{max-width:760px;margin:0 auto;padding:24px 18px}
.index-h1{font-size:1.5rem}.index-lead{color:#555}
.section-h2{font-size:1.15rem;border-left:5px solid var(--accent);padding-left:12px;margin-top:34px}
.card-list{list-style:none;padding:0;margin:0;display:grid;gap:14px}
.card a{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;color:var(--ink)}
.card a:hover{border-color:var(--accent);text-decoration:none}
.card-title{display:block;font-weight:700;font-size:1.05rem}
.card-desc{display:block;color:#666;font-size:.85rem;margin:6px 0}
.card-date{display:block;color:#aaa;font-size:.75rem}
.more{margin-top:10px;font-size:.85rem}
.count{color:#999;font-size:.8rem;text-align:center;margin-top:16px}
article h1{font-size:1.5rem;line-height:1.4}
.crumb{font-size:.8rem;color:#999}.meta{color:#999;font-size:.8rem}
.tags{margin:8px 0 20px}.tag{display:inline-block;background:#e7f0f8;color:#2d5f8a;
font-size:.72rem;padding:3px 8px;border-radius:20px;margin-right:6px}
.fac-table{width:100%;border-collapse:collapse;margin:16px 0}
.fac-table th{text-align:left;color:#666;font-size:.85rem;padding:8px 12px 8px 0;width:110px;vertical-align:top}
.fac-table td{padding:8px 0;border-bottom:1px solid var(--line)}
.article-body h2{border-left:5px solid var(--accent);padding-left:12px;margin-top:34px;font-size:1.2rem}
.article-body h3{margin-top:24px;font-size:1.05rem}
.article-body ul,.article-body ol{padding-left:1.4em}
.back{margin-top:40px}
.faq{margin-top:40px}.faq h2{border-left:5px solid var(--accent);padding-left:12px;font-size:1.2rem}
.faq-item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:10px 0}
.faq-item summary{font-weight:700;cursor:pointer}
.faq-item p{margin:10px 0 0;color:#555}
.related{margin-top:40px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.related h2{font-size:1.1rem;margin-top:0}
.related ul{margin:0;padding-left:1.2em}.related li{margin:6px 0}
.site-footer{border-top:1px solid var(--line);padding:24px 18px;text-align:center;color:#999;font-size:.78rem}
.disclaimer{max-width:600px;margin:0 auto 10px}
"""


def build_site(config):
    arts = [load_json(p, None) for p in sorted(ARTICLES.glob("post-*.json"))]
    arts = [a for a in arts if a]

    all_facs = load_json(DATA / "facilities.json", [])
    facs = [f for f in all_facs if f.get("status") == "published"]

    DOCS.mkdir(exist_ok=True)
    (DOCS / "style.css").write_text(STYLE, encoding="utf-8")
    (DOCS / "index.html").write_text(render_index(config, arts, facs), encoding="utf-8")
    (DOCS / "articles.html").write_text(render_articles_index(config, arts), encoding="utf-8")
    (DOCS / "facilities.html").write_text(render_facilities_index(config, facs), encoding="utf-8")
    (DOCS / "about-listing.html").write_text(render_about_listing(config), encoding="utf-8")
    (DOCS / "sitemap.xml").write_text(render_sitemap(config, arts, facs), encoding="utf-8")
    (DOCS / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {config['site_url']}/sitemap.xml\n",
        encoding="utf-8")
    for a in arts:
        (DOCS / f"{a['slug']}.html").write_text(
            render_article_page(config, a, arts), encoding="utf-8")
    for f in facs:
        (DOCS / f"{f['slug']}.html").write_text(
            render_facility_page(config, f), encoding="utf-8")
    print(f"[build] サイトを生成: 記事{len(arts)}件 / 事業所{len(facs)}件")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="API無しでサンプル記事を作る")
    parser.add_argument("--build-only", action="store_true", help="サイトだけ作り直す")
    args = parser.parse_args()

    config = load_json(DATA / "config.json", {})
    ARTICLES.mkdir(parents=True, exist_ok=True)  # 空だとGitに無い場合があるので必ず用意
    if args.build_only:
        build_site(config)
        return

    keywords = load_json(DATA / "keywords.json", [])
    todo = [k for k in keywords if k.get("status") == "todo"]
    n = 1 if args.demo else config.get("articles_per_run", 3)
    targets = todo[:n]

    if not targets:
        print("[info] 未執筆のキーワードがありません。data/keywords.json に追加してください。")
        build_site(config)
        return

    for kw in targets:
        print(f"[write] 執筆中: {kw['query']}")
        try:
            content = demo_article(kw["query"]) if args.demo \
                else generate_with_claude(kw["query"], config.get("model", "claude-sonnet-4-6"))
        except Exception as e:  # noqa: BLE001
            print(f"[error] 失敗: {kw['query']} -> {e}", file=sys.stderr)
            continue
        record = build_article_record(kw, content)
        write_json(ARTICLES / f"{record['slug']}.json", record)
        kw["status"] = "done"
        print(f"[ok] 完成: {record['title']}")

    write_json(DATA / "keywords.json", keywords)
    build_site(config)
    print("[done] 完了。")


if __name__ == "__main__":
    main()
