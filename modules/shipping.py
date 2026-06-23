"""
Logic inti: hitung gudang termurah per order.

Perubahan penting vs versi lama:
- find_destination_id() sekarang balikin (destination, had_error). had_error=True
  artinya cek destinasi sempat kena error API/rate-limit (bukan beneran nggak ada).
- find_destination_id() lebih tahan banting: zip -> subdistrict(+city) -> subdistrict
  -> city, dengan normalisasi nama kota (buang "Kota"/"Kab."/"Kabupaten").
- process_all_orders() punya RETRY PASS: order yang gagal gara-gara error transient
  dikumpulin, dijeda sebentar, lalu diproses ulang. Cuma yang BENER-BENER gagal
  setelah retry yang masuk REVIEW MANUAL.
"""
import math
import time
import pandas as pd
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _clean_city(city):
    return (str(city).replace("Kota", "").replace("Kabupaten", "")
            .replace("Kab.", "").replace("Adm.", "").strip())


def assign_warehouse_by_province(province):
    """Tentukan nama gudang dari PROVINSI tujuan (tanpa API).
    Return nama_gudang, atau None kalau provinsi kosong/tak dikenal (→ fallback API).

    Aturan (final, sesuai kesepakatan):
      - Lampung                                  -> Tangerang
      - Sumatra selain Lampung (Sumut/Aceh/Sumbar/
        Riau/Kepri/Jambi/Sumsel/Bengkulu/Babel)  -> Medan
      - Jawa/Bali/Banten/DKI/Yogya/NTB/NTT       -> Tangerang
      - Kalimantan (semua)                       -> Makassar
      - Sulawesi/Gorontalo/Maluku/Papua          -> Makassar
    """
    p = str(province).lower().strip()
    if not p or p in ("nan", "none"):
        return None

    if "lampung" in p:
        return "Tangerang"

    sumatra_kw = ["sumatera", "sumatra", "aceh", "riau", "kepri", "jambi",
                  "bengkulu", "bangka", "belitung", "nanggroe"]
    if any(k in p for k in sumatra_kw):
        return "Medan"

    timur_kw = ["kalimantan", "sulawesi", "gorontalo", "maluku", "papua"]
    if any(k in p for k in timur_kw):
        return "Makassar"

    barat_kw = ["jawa", "jakarta", "dki", "banten", "bali", "yogya", "diy",
                "nusa tenggara", "ntt", "ntb"]
    if any(k in p for k in barat_kw):
        return "Tangerang"

    return None  # tak dikenal → fallback ke RajaOngkir (hybrid)


def find_destination_id(api, row):
    """Cari ID destinasi RajaOngkir. Return (destination_dict | None, had_error: bool)."""
    had_error = False

    # 1) by ZIP (paling akurat)
    zip_code = row.get("zip")
    if zip_code and str(zip_code).strip() not in ("", "nan", "None"):
        zip_str = str(zip_code).replace(".0", "").strip()
        if zip_str.isdigit() and len(zip_str) <= 5:
            zip_str = zip_str.zfill(5)
            results, err = api._search(zip_str, limit=10)
            had_error = had_error or err
            if results:
                for r in results:
                    if str(r.get("zip_code")) == zip_str:
                        return r, had_error
                return results[0], had_error

    city_clean = _clean_city(row.get("city", ""))

    # 2) by subdistrict (difilter city kalau ada)
    subdistrict = row.get("subdistrict")
    if subdistrict and str(subdistrict).strip() not in ("", "nan", "None"):
        sub = str(subdistrict).strip()
        results, err = api._search(sub, limit=10)
        had_error = had_error or err
        if results:
            if city_clean:
                for r in results:
                    if city_clean.lower() in str(r.get("city_name", "")).lower():
                        return r, had_error
            return results[0], had_error
        # gabung subdistrict + city
        if city_clean:
            results, err = api._search(f"{sub} {city_clean}", limit=10)
            had_error = had_error or err
            if results:
                return results[0], had_error

    # 3) by city aja
    if city_clean:
        results, err = api._search(city_clean, limit=10)
        had_error = had_error or err
        if results:
            return results[0], had_error

    return None, had_error


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
        tie_warning = (f"⚠️ Ongkir sama ({best['ongkir']:,}) di {len(tied)} gudang: "
                       f"{', '.join(gudang_tied)}. Dipilih {best['nama_gudang']} "
                       f"(prioritas #{best['prioritas']})")
    return {"best_warehouse": best, "all_options": all_options,
            "tie_warning": tie_warning, "notes": "OK"}


def _process_one(api, row, warehouses, weight_gram, couriers,
                 pakai_mapping=True, hitung_ongkir=True, active_names=None):
    """Proses 1 order.
    Urutan: mapping provinsi (cepat, no/low API) -> fallback RajaOngkir (hybrid)."""
    if active_names is None:
        active_names = set(warehouses["nama_gudang"].tolist())

    # --- Jalur cepat: mapping provinsi ---
    if pakai_mapping:
        target = assign_warehouse_by_province(row.get("province"))
        if target and target in active_names:
            if not hitung_ongkir:
                # gudang sudah pasti dari provinsi → tanpa API sama sekali
                return {"status": "ok_map", "gudang": target,
                        "ongkir": "", "kurir": "(mapping provinsi)"}
            # ambil ongkir untuk gudang itu SAJA (bukan semua gudang)
            destination, had_error = find_destination_id(api, row)
            if destination:
                wh_row = warehouses[warehouses["nama_gudang"] == target].iloc[0]
                origin_id = wh_row.get("subdistrict_id")
                services = []
                if origin_id is not None and not pd.isna(origin_id):
                    services = api.calculate_cost(int(origin_id), int(destination["id"]),
                                                  weight_gram, couriers)
                cheapest = get_cheapest_service(services)
                if cheapest:
                    return {"status": "ok_map", "gudang": target,
                            "ongkir": int(cheapest["cost"]),
                            "kurir": f"{cheapest.get('code','').upper()} "
                                     f"{cheapest.get('service','')} ({cheapest.get('etd','')})"}
            # gudang sudah jelas dari provinsi, ongkir gagal → tetap assign gudangnya
            return {"status": "ok_map", "gudang": target, "ongkir": "",
                    "kurir": "(mapping provinsi)"}

    # --- Fallback hybrid: RajaOngkir cari gudang termurah ---
    destination, had_error = find_destination_id(api, row)
    if not destination:
        return {"status": "no_dest", "had_error": had_error}
    calc = calculate_best_warehouse(api, row, warehouses, destination, weight_gram, couriers)
    if not calc["best_warehouse"]:
        return {"status": "no_wh", "calc": calc}
    return {"status": "ok", "best": calc["best_warehouse"], "calc": calc}


def _run_batch(api, df_valid, idx_list, warehouses, weight_gram, couriers,
               workers, progress_callback, label, pakai_mapping=True,
               hitung_ongkir=True, active_names=None):
    """Jalankan _process_one untuk sekumpulan index. Paralel kalau workers>1.
    Progress HANYA di-update dari thread utama (aman buat Streamlit)."""
    outcomes = {}
    total = len(idx_list)
    if total == 0:
        return outcomes

    def _job(idx):
        return _process_one(api, df_valid.loc[idx], warehouses, weight_gram, couriers,
                            pakai_mapping=pakai_mapping, hitung_ongkir=hitung_ongkir,
                            active_names=active_names)

    if workers <= 1:  # mode serial
        for n, idx in enumerate(idx_list):
            outcomes[idx] = _job(idx)
            if progress_callback:
                progress_callback((n + 1) / total, f"{label} {n + 1}/{total}")
        return outcomes

    # mode paralel
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_job, idx): idx for idx in idx_list}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                outcomes[idx] = fut.result()
            except Exception as e:
                outcomes[idx] = {"status": "no_dest", "had_error": True, "err": str(e)}
            done += 1
            if progress_callback:
                progress_callback(done / total, f"{label} {done}/{total}")
    return outcomes


def process_all_orders(df_orders, warehouses, api, weight_gram=1000,
                       couriers="jne:tiki", progress_callback=None,
                       retry_delay=5.0, workers=5,
                       pakai_mapping=True, hitung_ongkir=True):
    # FILTER: hanya order dengan order_id numerik valid DAN city ada
    df_orders = df_orders.reset_index(drop=True)
    mask = df_orders.apply(is_valid_order, axis=1)
    df_valid = df_orders[mask].reset_index(drop=True)
    baris_skipped = len(df_orders) - len(df_valid)

    all_idx = list(df_valid.index)
    active_names = set(warehouses["nama_gudang"].tolist())

    # --- Pass 1 ---
    outcomes = _run_batch(api, df_valid, all_idx, warehouses, weight_gram, couriers,
                          workers, progress_callback, "Proses",
                          pakai_mapping=pakai_mapping, hitung_ongkir=hitung_ongkir,
                          active_names=active_names)

    # --- Pass 2: retry yang gagal gara-gara error transient ---
    retry_idx = [idx for idx in all_idx
                 if outcomes[idx]["status"] == "no_dest" and outcomes[idx].get("had_error")]
    if retry_idx:
        if progress_callback:
            progress_callback(0.0, f"Retry {len(retry_idx)} order yang sempat error API...")
        time.sleep(retry_delay)
        retry_out = _run_batch(api, df_valid, retry_idx, warehouses, weight_gram, couriers,
                               workers, progress_callback, "Retry",
                               pakai_mapping=pakai_mapping, hitung_ongkir=hitung_ongkir,
                               active_names=active_names)
        outcomes.update(retry_out)

    # --- Assemble hasil ---
    results, review_manual, notifications = [], [], []
    for idx, row in df_valid.iterrows():
        out = outcomes[idx]
        row_dict = row.to_dict()

        if out["status"] == "ok":
            best, calc = out["best"], out["calc"]
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

        elif out["status"] == "ok_map":
            row_dict["_gudang_rekomendasi"] = out["gudang"]
            row_dict["_ongkir_rekomendasi"] = out.get("ongkir", "")
            row_dict["_kurir_rekomendasi"] = out.get("kurir", "")
            row_dict["_catatan"] = "OK (mapping provinsi)"

        elif out["status"] == "no_wh":
            row_dict["_gudang_rekomendasi"] = "TIDAK ADA"
            row_dict["_ongkir_rekomendasi"] = ""
            row_dict["_kurir_rekomendasi"] = ""
            row_dict["_catatan"] = out["calc"]["notes"]
            review_manual.append({
                "order_id": row.get("order_id", ""),
                "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                "kota_tujuan": row.get("city", ""),
                "subdistrict": row.get("subdistrict", ""),
                "zip": row.get("zip", ""),
                "alasan": out["calc"]["notes"],
            })

        else:  # no_dest
            alasan = "Destinasi tidak ditemukan di RajaOngkir"
            catatan = "Destinasi tidak ditemukan"
            if out.get("had_error"):
                alasan = "Gagal cek destinasi (error API/rate-limit) walau sudah retry — coba proses ulang"
                catatan = "Error API (coba ulang)"
            row_dict["_gudang_rekomendasi"] = "REVIEW MANUAL"
            row_dict["_ongkir_rekomendasi"] = ""
            row_dict["_kurir_rekomendasi"] = ""
            row_dict["_catatan"] = catatan
            review_manual.append({
                "order_id": row.get("order_id", ""),
                "nama_pembeli": row.get("name", row.get("Nama lengkap", "")),
                "kota_tujuan": row.get("city", ""),
                "subdistrict": row.get("subdistrict", ""),
                "zip": row.get("zip", ""),
                "alasan": alasan,
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

    def sort_key(x):
        v = str(x).upper()
        if v in ("REVIEW MANUAL", "TIDAK ADA"):
            return (2, v)
        return (1, v)

    df_hasil = df_hasil.sort_values(by="Gudang Rekomendasi",
                                    key=lambda s: s.map(sort_key)).reset_index(drop=True)
    df_hasil.index = df_hasil.index + 1
    summary = df_hasil.groupby("Gudang Rekomendasi").size().to_dict()

    return {"df_hasil": df_hasil, "df_review": pd.DataFrame(review_manual),
            "summary": summary, "notifications": notifications, "baris_skipped": baris_skipped}
