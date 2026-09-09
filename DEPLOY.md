# Deploy Search + Article Scraper Engine 0.3.1

Untuk tim deployment: satu container berisi API, Swagger resmi, worker scraping,
dan penyimpanan SQLite pada volume Docker. Tidak memerlukan dashboard atau Redis.

## 1. Persiapan server

Gunakan Linux dengan Docker Engine dan Docker Compose plugin. Rekomendasi awal
untuk staging: 2 vCPU dan 2 GB RAM; ini titik awal, bukan hasil capacity test.
Server perlu akses keluar HTTPS ke SerpAPI, media yang diizinkan, dan DNS.
Browser pengguna Swagger perlu akses ke cdn.jsdelivr.net untuk aset Swagger UI.

Repository saat handoff bersifat public sehingga tim dapat langsung clone.
Jika visibilitas diubah menjadi private, akun tim perlu diberi akses terlebih dahulu.

```bash
git clone https://github.com/aqshaaqso/article-scraper-engine-api.git
cd article-scraper-engine-api
cp .env.example .env
chmod 600 .env
```

Jangan menimpa `.env` yang sudah ada saat update. Isi menggunakan editor server:

```env
SCRAPER_API_KEY=ISI_KEY_BARU_KHUSUS_SERVER
SERPAPI_API_KEY=ISI_KEY_AKUN_SERPAPI
REQUIRE_API_KEY=true
```

Buat SCRAPER_API_KEY acak di server, misalnya:

```bash
openssl rand -hex 32
```

`SCRAPER_API_KEY` digunakan client API pada header `X-API-Key`.
`SERPAPI_API_KEY` digunakan engine untuk menghubungi SerpAPI. Jangan commit `.env`
atau membagikan key melalui kode frontend. Allowlist 12 media sudah ada di template.
Pertahankan `ALLOW_HTTP=false`, `RESPECT_ROBOTS=true`, `ROBOTS_FAIL_CLOSED=true`.

## 2. Build dan start

```bash
docker compose up -d --build --wait --wait-timeout 120
docker compose ps
docker compose logs --tail=100 article-scraper
```

Nama container: `article-scraper-engine`. Image lokal: `news-search-scraper-engine:0.3.1`.
Nama grup Compose mengikuti nama folder repository. Container harus berstatus healthy.
Versi runtime dikunci di `requirements.lock`; base image dikunci dengan digest.
Build pertama mengunduh dependensi; perubahan source berikutnya memakai cache lapisan
dependensi. Dockerfile juga menjalankan proses sebagai user non-root.

Alamat setelah run:

- `http://IP-SERVER:8010/swagger/index.html` — Swagger UI.
- `http://IP-SERVER:8010/openapi.json` — import ke Postman.
- `http://IP-SERVER:8010/redoc` — dokumentasi API.
- `http://IP-SERVER:8010/health` — health check tanpa API key.

## 3. Uji alur lengkap

Di Swagger klik Authorize, masukkan SCRAPER_API_KEY (bukan key SerpAPI).
Jalankan `POST /v1/search/jobs`:

```json
{"query":"anak gunung krakatau","max_articles":50,"max_pages":5}
```

Setiap halaman mengirim satu request SerpAPI dan dapat memakai kuota. Default
50 artikel/5 halaman, tetapi hasil bisa lebih sedikit. Pencarian Google dibatasi
ke media dalam allowlist; hasil URL unik yang valid masuk antrean otomatis.
Respons 202 berisi `job_id` dan `result_url`. Panggil `GET /v1/jobs/{job_id}`
dengan header X-API-Key sampai status completed. JSON lengkap ada di
`items[].article`, termasuk `content`; kegagalan dicatat per item.

Untuk cek cepat tanpa kuota SerpAPI, jalankan GET /health dan GET /v1/jobs.
GET /v1/jobs tanpa key harus 401, dengan key yang benar harus 200.
Status healthy tidak berarti seluruh situs pasti dapat di-scrape.

## 4. Reverse proxy dan operasi

Compose mempublikasikan port 8010 pada seluruh interface. Untuk server yang
memakai reverse proxy pada host yang sama, ubah mapping menjadi
`127.0.0.1:8010:8010`, lalu sediakan HTTPS dari proxy. Batasi akses jaringan
sesuai kebutuhan tim. Gunakan lokasi root pada domain/subdomain tersendiri.

Pencarian berlangsung selama POST; 5 halaman bisa menunggu sekitar 225 detik,
ditambah validasi DNS. Untuk Nginx, contoh pengaturan pada blok location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8010;
    proxy_set_header Host $host;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```

Gunakan timeout client minimal 300 detik. Jangan retry POST secara otomatis
setelah timeout: periksa GET /v1/jobs dahulu agar tidak membuat job duplikat.
Pasang pembatasan request di gateway untuk client produksi. Rate limiter bawaan
hanya mengatur akses per domain media, bukan kuota request pencarian per client.

Satu proses API dengan 3 worker global adalah konfigurasi yang diuji. Jangan
menambah `uvicorn --workers` atau mereplikasi container pada database yang sama:
antrean saat ini belum dirancang untuk koordinasi multi-proses. API key memberi
akses ke seluruh riwayat job; belum ada pemisahan tenant atau pengguna.

## 5. Update, stop, dan backup

Untuk update 0.2.0 ke 0.3.1, buat backup data dengan prosedur di bawah sebelum rebuild.
Saat startup, engine menambah kolom konteks pencarian pada tabel job; job lama tetap
tersimpan. Tabel progres pencarian dibuat saat fitur historis pertama kali digunakan.
Tidak ada dependency runtime tambahan dan tidak perlu mengganti `.env`.

```bash
git pull --ff-only
docker compose up -d --build --wait --wait-timeout 120
```

Untuk stop/start tanpa rebuild:

```bash
docker compose stop
docker compose start
```

Perubahan `.env` memerlukan `docker compose up -d --force-recreate`.
Database disimpan di `/app/data/article_scraper.db` pada named volume Compose.
Jangan gunakan `docker compose down -v` karena akan menghapus volume hasil.
Untuk backup konsisten: tunggu job selesai, stop service, salin direktori data,
lalu start kembali (ganti nama folder backup tiap kali):

```bash
docker compose stop
docker cp article-scraper-engine:/app/data ./backup-data-YYYYMMDD
docker compose start
```

Simpan backup dan key di lokasi terbatas. Jangan mengganti nama project Compose
tanpa memindahkan volume karena project baru akan membuat volume kosong.

## Pemeriksaan fitur historis 0.3.1

Gunakan contoh rentang tanggal dan urutan endpoint pada
[README — Pencarian historis](README.md#pencarian-historis-dengan-tanggal-030).
Periksa versi `/openapi.json` menunjukkan `0.3.1`, lalu pastikan Swagger menampilkan
`start_date`, `end_date`, `timezone`, dan endpoint `/v1/search/runs`.
Contoh awal memakai `max_pages=1` agar penggunaan kuota tiap panggilan terbatas.
Lanjutkan dari `continue_url`, bukan membuat pencarian baru untuk setiap bulan.

Progres dan URL yang sudah ditemukan disimpan dalam volume SQLite yang sama dengan
hasil artikel. Setelah restart, job scraping yang belum selesai dilanjutkan saat startup;
pencarian provider dilanjutkan secara eksplisit melalui endpoint `/continue`.
Jika request pencarian terputus akibat proses mati, kuncinya kedaluwarsa paling lama
sekitar 10 menit sejak pembaruan terakhir. Periksa `/v1/search/runs` sebelum mengulang.

Validasi perubahan 0.3.1 menggunakan provider simulasi, database sementara, serta
pengujian API lokal. Pengambilan historis langsung memakai SerpAPI dan deployment
server untuk versi ini belum diverifikasi.

## Catatan validasi handoff 0.2.0 (sebelum perubahan historis)

E2E lokal Docker, kata kunci anak gunung krakatau: 5 halaman, 40 URL,
35 artikel sukses (10.815 kata), 5 gagal karena robots tidak dapat diperiksa,
HTTP 403, atau konten terlalu pendek. Ini contoh hasil, bukan jaminan setiap run.
Unit/integration tests memakai provider tiruan untuk keamanan, kontrak respons,
filter, batas pencarian, serta penyimpanan sukses/gagal. CI menguji source dan
build Docker, lalu memeriksa health, Swagger, OpenAPI, autentikasi, dan dependensi.
Uji beban dan deploy di server tujuan tetap perlu dilakukan oleh tim.
