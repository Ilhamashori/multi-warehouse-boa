"""
Multi Warehouse Shipping Calculator - Beauty of Angel
Main Streamlit Application
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO

from modules.auth import login_page, logout_button
from modules.rajaongkir import RajaOngkirAPI
from modules.gsheets import GSheetsClient
from modules.shipping import process_all_orders


# ===== PAGE CONFIG =====
st.set_page_config(
    page_title="Multi Gudang - BoA",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===== CACHED RESOURCES =====
@st.cache_resource
def get_gsheets_client():
    """Inisialisasi GSheets client sekali aja (cached)."""
    creds = dict(st.secrets["gcp_service_account"])
    sheet_id = st.secrets["gsheets"]["spreadsheet_id"]
    return GSheetsClient(creds, sheet_id)


@st.cache_resource
def get_rajaongkir_api():
    """Inisialisasi RajaOngkir API sekali aja (cached)."""
    key = st.secrets["rajaongkir"]["api_key"]
    return RajaOngkirAPI(key)


@st.cache_data(ttl=300)  # cache 5 menit
def load_master_gudang():
    gs = get_gsheets_client()
    df = gs.read_sheet("master_gudang")
    # Normalisasi tipe
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


# ===== HELPER =====
def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert DataFrame ke bytes Excel buat download."""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Hasil")
    return buf.getvalue()


# ===== MAIN APP =====
def main_app():
    # Header
    st.title("📦 Multi Gudang - Beauty of Angel")
    st.caption("Pengelompokan order berdasarkan gudang termurah")

    # Sidebar
    with st.sidebar:
        st.markdown(f"👤 **{st.session_state.get('username', 'User')}**")
        st.markdown("---")
        logout_button()
        st.markdown("---")
        st.markdown("### ℹ️ Info")
        try:
            wh = load_master_gudang()
            aktif = wh[wh["aktif"] == True]
            st.success(f"✅ {len(aktif)} gudang aktif")
            for _, row in aktif.iterrows():
                st.caption(f"• {row['nama_gudang']} (Prio #{int(row['prioritas'])})")
        except Exception as e:
            st.error(f"❌ Error load gudang: {e}")

    # Load config
    try:
        config = load_config()
        default_weight = int(config.get("default_berat_gram", 1000))
        couriers = config.get("kurir_aktif", "jne,tiki").replace(",", ":")
    except Exception as e:
        st.error(f"❌ Error load config: {e}")
        default_weight = 1000
        couriers = "jne:tiki"

    # ===== TAB =====
    tab1, tab2, tab3 = st.tabs(["📤 Upload & Proses", "📋 Riwayat", "⚙️ Pengaturan"])

    # ===== TAB 1: UPLOAD =====
    with tab1:
        st.markdown("### 1. Upload File Excel Order")

        col1, col2 = st.columns([2, 1])
        with col1:
            uploaded = st.file_uploader(
                "Pilih file Excel (.xlsx)",
                type=["xlsx"],
                help="Format sama seperti Excel order dari Mengantar",
            )
        with col2:
            weight_input = st.number_input(
                "Berat per order (gram)",
                min_value=100,
                max_value=50000,
                value=default_weight,
                step=100,
            )

        if uploaded:
            try:
                df_orders = pd.read_excel(uploaded)
                st.success(f"✅ File loaded: **{len(df_orders)} order**")

                with st.expander("👀 Preview 5 baris pertama"):
                    st.dataframe(df_orders.head(), use_container_width=True)

                st.markdown("### 2. Proses Pengelompokan")

                if st.button("🚀 Mulai Proses", type="primary", use_container_width=True):
                    api = get_rajaongkir_api()
                    warehouses = load_master_gudang()
                    warehouses_aktif = warehouses[warehouses["aktif"] == True]

                    if warehouses_aktif.empty:
                        st.error("❌ Tidak ada gudang aktif di master_gudang!")
                        st.stop()

                    # Progress bar
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()

                    def update_progress(pct, msg):
                        progress_bar.progress(pct)
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

                    # Simpan ke session state biar nggak hilang
                    st.session_state.last_result = result
                    st.session_state.last_filename = uploaded.name

                    st.success("✅ Proses selesai!")

                # Tampilkan hasil kalau ada
                if "last_result" in st.session_state:
                    result = st.session_state.last_result
                    show_results(result, st.session_state.last_filename, couriers, weight_input)

            except Exception as e:
                st.error(f"❌ Error baca file: {e}")

    # ===== TAB 2: RIWAYAT =====
    with tab2:
        st.markdown("### 📋 Riwayat Upload")
        try:
            gs = get_gsheets_client()
            df_log = gs.read_sheet("log_upload")
            if df_log.empty:
                st.info("Belum ada riwayat upload.")
            else:
                st.dataframe(df_log.sort_values(by="timestamp", ascending=False), use_container_width=True)
        except Exception as e:
            st.error(f"Error: {e}")

    # ===== TAB 3: PENGATURAN =====
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


def show_results(result, filename, couriers, weight):
    """Tampilkan hasil proses."""
    df_hasil = result["df_hasil"]
    df_review = result["df_review"]
    summary = result["summary"]
    notifications = result["notifications"]

    st.markdown("---")
    st.markdown("### 3. 📊 Ringkasan Hasil")

    # Metric
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 Total Order", len(df_hasil))
    col2.metric("✅ Berhasil", len(df_hasil) - len(df_review))
    col3.metric("⚠️ Perlu Review", len(df_review))
    col4.metric("🔔 Tie Warning", len(notifications))

    # Per gudang
    st.markdown("#### 🏢 Distribusi per Gudang:")
    cols = st.columns(len(summary)) if summary else [st.container()]
    for i, (gudang, count) in enumerate(summary.items()):
        with cols[i % len(cols)]:
            st.metric(gudang, f"{count} order")

    # Notifikasi tie
    if notifications:
        with st.expander(f"🔔 {len(notifications)} Notifikasi Ongkir Sama (klik untuk detail)"):
            for n in notifications:
                st.warning(f"**Order {n['order_id']}** - {n['nama_pembeli']}")
                st.caption(n["warning"])
                opsi_df = pd.DataFrame(n["opsi"])
                st.dataframe(opsi_df, use_container_width=True)
                st.markdown("---")

    # Review manual
    if not df_review.empty:
        st.markdown("#### ⚠️ Order Perlu Review Manual:")
        st.dataframe(df_review, use_container_width=True)

    # Tabel hasil
    st.markdown("#### 📋 Hasil Lengkap:")
    st.dataframe(df_hasil, use_container_width=True, height=400)

    # Tombol aksi
    st.markdown("---")
    st.markdown("### 4. 💾 Simpan & Download")

    col1, col2 = st.columns(2)

    with col1:
        st.download_button(
            label="📥 Download Excel",
            data=df_to_excel_bytes(df_hasil),
            file_name=f"hasil_{filename.replace('.xlsx', '')}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with col2:
        if st.button("☁️ Simpan ke Google Sheets", type="primary", use_container_width=True):
            save_to_gsheets(df_hasil, df_review, filename, couriers, weight)


def save_to_gsheets(df_hasil, df_review, filename, couriers, weight):
    """Simpan hasil ke GSheets: sheet baru + log + review."""
    try:
        gs = get_gsheets_client()
        now = datetime.now()
        sheet_name = f"hasil_{now.strftime('%Y-%m-%d')}"

        # Kalau sheet udah ada (upload 2x sehari), bikin versi jam
        if gs.sheet_exists(sheet_name):
            sheet_name = f"hasil_{now.strftime('%Y-%m-%d_%H%M')}"

        with st.spinner("Menyimpan ke Google Sheets..."):
            # 1. Buat sheet hasil
            gs.create_or_replace_sheet(sheet_name, df_hasil)

            # 2. Append ke log_upload
            gs.append_row("log_upload", [
                now.strftime("%Y-%m-%d %H:%M:%S"),
                st.session_state.get("username", "admin"),
                filename,
                len(df_hasil),
                len(df_hasil) - len(df_review),
                len(df_review),
                sheet_name,
            ])

            # 3. Append review manual kalau ada
            if not df_review.empty:
                rows = []
                for _, r in df_review.iterrows():
                    rows.append([
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                        str(r.get("order_id", "")),
                        str(r.get("nama_pembeli", "")),
                        str(r.get("kota_tujuan", "")),
                        str(r.get("subdistrict", "")),
                        str(r.get("zip", "")),
                        str(r.get("alasan", "")),
                    ])
                gs.append_rows("review_manual", rows)

        st.success(f"✅ Tersimpan ke sheet: **{sheet_name}**")
        st.balloons()

    except Exception as e:
        st.error(f"❌ Gagal simpan ke GSheets: {e}")


# ===== ENTRY =====
def main():
    if login_page():
        main_app()


if __name__ == "__main__":
    main()
