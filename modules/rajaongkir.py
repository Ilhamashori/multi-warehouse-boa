"""
RajaOngkir API V2 (via Komerce) wrapper — dengan cache, retry, dan backoff.

Perubahan penting vs versi lama:
- Setiap request HTTP sekarang punya RETRY + exponential backoff.
- Status 429 (rate limit) di-handle khusus: hormati header Retry-After kalau ada,
  kalau nggak pakai backoff. Ini yang dulu bikin order kena REVIEW MANUAL palsu.
- Ada throttle opsional (jeda antar call) biar nggak nge-gas API.
- Error API dibedakan dari "data kosong" lewat flag is_hard_error, jadi caller
  bisa retry yang error tanpa keburu cap "Destinasi tidak ditemukan".
"""
import requests
import time
import random
from typing import Optional


class RajaOngkirAPI:
    BASE_URL = "https://rajaongkir.komerce.id/api/v1"

    def __init__(self, api_key: str, timeout: int = 15, max_retry: int = 4,
                 throttle: float = 0.0):
        if not api_key:
            raise ValueError("API key RajaOngkir tidak boleh kosong")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retry = max_retry          # jumlah retry tambahan per call
        self.throttle = throttle            # jeda minimum antar call (detik)
        self.headers = {"key": api_key}
        # Cache in-memory
        self._cache_destination = {}        # keyword -> list destinations
        self._cache_cost = {}               # (origin,dest,weight,couriers,sort) -> services
        # Counter buat monitoring
        self.hits = 0
        self.cache_hits = 0
        self.errors = 0                     # call yang gagal SETELAH semua retry
        self._last_call = 0.0

    # ---------- internal ----------
    def _sleep_throttle(self):
        if self.throttle > 0:
            wait = self.throttle - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.time()

    def _request(self, method, url, **kwargs):
        """HTTP request dengan retry + backoff.
        Return (payload_dict | None, is_hard_error: bool).
        is_hard_error=True => gagal karena API/jaringan (BUKAN data kosong).
        """
        for attempt in range(self.max_retry + 1):
            self._sleep_throttle()
            try:
                r = requests.request(method, url, timeout=self.timeout, **kwargs)
                self.hits += 1

                if r.status_code == 429:  # rate limit
                    ra = r.headers.get("Retry-After", "")
                    wait = float(ra) if str(ra).strip().isdigit() else (2 ** attempt)
                    if attempt < self.max_retry:
                        time.sleep(wait + random.uniform(0, 0.5))
                        continue
                    self.errors += 1
                    return None, True

                if 500 <= r.status_code < 600:  # server error → layak retry
                    if attempt < self.max_retry:
                        time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                        continue
                    self.errors += 1
                    return None, True

                r.raise_for_status()
                return r.json(), False

            except requests.exceptions.RequestException as e:
                if attempt < self.max_retry:
                    time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                    continue
                self.errors += 1
                print(f"[RajaOngkir] gagal {method} {url} setelah {self.max_retry + 1}x: {e}")
                return None, True
        return None, True

    # ---------- public ----------
    def _search(self, keyword: str, limit: int = 10):
        """Cari destinasi. Return (list, is_hard_error)."""
        key = f"{str(keyword).lower().strip()}_{limit}"
        if key in self._cache_destination:
            self.cache_hits += 1
            return self._cache_destination[key], False
        url = f"{self.BASE_URL}/destination/domestic-destination"
        params = {"search": keyword, "limit": limit, "offset": 0}
        payload, hard_err = self._request("GET", url, headers=self.headers, params=params)
        if payload is None:
            return [], hard_err     # JANGAN cache hasil error
        result = payload.get("data", []) or []
        self._cache_destination[key] = result
        return result, False

    def search_destination(self, keyword: str, limit: int = 10) -> list:
        """Kompat lama: balikin list aja."""
        result, _ = self._search(keyword, limit)
        return result

    def calculate_cost(self, origin_id, destination_id, weight_gram,
                       couriers="jne:tiki", price_sort="lowest") -> list:
        key = (int(origin_id), int(destination_id), int(weight_gram), couriers, price_sort)
        if key in self._cache_cost:
            self.cache_hits += 1
            return self._cache_cost[key]
        url = f"{self.BASE_URL}/calculate/domestic-cost"
        data = {
            "origin": str(origin_id),
            "destination": str(destination_id),
            "weight": str(weight_gram),
            "courier": couriers,
            "price": price_sort,
        }
        headers = {**self.headers, "Content-Type": "application/x-www-form-urlencoded"}
        payload, hard_err = self._request("POST", url, headers=headers, data=data)
        if payload is None:
            return []               # gagal → kosong, JANGAN cache
        result = payload.get("data", []) or []
        self._cache_cost[key] = result
        return result

    def find_destination_by_zip(self, zip_code: str) -> Optional[dict]:
        results, _ = self._search(str(zip_code), limit=10)
        if not results:
            return None
        for r in results:
            if str(r.get("zip_code")) == str(zip_code):
                return r
        return results[0]

    def get_stats(self) -> dict:
        total = self.hits + self.cache_hits
        saved = (self.cache_hits / total * 100) if total else 0
        return {
            "api_hits": self.hits,
            "cache_hits": self.cache_hits,
            "errors": self.errors,
            "total_calls": total,
            "hemat_pct": round(saved, 1),
        }
