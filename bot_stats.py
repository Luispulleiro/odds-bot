"""
Bot v2 — Análisis de cuotas + estadísticas reales
Cruza: remates al arco, faltas, tarjetas, forma reciente vs cuotas bet365
Alertas: WhatsApp (Twilio)
"""

import os
import time
import logging
import requests
from datetime import datetime, timezone

# ── Configuración ──────────────────────────────────────────────────────────────
ODDS_API_KEY    = os.environ.get("ODDS_API_KEY", "")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
TWILIO_SID      = os.environ.get("TWILIO_SID", "")
TWILIO_TOKEN    = os.environ.get("TWILIO_TOKEN", "")
TWILIO_FROM     = os.environ.get("TWILIO_FROM", "whatsapp:+14155238886")
TU_WHATSAPP     = os.environ.get("TU_WHATSAPP", "")

UMBRAL_DIFERENCIA = 5.0   # % diferencia cuota bet365 vs Pinnacle
UMBRAL_CONFIANZA  = 60.0  # % mínimo de confianza estadística para alertar
INTERVALO_SEG     = 30600 # cada ~8.5 horas (500 requests/mes con 6 ligas)

# Ligas monitoreadas
# IDs de API-Football: https://api-football.com
LIGAS = {
    "Premier League":   {"odds_id": "soccer_epl",                        "api_id": 39},
    "La Liga":          {"odds_id": "soccer_spain_la_liga",               "api_id": 140},
    "Champions League": {"odds_id": "soccer_uefa_champs_league",          "api_id": 2},
    "Serie A":          {"odds_id": "soccer_italy_serie_a",               "api_id": 135},
    "Bundesliga":       {"odds_id": "soccer_germany_bundesliga",          "api_id": 78},
    "Liga Argentina":   {"odds_id": "soccer_argentina_primera_division",  "api_id": 128},
}

TEMPORADA = 2024  # Actualizar cada año

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── The Odds API ───────────────────────────────────────────────────────────────

def obtener_cuotas(odds_id: str) -> list:
    url = f"https://api.the-odds-api.com/v4/sports/{odds_id}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    "eu",
        "markets":    "h2h",
        "bookmakers": "bet365,pinnacle",
        "oddsFormat": "decimal",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"Error obteniendo cuotas {odds_id}: {e}")
        return []


def extraer_cuota_casa(evento: dict, bookmaker_key: str) -> dict:
    for bm in evento.get("bookmakers", []):
        if bm["key"] == bookmaker_key:
            for market in bm.get("markets", []):
                if market["key"] == "h2h":
                    return {o["name"]: o["price"] for o in market["outcomes"]}
    return {}


# ── API-Football ───────────────────────────────────────────────────────────────

def api_football(endpoint: str, params: dict) -> dict:
    url = f"https://v3.football.api-sports.io/{endpoint}"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"Error API-Football {endpoint}: {e}")
        return {}


def buscar_equipo_id(nombre: str, liga_id: int) -> int | None:
    data = api_football("teams", {"name": nombre, "league": liga_id, "season": TEMPORADA})
    equipos = data.get("response", [])
    if equipos:
        return equipos[0]["team"]["id"]
    # Búsqueda más amplia si no encuentra
    data2 = api_football("teams", {"search": nombre[:10]})
    equipos2 = data2.get("response", [])
    if equipos2:
        return equipos2[0]["team"]["id"]
    return None


def obtener_estadisticas_equipo(equipo_id: int, liga_id: int) -> dict:
    """Estadísticas de la temporada: goles, faltas, tarjetas, remates."""
    data = api_football("teams/statistics", {
        "team":   equipo_id,
        "league": liga_id,
        "season": TEMPORADA,
    })
    resp = data.get("response", {})
    if not resp:
        return {}

    goles_favor    = resp.get("goals", {}).get("for", {}).get("average", {}).get("total", 0)
    goles_contra   = resp.get("goals", {}).get("against", {}).get("average", {}).get("total", 0)
    partidos       = resp.get("fixtures", {}).get("played", {}).get("total", 1)

    # Faltas y tarjetas (si disponibles)
    cards          = resp.get("cards", {})
    amarillas      = sum(v.get("total", 0) or 0 for v in cards.get("yellow", {}).values())
    rojas          = sum(v.get("total", 0) or 0 for v in cards.get("red", {}).values())

    return {
        "goles_favor_avg":   float(goles_favor or 0),
        "goles_contra_avg":  float(goles_contra or 0),
        "amarillas_avg":     round(amarillas / max(partidos, 1), 2),
        "rojas_avg":         round(rojas / max(partidos, 1), 2),
        "partidos":          partidos,
    }


def obtener_forma_reciente(equipo_id: int, liga_id: int, ultimos: int = 5) -> dict:
    """Últimos N partidos: victorias, empates, derrotas, goles."""
    data = api_football("fixtures", {
        "team":   equipo_id,
        "league": liga_id,
        "season": TEMPORADA,
        "last":   ultimos,
        "status": "FT",
    })
    partidos = data.get("response", [])
    victorias = empates = derrotas = goles_f = goles_c = 0

    for p in partidos:
        teams  = p.get("teams", {})
        goals  = p.get("goals", {})
        es_local = teams.get("home", {}).get("id") == equipo_id

        gf = goals.get("home") if es_local else goals.get("away")
        gc = goals.get("away") if es_local else goals.get("home")
        goles_f += gf or 0
        goles_c += gc or 0

        winner = teams.get("home" if es_local else "away", {}).get("winner")
        if winner is True:
            victorias += 1
        elif winner is False:
            derrotas += 1
        else:
            empates += 1

    total = max(len(partidos), 1)
    return {
        "victorias": victorias,
        "empates":   empates,
        "derrotas":  derrotas,
        "puntos_pct": round((victorias * 3 + empates) / (total * 3) * 100, 1),
        "goles_favor_avg":  round(goles_f / total, 2),
        "goles_contra_avg": round(goles_c / total, 2),
    }


def obtener_remates(equipo_id: int, liga_id: int, ultimos: int = 5) -> dict:
    """Remates al arco promedio en últimos N partidos."""
    data = api_football("fixtures", {
        "team":   equipo_id,
        "league": liga_id,
        "season": TEMPORADA,
        "last":   ultimos,
        "status": "FT",
    })
    partidos = data.get("response", [])
    remates_total = remates_arco = 0
    contados = 0

    for p in partidos:
        fixture_id = p["fixture"]["id"]
        stats_data = api_football("fixtures/statistics", {"fixture": fixture_id})
        stats = stats_data.get("response", [])
        for team_stats in stats:
            if team_stats.get("team", {}).get("id") == equipo_id:
                for s in team_stats.get("statistics", []):
                    if s["type"] == "Total Shots":
                        remates_total += s.get("value") or 0
                    if s["type"] == "Shots on Goal":
                        remates_arco += s.get("value") or 0
                contados += 1
                break
        time.sleep(0.3)  # Respetar rate limit

    if contados == 0:
        return {"remates_total_avg": 0, "remates_arco_avg": 0, "precision_remate_pct": 0}

    return {
        "remates_total_avg":    round(remates_total / contados, 1),
        "remates_arco_avg":     round(remates_arco / contados, 1),
        "precision_remate_pct": round(remates_arco / max(remates_total, 1) * 100, 1),
    }


# ── Análisis y scoring ─────────────────────────────────────────────────────────

def calcular_confianza(stats_local: dict, stats_visit: dict,
                       forma_local: dict, forma_visit: dict,
                       remates_local: dict, remates_visit: dict,
                       seleccion: str, home: str, away: str) -> float:
    """
    Calcula un score de confianza (0-100) basado en estadísticas.
    Indica qué tan respaldada está estadísticamente una selección.
    """
    score = 50.0  # Base neutral

    # ── Forma reciente (peso 35%) ──────────────────────────────────────────
    if seleccion == home:
        score += (forma_local.get("puntos_pct", 50) - 50) * 0.35
    elif seleccion == away:
        score += (forma_visit.get("puntos_pct", 50) - 50) * 0.35
    else:  # Empate
        empate_score = 100 - abs(forma_local.get("puntos_pct", 50) - forma_visit.get("puntos_pct", 50))
        score += (empate_score - 50) * 0.35

    # ── Remates al arco (peso 35%) ─────────────────────────────────────────
    rem_l = remates_local.get("remates_arco_avg", 0)
    rem_v = remates_visit.get("remates_arco_avg", 0)
    total_rem = rem_l + rem_v
    if total_rem > 0:
        if seleccion == home:
            score += (rem_l / total_rem * 100 - 50) * 0.35
        elif seleccion == away:
            score += (rem_v / total_rem * 100 - 50) * 0.35

    # ── Goles a favor (peso 20%) ───────────────────────────────────────────
    gf_l = stats_local.get("goles_favor_avg", 0)
    gf_v = stats_visit.get("goles_favor_avg", 0)
    total_gf = gf_l + gf_v
    if total_gf > 0:
        if seleccion == home:
            score += (gf_l / total_gf * 100 - 50) * 0.20
        elif seleccion == away:
            score += (gf_v / total_gf * 100 - 50) * 0.20

    # ── Goles en contra (peso 10%) — penaliza defensa mala ────────────────
    gc_l = stats_local.get("goles_contra_avg", 0)
    gc_v = stats_visit.get("goles_contra_avg", 0)
    if seleccion == home and gc_l > gc_v:
        score -= (gc_l - gc_v) * 5
    elif seleccion == away and gc_v > gc_l:
        score -= (gc_v - gc_l) * 5

    return max(0.0, min(100.0, round(score, 1)))


# ── Generador de alertas ───────────────────────────────────────────────────────

def analizar_partido(evento: dict, liga_nombre: str, liga_id: int) -> list:
    alertas = []

    home = evento.get("home_team", "")
    away = evento.get("away_team", "")
    inicio = evento.get("commence_time", "")

    bet365   = extraer_cuota_casa(evento, "bet365")
    pinnacle = extraer_cuota_casa(evento, "pinnacle")

    if not bet365 or not pinnacle:
        return []

    # Solo analizar si hay diferencia de cuota
    hay_diferencia = False
    for sel, cuota_b365 in bet365.items():
        cuota_pin = pinnacle.get(sel)
        if cuota_pin and ((cuota_b365 - cuota_pin) / cuota_pin * 100) >= UMBRAL_DIFERENCIA:
            hay_diferencia = True
            break

    if not hay_diferencia:
        return []

    log.info(f"Diferencia de cuota detectada: {home} vs {away} — obteniendo estadísticas...")

    # Buscar IDs de equipos
    id_home = buscar_equipo_id(home, liga_id)
    id_away = buscar_equipo_id(away, liga_id)
    if not id_home or not id_away:
        log.warning(f"No se encontraron IDs para {home} o {away}")
        return []

    # Obtener estadísticas
    stats_home   = obtener_estadisticas_equipo(id_home, liga_id)
    stats_away   = obtener_estadisticas_equipo(id_away, liga_id)
    forma_home   = obtener_forma_reciente(id_home, liga_id)
    forma_away   = obtener_forma_reciente(id_away, liga_id)
    remates_home = obtener_remates(id_home, liga_id)
    remates_away = obtener_remates(id_away, liga_id)

    for seleccion, cuota_b365 in bet365.items():
        cuota_pin = pinnacle.get(seleccion)
        if not cuota_pin:
            continue

        diferencia_pct = (cuota_b365 - cuota_pin) / cuota_pin * 100
        if diferencia_pct < UMBRAL_DIFERENCIA:
            continue

        confianza = calcular_confianza(
            stats_home, stats_away,
            forma_home, forma_away,
            remates_home, remates_away,
            seleccion, home, away,
        )

        if confianza < UMBRAL_CONFIANZA:
            log.info(f"Diferencia detectada pero confianza baja ({confianza}%): {seleccion}")
            continue

        alertas.append({
            "liga":           liga_nombre,
            "partido":        f"{home} vs {away}",
            "inicio":         inicio,
            "seleccion":      seleccion,
            "cuota_b365":     cuota_b365,
            "cuota_pinnacle": cuota_pin,
            "diferencia_pct": round(diferencia_pct, 1),
            "confianza":      confianza,
            # Stats para el mensaje
            "forma_home":     forma_home,
            "forma_away":     forma_away,
            "remates_home":   remates_home,
            "remates_away":   remates_away,
            "stats_home":     stats_home,
            "stats_away":     stats_away,
            "home":           home,
            "away":           away,
        })

    return alertas


# ── Mensaje WhatsApp ───────────────────────────────────────────────────────────

def formatear_mensaje(alertas: list) -> str:
    ahora = datetime.now().strftime("%d/%m %H:%M")
    lineas = [f"🚨 *Bot de cuotas v2 — {ahora}*\n"]
    lineas.append(f"*{len(alertas)} alerta(s)* con respaldo estadístico:\n")

    for a in alertas:
        fh = a["forma_home"]
        fa = a["forma_away"]
        rh = a["remates_home"]
        ra = a["remates_away"]
        sh = a["stats_home"]
        sa = a["stats_away"]

        lineas.append(
            f"⚽ *{a['partido']}*\n"
            f"   Liga: {a['liga']}\n"
            f"   Selección: *{a['seleccion']}*\n"
            f"   bet365: *{a['cuota_b365']}* | Pinnacle: {a['cuota_pinnacle']} | Dif: +{a['diferencia_pct']}%\n"
            f"   Confianza estadística: *{a['confianza']}%*\n"
            f"\n"
            f"   📊 *Forma últimos 5 partidos:*\n"
            f"   {a['home']}: {fh.get('victorias',0)}V {fh.get('empates',0)}E {fh.get('derrotas',0)}D "
            f"({fh.get('puntos_pct',0)}% puntos)\n"
            f"   {a['away']}: {fa.get('victorias',0)}V {fa.get('empates',0)}E {fa.get('derrotas',0)}D "
            f"({fa.get('puntos_pct',0)}% puntos)\n"
            f"\n"
            f"   🎯 *Remates al arco (prom):*\n"
            f"   {a['home']}: {rh.get('remates_arco_avg',0)} | {a['away']}: {ra.get('remates_arco_avg',0)}\n"
            f"\n"
            f"   🟨 *Tarjetas amarillas (prom/partido):*\n"
            f"   {a['home']}: {sh.get('amarillas_avg',0)} | {a['away']}: {sa.get('amarillas_avg',0)}\n"
            f"\n"
            f"   ⚽ *Goles a favor (prom):*\n"
            f"   {a['home']}: {sh.get('goles_favor_avg',0)} | {a['away']}: {sa.get('goles_favor_avg',0)}\n"
        )

    lineas.append("_Verificá siempre la cuota antes de apostar._")
    return "\n".join(lineas)


def enviar_whatsapp(mensaje: str) -> bool:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
    try:
        r = requests.post(
            url,
            auth=(TWILIO_SID, TWILIO_TOKEN),
            data={"From": TWILIO_FROM, "To": TU_WHATSAPP, "Body": mensaje},
            timeout=15,
        )
        r.raise_for_status()
        log.info("WhatsApp enviado.")
        return True
    except Exception as e:
        log.error(f"Error enviando WhatsApp: {e}")
        return False


# ── Ciclo principal ────────────────────────────────────────────────────────────

def ciclo():
    todas_alertas = []

    for liga_nombre, liga_cfg in LIGAS.items():
        log.info(f"Procesando: {liga_nombre}")
        eventos = obtener_cuotas(liga_cfg["odds_id"])

        for evento in eventos:
            alertas = analizar_partido(evento, liga_nombre, liga_cfg["api_id"])
            todas_alertas.extend(alertas)
            time.sleep(1)

    if todas_alertas:
        msg = formatear_mensaje(todas_alertas)
        enviar_whatsapp(msg)
        log.info(f"Se enviaron {len(todas_alertas)} alertas.")
    else:
        log.info("Sin alertas en este ciclo.")


if __name__ == "__main__":
    log.info(f"Bot v2 iniciado — ciclo cada {INTERVALO_SEG//3600:.1f} horas")
    while True:
        try:
            ciclo()
        except Exception as e:
            log.error(f"Error en ciclo: {e}")
        time.sleep(INTERVALO_SEG)
