"""
Script sekali-jalan untuk cari subdistrict_id dari 4 gudang berdasarkan kode pos.

CARA PAKAI:
1. Set API_KEY di bawah (atau via env var RAJAONGKIR_KEY)
2. Run: python scripts/fetch_warehouse_ids.py
3. Copy hasilnya ke sheet `master_gudang` di Google Sheets (kolom subdistrict_id)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.rajaongkir import RajaOngkirAPI


# ==== GANTI API KEY DI SINI, ATAU SET ENV VAR ====
API_KEY = os.getenv("RAJAONGKIR_KEY", "PASTE_API_KEY_KAMU_DI_SINI")

WAREHOUSES = [
    {"kode": "G01", "nama": "Tangerang",     "zip": "15810"},
    {"kode": "G02", "nama": "Medan",         "zip": "20212"},
    {"kode": "G03", "nama": "Makassar",      "zip": "90111"},
    {"kode": "G04", "nama": "Ambon/Maluku",  "zip": "97124"},
]


def main():
    if API_KEY == "PASTE_API_KEY_KAMU_DI_SINI":
        print("❌ ERROR: Harap set API_KEY di file ini atau set env var RAJAONGKIR_KEY")
        return

    api = RajaOngkirAPI(API_KEY)

    print("\n" + "=" * 70)
    print("🔍 MENCARI SUBDISTRICT_ID UNTUK 4 GUDANG")
    print("=" * 70)

    results = []
    for wh in WAREHOUSES:
        print(f"\n📦 {wh['kode']} - {wh['nama']} (Kode Pos: {wh['zip']})")
        print("-" * 70)

        destinations = api.search_destination(wh["zip"], limit=10)

        if not destinations:
            print(f"  ❌ Tidak ditemukan destination untuk kode pos {wh['zip']}")
            continue

        print(f"  Ditemukan {len(destinations)} hasil:")
        for i, d in enumerate(destinations, 1):
            label = d.get("label", "")
            print(f"   {i}. ID={d.get('id')} | {label}")
            print(f"       Kel: {d.get('subdistrict_name')}, Kec: {d.get('district_name')}, "
                  f"Kota: {d.get('city_name')}, Prov: {d.get('province_name')}, Zip: {d.get('zip_code')}")

        # Auto-pilih yang kode pos-nya paling cocok
        best = None
        for d in destinations:
            if str(d.get("zip_code", "")) == str(wh["zip"]):
                best = d
                break
        if not best:
            best = destinations[0]

        print(f"\n  ✅ REKOMENDASI: ID={best.get('id')} - {best.get('label')}")
        results.append({
            "kode": wh["kode"],
            "nama": wh["nama"],
            "subdistrict_id": best.get("id"),
            "label": best.get("label", ""),
            "latitude": best.get("latitude"),
            "longitude": best.get("longitude"),
        })

    print("\n" + "=" * 70)
    print("📋 RINGKASAN UNTUK DI-COPY KE master_gudang DI GOOGLE SHEETS:")
    print("=" * 70)
    print(f"{'Kode':<6}{'Nama':<15}{'Subdistrict ID':<18}{'Label':<45}")
    print("-" * 85)
    for r in results:
        print(f"{r['kode']:<6}{r['nama']:<15}{str(r['subdistrict_id']):<18}{r['label'][:45]:<45}")

    print("\n✅ Selesai! Copy subdistrict_id di atas ke kolom 'subdistrict_id' di sheet master_gudang.")


if __name__ == "__main__":
    main()
