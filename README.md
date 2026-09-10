# Article Scraper Engine API

> **Hybrid Go migration:** the production-oriented path now keeps FastAPI as
> middleware and runs scraping, extraction, SerpAPI, and historical discovery
> in a Go worker through PostgreSQL. See [HYBRID_ARCHITECTURE.md](HYBRID_ARCHITECTURE.md).
> The original Python/SQLite engine remains available as the v0.3.1 oracle
> while parity validation is completed.

**Panduan handoff server:** [DEPLOY.md](DEPLOY.md). Versi engine: 0.3.1,
termasuk pencarian SerpAPI, scraper, Swagger UI resmi, dan Docker.

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
.\.venv\Scripts\python.exe -m pip install -e ".[dev,oracle]"
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

### Testing lokal lewat Docker Desktop

Setelah build pertama selesai, buka **Containers** di Docker Desktop. Cari grup
`article-scraper-lab`, lalu container `article-scraper-engine`. Klik **Start** untuk
menyalakan engine beserta worker; tidak perlu menjalankan `run.ps1` atau build ulang.

1. Buka http://127.0.0.1:8010/health untuk memastikan engine aktif.
2. Buka http://127.0.0.1:8010/swagger/index.html.
3. Klik **Authorize**, masukkan nilai `SCRAPER_API_KEY` dari `.env`, lalu authorize.
4. Jalankan `POST /v1/articles/scrape` dengan URL artikel dari domain yang diizinkan.
5. Untuk batch, jalankan `POST /v1/jobs` dengan `{"urls": ["URL_ARTIKEL"]}`.
6. Salin `job_id` ke `GET /v1/jobs/{job_id}`, lalu Execute sampai job selesai.

Klik **Stop** untuk menghentikan engine. Data hasil tetap berada di volume Docker.
Jika mengubah `.env`, jalankan `docker compose up -d --force-recreate` agar konfigurasi
baru diterapkan. Start biasa memakai konfigurasi yang tersimpan saat container dibuat.

Layanan tersedia pada port `8010`:

- `http://IP-SERVER:8010/swagger/index.html` — Swagger UI resmi.
- `http://IP-SERVER:8010/openapi.json` — spesifikasi OpenAPI.
- `http://IP-SERVER:8010/redoc` — dokumentasi ReDoc.
- `http://IP-SERVER:8010/health` — pemeriksaan kesehatan layanan.

Database SQLite dipaksa tersimpan di `/app/data/article_scraper.db` dalam volume Docker
`article-scraper-data`, sehingga data tidak hilang ketika container dibuat ulang. Untuk server
publik, letakkan layanan di belakang reverse proxy HTTPS dan batasi akses port `8010` dari
internet jika hanya reverse proxy yang boleh mengaksesnya.

## Cari kata kunci lalu scrape otomatis

Isi `SERPAPI_API_KEY` di `.env` menggunakan key dari akun SerpAPI. Key ini berbeda
dari `SCRAPER_API_KEY` yang dipakai untuk Authorize di Swagger. Setelah mengisi key:

```bash
docker compose up -d --build
```

Buka Swagger http://127.0.0.1:8010/swagger/index.html, Authorize menggunakan
`SCRAPER_API_KEY`, lalu pilih **Search news → POST /v1/search/jobs**:

```json
{"query": "anak gunung krakatau", "max_articles": 50, "max_pages": 5}
```

Engine mencari artikel termasuk arsip melalui Google untuk Indonesia/bahasa Indonesia via
[SerpAPI Organic Results](https://serpapi.com/organic-results). Pencarian dibatasi ke
`ALLOWED_DOMAINS`; semua hasil tetap divalidasi dengan aturan domain, DNS/IP publik,
port, dan HTTPS sebelum masuk antrean. URL duplikat (termasuk perbedaan fragment)
dan URL tidak valid dilewati. Halaman root/tag/topik/indeks umum juga dilewati;
filter ini heuristik, sehingga isi hasil tetap perlu ditinjau. Domain yang diizinkan
juga mencakup subdomainnya.
Allowlist bukan jaminan halaman aman atau bisa diekstrak; pemeriksaan redirect,
robots.txt, batas HTML, dan rate limiter tetap aktif saat scraping.

Respons berisi `job_id`, `result_url`, `urls`, `total`, `pages_fetched`, dan `skipped`.
Pencarian berlangsung selama request ini; scraping berjalan otomatis di worker.
Gunakan `GET /v1/jobs/{job_id}` setiap beberapa detik sampai `status=completed`.
Artikel lengkap ada pada `items[].article`; kegagalan per artikel ada pada
`items[].error_code` dan `items[].error_detail`. Tidak ada bypass paywall/robots.

Batas artikel 1–50 (default 50), batas halaman 1–5 (default 5). Setiap halaman
mengirim satu request SerpAPI yang dapat memakai kuota akun. Jumlah hasil bisa
lebih sedikit dari batas; ini bukan seluruh berita di internet. Jika hasil kosong
atau seluruh URL ditolak, respons `status=no_results`, `job_id=null`, `total=0`.
Jika key belum diisi, endpoint mengembalikan 503; masalah provider/key/kuota 502.
Pencarian bisa menunggu hingga sekitar 225 detik untuk 5 halaman (45 detik per halaman,
di luar validasi DNS); gunakan timeout client/reverse proxy yang sesuai.
Pencarian yang gagal tidak membuat job parsial. Jangan otomatis mengulang POST
yang timeout: periksa daftar job terlebih dahulu agar tidak membuat job duplikat.

## Pencarian historis dengan tanggal (0.3.1)

Di Swagger, jalankan `POST /v1/search/jobs` dengan contoh berikut:

```json
{
  "query": "anak gunung krakatau",
  "start_date": "2024-09-09",
  "end_date": "2026-09-09",
  "timezone": "Asia/Jakarta",
  "max_articles": 50,
  "max_pages": 5
}
```

Tanggal awal dan akhir **inklusif** dan memfilter **tanggal publikasi**, bukan
tanggal perubahan atau pengambilan. `start_date` boleh dikirim tanpa `end_date`;
akhir rentangnya otomatis menjadi hari ini menurut `timezone`. Alternatif ringkas:
`day` mencari satu tanggal pada bulan berjalan, `month` mencari satu bulan penuh
pada tahun berjalan, dan `year` mencari 12 bulan penuh. Filter ringkas tersebut
tidak boleh digabung satu sama lain atau dengan `start_date`/`end_date`. Tanpa
filter tanggal, pencarian umum tetap berlaku. Rentang bertanggal dibagi per bulan
kalender; contoh dua tahun di atas menyentuh 25 bulan (bulan pertama/terakhir parsial).
Maksimal 120 bulan kalender per pencarian, tahun 1900–2100. Zona laporan yang tersedia:
`UTC`, `Asia/Jakarta` (WIB), `Asia/Makassar` (WITA), `Asia/Jayapura` (WIT).
Filter ini sampai ketelitian hari; jam publikasi disajikan pada hasil bila tersedia.

Pencarian tetap memakai Google Organic Results. Filter tanggal provider memakai
[`tbs=cdr:1,cd_min:...,cd_max:...`](https://serpapi.com/blog/filtering-google-search-and-google-news-results/).
Untuk mengurangi kehilangan artikel di batas zona waktu, pencarian diperlebar satu hari
di kedua sisi setiap bulan. Tanggal metadata artikel diperiksa kembali terhadap rentang
asli setelah scraping; hasil mesin pencari bukan bukti tanggal terbit yang pasti.

Alur pengambilan bertahap:

1. Kirim request di atas satu kali. Simpan `search_id`, `progress_url`, `continue_url`,
   serta `job_id`/`result_url` bila ada URL yang berhasil ditemukan.
2. Baca `GET /v1/search/runs/{search_id}` untuk progres seluruh pencarian dan daftar
   `job_ids`. `GET /v1/search/runs` menampilkan 20 pencarian terakhir bila respons awal terputus.
3. Panggil `POST /v1/search/runs/{search_id}/continue` **tanpa body** untuk melanjutkan.
   Setiap panggilan memakai batas `max_pages` dan `max_articles` dari request awal.
   Ulangi secara berurutan selama `continue_url` masih tersedia; setiap halaman dapat memakai kuota.
4. Setelah `status=discovery_complete`, tidak ada kelanjutan hasil provider yang tersimpan.
   Tunggu juga `date_report.pending=0`: scraping job yang sudah dibuat mungkin masih berjalan.
5. Ambil artikel melalui setiap `GET /v1/jobs/{job_id}`. Untuk job bertanggal,
   respons standar hanya berisi artikel yang tanggal publikasinya masuk rentang.

Batas 50 artikel/5 halaman berlaku **per panggilan**, bukan batas keseluruhan dua tahun.
Sisa URL dari halaman yang belum selesai diproses disimpan, sehingga kelanjutan tidak
melewati URL tersebut atau perlu mengunduh ulang halaman itu. URL yang telah masuk job
tidak dimasukkan ulang dalam pencarian yang sama, termasuk URL yang muncul di bulan lain.
Ini deduplikasi URL yang dinormalisasi; salinan artikel pada URL berbeda masih perlu ditinjau.

Progres tersimpan di SQLite bersama job. Penyimpanan job dan kemajuan pencarian berada
dalam satu transaksi. Jika provider gagal, panggilan tersebut tidak membuat job parsial
dan checkpoint sebelumnya tetap bisa dilanjutkan. Error memuat `search_id` dan URL progres.
`pages_fetched` menghitung halaman pada batch yang tersimpan; `requests_attempted` juga
mencatat percobaan yang gagal/diulang, **bukan perhitungan tagihan atau unit provider**.
Setelah timeout, periksa progres dahulu. Request bersamaan untuk pencarian yang sama
mendapat 409; setelah proses mati, kunci panggilan terakhir kedaluwarsa dalam sekitar
10 menit dari pembaruan terakhir. Perubahan allowlist membutuhkan pencarian baru.

`months_completed` berarti penelusuran bulan telah selesai menurut pagination provider,
bukan bukti bahwa semua artikel bulan itu ditemukan. Halaman berulang dihentikan dan
dicatat di `warnings`. Hasil bergantung pada indeks pencarian, arsip yang masih tersedia,
dan keberhasilan ekstraksi; sistem tidak menjamin kelengkapan arsip dua tahun.

### Detail waktu dan laporan hasil

Field lama `published_at`, `modified_at`, dan `fetched_at` tetap tersedia.
`publication_time` serta `modification_time` menambahkan detail berikut:

```json
{
  "raw": "2024-09-09T08:15:00+07:00",
  "source": "json_ld.datePublished",
  "date": "2024-09-09",
  "local_datetime": "2024-09-09T08:15:00+07:00",
  "utc": "2024-09-09T01:15:00Z",
  "timezone": "+07:00",
  "precision": "second"
}
```

Parser menerima tanggal ISO, waktu ISO dengan/tanpa offset, dan tanggal dengan nama
bulan Indonesia serta WIB/WITA/WIT. Metadata JSON-LD diprioritaskan, lalu meta artikel,
itemprop, dan untuk publikasi, hasil ekstraksi Trafilatura. Nilai yang tidak dikenali
tetap tersedia sebagai `raw` dengan `precision=unknown`; tidak diterka dari waktu scraping.
Fallback ekstraksi juga bisa keliru, sehingga `source` disertakan untuk audit.

Jika sumber hanya mencantumkan tanggal, `precision=day`, sementara jam, zona waktu,
dan UTC tetap `null`. Jika ada jam tetapi tidak ada zona, UTC tetap `null`.
Saat filter, waktu dengan offset dikonversi ke zona laporan (`date_basis=report_timezone`).
Tanggal tanpa zona dibandingkan sebagai tanggal kalender sumber (`date_basis=source_date`),
tanpa menganggap sumber pasti memakai WIB. `fetched_at` selalu waktu pengambilan aktual dalam UTC.

Pada hasil job bertanggal, daftar `items` standar hanya menampilkan artikel dengan
`date_status=in_range` dan `included=true`. Artikel di luar rentang, tanggal tidak diketahui,
gagal, dan masih diproses disembunyikan dari daftar. Tambahkan query
`?include_excluded=true` pada `GET /v1/jobs/{job_id}` atau `GET /v1/jobs` untuk audit lengkap;
item tersebut tetap memiliki `date_status` berupa `out_of_range`, `unknown`, atau `pending`.
Job tanpa filter tidak berubah: memakai `not_filtered`, `included=null`, dan seluruh item
ditampilkan. `total` serta `succeeded` menghitung proses scraping, sedangkan jumlah artikel
yang memenuhi syarat ada pada `date_report.in_range`.

`date_report` tersedia per job dan secara gabungan pada progres pencarian:

- `in_range`: artikel yang lolos filter.
- `out_of_range`: tanggal publikasi di luar rentang.
- `unknown_date`: ekstraksi berhasil tetapi tanggal publikasi tidak diketahui.
- `failed`: scraping gagal.
- `pending`: URL masih menunggu/sedang diproses.
- `by_month`: jumlah artikel yang lolos per bulan dalam zona laporan, termasuk nilai nol.

Nilai nol saat pencarian belum selesai bukan bukti tidak ada berita pada bulan tersebut.
Riwayat job versi lama tetap bisa dibaca; detail tambahan baru tersedia untuk scraping baru.

## Antrean dan worker

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
