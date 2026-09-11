# Deployment Go Worker + FastAPI + PostgreSQL

## Persiapan

Server memerlukan Docker Engine dan Docker Compose. Salin `.env.example` menjadi
`.env`, lalu isi `SCRAPER_API_KEY`, `SERPAPI_API_KEY`, dan
`REQUIRE_API_KEY=true`. Jangan commit atau menaruh key di frontend.

## Build dan start

```bash
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

Stack menjalankan PostgreSQL, migrator Go, FastAPI middleware, dan Go worker.
Tidak ada container atau volume SQLite. Gunakan `API_PORT` untuk mengganti port host:

```bash
API_PORT=18010 docker compose up -d --build --wait --wait-timeout 180
```

Periksa:

```text
GET http://127.0.0.1:18010/health
GET http://127.0.0.1:18010/ready
```

Readiness harus menampilkan `database=postgresql` dan schema version yang sesuai.

## Update dan operasi

```bash
git pull --ff-only
docker compose up -d --build --wait --wait-timeout 180
```

Untuk melihat log:

```bash
docker compose logs --tail=200 fastapi-middleware go-worker postgres
```

Data berada pada volume `article-scraper-postgres`. Jangan menjalankan
`docker compose down -v` kecuali memang ingin menghapus seluruh database.

Untuk produksi, gunakan secret terpisah bagi setiap role PostgreSQL, tempatkan API
di belakang reverse proxy HTTPS, dan batasi port database dari jaringan publik.

## Pengujian manual

Gunakan header `X-API-Key`, mulai dengan `max_pages=1`, simpan `search_id`, lalu
ikuti `progress_url` dan `continue_url`. Setiap halaman pencarian dapat memakai
kuota SerpAPI. Detail filter tanggal dan respons tersedia di README.
