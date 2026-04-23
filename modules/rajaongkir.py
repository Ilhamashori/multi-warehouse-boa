"""
RajaOngkir API V2 (via Komerce) wrapper
Base URL: https://rajaongkir.komerce.id/api/v1
"""
import requests
import time
from typing import Optional


class RajaOngkirAPI:
    BASE_URL = "https://rajaongkir.komerce.id/api/v1"

    def __init__(self, api_key: str, timeout: int = 15):
        if not api_key:
            raise ValueError("API key RajaOngkir tidak boleh kosong")
        self.api_key = api_key
        self.timeout = timeout
        self.headers = {"key": api_key}

    def search_destination(self, keyword: str, limit: int = 10) -> list:
        """
        Cari destination (kecamatan) berdasarkan keyword.
        Keyword bisa nama kota, kecamatan, atau kode pos.

        Returns: list of dict, tiap dict berisi id, label, kelurahan, kecamatan, kota, provinsi, kode_pos
        """
        url = f"{self.BASE_URL}/destination/domestic-destination"
        params = {"search": keyword, "limit": limit, "offset": 0}

        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", []) or []
        except requests.exceptions.RequestException as e:
            print(f"[RajaOngkir] Error search_destination({keyword}): {e}")
            return []

    def calculate_cost(
        self,
        origin_id: int,
        destination_id: int,
        weight_gram: int,
        couriers: str = "jne:tiki",
        price_sort: str = "lowest",
    ) -> list:
        """
        Hitung ongkir dari origin ke destination.

        Args:
            origin_id: subdistrict_id gudang asal
            destination_id: subdistrict_id tujuan
            weight_gram: berat dalam gram
            couriers: kurir yg dicek, pisah titik dua. Contoh: "jne:tiki"
            price_sort: "lowest" atau "highest"

        Returns: list of dict, tiap dict: name, code, service, description, cost, etd
        """
        url = f"{self.BASE_URL}/calculate/domestic-cost"
        data = {
            "origin": str(origin_id),
            "destination": str(destination_id),
            "weight": str(weight_gram),
            "courier": couriers,
            "price": price_sort,
        }
        headers = {**self.headers, "Content-Type": "application/x-www-form-urlencoded"}

        try:
            r = requests.post(url, headers=headers, data=data, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", []) or []
        except requests.exceptions.RequestException as e:
            print(f"[RajaOngkir] Error calculate_cost({origin_id}->{destination_id}): {e}")
            return []

    def find_destination_by_zip(self, zip_code: str) -> Optional[dict]:
        """
        Shortcut: cari destination berdasarkan kode pos.
        Return destination pertama yang cocok atau None.
        """
        results = self.search_destination(str(zip_code), limit=5)
        if not results:
            return None
        # Filter yang kode pos-nya persis cocok
        for r in results:
            if str(r.get("zip_code")) == str(zip_code):
                return r
        # Kalau nggak ada yang persis, return yang pertama
        return results[0]


def safe_request_with_retry(func, *args, max_retry: int = 2, delay: float = 1.0, **kwargs):
    """Helper untuk retry request yang gagal (rate limit, network hiccup)."""
    for attempt in range(max_retry + 1):
        result = func(*args, **kwargs)
        if result:
            return result
        if attempt < max_retry:
            time.sleep(delay)
    return result
