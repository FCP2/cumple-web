import os
import re
import time
import datetime as dt
from urllib.parse import quote

import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


# =========================
# Configuración por entorno
# =========================
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
CREDENTIALS_FILE = "credenciales.json"  # lo generamos al vuelo si la env var existe

SHEET_NAME = os.getenv("SHEET_NAME", "cumpleanos_ejemplo")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", None)  # si None, toma la primera hoja

DIAS_VENTANA = int(os.getenv("DIAS_VENTANA", "3"))  # hoy + (DIAS_VENTANA-1)
NUMEROS_DESTINATARIOS = [
    tel.strip() for tel in os.getenv("NUMEROS_DESTINATARIOS", "5217292251844").split(",") if tel.strip()
]

PERSIST_DIR = os.getenv("PERSIST_DIR", "/data")
PROFILE_DIR = os.path.join(PERSIST_DIR, "chrome-profile")
QR_PATH = os.path.join(PERSIST_DIR, "qr.png")

WHATSAPP_URL = "https://web.whatsapp.com/send?phone={telefono}&text={mensaje}"
TIEMPO_CARGA_WA = int(os.getenv("TIEMPO_CARGA_WA", "12"))
TIEMPO_TRAS_NAVEGAR_CHAT = int(os.getenv("TIEMPO_TRAS_NAVEGAR_CHAT", "8"))
PAUSA_ENTRE_DESTINOS = int(os.getenv("PAUSA_ENTRE_DESTINOS", "2"))

# Crear credenciales.json si hay secreto en env
if GOOGLE_CREDENTIALS_JSON:
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        f.write(GOOGLE_CREDENTIALS_JSON)


# =========================
# Utilidades de fechas
# =========================
def parse_fecha_ddmmyy(s):
    """
    Acepta 'dd/mm/yy' o 'dd/mm/yyyy' (también tolera separadores . - o espacio).
    Devuelve (dia, mes). Valida rango.
    """
    s = str(s).strip()
    s = re.sub(r"[.\- ]", "/", s)
    partes = s.split("/")
    if len(partes) < 2:
        raise ValueError(f"Fecha inválida: {s}")
    d = int(partes[0])
    m = int(partes[1])
    # Valida con año “dummy”
    _ = dt.date(2000, m, d)
    return d, m


def dias_hasta_proximo(dia, mes, hoy=None):
    """
    Días hasta el próximo cumpleaños (>=0).
    Retorna (dias, anio_evento). Maneja fin de año y 29-feb a 28-feb en no bisiesto.
    """
    if hoy is None:
        hoy = dt.date.today()

    def _safe_date(y, m, d):
        try:
            return dt.date(y, m, d)
        except ValueError:
            if m == 2 and d == 29:
                return dt.date(y, 2, 28)
            raise

    this_year = hoy.year
    evento_este = _safe_date(this_year, mes, dia)
    if evento_este >= hoy:
        return (evento_este - hoy).days, this_year
    next_year = this_year + 1
    evento_sig = _safe_date(next_year, mes, dia)
    return (evento_sig - hoy).days, next_year


# =========================
# Google Sheets
# =========================
def abrir_worksheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sh = client.open(SHEET_NAME)
    if WORKSHEET_NAME:
        return sh.worksheet(WORKSHEET_NAME)
    # si no se especifica, usa la primera hoja
    return sh.get_worksheet(0)


# =========================
# Selenium / WhatsApp
# =========================
def construir_driver():
    opts = Options()
    # binario de Chromium es el del contenedor (variable la maneja Dockerfile)
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    # headless en servidor
    opts.add_argument("--headless=new")

    service = Service()  # chromedriver en PATH del contenedor
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def asegurar_sesion_whatsapp(driver):
    driver.get("https://web.whatsapp.com/")
    time.sleep(TIEMPO_CARGA_WA)
    try:
        driver.save_screenshot(QR_PATH)
    except Exception:
        pass


def enviar_whatsapp(driver, telefono, mensaje):
    url = WHATSAPP_URL.format(telefono=telefono, mensaje=quote(mensaje))
    driver.get(url)
    time.sleep(TIEMPO_TRAS_NAVEGAR_CHAT)
    # Intenta botón “Enviar” por aria-label en español
    try:
        btn = driver.find_element(By.XPATH, "//button[@aria-label='Enviar']")
        btn.click()
        time.sleep(1.5)
        return True
    except Exception:
        # Fallback: ENTER en el editor contenteditable
        try:
            caja = driver.find_element(By.XPATH, "//div[@contenteditable='true' and starts-with(@data-tab,'1')]")
            caja.send_keys(Keys.ENTER)
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[!] Falló envío a {telefono}: {e}")
            return False


# =========================
# Marcado de 'Enviado'
# =========================
def ya_enviado_en_anio(valor_enviado, anio):
    """
    'Enviado' puede ser '2024,2025' o con espacios. Si contiene el año, no reenviar.
    """
    if valor_enviado is None:
        return False
    tokens = [t for t in re.split(r"[,\s]+", str(valor_enviado).strip()) if t]
    return str(anio) in tokens


def marcar_enviado(ws, fila_real, anio):
    headers = ws.row_values(1)
    try:
        col_idx = headers.index("Enviado") + 1
    except ValueError:
        raise RuntimeError("No existe la columna 'Enviado' en la fila 1.")
    actual = ws.cell(fila_real, col_idx).value
    if actual and str(anio) in str(actual):
        nuevo = actual
    elif actual and str(actual).strip():
        nuevo = f"{actual},{anio}"
    else:
        nuevo = str(anio)
    ws.update_cell(fila_real, col_idx, nuevo)


# =========================
# Mensajería
# =========================
def construir_mensaje(nombre, cargo, fecha_str):
    return (
        f"🎉 *Recordatorio de Cumpleaños*\n"
        f"👤 *{nombre}* ({cargo})\n"
        f"📅 {fecha_str}\n\n"
        f"¡Felicidades anticipadas! 🎂🎈"
    )


# =========================
# Lógica principal
# =========================
def main():
    hoy = dt.date.today()
    ws = abrir_worksheet()
    datos = ws.get_all_records()  # lista de dicts sin encabezado
    if not datos:
        print("No hay registros.")
        return

    df = pd.DataFrame(datos)
    # Validaciones mínimas de columnas
    requeridas = {"Nombre", "Cargo", "Fecha", "Enviado"}
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise RuntimeError(f"Faltan columnas requeridas: {faltantes}")

    driver = construir_driver()
    try:
        asegurar_sesion_whatsapp(driver)

        enviados = 0
        omitidos = 0

        for idx, row in df.iterrows():
            nombre = str(row.get("Nombre", "")).strip()
            cargo = str(row.get("Cargo", "")).strip()
            fecha_val = row.get("Fecha")
            enviado_val = row.get("Enviado")

            if not nombre or fecha_val in (None, "", float("nan")):
                continue

            # parse fecha dd/mm/yy|yyyy
            try:
                d, m = parse_fecha_ddmmyy(fecha_val)
            except Exception as e:
                print(f"[!] Fecha inválida para {nombre} ({fecha_val}): {e}")
                continue

            dias, anio_evento = dias_hasta_proximo(d, m, hoy=hoy)

            if 0 <= dias < DIAS_VENTANA:
                # evita duplicado por año
                if ya_enviado_en_anio(enviado_val, anio_evento):
                    omitidos += 1
                    continue

                fecha_mostrar = f"{d:02d}/{m:02d}/{anio_evento}"
                mensaje = construir_mensaje(nombre, cargo, fecha_mostrar)

                exito = False
                for tel in NUMEROS_DESTINATARIOS:
                    ok = enviar_whatsapp(driver, tel, mensaje)
                    exito = exito or ok
                    time.sleep(PAUSA_ENTRE_DESTINOS)

                if exito:
                    fila_real = idx + 2  # +2 porque get_all_records omite encabezado (fila 1) y DataFrame es 0-index
                    try:
                        marcar_enviado(ws, fila_real, anio_evento)
                        enviados += 1
                    except Exception as e:
                        print(f"[!] No pude marcar Enviado para {nombre}: {e}")
                else:
                    print(f"[!] No se logró enviar WhatsApp para {nombre}.")

        print(f"✅ Terminado. Enviados: {enviados} | Omitidos (ya-enviado): {omitidos}")

    finally:
        # Cierra navegador
        try:
            driver.quit()
        except Exception:
            pass


# Para que app.py pueda llamarlo ordenadamente
def run_job():
    main()


if __name__ == "__main__":
    main()