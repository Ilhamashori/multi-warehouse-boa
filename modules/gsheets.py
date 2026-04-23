"""
Google Sheets wrapper pakai gspread + service account.
"""
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from typing import Optional


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GSheetsClient:
    def __init__(self, credentials_dict: dict, spreadsheet_id: str):
        """
        Args:
            credentials_dict: isi JSON service account (dict)
            spreadsheet_id: ID spreadsheet dari URL
        """
        creds = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
        self.client = gspread.authorize(creds)
        self.spreadsheet_id = spreadsheet_id
        self._ss = None

    @property
    def spreadsheet(self):
        if self._ss is None:
            self._ss = self.client.open_by_key(self.spreadsheet_id)
        return self._ss

    def read_sheet(self, sheet_name: str) -> pd.DataFrame:
        """Baca sheet jadi DataFrame."""
        ws = self.spreadsheet.worksheet(sheet_name)
        records = ws.get_all_records()
        return pd.DataFrame(records)

    def append_row(self, sheet_name: str, row: list):
        """Append 1 baris ke sheet."""
        ws = self.spreadsheet.worksheet(sheet_name)
        ws.append_row(row, value_input_option="USER_ENTERED")

    def append_rows(self, sheet_name: str, rows: list):
        """Append banyak baris sekaligus."""
        if not rows:
            return
        ws = self.spreadsheet.worksheet(sheet_name)
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    def create_or_replace_sheet(self, sheet_name: str, df: pd.DataFrame):
        """
        Bikin sheet baru dengan isi df. Kalau udah ada, ganti isinya.
        Dipakai buat sheet hasil per tanggal (hasil_YYYY-MM-DD).
        """
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=max(len(df) + 10, 100),
                cols=max(len(df.columns) + 2, 30),
            )

        if df.empty:
            return

        # Write header + data
        data = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")

    def sheet_exists(self, sheet_name: str) -> bool:
        try:
            self.spreadsheet.worksheet(sheet_name)
            return True
        except gspread.WorksheetNotFound:
            return False
