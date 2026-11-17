# Article Scraper Engine API

Backend API untuk ekstraksi konten dan metadata artikel berita berbasis arsitektur **hybrid Go + FastAPI + PostgreSQL** (`v0.4.0`).

- **FastAPI** bertugas sebagai HTTP middleware publik (autentikasi `X-API-Key`, validasi request/skema, Swagger/OpenAPI, dan pembacaan progres/hasil).
- **Go Worker** bertugas menjalankan pipeline inti di background: discovery berita via SerpAPI, validasi keamanan URL (anti-SSRF), pengecekan `robots.txt`, throttling terdistribusi per domain, HTTP fetch, ekstraksi konten (`go-readability/v2` + JSON-LD + OpenGraph), retry, dan klasifikasi tanggal publikasi.
- **PostgreSQL** berfungsi sebagai antrean command yang tahan crash (`FOR UPDATE SKIP LOCKED`) sekaligus penyimpanan hasil dan progres pencarian historis.

> Untuk detail batas kepemilikan komponen dan panduan deployment server, lihat [HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md) dan [DEPLOY.md](DEPLOY.md).

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Arsitektur Singkat](#arsitektur-singkat)
- [Prasyarat & Quick Start](#prasyarat--quick-start)
- [Konfigurasi Environment](#konfigurasi-environment)
- [Dokumentasi API (Payload, Input & Output)](#dokumentasi-api-payload-input--output)
- [Filter Tanggal & Klasifikasi Artikel](#filter-tanggal--klasifikasi-artikel)
- [Keamanan & Kode Error](#keamanan--kode-error)
- [Pengembangan & Pengujian](#pengembangan--pengujian)
- [Struktur Proyek](#struktur-proyek)

---

## Fitur Utama

- **Scrape Sinkron & Batch Asinkron**: Scrape 1 URL secara langsung atau antrekan hingga 100 URL per job secara paralel (`WORKER_COUNT`).
- **Discovery Berita Otomatis (SerpAPI)**: Pencarian berita Google News/Web berbasis kata kunci yang terhubung ke antrean scraping, lengkap dengan dukungan checkpoint per bulan untuk pencarian historis (hingga 120 bulan kalender).
- **Ekstraksi Konten & Waktu Terstruktur**: Menggabungkan `go-readability/v2`, JSON-LD (`NewsArticle`, `Article`, `ReportageNewsArticle`), OpenGraph, dan `itemprop`, termasuk normalisasi tanggal publikasi/modifikasi (ISO-8601 dan format tanggal bahasa Indonesia `WIB`/`WITA`/`WIT`).
- **Perlindungan Anti-SSRF Ketat**: Memvalidasi skema, port standar (`443`/`80`), allowlist domain, larangan credential di URL, resolusi DNS publik (memblokir IP lokal/privat/loopback/metadata), serta validasi ulang pada setiap HTTP redirect.
- **Kepatuhan Crawling**: Pemeriksaan `robots.txt` (opsi *fail-closed*), dukungan `Crawl-delay`, serta rate limiting per domain berbasis PostgreSQL.
- **Observabilitas**: Endpoint `/health`, `/ready`, `/metrics` (format Prometheus), dan header korelasi `X-Request-ID`.

---

## Arsitektur Singkat

```text
Client / Dashboard
   │  HTTP + X-API-Key
   ▼
FastAPI Middleware (Python)
   │  Insert command / query progress
   ▼
PostgreSQL 17 (Queue & Store)
   ▲  Claim command & items (SKIP LOCKED), heartbeat, rate limit
   │
Go Worker Pool (cmd/worker)
   ├── SerpAPI Discovery & Monthly Checkpointing
   ├── URL Security Policy (DNS + Public IP Pinning)
   ├── Robots.txt Checker & HTTP Fetcher
   └── HTML Extractor & Date Classifier
```

> `DATABASE_URL` wajib dikonfigurasi. Tidak ada fallback engine Python maupun SQLite.

---

## Prasyarat & Quick Start

### Prasyarat

- **Docker Engine** & **Docker Compose** (untuk menjalankan seluruh stack)
- Opsional untuk pengembangan lokal: **Python 3.11+** dan **Go 1.26+**

### Menjalankan Stack dengan Docker Compose

1. Salin template environment (jangan menimpa `.env` yang sudah ada):

```powershell
Copy-Item .env.example .env
```

2. Isi variabel minimal di `.env`:

```env
SCRAPER_API_KEY=ganti-dengan-api-key-lokal
SERPAPI_API_KEY=ganti-dengan-key-serpapi   # wajib hanya jika memakai /v1/search/*
REQUIRE_API_KEY=true
```

3. Jalankan stack PostgreSQL, migrator Go, FastAPI middleware, dan Go worker:

```powershell
# PowerShell helper (default port 8010)
.\run.ps1 -ApiPort 18010
```

Atau langsung menggunakan Docker Compose:

```bash
API_PORT=18010 docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

Untuk menskalakan worker:

```bash
docker compose up -d --scale go-worker=2
```

### Akses Lokal

Jika memakai `API_PORT=18010`:

| Layanan | URL |
| :--- | :--- |
| Swagger UI | `http://127.0.0.1:18010/swagger/index.html` |
| ReDoc | `http://127.0.0.1:18010/redoc` |
| OpenAPI Schema | `http://127.0.0.1:18010/openapi.json` |
| Health Check | `http://127.0.0.1:18010/health` |
| Readiness Check | `http://127.0.0.1:18010/ready` |

---

## Konfigurasi Environment

| Variabel | Default | Keterangan |
| :--- | :--- | :--- |
| `DATABASE_URL` | *(wajib)* | Connection string PostgreSQL (diset otomatis di `compose.yaml`) |
| `REQUIRE_API_KEY` | `false` (`true` di Compose) | Mewajibkan header `X-API-Key` pada endpoint non-publik |
| `SCRAPER_API_KEY` | `""` (`dev-api-key` di Compose) | Nilai secret untuk autentikasi header `X-API-Key` |
| `SERPAPI_API_KEY` | `""` | API key SerpAPI untuk endpoint `/v1/search/*` |
| `ALLOWED_DOMAINS` | `""` *(12 media ID di `.env.example`)* | Daftar domain yang diizinkan (dipisah koma). Wajib diisi untuk `/v1/search/*` |
| `ALLOW_HTTP` | `false` | Mengizinkan skema `http://` selain `https://` |
| `RESPECT_ROBOTS` | `true` | Memeriksa `robots.txt` sebelum mengambil halaman |
| `ROBOTS_FAIL_CLOSED` | `true` | Menolak scraping jika `robots.txt` gagal diakses atau tidak valid |
| `HTTP_TIMEOUT_SECONDS` | `15` | Timeout request HTTP keluar (detik) |
| `MAX_HTML_BYTES` | `5242880` | Batas maksimal ukuran body HTML (5 MB) |
| `MAX_REDIRECTS` | `3` | Batas maksimal hop redirect |
| `MIN_WORD_COUNT` | `80` | Jumlah kata minimal agar artikel dianggap valid |
| `SCRAPER_USER_AGENT` | `ArticleScraperLab/0.4-go` | Header `User-Agent` milik worker |
| `WORKER_COUNT` | `3` | Jumlah goroutine worker per instance untuk memproses `job_items` |
| `DOMAIN_DELAY_SECONDS` | `1.0` | Jeda minimal antar-request ke domain yang sama (detik) |
| `SYNC_SCRAPE_TIMEOUT_SECONDS` | `30` | Batas tunggu FastAPI untuk `POST /v1/articles/scrape` |
| `LEASE_SECONDS` | `600` | Durasi lease worker sebelum dianggap kedaluwarsa |
| `POLL_INTERVAL_SECONDS` | `1` | Interval polling worker ke PostgreSQL |
| `MAX_ATTEMPTS` | `3` | Maksimal percobaan ulang untuk error yang bersifat *retryable* |
| `WORKER_ID` | `<hostname>-worker` | Identitas worker untuk heartbeat dan kepemilikan lease |
| `API_PORT` | `8010` | Port host yang dipetakan ke FastAPI middleware di `compose.yaml` |

---

## Dokumentasi API (Payload, Input & Output)

### Autentikasi & Header

- Sertakan `X-API-Key: <SCRAPER_API_KEY>` pada seluruh endpoint selain `/health`, `/ready`, `/swagger/index.html`, `/openapi.json`, dan `/redoc`.
- Sertakan opsional `X-Request-ID` (maks. 128 karakter) untuk pelacakan log dan `correlation_id`.

### Ringkasan Endpoint

| Method & Path | Fungsi | Auth |
| :--- | :--- | :--- |
| `POST /v1/articles/scrape` | Scrape satu URL secara sinkron | Ya |
| `POST /v1/jobs` | Antrekan batch URL (1–100 URL) secara asinkron | Ya |
| `GET /v1/jobs` | Lihat 20 job terbaru | Ya |
| `GET /v1/jobs/{job_id}` | Pantau progres dan hasil detail job | Ya |
| `POST /v1/search/jobs` | Cari berita via SerpAPI lalu antrekan scraping | Ya |
| `GET /v1/search/runs` | Lihat 20 pencarian historis terbaru | Ya |
| `GET /v1/search/runs/{search_id}` | Pantau progres bulanan & `date_report` pencarian historis | Ya |
| `POST /v1/search/runs/{search_id}/continue` | Lanjutkan checkpoint pencarian historis berikutnya | Ya |
| `GET /health` | Cek status proses & ringkasan konfigurasi | Tidak |
| `GET /ready` | Cek koneksi PostgreSQL dan versi skema (`schema_version`) | Tidak |
| `GET /metrics` | Metrik antrean, worker, error, dan latensi domain (Prometheus) | Ya |

---

### 1. `POST /v1/articles/scrape` — Scrape Satu Artikel (Sinkron)

**Schema Payload Input (`ScrapeRequest`)**:

| Field | Tipe | Wajib | Aturan |
| :--- | :--- | :--- | :--- |
| `url` | `string` | Ya | Panjang 8–2048 karakter, URL HTTPS/HTTP valid |

**Contoh Input:**

```bash
curl -X POST "http://127.0.0.1:18010/v1/articles/scrape" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SCRAPER_API_KEY>" \
  -d '{
    "url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh"
  }'
```

**Contoh Output (`200 OK` — `ArticleResponse`):**

```json
{
  "status": "success",
  "source_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
  "final_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
  "canonical_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
  "domain": "www.antaranews.com",
  "title": "Khasiat cengkeh untuk kesehatan tubuh",
  "author": "M. Hilal Eka Saputra Harahap",
  "published_at": "2025-05-17T14:08:21+07:00",
  "modified_at": "2025-05-17T14:08:21+07:00",
  "publication_time": {
    "raw": "2025-05-17T14:08:21+07:00",
    "source": "json_ld.datePublished",
    "date": "2025-05-17",
    "local_datetime": "2025-05-17T14:08:21+07:00",
    "utc": "2025-05-17T07:08:21Z",
    "timezone": "+07:00",
    "precision": "second"
  },
  "modification_time": {
    "raw": "2025-05-17T14:08:21+07:00",
    "source": "json_ld.dateModified",
    "date": "2025-05-17",
    "local_datetime": "2025-05-17T14:08:21+07:00",
    "utc": "2025-05-17T07:08:21Z",
    "timezone": "+07:00",
    "precision": "second"
  },
  "source": "ANTARA",
  "section": null,
  "description": "Cengkeh (Syzygium aromaticum) adalah rempah asli Indonesia...",
  "image_url": "https://img.antaranews.com/cache/1200x800/2025/05/14/Harga-komoditi-cengkeh.jpg",
  "content": "Jakarta (ANTARA) - Cengkeh (Syzygium aromaticum) adalah rempah asli Indonesia...",
  "word_count": 443,
  "content_hash": "3639baddf502180e35132f4c77f3ac30fbd90926cef1a4ed0bddcb04342d225f",
  "robots_status": "allowed",
  "fetched_at": "2026-09-10T07:04:17.025156Z"
}
```

**Contoh Output Error (`422` / `403` / `502` — `ApiError`):**

```json
{
  "error": "unsafe_url",
  "detail": "domain tidak terdaftar pada ALLOWED_DOMAINS"
}
```

---

### 2. `POST /v1/jobs` — Antrekan Batch URL (Asinkron)

**Schema Payload Input (`BatchScrapeRequest`)**:

| Field | Tipe | Wajib | Aturan |
| :--- | :--- | :--- | :--- |
| `urls` | `array[string]` | Ya | Minimal 1 URL, maksimal 100 URL per job |

**Contoh Input:**

```bash
curl -X POST "http://127.0.0.1:18010/v1/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SCRAPER_API_KEY>" \
  -d '{
    "urls": [
      "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
      "https://www.kompas.com/tren/read/2025/05/17/contoh-artikel"
    ]
  }'
```

**Contoh Output (`202 Accepted` — `JobAccepted`):**

```json
{
  "job_id": "c4d8523d85454223a772a9b39b7d7db4",
  "status": "queued",
  "total": 2,
  "worker_count": 3
}
```

---

### 3. `GET /v1/jobs/{job_id}` & `GET /v1/jobs` — Ambil Progres & Hasil Job

**Query Parameter:**

| Parameter | Tipe | Default | Keterangan |
| :--- | :--- | :--- | :--- |
| `include_excluded` | `boolean` | `false` | Khusus job bertanggal: jika `true`, ikut menampilkan item `out_of_range`, `unknown`, `failed`, dan `pending` |

**Contoh Input:**

```bash
curl -X GET "http://127.0.0.1:18010/v1/jobs/c4d8523d85454223a772a9b39b7d7db4?include_excluded=false" \
  -H "X-API-Key: <SCRAPER_API_KEY>"
```

**Contoh Output (`200 OK` — `JobResponse`):**

```json
{
  "job_id": "c4d8523d85454223a772a9b39b7d7db4",
  "status": "completed",
  "total": 3,
  "completed": 3,
  "succeeded": 1,
  "failed": 2,
  "error_code": null,
  "error_detail": null,
  "created_at": "2026-09-10T07:04:13.460237Z",
  "started_at": "2026-09-10T07:04:13.941645Z",
  "finished_at": "2026-09-10T07:04:18.749864Z",
  "duration_ms": 4808,
  "items": [
    {
      "position": 1,
      "url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
      "status": "success",
      "error_code": null,
      "error_detail": null,
      "article": {
        "status": "success",
        "source_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
        "final_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
        "canonical_url": "https://www.antaranews.com/berita/4839869/khasiat-cengkeh-untuk-kesehatan-tubuh",
        "domain": "www.antaranews.com",
        "title": "Khasiat cengkeh untuk kesehatan tubuh",
        "author": "M. Hilal Eka Saputra Harahap",
        "published_at": "2025-05-17T14:08:21+07:00",
        "modified_at": "2025-05-17T14:08:21+07:00",
        "publication_time": {
          "raw": "2025-05-17T14:08:21+07:00",
          "source": "json_ld.datePublished",
          "date": "2025-05-17",
          "local_datetime": "2025-05-17T14:08:21+07:00",
          "utc": "2025-05-17T07:08:21Z",
          "timezone": "+07:00",
          "precision": "second"
        },
        "modification_time": {
          "raw": "2025-05-17T14:08:21+07:00",
          "source": "json_ld.dateModified",
          "date": "2025-05-17",
          "local_datetime": "2025-05-17T14:08:21+07:00",
          "utc": "2025-05-17T07:08:21Z",
          "timezone": "+07:00",
          "precision": "second"
        },
        "source": "ANTARA",
        "section": null,
        "description": "Cengkeh (Syzygium aromaticum) adalah rempah asli Indonesia...",
        "image_url": "https://img.antaranews.com/cache/1200x800/2025/05/14/Harga-komoditi-cengkeh.jpg",
        "content": "Jakarta (ANTARA) - Cengkeh (Syzygium aromaticum) adalah rempah asli Indonesia...",
        "word_count": 443,
        "content_hash": "3639baddf502180e35132f4c77f3ac30fbd90926cef1a4ed0bddcb04342d225f",
        "robots_status": "allowed",
        "fetched_at": "2026-09-10T07:04:17.025156Z"
      },
      "date_status": "in_range",
      "included": true,
      "date_basis": "report_timezone"
    }
  ],
  "requested_date_filter": {
    "mode": "range",
    "day": null,
    "month": null,
    "year": null,
    "start_date": "2025-05-01",
    "end_date": "2025-05-31"
  },
  "date_filter": {
    "start_date": "2025-05-01",
    "end_date": "2025-05-31",
    "timezone": "Asia/Jakarta"
  },
  "date_report": {
    "in_range": 1,
    "out_of_range": 0,
    "unknown_date": 0,
    "failed": 2,
    "pending": 0,
    "by_month": {
      "2025-05": 1
    }
  },
  "search_id": "90b2f34537744e20a0152873cc85e472"
}
```

> Untuk batch job biasa (`POST /v1/jobs`), field `requested_date_filter`, `date_filter`, `date_report`, dan `search_id` bernilai `null`, sedangkan setiap item memakai `date_status: "not_filtered"` dan `included: null`.

---

### 4. `POST /v1/search/jobs` — Discovery Berita (SerpAPI) + Scrape

**Schema Payload Input (`SearchRequest`)**:

| Field | Tipe | Default | Aturan |
| :--- | :--- | :--- | :--- |
| `query` | `string` | *(wajib)* | Kata kunci pencarian, 2–300 karakter |
| `max_articles` | `integer` | `50` | Batas artikel per panggilan/eksekusi (1–50) |
| `max_pages` | `integer` | `5` | Batas halaman SerpAPI per eksekusi (1–5) |
| `day` | `integer` | `null` | 1–31 pada bulan & tahun berjalan |
| `month` | `integer` | `null` | 1–12 pada tahun berjalan |
| `year` | `integer` | `null` | 1900–2100 |
| `start_date` | `date` (`YYYY-MM-DD`) | `null` | Tanggal awal inklusif (jika tanpa `end_date`, berakhir di hari ini) |
| `end_date` | `date` (`YYYY-MM-DD`) | `null` | Tanggal akhir inklusif (wajib disertai `start_date`) |
| `timezone` | `string` | `"Asia/Jakarta"` | `"UTC"`, `"Asia/Jakarta"`, `"Asia/Makassar"`, atau `"Asia/Jayapura"` |

#### Contoh A — Pencarian Umum (Tanpa Filter Tanggal)

**Input:**

```bash
curl -X POST "http://127.0.0.1:18010/v1/search/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SCRAPER_API_KEY>" \
  -d '{
    "query": "Indonesia",
    "max_articles": 10,
    "max_pages": 1
  }'
```

**Output (`202 Accepted` — `SearchAccepted`):**

```json
{
  "query": "Indonesia",
  "status": "queued",
  "job_id": "a1b2c3d4e5f64789a0b1c2d3e4f5a6b7",
  "total": 0,
  "pages_fetched": 0,
  "skipped": 0,
  "urls": [],
  "result_url": "/v1/jobs/a1b2c3d4e5f64789a0b1c2d3e4f5a6b7",
  "search_id": null,
  "continue_url": null,
  "progress_url": null,
  "requested_date_filter": null,
  "date_filter": null
}
```

#### Contoh B — Pencarian Historis Bertanggal

**Input:**

```bash
curl -X POST "http://127.0.0.1:18010/v1/search/jobs" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SCRAPER_API_KEY>" \
  -d '{
    "query": "anak gunung krakatau",
    "month": 9,
    "timezone": "Asia/Jakarta",
    "max_articles": 10,
    "max_pages": 1
  }'
```

**Output (`202 Accepted` — `SearchAccepted`):**

```json
{
  "query": "anak gunung krakatau",
  "status": "ready",
  "job_id": null,
  "total": 0,
  "pages_fetched": 0,
  "skipped": 0,
  "urls": [],
  "result_url": null,
  "search_id": "90b2f34537744e20a0152873cc85e472",
  "continue_url": "/v1/search/runs/90b2f34537744e20a0152873cc85e472/continue",
  "progress_url": "/v1/search/runs/90b2f34537744e20a0152873cc85e472",
  "requested_date_filter": {
    "mode": "month",
    "day": null,
    "month": 9,
    "year": null,
    "start_date": null,
    "end_date": null
  },
  "date_filter": {
    "start_date": "2026-09-01",
    "end_date": "2026-09-30",
    "timezone": "Asia/Jakarta"
  }
}
```

---

### 5. `GET /v1/search/runs/{search_id}` & `POST /v1/search/runs/{search_id}/continue`

#### Pantau Progres Pencarian Historis

**Contoh Input (`GET /v1/search/runs/{search_id}`):**

```bash
curl -X GET "http://127.0.0.1:18010/v1/search/runs/90b2f34537744e20a0152873cc85e472" \
  -H "X-API-Key: <SCRAPER_API_KEY>"
```

**Contoh Output (`200 OK` — `SearchProgress`):**

```json
{
  "search_id": "90b2f34537744e20a0152873cc85e472",
  "query": "anak gunung krakatau",
  "date_filter": {
    "start_date": "2025-07-15",
    "end_date": "2025-09-10",
    "timezone": "Asia/Jakarta"
  },
  "requested_date_filter": {
    "mode": "range",
    "day": null,
    "month": null,
    "year": null,
    "start_date": "2025-07-15",
    "end_date": "2025-09-10"
  },
  "status": "ready",
  "months_total": 3,
  "months_completed": 1,
  "current_start_date": "2025-08-01",
  "current_end_date": "2025-08-31",
  "pages_fetched": 1,
  "requests_attempted": 1,
  "discovered": 10,
  "skipped": 2,
  "job_ids": [
    "c4d8523d85454223a772a9b39b7d7db4"
  ],
  "date_report": {
    "in_range": 7,
    "out_of_range": 2,
    "unknown_date": 0,
    "failed": 1,
    "pending": 0,
    "by_month": {
      "2025-07": 7,
      "2025-08": 0,
      "2025-09": 0
    }
  },
  "created_at": "2026-09-10T07:04:10Z",
  "updated_at": "2026-09-10T07:04:19Z",
  "continue_url": "/v1/search/runs/90b2f34537744e20a0152873cc85e472/continue",
  "warnings": [],
  "warning": "Selesai menelusuri hasil provider bukan jaminan seluruh arsip ditemukan."
}
```

#### Lanjutkan Checkpoint Pencarian

**Contoh Input (`POST /v1/search/runs/{search_id}/continue` — tanpa request body):**

```bash
curl -X POST "http://127.0.0.1:18010/v1/search/runs/90b2f34537744e20a0152873cc85e472/continue" \
  -H "X-API-Key: <SCRAPER_API_KEY>"
```

**Contoh Output (`202 Accepted` — `SearchAccepted`):**

Mengembalikan struktur `SearchAccepted` yang sama dengan `POST /v1/search/jobs`. Ketika seluruh window bulan selesai ditelusuri, `status` menjadi `"discovery_complete"` dan `continue_url` menjadi `null`. Jika pencarian gagal atau checkpoint sedang aktif diproses request lain, endpoint mengembalikan `409 Conflict`.

---

### 6. Endpoint Sistem & Monitoring

**`GET /health` (`200 OK`):**

```json
{
  "status": "ok",
  "allow_http": false,
  "respect_robots": true,
  "domain_allowlist_enabled": true,
  "api_key_required": true,
  "max_urls_per_job": 100
}
```

**`GET /ready` (`200 OK` atau `503`):**

```json
{
  "status": "ready",
  "database": "postgresql",
  "schema_version": 4
}
```

**`GET /metrics` (`200 OK` — `text/plain` Prometheus):**

```text
# TYPE article_scraper_command_queue_depth gauge
article_scraper_command_queue_depth 0
# TYPE article_scraper_oldest_command_seconds gauge
article_scraper_oldest_command_seconds 0.000
article_scraper_active_workers 1
article_scraper_expired_leases 0
article_scraper_items_total{status="success"} 48
article_scraper_items_total{status="failed"} 2
article_scraper_search_requests_total 5
article_scraper_search_failures_total 0
article_scraper_errors_total{code="unsafe_url"} 1
article_scraper_domain_latency_seconds{domain="www.antaranews.com"} 0.842100
```

---

## Filter Tanggal & Klasifikasi Artikel

1. **Eksklusivitas filter**: `day`, `month`, `year`, dan `start_date`/`end_date` tidak boleh digabungkan satu sama lain.
2. **Validasi waktu berjalan**: `day`, `month`, `year`, dan `end_date` yang berada di masa depan terhadap `timezone` ditolak dengan `422 Unprocessable Entity`. Bulan/tahun berjalan otomatis dibatasi sampai hari ini.
3. **Rentang canonical persisten**: Rentang tanggal yang sudah dinormalisasi disimpan di PostgreSQL (`search_runs`), sehingga panggilan `/continue` di hari berikutnya tidak menggeser tanggal akhir.
4. **Verifikasi pasca-ekstraksi**: Go worker memverifikasi tanggal publikasi dari JSON-LD, meta tag, `itemprop`, atau Readability:
   - Jika memiliki offset zona waktu, waktu dikonversi ke `timezone` laporan (`date_basis: "report_timezone"`).
   - Jika tanpa zona waktu, dibandingkan langsung sebagai tanggal kalender sumber (`date_basis: "source_date"`).
   - `date_status` pada item dapat bernilai `not_filtered`, `pending`, `in_range`, `out_of_range`, atau `unknown`.
5. **Penggunaan kuota SerpAPI**: Setiap halaman discovery (`max_pages`) mengonsumsi kuota SerpAPI. Gunakan `max_pages: 1` saat pengujian awal dan hentikan panggilan `/continue` ketika `continue_url` bernilai `null`.

---

## Keamanan & Kode Error

| Error Code | HTTP Status | Keterangan |
| :--- | :--- | :--- |
| `unsafe_url` | `422` | Skema bukan HTTPS/HTTP, URL ber-credential, port non-standar, domain di luar `ALLOWED_DOMAINS`, atau IP lokal/privat/khusus |
| `robots_denied` | `403` | Ditolak oleh `robots.txt`, atau `robots.txt` gagal diambil saat `ROBOTS_FAIL_CLOSED=true` |
| `extraction_failed` | `422` | HTML tidak valid, judul tidak ditemukan, atau jumlah kata di bawah `MIN_WORD_COUNT` |
| `fetch_failed` | `502` | Gagal koneksi, HTTP non-2xx, bukan HTML, melebihi `MAX_HTML_BYTES`/`MAX_REDIRECTS`, atau error SerpAPI |
| `job_not_found` | `404` | `job_id` atau `search_id` tidak ditemukan |

---

## Pengembangan & Pengujian

Pemeriksaan kode dan unit test berjalan tanpa menyalakan container ataupun menggunakan kuota SerpAPI:

```powershell
# Python middleware lint & unit tests
.\.venv\Scripts\python.exe -m pip install -r requirements.middleware.lock -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q

# Go worker & engine tests
go test -race ./...
go vet ./...
```

Smoke test end-to-end terhadap stack Docker Compose yang sedang berjalan:

```powershell
$env:SCRAPER_API_KEY = "ci-test-key"
python scripts/hybrid_smoke.py --include-search-failure
```

---

## Struktur Proyek

```text
.
├── cmd/
│   ├── migrate/                 # Migrator skema PostgreSQL
│   ├── worker/                  # Entrypoint Go background worker
│   └── workerhealth/            # Healthcheck heartbeat worker
├── internal/
│   ├── config/                  # Loader konfigurasi Go worker
│   ├── domain/                  # Struct domain Article, ArticleTime, WorkerError
│   ├── extractor/               # Ekstraksi Readability/JSON-LD & parser tanggal
│   ├── fetcher/                 # HTTP fetcher, redirect validator, robots.txt
│   ├── search/                  # Discovery SerpAPI & checkpointing bulanan
│   ├── security/                # Kebijakan URL anti-SSRF & DNS IP pinning
│   └── store/                   # Operasi antrean PostgreSQL & klasifikasi tanggal
├── migrations/                  # Skrip migrasi SQL (001 - 004)
├── src/article_scraper_lab/
│   ├── main.py                  # Aplikasi FastAPI, Swagger UI & exception handlers
│   ├── routes.py                # Definisi endpoint HTTP
│   ├── models.py                # Model request/response Pydantic & validasi tanggal
│   ├── postgres_gateway.py      # Gateway query & command ke PostgreSQL
│   ├── dependencies.py          # Wiring gateway & validasi X-API-Key
│   └── config.py                # Loader environment middleware
├── scripts/hybrid_smoke.py      # Smoke test stack hybrid
├── tests/                       # Unit & contract test FastAPI
├── docker/postgres-init.sql     # Inisialisasi role PostgreSQL dev
├── compose.yaml                 # Orkestrasi Postgres, migrate, middleware, worker
├── Dockerfile.middleware        # Image FastAPI middleware
├── Dockerfile.worker            # Image Go worker, migrate, dan workerhealth
├── run.ps1                      # Helper startup Docker Compose untuk Windows
├── HYBRID_ARCHITECTURE.md       # Dokumen arsitektur & batas kepemilikan komponen
└── DEPLOY.md                    # Panduan deployment server
```
