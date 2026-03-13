"""
Bot de detección de cuotas erróneas - bet365 vs Pinnacle
Deportes: Fútbol y Tenis
Alertas: WhatsApp (Twilio)
"""

import os
import time
import logging
import requests
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────────────────────
ODDS_API_KEY     = os.environ.get("ODDS_API_KEY", "TU_API_KEY_AQUI")
TWILIO_SID       = os.environ.get("TWILIO_SID", "TU_TWILIO_SID")
TWILIO_TOKEN     = os.environ.get("TWILIO_TOKEN", "TU_TWILIO_TOKEN")
TWILIO_FROM      = os.environ.get("TWILIO_FROM", "whatsapp:+14155238886")  # Número Twilio sandbox
TU_WHATSAPP      = os.environ.get("TU_WHATSAPP", "whatsapp:+54911XXXXXXXX")  # Tu número con código de país

# Umbral: si la cuota de bet365 supera X% a la de Pinnacle → alerta
UMBRAL_PORCENTAJE = 5.0

# Intervalo de consulta en segundos (por defecto: cada 5 minutos)
INTERVALO_SEGUNDOS = 300

# Deportes habilitados (IDs de The Odds API)
DEPORTES = {
    "Fútbol - Premier League":   "soccer_epl",
    "Fútbol - La Liga":          "soccer_spain_la_liga",
    "Fútbol - Champions League": "soccer_uefa_champs_league",
    "Fútbol - Serie A":          "soccer_italy_serie_a",
    "Fútbol - Bundesliga":       "soccer_germany_bundesliga",
    "Tenis - ATP":               "tennis_atp_french_open",
    "Tenis - WTA":               "tennis_wta_french_open",
}

BASE_URL = "https://api.the-odds-api.com/v4/sports"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Funciones principales ──────────────────────────────────────────────────────

def obtener_cuotas(deporte_id: str) -> list[dict]:
    """Descarga cuotas de bet365 y Pinnacle para un deporte dado."""
    url = f"{BASE_URL}/{deporte_id}/odds"
    params = {
        "apiKey":    ODDS_API_KEY,
        "regions":   "eu",
        "markets":   "h2h",
        "bookmakers": "bet365,pinnacle",
        "oddsFormat": "decimal",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.warning(f"Error consultando {deporte_id}: {e}")
        return []


def extraer_cuotas_casa(evento: dict, bookmaker_key: str) -> dict | None:
    """Devuelve {seleccion: cuota} para una casa dada en un evento."""
    for bm in evento.get("bookmakers", []):
        if bm["key"] == bookmaker_key:
            for market in bm.get("markets", []):
                if market["key"] == "h2h":
                    return {o["name"]: o["price"] for o in market["outcomes"]}
    return None


def detectar_errores(eventos: list[dict], deporte_nombre: str) -> list[dict]:
    """Compara cuotas bet365 vs Pinnacle y devuelve anomalías."""
    alertas = []

    for evento in eventos:
        nombre    = f"{evento.get('home_team', '?')} vs {evento.get('away_team', '?')}"
        inicio    = evento.get("commence_time", "")

        bet365   = extraer_cuotas_casa(evento, "bet365")
        pinnacle = extraer_cuotas_casa(evento, "pinnacle")

        if not bet365 or not pinnacle:
            continue

        for seleccion, cuota_b365 in bet365.items():
            cuota_pin = pinnacle.get(seleccion)
            if not cuota_pin:
                continue

            diferencia_pct = ((cuota_b365 - cuota_pin) / cuota_pin) * 100

            if diferencia_pct >= UMBRAL_PORCENTAJE:
                alertas.append({
                    "deporte":       deporte_nombre,
                    "partido":       nombre,
                    "inicio":        inicio,
                    "seleccion":     seleccion,
                    "cuota_b365":    cuota_b365,
                    "cuota_pinnacle": cuota_pin,
                    "diferencia_pct": round(diferencia_pct, 2),
                })
                log.info(
                    f"ALERTA | {nombre} | {seleccion} | "
                    f"bet365={cuota_b365} Pinnacle={cuota_pin} "
                    f"Dif={diferencia_pct:.1f}%"
                )

    return alertas


def formatear_mensaje(alertas: list[dict]) -> str:
    """Genera el mensaje de WhatsApp."""
    lineas = [f"🚨 *Bot de cuotas — {datetime.now().strftime('%d/%m %H:%M')}*\n"]
    lineas.append(f"Se encontraron *{len(alertas)} posibles errores* de cotización en bet365:\n")

    for a in alertas:
        lineas.append(
            f"⚽ *{a['partido']}*\n"
            f"   Deporte: {a['deporte']}\n"
            f"   Selección: {a['seleccion']}\n"
            f"   bet365: *{a['cuota_b365']}* | Pinnacle: {a['cuota_pinnacle']}\n"
            f"   Diferencia: +{a['diferencia_pct']}% vs mercado\n"
        )

    lineas.append("_Verificá la cuota antes de apostar._")
    return "\n".join(lineas)


def enviar_whatsapp(mensaje: str) -> bool:
    """Envía el mensaje por WhatsApp usando Twilio."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    try:
        resp = requests.post(
            url,
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"From": TWILIO_FROM, "To": TU_WHATSAPP, "Body": mensaje},
            timeout=15,
        )
        resp.raise_for_status()
        log.info("WhatsApp enviado correctamente.")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Error enviando WhatsApp: {e}")
        return False


def ciclo_completo():
    """Un ciclo de consulta y análisis para todos los deportes."""
    todas_alertas = []

    for nombre, deporte_id in DEPORTES.items():
        log.info(f"Consultando: {nombre}")
        eventos = obtener_cuotas(deporte_id)
        alertas = detectar_errores(eventos, nombre)
        todas_alertas.extend(alertas)
        time.sleep(1)  # Pausa breve entre requests

    if todas_alertas:
        mensaje = formatear_mensaje(todas_alertas)
        enviar_whatsapp(mensaje)
    else:
        log.info("Sin anomalías en este ciclo.")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Bot iniciado. Umbral de alerta: %.1f%%", UMBRAL_PORCENTAJE)
    log.info("Intervalo de consulta: %d segundos", INTERVALO_SEGUNDOS)

    while True:
        try:
            ciclo_completo()
        except Exception as e:
            log.error(f"Error inesperado en ciclo: {e}")
        time.sleep(INTERVALO_SEGUNDOS)
