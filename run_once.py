"""
BuffettEye — Ejecución de un ciclo único.
Usado por GitHub Actions: corre, escanea, alerta y termina.
El bucle continuo (main_agent.py) sigue siendo válido para ejecución local.
"""

import logging
import sys
from datetime import datetime

import pytz

from config import (
    ARGENTINA_TZ,
    CRYPTO_ENABLED,
    CRYPTO_WATCHLIST,
    MARKET_CLOSE,
    MARKET_OPEN,
    WATCHLIST,
)
from internet_search import get_news_context
from scanner_tecnico import scan_market
from telegram_alerts import send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("buffetteye.run_once")


def is_market_open() -> bool:
    now = datetime.now(ARGENTINA_TZ)
    if now.weekday() >= 5:
        return False
    open_min  = MARKET_OPEN[0]  * 60 + MARKET_OPEN[1]
    close_min = MARKET_CLOSE[0] * 60 + MARKET_CLOSE[1]
    cur_min   = now.hour * 60 + now.minute
    return open_min <= cur_min < close_min


def _despachar(signals: list[dict]) -> int:
    """Busca noticias y envía las alertas. Retorna cuántas se enviaron."""
    alerts_sent = 0
    for signal_data in signals:
        ticker = signal_data["ticker"]
        news_data = {"headlines": [], "summary": "", "sources_used": [], "headline_count": 0}

        if signal_data.get("volumen_spike") or signal_data.get("rsi_oversold"):
            news_data = get_news_context(
                ticker,
                signal_data.get("name", ticker),
                asset_class=signal_data.get("asset_class", "cedear"),
            )

        if send_alert(signal_data, news_data):
            alerts_sent += 1
            logger.info(f"Alerta enviada: {ticker} (score={signal_data['opportunity_score']})")
    return alerts_sent


def main() -> None:
    now_str = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    logger.info(f"BuffettEye run_once iniciado - {now_str}")

    rueda_abierta = is_market_open()
    signals: list[dict] = []

    # ── CEDEARs: solo en horario de rueda BYMA ───────────────────────────────
    if rueda_abierta:
        logger.info(f"Rueda abierta - escaneando {len(WATCHLIST)} CEDEARs...")
        signals += scan_market(watchlist=WATCHLIST)
    else:
        logger.info("Rueda BYMA cerrada - se omiten los CEDEARs.")

    # ── Cripto: 24/7, incluidos fines de semana y feriados ───────────────────
    if CRYPTO_ENABLED:
        logger.info(f"Escaneando {len(CRYPTO_WATCHLIST)} criptomonedas (mercado 24/7)...")
        signals += scan_market(watchlist=CRYPTO_WATCHLIST)
    elif not rueda_abierta:
        logger.info("Cripto deshabilitada y rueda cerrada. Sin acciones.")
        sys.exit(0)

    if not signals:
        logger.info("Sin señales activas en este ciclo.")
        sys.exit(0)

    logger.info(f"{len(signals)} señal(es) encontradas.")
    alerts_sent = _despachar(signals)
    logger.info(f"Ciclo completo. Alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
