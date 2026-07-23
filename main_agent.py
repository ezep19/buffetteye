"""
BuffettEye — Orquestador Principal
Bucle continuo que escanea el mercado durante la rueda bursátil argentina
y despacha alertas a Telegram cuando detecta oportunidades de valor.

Flujo por ciclo:
  1. Verificar horario bursátil (11:00–17:00 ARS).
  2. Ejecutar scan_market() → lista de señales con alerta_activa=True.
  3. Para cada señal: buscar noticias → enviar alerta Telegram.
  4. Dormir SCAN_INTERVAL_MINUTES y repetir.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime, timedelta

from config import (
    ARGENTINA_TZ,
    CRYPTO_ENABLED,
    CRYPTO_WATCHLIST,
    MARKET_CLOSE,
    MARKET_OPEN,
    SCAN_INTERVAL_MINUTES,
    WATCHLIST,
)
from internet_search import get_news_context
from scanner_tecnico import scan_market
from telegram_alerts import send_alert, send_heartbeat, send_startup_message

# ── Logging ───────────────────────────────────────────────────────────────────
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        _stream_handler,
        logging.FileHandler("buffetteye.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("buffetteye.main")

# ── Control de duplicados ─────────────────────────────────────────────────────
# Evita enviar la misma alerta repetidas veces en la misma rueda.
# Clave: (ticker, opportunity_score) — se limpia al abrir cada nueva rueda.
_alerted_today: set[tuple] = set()

# ── Métricas de sesión ────────────────────────────────────────────────────────
_scan_count = 0
_alerts_sent = 0
_heartbeat_every = 4  # ciclos entre heartbeats (4 × 15m = 1h aprox)

# ── Señal de salida limpia ────────────────────────────────────────────────────
_running = True


def _handle_sigint(sig, frame):  # noqa: ARG001
    global _running
    logger.info("Señal de salida recibida — deteniendo BuffettEye...")
    _running = False


signal.signal(signal.SIGINT, _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


# ── Helpers de horario ────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    """True si el reloj de Argentina está dentro del horario bursátil de BYMA."""
    now = datetime.now(ARGENTINA_TZ)
    # Excluir fines de semana
    if now.weekday() >= 5:  # 5=sábado, 6=domingo
        return False
    open_minutes  = MARKET_OPEN[0]  * 60 + MARKET_OPEN[1]
    close_minutes = MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1]
    current_mins  = now.hour * 60 + now.minute
    return open_minutes <= current_mins < close_minutes


def _minutes_until_open() -> int:
    """Cuántos minutos faltan para la próxima apertura bursátil."""
    now = datetime.now(ARGENTINA_TZ)
    open_h, open_m = MARKET_OPEN
    target = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    # Si ya pasó la apertura hoy, apuntamos al próximo día hábil
    # (timedelta y no replace(day=...): el día 31 rompía con ValueError)
    if now >= target:
        target += timedelta(days=1)
    while target.weekday() >= 5:  # saltear sábado y domingo
        target += timedelta(days=1)
    delta = (target - now).total_seconds() / 60
    return max(0, int(delta))


def _today_str() -> str:
    return datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d")


# ── Ciclo principal ───────────────────────────────────────────────────────────

def run_scan_cycle(incluir_cedears: bool = True) -> None:
    """
    Ejecuta un ciclo completo de escaneo y despacho de alertas.

    incluir_cedears: False fuera del horario de rueda BYMA — ahí solo tiene
    sentido escanear cripto, que cotiza 24/7.
    """
    global _scan_count, _alerts_sent

    _scan_count += 1
    alcance = "CEDEARs + cripto" if incluir_cedears else "solo cripto"
    logger.info(f"═══ Ciclo #{_scan_count} iniciado ({alcance}) ═══")

    # 1. Escaneo técnico de los watchlists habilitados
    signals: list[dict] = []
    if incluir_cedears:
        signals += scan_market(watchlist=WATCHLIST)
    if CRYPTO_ENABLED:
        signals += scan_market(watchlist=CRYPTO_WATCHLIST)
    logger.info(f"Ciclo #{_scan_count}: {len(signals)} señal(es) con alerta activa")

    # 2. Procesar cada señal
    for signal_data in signals:
        ticker = signal_data["ticker"]
        score  = signal_data["opportunity_score"]
        key    = (ticker, score, _today_str())

        # Evitar duplicados dentro de la misma rueda
        if key in _alerted_today:
            logger.debug(f"{ticker}: alerta ya enviada este ciclo, omitiendo")
            continue

        # Contexto de noticias — solo si hay pico de volumen o RSI en descuento
        news_data = {"headlines": [], "summary": "", "sources_used": [], "headline_count": 0}
        if signal_data.get("volumen_spike") or signal_data.get("rsi_oversold"):
            logger.info(f"{ticker}: buscando contexto de noticias...")
            name = signal_data.get("name", ticker)
            news_data = get_news_context(
                ticker, name, asset_class=signal_data.get("asset_class", "cedear")
            )

        # Envío de alerta
        sent = send_alert(signal_data, news_data)
        if sent:
            _alerted_today.add(key)
            _alerts_sent += 1
        else:
            logger.warning(f"{ticker}: fallo al enviar alerta")

        time.sleep(0.5)  # pequeña pausa entre envíos para no saturar Telegram

    # 3. Heartbeat periódico
    if _scan_count % _heartbeat_every == 0:
        send_heartbeat(_scan_count, _alerts_sent)


def main() -> None:
    global _alerted_today

    logger.info("=" * 42)
    logger.info("   BuffettEye v1.0 - Iniciando...")
    logger.info("=" * 42)
    cripto_txt = f" + {len(CRYPTO_WATCHLIST)} cripto 24/7" if CRYPTO_ENABLED else ""
    logger.info(
        f"Watchlist: {len(WATCHLIST)} CEDEARs{cripto_txt} | "
        f"Intervalo: {SCAN_INTERVAL_MINUTES}m"
    )

    send_startup_message()

    current_day = _today_str()

    while _running:
        # Limpiar deduplicador si cambia el día de rueda
        new_day = _today_str()
        if new_day != current_day:
            logger.info(f"Nueva rueda detectada ({new_day}) — limpiando deduplicador")
            _alerted_today.clear()
            current_day = new_day

        rueda_abierta = _is_market_open()

        # Escaneamos si la rueda está abierta (CEDEARs + cripto) o si hay cripto
        # habilitada (que cotiza 24/7, fines de semana y feriados incluidos).
        if rueda_abierta or CRYPTO_ENABLED:
            if not rueda_abierta:
                mins_left = _minutes_until_open()
                now_str = datetime.now(ARGENTINA_TZ).strftime("%H:%M ARS")
                logger.info(
                    f"[{now_str}] Rueda BYMA cerrada (reapertura en ~{mins_left}m) — "
                    f"escaneando solo cripto."
                )
            try:
                run_scan_cycle(incluir_cedears=rueda_abierta)
            except Exception as exc:
                logger.exception(f"Error inesperado en ciclo de escaneo: {exc}")

            # Esperar hasta el próximo ciclo
            logger.info(f"Próximo escaneo en {SCAN_INTERVAL_MINUTES} minutos...")
            _interruptible_sleep(SCAN_INTERVAL_MINUTES * 60)

        else:
            # Rueda cerrada y cripto deshabilitada — polling cada 5 minutos
            mins_left = _minutes_until_open()
            now_str = datetime.now(ARGENTINA_TZ).strftime("%H:%M ARS")
            logger.info(
                f"[{now_str}] Mercado cerrado — "
                f"reapertura en ~{mins_left}m. Revisando en 5min..."
            )
            _interruptible_sleep(300)  # 5 minutos

    logger.info("BuffettEye detenido correctamente.")


def _interruptible_sleep(seconds: float) -> None:
    """sleep() que respeta la señal de salida en intervalos de 1 segundo."""
    end = time.monotonic() + seconds
    while _running and time.monotonic() < end:
        time.sleep(1)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
