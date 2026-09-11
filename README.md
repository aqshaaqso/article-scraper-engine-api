# Article Scraper Engine API

Article scraper berbasis Go dengan FastAPI sebagai middleware HTTP dan PostgreSQL
sebagai antrean serta penyimpanan hasil. FastAPI tidak melakukan scraping, ekstraksi,
discovery SerpAPI, atau penyimpanan SQLite. Lihat [HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md)
untuk batas kepemilikan tiap komponen.

## Arsitektur aktif

1. FastAPI mengautentikasi dan memvalidasi request publik.
2. FastAPI menulis command ke PostgreSQL dan membaca progres/hasil.
3. Go worker mengambil command, melakukan discovery, validasi URL, robots policy,
   throttling, fetch, ekstraksi, retry, dan klasifikasi tanggal.
4. PostgreSQL menyimpan command, search run, job, item, dan heartbeat worker.

`DATABASE_URL` wajib tersedia. Tidak ada fallback engine Python/SQLite.

## Menjalankan stack

Siapkan `.env` tanpa menimpa file yang sudah ada:

```powershell
Copy-Item .env.example .env
```

Isi minimal:

```env
SCRAPER_API_KEY=GANTI_DENGAN_API_KEY_LOKAL
SERPAPI_API_KEY=GANTI_DENGAN_KEY_SERPAPI
REQUIRE_API_KEY=true
```

Jalankan Docker Desktop, kemudian:

```powershell
$env:API_PORT = "18010"
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

Atau gunakan:

```powershell
.\run.ps1 -ApiPort 18010
```

Alamat lokal ketika memakai port tersebut:

- `http://127.0.0.1:18010/health`
- `http://127.0.0.1:18010/ready`
- `http://127.0.0.1:18010/swagger/index.html`
- `http://127.0.0.1:18010/openapi.json`

Jika Docker menyatakan `manually paused`, aktifkan kembali Docker Desktop sebelum
mengulang perintah. Nama environment yang benar adalah `API_PORT`, tanpa backslash.

## Endpoint utama

- `POST /v1/articles/scrape`: scrape satu URL melalui Go worker.
- `POST /v1/jobs`: antrekan beberapa URL.
- `GET /v1/jobs/{job_id}`: progres dan hasil job.
- `POST /v1/search/jobs`: discovery SerpAPI dan scraping otomatis.
- `GET /v1/search/runs/{search_id}`: progres historical search.
- `POST /v1/search/runs/{search_id}/continue`: lanjutkan checkpoint tanpa body.

Endpoint selain health/readiness menggunakan header:

```text
X-API-Key: nilai SCRAPER_API_KEY
```

## Filter tanggal

Request tanpa tanggal tetap melakukan pencarian umum. Filter berikut saling eksklusif:

- `day`: satu tanggal pada bulan dan tahun berjalan.
- `month`: satu bulan pada tahun berjalan.
- `year`: satu tahun yang diberikan.
- hanya `start_date`: dari tanggal tersebut sampai hari ini.
- `start_date` dan `end_date`: rentang eksplisit inklusif.

Hari ini dihitung berdasarkan `timezone`, dengan default `Asia/Jakarta`. Bulan dan
tahun berjalan dibatasi sampai hari ini; periode lampau memakai batas penuh. Tanggal,
bulan, tahun, dan `end_date` masa depan ditolak dengan HTTP 422.

Contoh:

```json
{
  "query": "anak gunung krakatau",
  "month": 9,
  "timezone": "Asia/Jakarta",
  "max_articles": 10,
  "max_pages": 1
}
```

Respons bertanggal menyertakan:

- `requested_date_filter`: input awal dan mode `day`, `month`, `year`, `start_date`,
  `range`, atau `legacy_range`.
- `date_filter`: rentang canonical `start_date`, `end_date`, dan `timezone` yang
  dipakai Go worker.

Rentang canonical disimpan di PostgreSQL. `/continue` tidak menghitung ulang tanggal
akhir ketika dipanggil pada hari berikutnya. `current_start_date` dan
`current_end_date` menunjukkan window bulanan aktual; keduanya menjadi `null` ketika
status `discovery_complete` atau `failed`.

Setiap halaman discovery memakai kuota SerpAPI. Gunakan `max_pages: 1` saat pengujian
awal dan jangan memanggil `/continue` setelah `continue_url` menjadi `null`.

## Hasil dan audit tanggal

Tanggal publikasi diverifikasi oleh Go setelah artikel diekstrak. Respons job bertanggal
secara default hanya menampilkan item dengan `included=true`. Gunakan:

```text
GET /v1/jobs/{job_id}?include_excluded=true
```

untuk melihat item `out_of_range`, `unknown`, gagal, atau masih diproses.

## Pemeriksaan source

Pemeriksaan kode tidak menyalakan engine atau memakai SerpAPI:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.middleware.lock -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
go test ./...
go vet ./...
```

Pengujian engine, container, Postman, dan penggunaan kuota provider dilakukan manual
oleh pengguna.
