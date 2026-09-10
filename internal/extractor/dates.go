package extractor

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/domain"
)

var namedDate = regexp.MustCompile(`(?i)(\d{1,2})\s+(januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember)\s+(\d{4})(?:\s*(?:,|pukul)?\s*(\d{2})[:.](\d{2})(?::(\d{2}))?(?:\s*(WIB|WITA|WIT))?)?\s*$`)
var months = map[string]time.Month{"januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6, "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12}

func ParseArticleTime(raw *string, source *string) domain.ArticleTime {
	out := domain.ArticleTime{Raw: raw, Source: source, Precision: "unknown"}
	if raw == nil {
		return out
	}
	value := strings.TrimSpace(*raw)
	if m := namedDate.FindStringSubmatch(value); m != nil {
		day, _ := strconv.Atoi(m[1])
		year, _ := strconv.Atoi(m[3])
		value = fmt.Sprintf("%04d-%02d-%02d", year, months[strings.ToLower(m[2])], day)
		if m[4] != "" {
			value += "T" + m[4] + ":" + m[5]
			if m[6] != "" {
				value += ":" + m[6]
			}
			if m[7] != "" {
				value += map[string]string{"WIB": "+07:00", "WITA": "+08:00", "WIT": "+09:00"}[strings.ToUpper(m[7])]
			}
		}
	}
	if matched, _ := regexp.MatchString(`^\d{4}-\d{2}-\d{2}$`, value); matched {
		if t, err := time.Parse("2006-01-02", value); err == nil {
			d := t.Format("2006-01-02")
			out.Date = &d
			out.Precision = "day"
		}
		return out
	}
	precision := "minute"
	layout := "2006-01-02T15:04"
	normalized := strings.Replace(value, " ", "T", 1)
	if strings.Contains(normalized, ".") {
		precision = "fraction"
		layout = time.RFC3339Nano
	} else if regexp.MustCompile(`T\d{2}:\d{2}:\d{2}`).MatchString(normalized) {
		precision = "second"
		layout = "2006-01-02T15:04:05"
	}
	hasZone := regexp.MustCompile(`(?i)(Z|[+-]\d{2}:?\d{2})$`).MatchString(normalized)
	if regexp.MustCompile(`[+-]\d{4}$`).MatchString(normalized) {
		normalized = normalized[:len(normalized)-2] + ":" + normalized[len(normalized)-2:]
	}
	if strings.HasSuffix(strings.ToUpper(normalized), "Z") {
		normalized = strings.TrimSuffix(strings.TrimSuffix(normalized, "Z"), "z") + "Z"
	}
	if hasZone {
		if precision == "minute" {
			layout = "2006-01-02T15:04Z07:00"
		} else {
			layout = time.RFC3339Nano
		}
	}
	t, err := time.Parse(layout, normalized)
	if err != nil {
		return out
	}
	d := t.Format("2006-01-02")
	local := t.Format("2006-01-02T15:04:05.999999999Z07:00")
	if precision == "minute" {
		local = t.Format("2006-01-02T15:04")
	}
	out.Date = &d
	out.LocalDateTime = &local
	out.Precision = precision
	if hasZone {
		_, offset := t.Zone()
		zone := fmt.Sprintf("%+03d:%02d", offset/3600, (offset%3600)/60)
		utc := t.UTC()
		out.Timezone = &zone
		out.UTC = &utc
	}
	return out
}
