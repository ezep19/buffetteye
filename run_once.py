"""
BuffettEye — Ejecución de un ciclo único.
Usado por GitHub Actions: corre, escanea, alerta y termina.
El bucle continuo (main_agent.py) sigue siendo válido para ejecución local.
"""

import logging
import sys
from datetime import datetime

import pytz

from config import ARGENTINA_TZ, MARKET_CLOSE, MARKET_OPEN, WATCHLIST
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


def main() -> None:
    now_str = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    logger.info(f"BuffettEye run_once iniciado - {now_str}")

    if not is_market_open():
        logger.info("Mercado cerrado. Sin acciones.")
        sys.exit(0)

    logger.info(f"Rueda abierta - escaneando {len(WATCHLIST)} CEDEARs...")
    signals = scan_market(watchlist=WATCHLIST)

    if not signals:
        logger.info("Sin señales activas en este ciclo.")
        sys.exit(0)

    logger.info(f"{len(signals)} señal(es) encontradas.")
    alerts_sent = 0

    for signal_data in signals:
        ticker = signal_data["ticker"]
        news_data = {"headlines": [], "summary": "", "sources_used": [], "headline_count": 0}

        if signal_data.get("volumen_spike") or signal_data.get("rsi_oversold"):
            news_data = get_news_context(ticker, signal_data.get("name", ticker))

        if send_alert(signal_data, news_data):
            alerts_sent += 1
            logger.info(f"Alerta enviada: {ticker} (score={signal_data['opportunity_score']})")

    logger.info(f"Ciclo completo. Alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
