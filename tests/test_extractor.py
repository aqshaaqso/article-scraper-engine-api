from article_scraper_lab.extractor import ArticleExtractor


def test_extracts_newsarticle_metadata_and_main_content() -> None:
    paragraphs = " ".join(f"kata{i}" for i in range(120))
    html = f"""
    <html>
      <head>
        <title>Fallback title</title>
        <link rel="canonical" href="https://news.example/artikel-utama">
        <meta property="og:image" content="/image.jpg">
        <script type="application/ld+json">
        {{
          "@context": "https://schema.org",
          "@type": "NewsArticle",
          "headline": "Judul Artikel Utama",
          "datePublished": "2026-08-31T08:00:00+07:00",
          "author": {{"@type": "Person", "name": "Reporter Uji"}},
          "publisher": {{"@type": "Organization", "name": "Media Uji"}}
        }}
        </script>
      </head>
      <body>
        <nav>Menu yang harus dibuang</nav>
        <article><h1>Judul Artikel Utama</h1><p>{paragraphs}</p></article>
        <footer>Footer yang harus dibuang</footer>
      </body>
    </html>
    """
    article = ArticleExtractor(min_word_count=80).extract(
        html=html,
        source_url="https://news.example/link-awal",
        final_url="https://news.example/artikel-utama",
        robots_status="allowed",
    )
    assert article.title == "Judul Artikel Utama"
    assert article.author == "Reporter Uji"
    assert article.source == "Media Uji"
    assert article.word_count >= 120
    assert article.canonical_url == "https://news.example/artikel-utama"
    assert article.image_url == "https://news.example/image.jpg"
    assert article.content_hash
