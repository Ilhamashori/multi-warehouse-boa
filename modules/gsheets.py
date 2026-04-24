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
        ws = self.spreadsheet.worksheet(sheet_name)
        records = ws.get_all_records()
        return pd.DataFrame(records)

    def append_row(self, sheet_name: str, row: list):
        ws = self.spreadsheet.worksheet(sheet_name)
        ws.append_row(row, value_input_option="USER_ENTERED")

    def append_rows(self, sheet_name: str, rows: list):
        if not rows:
            return
        ws = self.spreadsheet.worksheet(sheet_name)
        ws.append_rows(rows, value_input_option="USER_ENTERED")

    def create_or_replace_sheet(self, sheet_name: str, df: pd.DataFrame):
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
        data = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")

    def sheet_exists(self, sheet_name: str) -> bool:
        try:
            self.spreadsheet.worksheet(sheet_name)
            return True
        except gspread.WorksheetNotFound:
            return False

    def delete_sheet(self, sheet_name: str) -> bool:
        """Hapus sheet/tab. Return True kalau berhasil."""
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            self.spreadsheet.del_worksheet(ws)
            return True
        except gspread.WorksheetNotFound:
            return False
        except Exception as e:
            print(f"Error delete sheet {sheet_name}: {e}")
            return False

    def delete_rows_by_column(self, sheet_name: str, column_name: str, value: str) -> int:
        """Hapus semua baris di sheet yang kolom `column_name`-nya == value.
        Return jumlah baris yang dihapus."""
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            all_values = ws.get_all_values()
            if len(all_values) < 2:
                return 0

            headers = all_values[0]
            if column_name not in headers:
                return 0
            col_idx = headers.index(column_name)

            # Cari baris yang cocok (baris 2 dan seterusnya)
            rows_to_delete = []
            for i, row in enumerate(all_values[1:], start=2):  # start=2 karena baris 1 header
                if col_idx < len(row) and str(row[col_idx]).strip() == str(value).strip():
                    rows_to_delete.append(i)

            # Hapus dari bawah ke atas biar index-nya gak bergeser
            for row_num in sorted(rows_to_delete, reverse=True):
                ws.delete_rows(row_num)

            return len(rows_to_delete)
        except gspread.WorksheetNotFound:
            return 0
        except Exception as e:
            print(f"Error delete rows in {sheet_name}: {e}")
            return 0
