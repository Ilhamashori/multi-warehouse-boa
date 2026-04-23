"""
Login sederhana. Username & password disimpan di Streamlit secrets.
"""
import streamlit as st
import hashlib


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def check_login(username: str, password: str) -> bool:
    """Cek username & password terhadap secrets."""
    try:
        correct_user = st.secrets["auth"]["username"]
        correct_pass = st.secrets["auth"]["password"]
        return username == correct_user and password == correct_pass
    except Exception as e:
        st.error(f"Konfigurasi auth error: {e}")
        return False


def login_page():
    """
    Tampilkan form login. Return True kalau user udah login.
    Session state dipakai buat inget status login.
    """
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if st.session_state.logged_in:
        return True

    st.markdown("### 🔐 Login")
    st.caption("Silakan login untuk akses tools")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login", use_container_width=True)

        if submit:
            if check_login(username, password):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.success("✅ Login berhasil!")
                st.rerun()
            else:
                st.error("❌ Username atau password salah")

    return False


def logout_button():
    """Tombol logout di sidebar."""
    if st.sidebar.button("🚪 Logout", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.pop("username", None)
        st.rerun()
