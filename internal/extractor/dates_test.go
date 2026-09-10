package extractor

import "testing"

func ptr(v string) *string { return &v }
func TestParseArticleTime(t *testing.T) {
	cases := []struct {
		raw, precision, date string
		utc                  bool
	}{{"2024-02-29", "day", "2024-02-29", false}, {"2024-02-29T23:45", "minute", "2024-02-29", false}, {"2024-03-01T00:30:00+07:00", "second", "2024-03-01", true}, {"2024-03-01T00:30:00.123Z", "fraction", "2024-03-01", true}, {"Kamis, 29 Februari 2024 23.45 WIB", "minute", "2024-02-29", true}, {"dua tahun lalu", "unknown", "", false}}
	for _, c := range cases {
		got := ParseArticleTime(ptr(c.raw), ptr("fixture"))
		if got.Precision != c.precision {
			t.Errorf("%s precision %s", c.raw, got.Precision)
		}
		if c.date != "" && (got.Date == nil || *got.Date != c.date) {
			t.Errorf("%s date %#v", c.raw, got.Date)
		}
		if (got.UTC != nil) != c.utc {
			t.Errorf("%s utc mismatch", c.raw)
		}
	}
}
