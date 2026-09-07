import os
import re
import sqlite3
from datetime import datetime
import pandas as pd
import pdfplumber
import streamlit as st
import folium
from folium.plugins import HeatMap, MarkerCluster
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from streamlit_folium import st_folium

# ==========================================
# CONFIGURACIÓN INICIAL Y CONSTANTES
# ==========================================
st.set_page_config(
    page_title="Sistema de Análisis Criminal & Mapa Delictual",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_PATH = os.path.join("database", "criminal_map.db")
DEFAULT_CENTER = [-34.6534, -58.6198]  # Centro por defecto: Morón, Buenos Aires

# ==========================================
# BASE DE DATOS (SQLite)
# ==========================================
def init_db():
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hechos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ipp TEXT UNIQUE,
            fecha_hora TEXT,
            delito TEXT,
            modalidad TEXT,
            direccion TEXT,
            latitud REAL,
            longitud REAL,
            arma TEXT,
            elementos TEXT,
            detalles TEXT,
            color TEXT,
            fecha_carga TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_hecho(data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO hechos (
                ipp, fecha_hora, delito, modalidad, direccion, 
                latitud, longitud, arma, elementos, detalles, color, fecha_carga
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['ipp'], data['fecha_hora'], data['delito'], data['modalidad'],
            data['direccion'], data['latitud'], data['longitud'], data['arma'],
            data['elementos'], data['detalles'], data['color'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return True, "Hecho registrado correctamente en la base de datos."
    except sqlite3.IntegrityError:
        return False, "Error: El número de N° IPP/Sumario ya existe en el sistema."
    except Exception as e:
        return False, f"Error al guardar: {str(e)}"
    finally:
        conn.close()

def load_hechos():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM hechos ORDER BY fecha_hora DESC", conn)
    conn.close()
    return df

# ==========================================
# MÓDULO DE AUTENTICACIÓN
# ==========================================
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.title("🛡️ Sistema de Análisis Criminal")
    st.subheader("Acceso Restringido")
    
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Ingresar")
        
        if submit:
            # Credenciales operativas por defecto
            if username == "analista" and password == "moron2026":
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Credenciales incorrectas.")
    return False

# ==========================================
# PARSER DE PDF Y GEOCODIFICACIÓN
# ==========================================
def parse_pdf_denuncia(pdf_file):
    extracted_text = ""
    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    extracted_text += text + "\n"
    except Exception as e:
        st.error(f"Error al leer el archivo PDF: {e}")
        return {}

    # Patrones mediante Expresiones Regulares
    ipp_match = re.search(r"(?:IPP|Sumario|Causa|N°)\s*[:\.-]?\s*([\d\/-]+)", extracted_text, re.IGNORECASE)
    fecha_match = re.search(r"(\d{1,2}[\/-]\d{1,2}[\/-]\d{2,4}\s*(?:\d{1,2}:\d{2})?)", extracted_text)
    
    # Búsqueda heurística de dirección
    direccion = ""
    for line in extracted_text.split("\n"):
        if any(kw in line.lower() for kw in ["lugar del hecho:", "intersección:", "calle:", "ubicación:", "domicilio:"]):
            direccion = re.sub(r"(?i)(lugar del hecho|intersección|calle|ubicación|domicilio)\s*[:\.-]?", "", line).strip()
            break

    # Búsqueda heurística de Carátula / Delito
    delito = "Robo"
    for line in extracted_text.split("\n"):
        if "caratula" in line.lower() or "delito:" in line.lower():
            delito = re.sub(r"(?i)(carátula|caratula|delito)\s*[:\.-]?", "", line).strip()
            break

    return {
        "ipp": ipp_match.group(1) if ipp_match else "",
        "fecha_hora": fecha_match.group(1) if fecha_match else datetime.now().strftime("%Y-%m-%d %H:%M"),
        "direccion": direccion,
        "delito": delito,
        "raw_text": extracted_text
    }

@st.cache_data(show_spinner=False)
def geocode_address(address_text):
    if not address_text:
        return None, None
    
    # Forzar la búsqueda dentro de Morón/GBA si no está especificado
    query = address_text
    if "morón" not in query.lower() and "moron" not in query.lower():
        query += ", Morón, Buenos Aires, Argentina"
        
    try:
        geolocator = Nominatim(user_agent="criminal_analysis_app_v1")
        geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
        location = geocode(query)
        if location:
            return location.latitude, location.longitude
    except Exception:
        pass
    return None, None

def get_tactical_color(delito, arma):
    delito_lower = delito.lower()
    arma_lower = arma.lower()
    
    if any(w in delito_lower for w in ["homicidio", "lesiones"]) or "agravado" in delito_lower or (arma_lower and arma_lower != "no"):
        return "red"
    elif "robo" in delito_lower or "arrebato" in delito_lower:
        return "orange"
    elif "hurto" in delito_lower or "tentativa" in delito_lower:
        return "yellow"
    elif any(w in delito_lower for w in ["vehículo", "hallazgo", "secuestro", "auto", "moto"]):
        return "black"
    else:
        return "blue"

# ==========================================
# INTERFAZ PRINCIPAL STREAMLIT
# ==========================================
def main():
    init_db()

    if not check_password():
        return

    # BARRA LATERAL
    st.sidebar.title("🛡️ Panel de Control")
    if st.sidebar.button("Cerrar Sesión"):
        st.session_state["authenticated"] = False
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Filtros de Mapa")

    df_all = load_hechos()

    if not df_all.empty:
        tipos_delito = ["Todos"] + list(df_all["delito"].unique())
        selected_delito = st.sidebar.selectbox("Tipo de Delito (Carátula)", tipos_delito)

        modalidades = ["Todas"] + list(df_all["modalidad"].unique())
        selected_modalidad = st.sidebar.selectbox("Modalidad", modalidades)

        # Aplicar Filtros
        df_filtered = df_all.copy()
        if selected_delito != "Todos":
            df_filtered = df_filtered[df_filtered["delito"] == selected_delito]
        if selected_modalidad != "Todas":
            df_filtered = df_filtered[df_filtered["modalidad"] == selected_modalidad]
    else:
        df_filtered = pd.DataFrame()

    st.sidebar.markdown("---")
    map_layer = st.sidebar.radio("Capas del Mapa", ["Pines Tácticos", "Mapa de Calor (Heatmap)"])

    # PANEL PRINCIPAL
    st.title("Mapa Delictual Interactivo & Análisis Operativo")

    tab1, tab2, tab3 = st.tabs([
        "🗺️ Mapa Operativo", 
        "📄 Procesar Denuncia (PDF)", 
        "📊 Base de Datos & Exportación"
    ])

    # --------------------------------------------------
    # TAB 1: MAPA OPERATIVO
    # --------------------------------------------------
    with tab1:
        if df_filtered.empty:
            st.info("No hay registros en la base de datos o los filtros no devolvieron resultados.")
            m = folium.Map(location=DEFAULT_CENTER, zoom_start=14, tiles="CartoDB positron")
            st_folium(m, width="100%", height=600)
        else:
            # Métricas Rápidas
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total de Hechos", len(df_filtered))
            c2.metric("Delitos con Arma", len(df_filtered[df_filtered["arma"].str.lower() != "no"]))
            delito_comun = df_filtered["delito"].mode()[0] if not df_filtered.empty else "N/A"
            c3.metric("Mayor Incidencia", delito_comun)
            c4.metric("Zona Base", "Morón")

            m = folium.Map(location=DEFAULT_CENTER, zoom_start=13, tiles="CartoDB positron")

            if map_layer == "Mapa de Calor (Heatmap)":
                heat_data = [[row["latitud"], row["longitud"]] for _, row in df_filtered.iterrows() if pd.notnull(row["latitud"])]
                HeatMap(heat_data, radius=15, blur=10).add_to(m)
            else:
                marker_cluster = MarkerCluster().add_to(m)
                for _, row in df_filtered.iterrows():
                    if pd.notnull(row["latitud"]) and pd.notnull(row["longitud"]):
                        # Diseño HTML limpio para Tooltip / Popup
                        popup_html = f"""
                        <div style='font-family: Arial; width: 220px;'>
                            <h4 style='margin-bottom:5px; color:#1A365D;'>{row['delito']}</h4>
                            <b>N° IPP:</b> {row['ipp']}<br>
                            <b>Fecha/Hora:</b> {row['fecha_hora']}<br>
                            <b>Modalidad:</b> {row['modalidad']}<br>
                            <b>Ubicación:</b> {row['direccion']}<br>
                            <b>Arma:</b> {row['arma']}<br>
                            <b>Sustraído:</b> {row['elementos']}<br>
                            <hr style='margin:5px 0;'>
                            <small><b>Detalles:</b> {row['detalles']}</small>
                        </div>
                        """
                        folium.Marker(
                            location=[row["latitud"], row["longitud"]],
                            popup=folium.Popup(popup_html, max_width=260),
                            tooltip=f"{row['delito']} - {row['direccion']}",
                            icon=folium.Icon(color=row["color"], icon="info-sign")
                        ).add_to(marker_cluster)

            st_folium(m, width="100%", height=600, returned_objects=[])

    # --------------------------------------------------
    # TAB 2: PROCESAR DENUNCIA (PDF)
    # --------------------------------------------------
    with tab2:
        st.subheader("Carga y Extracción de Partes / Denuncias")
        uploaded_file = st.file_uploader("Seleccione un archivo PDF de denuncia", type=["pdf"])

        parsed_data = {}
        if uploaded_file:
            with st.spinner("Analizando documento mediante Regex & NLP básico..."):
                parsed_data = parse_pdf_denuncia(uploaded_file)
                st.success("PDF procesado. Verifique y ajuste la información extraída a continuación:")

        with st.form("form_alta_hecho"):
            st.markdown("### Validación Manual de Datos")
            col1, col2 = st.columns(2)

            with col1:
                ipp = st.text_input("N° IPP / Sumario", value=parsed_data.get("ipp", ""))
                fecha_hora = st.text_input("Fecha y Hora", value=parsed_data.get("fecha_hora", ""))
                delito = st.selectbox("Carátula / Delito", [
                    "Robo Agravado", "Robo Simple", "Hurto", "Homicidio", 
                    "Lesiones", "Arrebato", "Ataque Vehicular / Secuestro", "Daños"
                ], index=0)
                modalidad = st.selectbox("Modalidad / Modus Operandi", [
                    "Motochorros", "Entradera", "Escruche", "Vía Pública", 
                    "Comercio", "Automotor", "Otro"
                ])

            with col2:
                direccion = st.text_input("Lugar del Hecho / Dirección Exacta", value=parsed_data.get("direccion", ""))
                arma = st.selectbox("Uso de Arma", ["No", "De Fuego", "Blanca / Punzante", "Impropia / Réplica"])
                elementos = st.text_input("Elementos Sustraídos", placeholder="Ej: Celular, Billetera, Rodado")
                detalles = st.text_area("Síntesis del Modus Operandi / Observaciones", placeholder="Resumen del hecho...")

            btn_geocode = st.form_submit_button("Geocodificar y Registrar Hecho")

            if btn_geocode:
                if not ipp or not direccion:
                    st.error("Los campos N° IPP y Dirección son obligatorios.")
                else:
                    with st.spinner("Geocodificando dirección en Morón/GBA..."):
                        lat, lon = geocode_address(direccion)
                        
                        if lat is None or lon is None:
                            st.error("No se pudo geocodificar la dirección ingresada. Por favor especifique intersección o calle y altura correcta.")
                        else:
                            color = get_tactical_color(delito, arma)
                            payload = {
                                "ipp": ipp,
                                "fecha_hora": fecha_hora,
                                "delito": delito,
                                "modalidad": modalidad,
                                "direccion": direccion,
                                "latitud": lat,
                                "longitud": lon,
                                "arma": arma,
                                "elementos": elementos,
                                "detalles": detalles,
                                "color": color
                            }
                            success, msg = save_hecho(payload)
                            if success:
                                st.success(f"{msg} (Coordenadas: {lat:.4f}, {lon:.4f})")
                            else:
                                st.error(msg)

    # --------------------------------------------------
    # TAB 3: BASE DE DATOS Y EXPORTACIÓN
    # --------------------------------------------------
    with tab3:
        st.subheader("Registros Acumulados")
        df_db = load_hechos()

        if df_db.empty:
            st.info("La base de datos está vacía.")
        else:
            st.dataframe(df_db, use_container_width=True)

            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                csv_data = df_db.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Exportar Base de Datos a CSV",
                    data=csv_data,
                    file_name=f"base_delictual_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )

            with col_exp2:
                # Generación de Excel en memoria
                import io
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df_db.to_excel(writer, index=False, sheet_name='Hechos')
                
                st.download_button(
                    label="📊 Exportar Base de Datos a Excel (.xlsx)",
                    data=buffer.getvalue(),
                    file_name=f"base_delictual_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

if __name__ == "__main__":
    main()
