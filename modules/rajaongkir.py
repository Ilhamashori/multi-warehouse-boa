"""
RajaOngkir API V2 (via Komerce) wrapper — DENGAN CACHE
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
        # Cache in-memory
        self._cache_destination = {}  # keyword -> list of destinations
        self._cache_cost = {}  # (origin_id, dest_id, weight, couriers) -> services
        # Counter buat monitoring
        self.hits = 0
        self.cache_hits = 0

    def search_destination(self, keyword: str, limit: int = 10) -> list:
        cache_key = f"{keyword.lower().strip()}_{limit}"
        if cache_key in self._cache_destination:
            self.cache_hits += 1
            return self._cache_destination[cache_key]

        url = f"{self.BASE_URL}/destination/domestic-destination"
        params = {"search": keyword, "limit": limit, "offset": 0}
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
            self.hits += 1
            r.raise_for_status()
            payload = r.json()
            result = payload.get("data", []) or []
            self._cache_destination[cache_key] = result
            return result
        except requests.exceptions.RequestException as e:
            print(f"[RajaOngkir] Error search_destination({keyword}): {e}")
            return []

    def calculate_cost(self, origin_id, destination_id, weight_gram, couriers="jne:tiki", price_sort="lowest") -> list:
        cache_key = (int(origin_id), int(destination_id), int(weight_gram), couriers, price_sort)
        if cache_key in self._cache_cost:
            self.cache_hits += 1
            return self._cache_cost[cache_key]

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
            self.hits += 1
            r.raise_for_status()
            payload = r.json()
            result = payload.get("data", []) or []
            self._cache_cost[cache_key] = result
            return result
        except requests.exceptions.RequestException as e:
            print(f"[RajaOngkir] Error calculate_cost({origin_id}->{destination_id}): {e}")
            return []

    def find_destination_by_zip(self, zip_code: str) -> Optional[dict]:
        results = self.search_destination(str(zip_code), limit=5)
        if not results:
            return None
        for r in results:
            if str(r.get("zip_code")) == str(zip_code):
                return r
        return results[0]

    def get_stats(self) -> dict:
        total = self.hits + self.cache_hits
        saved_pct = (self.cache_hits / total * 100) if total > 0 else 0
        return {
            "api_hits": self.hits,
            "cache_hits": self.cache_hits,
            "total_calls": total,
            "hemat_pct": round(saved_pct, 1),
        }


def safe_request_with_retry(func, *args, max_retry: int = 2, delay: float = 1.0, **kwargs):
    for attempt in range(max_retry + 1):
        result = func(*args, **kwargs)
        if result:
            return result
        if attempt < max_retry:
            time.sleep(delay)
    return result
