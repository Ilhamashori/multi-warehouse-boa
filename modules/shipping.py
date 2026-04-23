"""
Logic inti: hitung gudang termurah per order, dengan tie-breaker.
"""
import math
import pandas as pd
from typing import Optional
from modules.rajaongkir import RajaOngkirAPI


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Jarak haversine dalam km."""
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        return 6371 * c  # radius bumi km
    except Exception:
        return float("inf")


def parse_etd(etd_str: str) -> float:
    """
    Parse string ETD jadi angka rata-rata hari.
    Contoh: "2-3 HARI" -> 2.5, "1 HARI" -> 1, "" -> 999
    """
    if not etd_str:
        return 999.0
    s = str(etd_str).upper().replace("HARI", "").replace("DAY", "").strip()
    try:
        if "-" in s:
            parts = s.split("-")
            return (float(parts[0].strip()) + float(parts[1].strip())) / 2
        return float(s.strip())
    except Exception:
        return 999.0


def get_cheapest_service(services: list) -> Optional[dict]:
    """
    Dari list service (response RajaOngkir), ambil yang paling murah.
    Kalau cost sama, pilih yang ETD terkecil.
    """
    if not services:
        return None
    # Filter yang cost-nya valid (> 0)
    valid = [s for s in services if s.get("cost") and int(s.get("cost", 0)) > 0]
    if not valid:
        return None

    # Sort by cost asc, lalu etd asc
    valid.sort(key=lambda s: (int(s["cost"]), parse_etd(s.get("etd", ""))))
    return valid[0]


def find_destination_id(api: RajaOngkirAPI, row: pd.Series) -> Optional[dict]:
    """
    Cari destination_id dari data order.
    Strategi: coba kode pos dulu (paling akurat), fallback ke subdistrict/city.
    """
    # Prioritas 1: kode pos
    zip_code = row.get("zip")
    if zip_code and str(zip_code).strip() not in ("", "nan", "None"):
        zip_str = str(zip_code).replace(".0", "").strip()
        # Padding 5 digit kalau perlu
        if zip_str.isdigit() and len(zip_str) <= 5:
            zip_str = zip_str.zfill(5)
            result = api.find_destination_by_zip(zip_str)
            if result:
                return result

    # Prioritas 2: subdistrict (kecamatan)
    subdistrict = row.get("subdistrict")
    if subdistrict and str(subdistrict).strip() not in ("", "nan", "None"):
        results = api.search_destination(str(subdistrict).strip(), limit=5)
        if results:
            # Coba cocokkan dengan kota juga kalau ada
            city = str(row.get("city", "")).strip().lower()
            if city:
                for r in results:
                    if city in str(r.get("city_name", "")).lower():
                        return r
            return results[0]

    # Prioritas 3: city
    city = row.get("city")
    if city and str(city).strip() not in ("", "nan", "None"):
        # Bersihkan prefix "Kota"/"Kab."
        city_clean = str(city).replace("Kota", "").replace("Kab.", "").replace("Kabupaten", "").strip()
        results = api.search_destination(city_clean, limit=5)
        if results:
            return results[0]

    return None


def calculate_best_warehouse(
    api: RajaOngkirAPI,
    order_row: pd.Series,
    warehouses: pd.DataFrame,
    destination: dict,
    weight_gram: int,
    couriers: str,
) -> dict:
    """
    Hitung gudang termurah untuk 1 order.

    Returns dict:
    {
        "best_warehouse": {kode, nama, ongkir, kurir, service, etd},
        "all_options": [{warehouse, cost, courier, etd}, ...],
        "tie_warning": str or None,
        "notes": str
    }
    """
    dest_id = destination.get("id")
    dest_lat = destination.get("latitude")
    dest_lon = destination.get("longitude")

    all_options = []

    for _, wh in warehouses.iterrows():
        if not wh.get("aktif", True):
            continue

        origin_id = wh.get("subdistrict_id")
        if not origin_id or pd.isna(origin_id):
            continue

        services = api.calculate_cost(
            origin_id=int(origin_id),
            destination_id=int(dest_id),
            weight_gram=weight_gram,
            couriers=couriers,
        )

        cheapest = get_cheapest_service(services)
        if not cheapest:
            continue

        # Hitung jarak kalau ada koordinat
        jarak = float("inf")
        try:
            if dest_lat and dest_lon and wh.get("latitude") and wh.get("longitude"):
                jarak = haversine_km(
                    float(wh["latitude"]), float(wh["longitude"]),
                    float(dest_lat), float(dest_lon),
                )
        except Exception:
            pass

        all_options.append({
            "kode_gudang": wh["kode_gudang"],
            "nama_gudang": wh["nama_gudang"],
            "prioritas": int(wh.get("prioritas", 99)),
            "ongkir": int(cheapest["cost"]),
            "kurir": cheapest.get("code", "").upper(),
            "service": cheapest.get("service", ""),
            "etd": cheapest.get("etd", ""),
            "etd_hari": parse_etd(cheapest.get("etd", "")),
            "jarak_km": round(jarak, 1) if jarak != float("inf") else None,
        })

    if not all_options:
        return {
            "best_warehouse": None,
            "all_options": [],
            "tie_warning": None,
            "notes": "Tidak ada gudang yang bisa melayani destination ini",
        }

    # Sort by: ongkir asc, prioritas asc, etd asc, jarak asc
    all_options.sort(
        key=lambda x: (
            x["ongkir"],
            x["prioritas"],
            x["etd_hari"],
            x["jarak_km"] if x["jarak_km"] is not None else 999999,
        )
    )

    best = all_options[0]

    # Cek tie (ongkir sama dengan opsi berikutnya)
    tie_warning = None
    tied = [o for o in all_options if o["ongkir"] == best["ongkir"]]
    if len(tied) > 1:
        gudang_tied = [o["nama_gudang"] for o in tied]
        tie_warning = f"⚠️ Ongkir sama ({best['ongkir']:,}) di {len(tied)} gudang: {', '.join(gudang_tied)}. Dipilih {best['nama_gudang']} (prioritas #{best['prioritas']})"

    return {
        "best_warehouse": best,
        "all_options": all_options,
        "tie_warning": tie_warning,
        "notes": "OK",
    }


def process_all_orders(
    df_orders: pd.DataFrame,
    warehouses: pd.DataFrame,
    api: RajaOngkirAPI,
    weight_gram: int = 1000,
    couriers: str = "jne:tiki",
    progress_callback=None,
) -> dict:
    """
    Proses semua order, return dict berisi:
    - df_hasil: DataFrame hasil pengelompokan
    - df_review: DataFrame order yang perlu review manual
    - summary: dict summary per gudang
    - notifications: list tie warnings
    """
    results = []
    review_manual = []
    notifications = []

    total = len(df_orders)

    for idx, row in df_orders.iterrows():
        if progress_callback:
            progress_callback((idx + 1) / total, f"Proses order {idx + 1}/{total}")

        # Step 1: cari destination_id
        destination = find_destination_id(api, row)

        if not destination:
            review_manual.append({
                "order_id": row.get("order_id", ""),
                "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                "kota_tujuan": row.get("city", ""),
                "subdistrict": row.get("subdistrict", ""),
                "zip": row.get("zip", ""),
                "alasan": "Destinasi tidak ditemukan di RajaOngkir (kode pos/kecamatan/kota tidak cocok)",
            })
            # Tambahkan row dengan kolom kosong
            row_dict = row.to_dict()
            row_dict["_gudang_rekomendasi"] = "REVIEW MANUAL"
            row_dict["_ongkir_rekomendasi"] = ""
            row_dict["_kurir_rekomendasi"] = ""
            row_dict["_catatan"] = "Destinasi tidak ditemukan"
            results.append(row_dict)
            continue

        # Step 2: hitung gudang termurah
        calc = calculate_best_warehouse(
            api=api,
            order_row=row,
            warehouses=warehouses,
            destination=destination,
            weight_gram=weight_gram,
            couriers=couriers,
        )

        best = calc["best_warehouse"]
        row_dict = row.to_dict()

        if not best:
            row_dict["_gudang_rekomendasi"] = "TIDAK ADA"
            row_dict["_ongkir_rekomendasi"] = ""
            row_dict["_kurir_rekomendasi"] = ""
            row_dict["_catatan"] = calc["notes"]
            review_manual.append({
                "order_id": row.get("order_id", ""),
                "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                "kota_tujuan": row.get("city", ""),
                "subdistrict": row.get("subdistrict", ""),
                "zip": row.get("zip", ""),
                "alasan": calc["notes"],
            })
        else:
            row_dict["_gudang_rekomendasi"] = best["nama_gudang"]
            row_dict["_ongkir_rekomendasi"] = best["ongkir"]
            row_dict["_kurir_rekomendasi"] = f"{best['kurir']} {best['service']} ({best['etd']})"
            row_dict["_catatan"] = "OK"
            if calc["tie_warning"]:
                row_dict["_catatan"] = calc["tie_warning"]
                notifications.append({
                    "order_id": row.get("order_id", ""),
                    "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                    "warning": calc["tie_warning"],
                    "opsi": calc["all_options"],
                })

        results.append(row_dict)

    df_hasil = pd.DataFrame(results)

    # Reorder kolom: taruh kolom rekomendasi di depan
    priority_cols = ["_gudang_rekomendasi", "_ongkir_rekomendasi", "_kurir_rekomendasi", "_catatan"]
    other_cols = [c for c in df_hasil.columns if c not in priority_cols]
    df_hasil = df_hasil[priority_cols + other_cols]
    # Rename biar rapih
    df_hasil = df_hasil.rename(columns={
        "_gudang_rekomendasi": "Gudang Rekomendasi",
        "_ongkir_rekomendasi": "Ongkir",
        "_kurir_rekomendasi": "Kurir",
        "_catatan": "Catatan",
    })

    # Sort by gudang rekomendasi biar kelompok
    df_hasil = df_hasil.sort_values(by="Gudang Rekomendasi", kind="stable").reset_index(drop=True)

    # Summary per gudang
    summary = df_hasil.groupby("Gudang Rekomendasi").size().to_dict()

    return {
        "df_hasil": df_hasil,
        "df_review": pd.DataFrame(review_manual),
        "summary": summary,
        "notifications": notifications,
    }
