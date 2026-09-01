# Article Scraper Engine API

Engine backend untuk mengubah URL artikel berita menjadi JSON terstruktur, baik satu per satu
maupun melalui antrean asinkron. Proyek ini hanya menyediakan API dan dokumentasi; dashboard
berada di aplikasi terpisah.

Pipeline:

1. Validasi URL dan DNS untuk mencegah SSRF.
2. Periksa `robots.txt`.
3. Fetch HTML menggunakan koneksi ke IP publik yang sudah divalidasi.
4. Validasi ulang setiap redirect sebelum diikuti.
5. Ekstrak metadata JSON-LD/OpenGraph dan isi utama dengan Trafilatura.
6. Validasi panjang artikel dan hitung hash konten.

## Menjalankan di lokal (Windows)

### Setup pertama kali

Buka PowerShell, lalu jalankan satu per satu:

```powershell
cd C:\workstuff\medsos\article-scraper-lab
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Perintah setup hanya perlu dijalankan sekali. Jika `.env` sudah ada, jangan salin ulang agar
konfigurasi lokal yang sudah dibuat tidak tertimpa.

### Start harian

```powershell
cd C:\workstuff\medsos\article-scraper-lab
.\run.ps1
```

Biarkan jendela PowerShell tersebut tetap terbuka selama aplikasi digunakan. Buka salah satu URL
berikut di browser:

- http://127.0.0.1:8010/swagger/index.html — Swagger UI resmi dari `swagger-ui-dist`.
- http://127.0.0.1:8010/openapi.json — skema OpenAPI mentah.
- http://127.0.0.1:8010/redoc — dokumentasi ReDoc.
- http://127.0.0.1:8010/health — pemeriksaan kesehatan aplikasi.

Semua URL tersebut juga dicetak langsung di PowerShell setiap kali `run.ps1` dijalankan. Membuka
alamat root http://127.0.0.1:8010 akan diarahkan ke Swagger UI.

Untuk menghentikan aplikasi, kembali ke jendela PowerShell yang menjalankan server lalu tekan
`Ctrl+C`.

### Mencoba scraper dari Swagger UI

1. Buka http://127.0.0.1:8010/swagger/index.html.
2. Buka bagian **Articles**, lalu pilih `POST /v1/articles/scrape`.
3. Klik **Try it out**.
4. Ganti nilai `url` dengan URL artikel dari domain yang diizinkan dalam `.env`.
5. Klik **Execute** dan lihat hasil pada bagian **Response body**.

Untuk banyak URL, gunakan `POST /v1/jobs`, lalu salin `job_id` dari respons dan periksa progresnya
melalui `GET /v1/jobs/{job_id}`.

## Deploy dengan Docker

Salin folder ini ke server yang sudah memiliki Docker, buat `.env` dari `.env.example`, lalu
buat API key acak:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Salin hasilnya ke `SCRAPER_API_KEY` dalam `.env`, lalu jalankan dari folder proyek:

```bash
docker compose up -d --build
```

Deployment Docker mewajibkan API key. Tombol **Authorize** di Swagger hanya digunakan developer
untuk pengujian manual. Backend dashboard terpisah menyimpan key dalam environment-nya dan
menyertakan header `X-API-Key` secara server-to-server pada setiap request. Pengguna akhir tidak
perlu melihat atau memasukkan API key. Jangan menaruh API key di URL atau kode frontend browser.

Layanan tersedia pada port `8010`:

- `http://IP-SERVER:8010/swagger/index.html` — Swagger UI resmi.
- `http://IP-SERVER:8010/openapi.json` — spesifikasi OpenAPI.
- `http://IP-SERVER:8010/redoc` — dokumentasi ReDoc.
- `http://IP-SERVER:8010/health` — pemeriksaan kesehatan layanan.

Database SQLite dipaksa tersimpan di `/app/data/article_scraper.db` dalam volume Docker
`article-scraper-data`, sehingga data tidak hilang ketika container dibuat ulang. Untuk server
publik, letakkan layanan di belakang reverse proxy HTTPS dan batasi akses port `8010` dari
internet jika hanya reverse proxy yang boleh mengaksesnya.

## Antrean asinkron

Kirim maksimal 100 URL melalui `POST /v1/jobs`. Secara default tiga URL diproses sekaligus,
sementara sisanya otomatis menunggu di antrean. Ketiga worker dibagi oleh semua job aktif, bukan
dibuat tiga worker baru untuk setiap job. Progres dan hasil disimpan di SQLite sehingga tetap
tersedia setelah client terputus.

Endpoint terkait:

- `POST /v1/jobs` untuk membuat job dari `{"urls": ["https://..."]}`.
- `GET /v1/jobs/{job_id}` untuk membaca progres dan hasil.
- `GET /v1/jobs` untuk melihat job terbaru.

Jumlah proses paralel dapat diubah dengan `WORKER_COUNT`. Jeda minimum per domain dapat diubah
dengan `DOMAIN_DELAY_SECONDS`. Rate limiter berlaku pada endpoint tunggal maupun antrean: request
ke domain yang sama diserialkan dan mengikuti nilai terbesar antara jeda konfigurasi dan
`Crawl-delay` dari `robots.txt`.

## Request

`POST /v1/articles/scrape`

```json
{
  "url": "https://example-news-site.com/article"
}
```

Secara default hanya HTTPS yang diizinkan. Untuk membatasi eksperimen ke domain tertentu, isi:

```env
ALLOWED_DOMAINS=example.com,news.example.org
```

Domain yang terdaftar juga mengizinkan subdomainnya. Jangan menonaktifkan pemeriksaan URL untuk
mencoba mengakses localhost, jaringan privat, metadata cloud, CAPTCHA, login, atau paywall.

## Validasi

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```
