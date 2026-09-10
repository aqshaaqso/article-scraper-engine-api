package extractor

import (
	"fmt"
	"strings"
	"testing"
)

func TestExtractsNewsArticle(t *testing.T) {
	words := make([]string, 120)
	for i := range words {
		words[i] = fmt.Sprintf("kata%d", i)
	}
	html := `<html><head><title>Fallback</title><link rel="canonical" href="https://news.example/artikel-utama"><meta property="og:image" content="/image.jpg"><script type="application/ld+json">{"@type":"NewsArticle","headline":"Judul Artikel Utama","datePublished":"2026-08-31T08:00:00+07:00","author":{"name":"Reporter Uji"},"publisher":{"name":"Media Uji"}}</script></head><body><nav>Menu</nav><article><h1>Judul Artikel Utama</h1><p>` + strings.Join(words, " ") + `</p></article><footer>Footer</footer></body></html>`
	got, err := (Extractor{MinWordCount: 80}).Extract([]byte(html), "https://news.example/link-awal", "https://news.example/artikel-utama", "allowed")
	if err != nil {
		t.Fatal(err)
	}
	if got.Title != "Judul Artikel Utama" || got.Author == nil || *got.Author != "Reporter Uji" || got.WordCount < 120 || got.ImageURL == nil || *got.ImageURL != "https://news.example/image.jpg" {
		t.Fatalf("unexpected article %#v", got)
	}
}
