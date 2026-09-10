package extractor

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"

	readability "codeberg.org/readeck/go-readability/v2"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/domain"
	"golang.org/x/net/html"
)

type Extractor struct{ MinWordCount int }

func (e Extractor) Extract(body []byte, sourceURL, finalURL, robotsStatus string) (domain.Article, error) {
	pageURL, err := url.Parse(finalURL)
	if err != nil {
		return domain.Article{}, fmt.Errorf("final URL tidak valid")
	}
	doc, err := html.Parse(bytes.NewReader(body))
	if err != nil {
		return domain.Article{}, fmt.Errorf("HTML tidak valid")
	}
	structured := findArticleJSONLD(doc)
	readable, readErr := readability.FromDocument(doc, pageURL)
	var text, title, byline, site, description, image string
	var readabilityPublished *time.Time
	if readErr == nil {
		var b strings.Builder
		if readable.RenderText(&b) == nil {
			text = clean(b.String())
		}
		title = readable.Title()
		byline = readable.Byline()
		site = readable.SiteName()
		description = readable.Excerpt()
		image = readable.ImageURL()
		if stamp, stampErr := readable.PublishedTime(); stampErr == nil {
			readabilityPublished = &stamp
		}
	}
	if text == "" {
		text = clean(asString(structured["articleBody"]))
	}
	words := wordCount(text)
	if words < e.MinWordCount {
		return domain.Article{}, fmt.Errorf("Isi artikel terlalu pendek: %d kata; minimal %d", words, e.MinWordCount)
	}
	title = first(asString(structured["headline"]), title, meta(doc, "property", "og:title"), elementText(findElement(doc, "title", "", "")))
	if title == "" {
		return domain.Article{}, fmt.Errorf("Judul artikel tidak ditemukan")
	}
	canonical := first(attr(findElement(doc, "link", "rel", "canonical"), "href"), mainEntity(structured["mainEntityOfPage"]), asString(structured["url"]), finalURL)
	canonical = resolve(pageURL, canonical)
	author := first(authorName(structured["author"]), byline, meta(doc, "name", "author"))
	publisher := publisherName(structured["publisher"])
	pub := articleTime(doc, structured, false, readabilityPublished)
	mod := articleTime(doc, structured, true, nil)
	img := first(imageValue(structured["image"]), meta(doc, "property", "og:image"), image)
	if img != "" {
		img = resolve(pageURL, img)
	}
	hash := sha256.Sum256([]byte(text))
	host := ""
	if u, parseErr := url.Parse(canonical); parseErr == nil {
		host = u.Hostname()
	}
	now := time.Now().UTC()
	return domain.Article{Status: "success", SourceURL: sourceURL, FinalURL: finalURL, CanonicalURL: canonical, Domain: host, Title: title, Author: optional(author), PublishedAt: pub.Raw, ModifiedAt: mod.Raw, PublicationTime: pub, ModificationTime: mod, Source: optional(first(publisher, site, meta(doc, "property", "og:site_name"))), Section: optional(section(structured["articleSection"])), Description: optional(first(asString(structured["description"]), description, meta(doc, "property", "og:description"))), ImageURL: optional(img), Content: text, WordCount: words, ContentHash: hex.EncodeToString(hash[:]), RobotsStatus: robotsStatus, FetchedAt: now}, nil
}

func articleTime(doc *html.Node, data map[string]any, modified bool, readabilityTime *time.Time) domain.ArticleTime {
	field := "datePublished"
	prop := "article:published_time"
	if modified {
		field = "dateModified"
		prop = "article:modified_time"
	}
	values := [][2]string{{asString(data[field]), "json_ld." + field}, {meta(doc, "property", prop), "meta." + prop}, {meta(doc, "name", prop), "meta." + prop}}
	if n := findElement(doc, "", "itemprop", field); n != nil {
		values = append(values, [2]string{first(attr(n, "content"), attr(n, "datetime"), elementText(n)), "itemprop." + field})
	}
	fallback := ParseArticleTime(nil, nil)
	for _, v := range values {
		if v[0] == "" {
			continue
		}
		parsed := ParseArticleTime(optional(v[0]), optional(v[1]))
		if fallback.Raw == nil {
			fallback = parsed
		}
		if parsed.Date != nil {
			return parsed
		}
	}
	if readabilityTime != nil {
		raw := readabilityTime.Format(time.RFC3339Nano)
		source := "readability.date"
		return ParseArticleTime(&raw, &source)
	}
	return fallback
}
func findArticleJSONLD(doc *html.Node) map[string]any {
	var found map[string]any
	walk(doc, func(n *html.Node) {
		if found != nil || n.Type != html.ElementNode || n.Data != "script" || strings.ToLower(attr(n, "type")) != "application/ld+json" {
			return
		}
		var value any
		if json.Unmarshal([]byte(elementText(n)), &value) == nil {
			visitJSON(value, func(m map[string]any) {
				if found != nil {
					return
				}
				types := []string{asString(m["@type"])}
				if a, ok := m["@type"].([]any); ok {
					types = nil
					for _, v := range a {
						types = append(types, asString(v))
					}
				}
				for _, typ := range types {
					if typ == "NewsArticle" || typ == "Article" || typ == "ReportageNewsArticle" {
						found = m
						return
					}
				}
			})
		}
	})
	if found == nil {
		return map[string]any{}
	}
	return found
}
func visitJSON(v any, fn func(map[string]any)) {
	switch x := v.(type) {
	case map[string]any:
		fn(x)
		for _, v := range x {
			visitJSON(v, fn)
		}
	case []any:
		for _, v := range x {
			visitJSON(v, fn)
		}
	}
}
func walk(n *html.Node, fn func(*html.Node)) {
	fn(n)
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		walk(c, fn)
	}
}
func findElement(doc *html.Node, tag, key, value string) *html.Node {
	var out *html.Node
	walk(doc, func(n *html.Node) {
		if out != nil || n.Type != html.ElementNode {
			return
		}
		if tag != "" && n.Data != tag {
			return
		}
		if key != "" && attr(n, key) != value {
			return
		}
		out = n
	})
	return out
}
func meta(doc *html.Node, key, value string) string {
	n := findElement(doc, "meta", key, value)
	return attr(n, "content")
}
func attr(n *html.Node, key string) string {
	if n == nil {
		return ""
	}
	for _, a := range n.Attr {
		if strings.EqualFold(a.Key, key) {
			return strings.TrimSpace(a.Val)
		}
	}
	return ""
}
func elementText(n *html.Node) string {
	if n == nil {
		return ""
	}
	var b strings.Builder
	walk(n, func(x *html.Node) {
		if x.Type == html.TextNode {
			b.WriteString(x.Data)
		}
	})
	return strings.TrimSpace(b.String())
}
func clean(s string) string {
	lines := strings.Split(s, "\n")
	out := make([]string, 0, len(lines))
	space := regexp.MustCompile(`\s+`)
	for _, line := range lines {
		if v := strings.TrimSpace(space.ReplaceAllString(line, " ")); v != "" {
			out = append(out, v)
		}
	}
	return strings.Join(out, "\n\n")
}
func wordCount(s string) int {
	count := 0
	in := false
	for _, r := range s {
		is := unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_'
		if is && !in {
			count++
		}
		in = is
	}
	return count
}
func first(v ...string) string {
	for _, s := range v {
		if strings.TrimSpace(s) != "" {
			return strings.TrimSpace(s)
		}
	}
	return ""
}
func optional(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
func asString(v any) string {
	switch x := v.(type) {
	case string:
		return strings.TrimSpace(x)
	case float64:
		return fmt.Sprint(x)
	}
	return ""
}
func resolve(base *url.URL, raw string) string {
	u, err := base.Parse(raw)
	if err != nil {
		return base.String()
	}
	return u.String()
}
func mainEntity(v any) string {
	if m, ok := v.(map[string]any); ok {
		return asString(m["@id"])
	}
	return asString(v)
}
func publisherName(v any) string {
	if m, ok := v.(map[string]any); ok {
		return asString(m["name"])
	}
	return ""
}
func authorName(v any) string {
	switch x := v.(type) {
	case map[string]any:
		return asString(x["name"])
	case []any:
		var names []string
		for _, v := range x {
			if n := authorName(v); n != "" {
				names = append(names, n)
			}
		}
		return strings.Join(names, ", ")
	default:
		return asString(v)
	}
}
func imageValue(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case map[string]any:
		return first(asString(x["url"]), asString(x["contentUrl"]))
	case []any:
		if len(x) > 0 {
			return imageValue(x[0])
		}
	}
	return ""
}
func section(v any) string {
	if a, ok := v.([]any); ok {
		var out []string
		for _, v := range a {
			if s := asString(v); s != "" {
				out = append(out, s)
			}
		}
		return strings.Join(out, ", ")
	}
	return asString(v)
}
