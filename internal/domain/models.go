package domain

import "time"

type Command struct {
	ID       string
	Type     string
	Payload  []byte
	Attempts int
}

type ArticleTime struct {
	Raw           *string    `json:"raw"`
	Source        *string    `json:"source"`
	Date          *string    `json:"date"`
	LocalDateTime *string    `json:"local_datetime"`
	UTC           *time.Time `json:"utc"`
	Timezone      *string    `json:"timezone"`
	Precision     string     `json:"precision"`
}

type Article struct {
	Status           string      `json:"status"`
	SourceURL        string      `json:"source_url"`
	FinalURL         string      `json:"final_url"`
	CanonicalURL     string      `json:"canonical_url"`
	Domain           string      `json:"domain"`
	Title            string      `json:"title"`
	Author           *string     `json:"author"`
	PublishedAt      *string     `json:"published_at"`
	ModifiedAt       *string     `json:"modified_at"`
	PublicationTime  ArticleTime `json:"publication_time"`
	ModificationTime ArticleTime `json:"modification_time"`
	Source           *string     `json:"source"`
	Section          *string     `json:"section"`
	Description      *string     `json:"description"`
	ImageURL         *string     `json:"image_url"`
	Content          string      `json:"content"`
	WordCount        int         `json:"word_count"`
	ContentHash      string      `json:"content_hash"`
	RobotsStatus     string      `json:"robots_status"`
	FetchedAt        time.Time   `json:"fetched_at"`
}

type WorkerError struct {
	Code, Detail string
	Retryable    bool
}

func (e *WorkerError) Error() string { return e.Detail }
