# 📦 Multi Gudang - Beauty of Angel

Tools pengelompokan order e-commerce berdasarkan **gudang termurah** menggunakan RajaOngkir V2 (Komerce) API.

## 🎯 Fitur

- ✅ Upload Excel order (format Mengantar)
- ✅ Auto-hitung ongkir dari multiple gudang ke tiap tujuan
- ✅ Tentukan gudang termurah per order
- ✅ Tie-breaker: prioritas gudang → ETD → jarak
- ✅ Notifikasi kalau ongkir sama antar gudang
- ✅ Review manual untuk order yang destinasi-nya nggak ketemu
- ✅ Auto-save ke Google Sheets (1 sheet per tanggal)
- ✅ Download hasil Excel
- ✅ Login sederhana

## 🏗️ Struktur Project

```
multi-warehouse-boa/
├── app.py                        # Main Streamlit app
├── requirements.txt              # Dependencies
├── .streamlit/
│   ├── secrets.toml             # (TIDAK di-commit) Credentials
│   └── secrets.toml.example     # Template
├── modules/
│   ├── auth.py                  # Login
│   ├── gsheets.py               # Google Sheets connector
│   ├── rajaongkir.py            # RajaOngkir API wrapper
│   └── shipping.py              # Logic pengelompokan
├── scripts/
│   └── fetch_warehouse_ids.py   # Helper cari subdistrict_id gudang
└── README.md
```

## 🚀 Setup Lokal

### 1. Clone repo & install dependencies

```bash
git clone <repo-url>
cd multi-warehouse-boa
python -m venv venv
source venv/bin/activate   # Mac/Linux
# atau: venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### 2. Setup Google Sheets

1. Pastikan Google Sheets udah punya **5 tab**: `master_gudang`, `master_city`, `config`, `log_upload`, `review_manual`
2. Isi `master_gudang` dengan data gudang kamu (lihat tahap 4)
3. Share GSheets ke email service account kamu (Editor)

### 3. Setup secrets.toml

Copy template:
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` dengan isi:
- Username/password login
- API key RajaOngkir (dari dashboard Komerce → Shipping Cost)
- Spreadsheet ID (dari URL GSheets)
- Isi JSON service account (copy semua field dari file JSON)

### 4. Cari subdistrict_id gudang

Jalankan helper script:
```bash
export RAJAONGKIR_KEY="paste-api-key-kamu"
python scripts/fetch_warehouse_ids.py
```

Output akan tampilkan subdistrict_id untuk tiap kode pos gudang. Copy ke kolom `subdistrict_id` di sheet `master_gudang`.

**Format `master_gudang`:**

| kode_gudang | nama_gudang | kota_rajaongkir | subdistrict_id | prioritas | latitude | longitude | aktif |
|---|---|---|---|---|---|---|---|
| G01 | Tangerang | Tangerang | 12345 | 1 | -6.1783 | 106.6319 | TRUE |
| G02 | Medan | Medan | 23456 | 2 | 3.5952 | 98.6722 | TRUE |
| ... | ... | ... | ... | ... | ... | ... | ... |

### 5. Run aplikasi

```bash
streamlit run app.py
```

Buka di browser: http://localhost:8501

## ☁️ Deploy ke Streamlit Cloud

1. Push repo ke GitHub (pastikan `.streamlit/secrets.toml` **TIDAK** ikut ke-push - cek `.gitignore`)
2. Login ke https://share.streamlit.io
3. Klik **New app** → connect repo → pilih `app.py`
4. Setting → **Secrets** → paste isi `secrets.toml` kamu
5. Deploy!

## 📋 Format Excel Input

Excel upload HARUS punya kolom minimal:
- `order_id` - ID order
- `name` atau `Nama lengkap` - Nama pembeli
- `city` - Kota tujuan
- `subdistrict` - Kecamatan (opsional tapi recommended)
- `zip` - Kode pos (opsional tapi recommended)

Kolom lain boleh ada, akan ikut kebawa ke output.

## 🆘 Troubleshooting

**"Destinasi tidak ditemukan"** → Kode pos/kota di order nggak cocok di RajaOngkir. Cek sheet `review_manual`, perbaiki manual.

**"Tidak ada gudang yang bisa melayani"** → Kurir tsb nggak melayani rute itu. Coba ganti kurir di `config` (misal tambah `sicepat`).

**Error service account** → Cek file JSON credentials udah di-paste lengkap di `secrets.toml`. Cek juga GSheets udah di-share ke email service account.

## 📝 Credits

Made for **Beauty of Angel** brand.
Powered by RajaOngkir V2 (Komerce) API + Streamlit + Google Sheets.
