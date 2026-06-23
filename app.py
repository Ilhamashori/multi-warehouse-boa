"""
Multi Warehouse Shipping Calculator - Beauty of Angel
Main Streamlit Application

Perubahan vs versi lama:
- Sidebar: checkbox ON/OFF tiap gudang (bisa matiin Ambon sekali klik, tanpa
  edit Google Sheet). Default ngikut kolom 'aktif' di master_gudang.
- Hasil: download Excel TERPISAH per gudang (Tangerang, Medan, Makassar, ...),
  plus 1 Excel multi-sheet (1 sheet/gudang) kalau mau sekalian.
- Simpan GSheets: opsi bikin 1 tab per gudang.
- RajaOngkir API dibangun dengan retry/throttle (lihat modules/rajaongkir.py).
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO

from modules.auth import login_page, logout_button
from modules.rajaongkir import RajaOngkirAPI
from modules.gsheets import GSheetsClient
from modules.shipping import process_all_orders


st.set_page_config(
    page_title="Multi Gudang - BoA",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_gsheets_client():
    creds = dict(st.secrets["gcp_service_account"])
    sheet_id = st.secrets["gsheets"]["spreadsheet_id"]
    return GSheetsClient(creds, sheet_id)


@st.cache_resource
def get_rajaongkir_api():
    cfg = st.secrets["rajaongkir"]
    key = cfg["api_key"]
    # Opsional di secrets.toml: throttle_detik, max_retry
    try:
        throttle = float(cfg.get("throttle_detik", 0.0))
    except Exception:
        throttle = 0.0
    try:
        max_retry = int(cfg.get("max_retry", 4))
    except Exception:
        max_retry = 4
    return RajaOngkirAPI(key, max_retry=max_retry, throttle=throttle)


@st.cache_data(ttl=300)
def load_master_gudang():
    gs = get_gsheets_client()
    df = gs.read_sheet("master_gudang")
    if "aktif" in df.columns:
        df["aktif"] = df["aktif"].apply(lambda x: str(x).upper() == "TRUE" or x is True)
    for col in ["subdistrict_id", "prioritas"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["latitude", "longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=300)
def load_config():
    gs = get_gsheets_client()
    df = gs.read_sheet("config")
    return dict(zip(df["key"], df["value"]))


def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Hasil") -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=str(sheet_name)[:31])
    return buf.getvalue()


def df_to_multisheet_bytes(df: pd.DataFrame, group_col: str = "Gudang Rekomendasi") -> bytes:
    """1 sheet per gudang dalam 1 file Excel."""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for g, sub in df.groupby(group_col):
            safe = str(g)[:31].replace("/", "-").replace("\\", "-")
            sub.to_excel(writer, index=False, sheet_name=safe or "Sheet")
    return buf.getvalue()


def main_app():
    st.title("📦 Multi Gudang - Beauty of Angel")
    st.caption("Pengelompokan order berdasarkan gudang termurah")

    # ---- Sidebar: toggle ON/OFF gudang ----
    active_map = {}
    with st.sidebar:
        st.markdown(f"👤 **{st.session_state.get('username', 'User')}**")
        st.markdown("---")
        logout_button()
        st.markdown("---")
        st.markdown("### 🏢 Gudang (centang = aktif)")
        try:
            wh = load_master_gudang()
            wh = wh.sort_values(by="prioritas", na_position="last")
            for _, row in wh.iterrows():
                kode = str(row["kode_gudang"])
                default = bool(row.get("aktif", True))
                prio = int(row["prioritas"]) if pd.notna(row.get("prioritas")) else 99
                active_map[kode] = st.checkbox(
                    f"{row['nama_gudang']} (Prio #{prio})",
                    value=default, key=f"wh_{kode}",
                )
            n_aktif = sum(1 for v in active_map.values() if v)
            if n_aktif == 0:
                st.warning("⚠️ Semua gudang OFF — nyalakan minimal 1.")
            else:
                st.success(f"✅ {n_aktif} gudang aktif")
        except Exception as e:
            st.error(f"❌ Error load gudang: {e}")
    st.session_state["active_wh_map"] = active_map

    try:
        config = load_config()
        default_weight = int(config.get("default_berat_gram", 1000))
        couriers = config.get("kurir_aktif", "jne,tiki").replace(",", ":")
    except Exception as e:
        st.error(f"❌ Error load config: {e}")
        default_weight = 1000
        couriers = "jne:tiki"

    tab1, tab2, tab3 = st.tabs(["📤 Upload & Proses", "📋 Riwayat", "⚙️ Pengaturan"])

    with tab1:
        st.markdown("### 1. Upload File Excel Order")
        col1, col2 = st.columns([2, 1])
        with col1:
            uploaded = st.file_uploader("Pilih file Excel (.xlsx)", type=["xlsx"])
        with col2:
            weight_input = st.number_input("Berat per order (gram)", 100, 50000, default_weight, 100)

        if uploaded:
            try:
                df_orders = pd.read_excel(uploaded)
                st.success(f"✅ File loaded: **{len(df_orders)} baris**")

                with st.expander("👀 Preview 5 baris pertama"):
                    st.dataframe(df_orders.head(), use_container_width=True)

                st.markdown("### 2. Proses Pengelompokan")
                if st.button("🚀 Mulai Proses", type="primary", use_container_width=True):
                    api = get_rajaongkir_api()
                    warehouses = load_master_gudang()

                    # Pakai pilihan checkbox sebagai sumber kebenaran "aktif"
                    chosen_kode = [k for k, v in st.session_state.get("active_wh_map", {}).items() if v]
                    warehouses_aktif = warehouses[
                        warehouses["kode_gudang"].astype(str).isin(chosen_kode)
                    ].copy()
                    warehouses_aktif["aktif"] = True

                    if warehouses_aktif.empty:
                        st.error("❌ Tidak ada gudang aktif! Centang minimal 1 gudang di sidebar.")
                        st.stop()

                    st.info("🏢 Gudang dipakai: " +
                            ", ".join(warehouses_aktif["nama_gudang"].tolist()))

                    progress_bar = st.progress(0.0)
                    status_text = st.empty()

                    def update_progress(pct, msg):
                        progress_bar.progress(min(max(pct, 0.0), 1.0))
                        status_text.text(msg)

                    with st.spinner("Memproses..."):
                        result = process_all_orders(
                            df_orders=df_orders,
                            warehouses=warehouses_aktif,
                            api=api,
                            weight_gram=weight_input,
                            couriers=couriers,
                            progress_callback=update_progress,
                        )
                    progress_bar.empty()
                    status_text.empty()

                    st.session_state.last_result = result
                    st.session_state.last_filename = uploaded.name
                    stats = api.get_stats()
                    st.success(
                        f"✅ Proses selesai! (API hits: {stats['api_hits']}, "
                        f"cache: {stats['cache_hits']}, error: {stats['errors']})"
                    )

                if "last_result" in st.session_state:
                    show_results(st.session_state.last_result, st.session_state.last_filename,
                                 couriers, weight_input)
            except Exception as e:
                st.error(f"❌ Error: {e}")

    with tab2:
        show_history_tab()

    with tab3:
        st.markdown("### ⚙️ Pengaturan")
        try:
            config = load_config()
            st.markdown("#### Config Saat Ini:")
            for k, v in config.items():
                st.text(f"{k} = {v}")
            st.markdown("---")
            st.markdown("#### Master Gudang:")
            wh = load_master_gudang()
            st.dataframe(wh, use_container_width=True)
        except Exception as e:
            st.error(f"Error: {e}")
        if st.button("🔄 Refresh Cache"):
            st.cache_data.clear()
            st.success("Cache cleared!")
            st.rerun()


def show_history_tab():
    """Tab Riwayat dengan fitur hapus."""
    st.markdown("### 📋 Riwayat Upload")
    try:
        gs = get_gsheets_client()
        df_log = gs.read_sheet("log_upload")
        if df_log.empty:
            st.info("Belum ada riwayat upload.")
            return

        df_log = df_log.sort_values(by="timestamp", ascending=False).reset_index(drop=True)

        col_headers = st.columns([2, 1.5, 2.5, 1, 1, 1, 2, 1])
        col_headers[0].markdown("**Timestamp**")
        col_headers[1].markdown("**User**")
        col_headers[2].markdown("**File**")
        col_headers[3].markdown("**Order**")
        col_headers[4].markdown("**Berhasil**")
        col_headers[5].markdown("**Review**")
        col_headers[6].markdown("**Sheet**")
        col_headers[7].markdown("**Aksi**")
        st.divider()

        for idx, row in df_log.iterrows():
            sheet_name = str(row.get("sheet_hasil", ""))
            timestamp = str(row.get("timestamp", ""))

            cols = st.columns([2, 1.5, 2.5, 1, 1, 1, 2, 1])
            cols[0].text(timestamp)
            cols[1].text(str(row.get("user", "")))
            cols[2].text(str(row.get("nama_file", ""))[:30])
            cols[3].text(str(row.get("jumlah_order", "")))
            cols[4].text(str(row.get("berhasil", "")))
            cols[5].text(str(row.get("review_manual", "")))
            cols[6].text(sheet_name)

            confirm_key = f"confirm_delete_{idx}"
            if confirm_key not in st.session_state:
                st.session_state[confirm_key] = False

            with cols[7]:
                if not st.session_state[confirm_key]:
                    if st.button("🗑️", key=f"del_{idx}", help=f"Hapus {sheet_name}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    c1, c2 = st.columns(2)
                    if c1.button("✅", key=f"yes_{idx}", help="Yakin"):
                        delete_history_entry(gs, sheet_name, timestamp)
                        st.session_state[confirm_key] = False
                        st.rerun()
                    if c2.button("❌", key=f"no_{idx}", help="Batal"):
                        st.session_state[confirm_key] = False
                        st.rerun()

            if st.session_state[confirm_key]:
                st.warning(f"⚠️ Yakin hapus **{sheet_name}**? Ini akan hapus sheet hasil, log, dan data review manual terkait.")
    except Exception as e:
        st.error(f"Error load riwayat: {e}")


def delete_history_entry(gs, sheet_name, timestamp):
    """Hapus sheet hasil + log + review manual."""
    try:
        with st.spinner(f"Menghapus {sheet_name}..."):
            deleted_sheet = gs.delete_sheet(sheet_name)
            deleted_log = gs.delete_rows_by_column("log_upload", "sheet_hasil", sheet_name)
            deleted_review = gs.delete_rows_by_column("review_manual", "timestamp", timestamp)
        st.success(f"✅ Terhapus: sheet ({'ya' if deleted_sheet else 'tidak ada'}), "
                   f"log ({deleted_log} baris), review ({deleted_review} baris)")
    except Exception as e:
        st.error(f"❌ Gagal hapus: {e}")


def show_results(result, filename, couriers, weight):
    df_hasil = result["df_hasil"]
    df_review = result["df_review"]
    summary = result["summary"]
    notifications = result["notifications"]

    st.markdown("---")
    st.markdown("### 3. 📊 Ringkasan Hasil")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 Total Order", len(df_hasil))
    col2.metric("✅ Berhasil", len(df_hasil) - len(df_review))
    col3.metric("⚠️ Perlu Review", len(df_review))
    col4.metric("🔔 Tie Warning", len(notifications))

    if summary:
        st.markdown("#### 🏢 Distribusi per Gudang:")
        cols = st.columns(len(summary))
        for i, (gudang, count) in enumerate(summary.items()):
            with cols[i % len(cols)]:
                st.metric(gudang, f"{count} order")

    if notifications:
        with st.expander(f"🔔 {len(notifications)} Notifikasi Ongkir Sama"):
            for n in notifications:
                st.warning(f"**Order {n['order_id']}** - {n['nama_pembeli']}")
                st.caption(n["warning"])
                st.dataframe(pd.DataFrame(n["opsi"]), use_container_width=True)

    if not df_review.empty:
        st.markdown("#### ⚠️ Order Perlu Review Manual:")
        st.dataframe(df_review, use_container_width=True)

    st.markdown("#### 📋 Hasil Lengkap:")
    st.dataframe(df_hasil, use_container_width=True, height=400)

    st.markdown("---")
    st.markdown("### 4. 💾 Download per Gudang")

    base = filename.replace(".xlsx", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    # Daftar gudang real (kecuali REVIEW MANUAL / TIDAK ADA)
    semua_gudang = list(df_hasil["Gudang Rekomendasi"].unique())
    gudang_real = [g for g in semua_gudang if str(g).upper() not in ("REVIEW MANUAL", "TIDAK ADA")]
    gudang_review = [g for g in semua_gudang if str(g).upper() in ("REVIEW MANUAL", "TIDAK ADA")]

    if gudang_real:
        st.caption("Tiap tombol = 1 file Excel berisi order gudang itu aja.")
        per_row = 4
        for start in range(0, len(gudang_real), per_row):
            chunk = gudang_real[start:start + per_row]
            cols = st.columns(len(chunk))
            for i, g in enumerate(chunk):
                sub = df_hasil[df_hasil["Gudang Rekomendasi"] == g]
                cols[i].download_button(
                    label=f"📥 {g} ({len(sub)})",
                    data=df_to_excel_bytes(sub, sheet_name=g),
                    file_name=f"{g}_{base}_{ts}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"dl_{g}",
                )

    # Tombol REVIEW MANUAL terpisah (kalau ada)
    if gudang_review:
        for g in gudang_review:
            sub = df_hasil[df_hasil["Gudang Rekomendasi"] == g]
            st.download_button(
                label=f"📥 {g} ({len(sub)})",
                data=df_to_excel_bytes(sub, sheet_name="ReviewManual"),
                file_name=f"REVIEW_{base}_{ts}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_review_{g}",
            )

    st.markdown("##### Atau sekaligus:")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            label="📦 Semua (1 file, 1 sheet/gudang)",
            data=df_to_multisheet_bytes(df_hasil),
            file_name=f"hasil_PERGUDANG_{base}_{ts}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            label="📄 Semua (1 file gabungan)",
            data=df_to_excel_bytes(df_hasil),
            file_name=f"hasil_{base}_{ts}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown("### 5. ☁️ Simpan ke Google Sheets")
    pisah_tab = st.checkbox("Bikin juga 1 tab per gudang di Google Sheets", value=False)
    if st.button("☁️ Simpan ke Google Sheets", type="primary", use_container_width=True):
        save_to_gsheets(df_hasil, df_review, filename, pisah_per_gudang=pisah_tab)


def save_to_gsheets(df_hasil, df_review, filename, pisah_per_gudang=False):
    try:
        gs = get_gsheets_client()
        now = datetime.now()
        sheet_name = f"hasil_{now.strftime('%Y-%m-%d')}"
        if gs.sheet_exists(sheet_name):
            sheet_name = f"hasil_{now.strftime('%Y-%m-%d_%H%M')}"

        with st.spinner("Menyimpan ke Google Sheets..."):
            gs.create_or_replace_sheet(sheet_name, df_hasil)
            sheet_id = gs.spreadsheet.id
            try:
                gid = gs.spreadsheet.worksheet(sheet_name).id
                sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}"
                sheet_link = f'=HYPERLINK("{sheet_url}";"{sheet_name}")'
            except Exception:
                sheet_link = sheet_name
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            gs.append_row("log_upload", [
                timestamp_str,
                st.session_state.get("username", "admin"),
                filename,
                len(df_hasil),
                len(df_hasil) - len(df_review),
                len(df_review),
                sheet_link,
            ])
            if not df_review.empty:
                rows = []
                for _, r in df_review.iterrows():
                    rows.append([
                        timestamp_str,
                        str(r.get("order_id", "")),
                        str(r.get("nama_pembeli", "")),
                        str(r.get("kota_tujuan", "")),
                        str(r.get("subdistrict", "")),
                        str(r.get("zip", "")),
                        str(r.get("alasan", "")),
                    ])
                gs.append_rows("review_manual", rows)

            # Opsi: 1 tab per gudang
            tab_dibuat = []
            if pisah_per_gudang:
                for g, sub in df_hasil.groupby("Gudang Rekomendasi"):
                    safe_g = str(g).replace("/", "-")[:20]
                    tab = f"{sheet_name}_{safe_g}"
                    gs.create_or_replace_sheet(tab, sub)
                    tab_dibuat.append(tab)

        msg = f"✅ Tersimpan ke sheet: **{sheet_name}**"
        if pisah_per_gudang and tab_dibuat:
            msg += f"\n\nTab per gudang: {', '.join(tab_dibuat)}"
        st.success(msg)
        st.balloons()
    except Exception as e:
        st.error(f"❌ Gagal: {e}")


def main():
    if login_page():
        main_app()


if __name__ == "__main__":
    main()
