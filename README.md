# Meta Ads Autopilot

Otomasi campaign Meta Ads berbasis aturan. Mengevaluasi semua campaign dan ad set tiap 15 menit, mengambil satu keputusan per objek sesuai urutan prioritas.

Tanpa dependensi eksternal. Cukup Python 3.8+ dan satu access token Meta.

## Cara kerja

Setiap siklus, engine membaca spend dan jumlah order hari ini dari Meta Insights, lalu menerapkan 5 aturan berurutan. Evaluasi berhenti di aturan pertama yang cocok.

| # | Aksi | Kondisi |
|---|---|---|
| 1 | PAUSE | CPR >= Rp 50.000 dan sudah ada order, ATAU belanja >= Rp 35.000 (CBO) / Rp 25.000 (ABO) dengan nol order |
| 2 | REACTIVATE | CPR <= Rp 35.000, ada order baru, lewat cooldown 15 menit |
| 3 | SCALE UP | Order naik, budget ditambah 10-40 persen sesuai jumlah order baru |
| 4 | SCALE DOWN | CPR >= Rp 40.000, budget dipotong 20 persen, cooldown 15 menit |
| 5 | NO ACTION | Selain kondisi di atas |

### Pengali CPR saat scale up

Makin mahal biaya per order, makin kecil kenaikan budget. Tier order dikali pengali ini.

| CPR | Pengali |
|---|---|
| <= Rp 25.000 | 100% |
| <= Rp 30.000 | 75% |
| <= Rp 35.000 | 50% |
| < Rp 40.000 | 0% (tidak scale) |
| >= Rp 40.000 | scale down |

Ada pengaman tambahan: kalau CPR naik lebih dari 50 persen dibanding siklus sebelumnya, pengali dikunci maksimal 25 persen. Tujuannya mencegah penambahan budget saat efisiensi sedang memburuk.

### Skala tier

| Order baru | Kenaikan |
|---|---|
| 1 | +10% |
| 2 | +20% |
| 3-6 | +20-30% |
| 7+ | +40% |

Batas budget minimum Rp 10.000, maksimum Rp 1.500.000.

### Aturan khusus

BID COST CAP (nama mengandung "BID") tidak pernah di-scale dan tidak pernah diubah budgetnya. Kalau CPR tembus Rp 35.000 atau belanja tembus Rp 30.000 tanpa order, campaign dipause. Anggaran BID sepenuhnya kontrol manual.

CBO dievaluasi di level campaign, ABO di level ad set. Ad set yang campaign induknya PAUSED dilewati, karena Meta menolak penulisan ke sana.

## Jam operasi

Autopilot hanya berjalan jam 04:00-19:59 WIB. Scale up hanya diizinkan sebelum jam 19:00. Di luar jam itu engine keluar tanpa melakukan apa pun.

## Instalasi

```bash
git clone <repo-url> meta-autopilot
cd meta-autopilot
mkdir -p ~/.autopilot
echo "YOUR_META_ACCESS_TOKEN" > ~/.autopilot/meta_token
chmod 600 ~/.autopilot/meta_token
```

Isi daftar akun di `meta_ads_automation.py`, cari baris:

```python
accounts = [f"act_{a}" for a in [
    "ACCOUNT_01", "ACCOUNT_02",
]]
```

Ganti placeholder dengan account ID tanpa prefix `act_`.

## Menjalankan

```bash
# uji tanpa eksekusi
python3 meta_ads_automation.py

# eksekusi sungguhan
touch ~/.autopilot/meta_autopilot_live
python3 meta_ads_automation.py

# kembali ke mode uji
rm ~/.autopilot/meta_autopilot_live
```

Uji satu akun saja:

```bash
python3 meta_ads_automation.py --account act_123456789
```

Cek kesehatan token dan izin tulis:

```bash
python3 meta_ads_automation.py --check-tokens --account act_123456789
```

## Jadwal cron

```
*/15 4-20 * * *   autopilot
0 21 * * *        reset budget harian
0 22 * * *        laporan harian
0 */6 * * *       alert
0 * * * *         tracker ABO
```

Semua script membaca token dari `AUTOPILOT_DIR`, default `~/.autopilot`. Set kalau mau lokasi lain:

```bash
export AUTOPILOT_DIR=/path/to/folder
```

## Variabel environment

| Nama | Fungsi |
|---|---|
| AUTOPILOT_DIR | folder berisi meta_token dan state.json |
| META_TOKEN | alternatif, token langsung dari env |

## Peringatan

Script ini menulis ke akun iklan sungguhan. Selalu jalankan tanpa flag live dulu untuk melihat keputusan yang akan diambil. Pastikan daftar akun dan ambang batas sudah benar sebelum mengaktifkan mode live.

Mode uji tetap memanggil API untuk membaca, jadi tetap butuh token yang valid.

## Struktur

| File | Fungsi |
|---|---|
| meta_ads_automation.py | engine utama |
| meta_ads_budget_reset.py | reset budget harian ke baseline |
| meta_ads_daily_report.sh | laporan performa harian |
| meta_ads_alert.sh | pemantau, output hanya kalau ada masalah |
| abo_first_purchase_tracker.py | catat first spend per ad set ABO |

## Lisensi

MIT
