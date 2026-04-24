"""
Logic inti: hitung gudang termurah per order.
"""
import math
import pandas as pd
from typing import Optional
from modules.rajaongkir import RajaOngkirAPI


def haversine_km(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        return 6371 * 2 * math.asin(math.sqrt(a))
    except Exception:
        return float("inf")


def parse_etd(etd_str):
    if not etd_str:
        return 999.0
    s = str(etd_str).upper().replace("HARI", "").replace("DAY", "").strip()
    try:
        if "-" in s:
            p = s.split("-")
            return (float(p[0].strip()) + float(p[1].strip())) / 2
        return float(s.strip())
    except Exception:
        return 999.0


def get_cheapest_service(services):
    if not services:
        return None
    valid = [s for s in services if s.get("cost") and int(s.get("cost", 0)) > 0]
    if not valid:
        return None
    valid.sort(key=lambda s: (int(s["cost"]), parse_etd(s.get("etd", ""))))
    return valid[0]


def is_valid_order(row):
    """Order valid HARUS punya order_id numerik DAN city terisi."""
    order_id = row.get("order_id")
    city = row.get("city")
    try:
        oid = str(order_id).strip()
        if oid in ("", "nan", "None", "NaN"):
            return False
        float(oid)
    except (ValueError, TypeError):
        return False
    if not city or str(city).strip() in ("", "nan", "None", "NaN"):
        return False
    return True


def find_destination_id(api, row):
    zip_code = row.get("zip")
    if zip_code and str(zip_code).strip() not in ("", "nan", "None"):
        zip_str = str(zip_code).replace(".0", "").strip()
        if zip_str.isdigit() and len(zip_str) <= 5:
            zip_str = zip_str.zfill(5)
            result = api.find_destination_by_zip(zip_str)
            if result:
                return result
    subdistrict = row.get("subdistrict")
    if subdistrict and str(subdistrict).strip() not in ("", "nan", "None"):
        results = api.search_destination(str(subdistrict).strip(), limit=5)
        if results:
            city = str(row.get("city", "")).strip().lower()
            if city:
                for r in results:
                    if city in str(r.get("city_name", "")).lower():
                        return r
            return results[0]
    city = row.get("city")
    if city and str(city).strip() not in ("", "nan", "None"):
        city_clean = str(city).replace("Kota", "").replace("Kab.", "").replace("Kabupaten", "").strip()
        results = api.search_destination(city_clean, limit=5)
        if results:
            return results[0]
    return None


def calculate_best_warehouse(api, row, warehouses, destination, weight_gram, couriers):
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
        services = api.calculate_cost(int(origin_id), int(dest_id), weight_gram, couriers)
        cheapest = get_cheapest_service(services)
        if not cheapest:
            continue
        jarak = float("inf")
        try:
            if dest_lat and dest_lon and wh.get("latitude") and wh.get("longitude"):
                jarak = haversine_km(float(wh["latitude"]), float(wh["longitude"]),
                                     float(dest_lat), float(dest_lon))
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
        return {"best_warehouse": None, "all_options": [], "tie_warning": None,
                "notes": "Tidak ada gudang yang bisa melayani destination ini"}
    all_options.sort(key=lambda x: (x["ongkir"], x["prioritas"], x["etd_hari"],
                                     x["jarak_km"] if x["jarak_km"] is not None else 999999))
    best = all_options[0]
    tie_warning = None
    tied = [o for o in all_options if o["ongkir"] == best["ongkir"]]
    if len(tied) > 1:
        gudang_tied = [o["nama_gudang"] for o in tied]
        tie_warning = f"⚠️ Ongkir sama ({best['ongkir']:,}) di {len(tied)} gudang: {', '.join(gudang_tied)}. Dipilih {best['nama_gudang']} (prioritas #{best['prioritas']})"
    return {"best_warehouse": best, "all_options": all_options,
            "tie_warning": tie_warning, "notes": "OK"}


def process_all_orders(df_orders, warehouses, api, weight_gram=1000, couriers="jne:tiki", progress_callback=None):
    # FILTER: hanya order dengan order_id numerik valid DAN city ada
    df_orders = df_orders.reset_index(drop=True)
    mask = df_orders.apply(is_valid_order, axis=1)
    df_valid = df_orders[mask].reset_index(drop=True)
    baris_skipped = len(df_orders) - len(df_valid)

    results, review_manual, notifications = [], [], []
    total = len(df_valid)

    for idx, row in df_valid.iterrows():
        if progress_callback:
            progress_callback((idx + 1) / max(total, 1), f"Proses order {idx + 1}/{total}")
        destination = find_destination_id(api, row)
        row_dict = row.to_dict()

        if not destination:
            review_manual.append({
                "order_id": row.get("order_id", ""),
                "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                "kota_tujuan": row.get("city", ""),
                "subdistrict": row.get("subdistrict", ""),
                "zip": row.get("zip", ""),
                "alasan": "Destinasi tidak ditemukan di RajaOngkir",
            })
            row_dict["_gudang_rekomendasi"] = "REVIEW MANUAL"
            row_dict["_ongkir_rekomendasi"] = ""
            row_dict["_kurir_rekomendasi"] = ""
            row_dict["_catatan"] = "Destinasi tidak ditemukan"
            results.append(row_dict)
            continue

        calc = calculate_best_warehouse(api, row, warehouses, destination, weight_gram, couriers)
        best = calc["best_warehouse"]
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
    if df_hasil.empty:
        return {"df_hasil": df_hasil, "df_review": pd.DataFrame(review_manual),
                "summary": {}, "notifications": notifications, "baris_skipped": baris_skipped}

    priority_cols = ["_gudang_rekomendasi", "_ongkir_rekomendasi", "_kurir_rekomendasi", "_catatan"]
    other_cols = [c for c in df_hasil.columns if c not in priority_cols]
    df_hasil = df_hasil[priority_cols + other_cols]
    df_hasil = df_hasil.rename(columns={
        "_gudang_rekomendasi": "Gudang Rekomendasi",
        "_ongkir_rekomendasi": "Ongkir",
        "_kurir_rekomendasi": "Kurir",
        "_catatan": "Catatan",
    })
    # Sort: order berhasil duluan, REVIEW MANUAL / TIDAK ADA di akhir
    def sort_key(x):
        v = str(x).upper()
        if v in ("REVIEW MANUAL", "TIDAK ADA"):
            return (2, v)
        return (1, v)
    df_hasil = df_hasil.sort_values(by="Gudang Rekomendasi", key=lambda s: s.map(sort_key)).reset_index(drop=True)
    df_hasil.index = df_hasil.index + 1  # Mulai dari 1
    summary = df_hasil.groupby("Gudang Rekomendasi").size().to_dict()

    return {"df_hasil": df_hasil, "df_review": pd.DataFrame(review_manual),
            "summary": summary, "notifications": notifications, "baris_skipped": baris_skipped}
