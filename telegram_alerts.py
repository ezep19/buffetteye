"""
BuffettEye — Módulo de Alertas Telegram
Envía mensajes con formato Markdown usando requests (sin dependencia de python-telegram-bot).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

from config import ARGENTINA_TZ, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

logger = logging.getLogger("buffetteye.alerts")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# ── Íconos de contexto ────────────────────────────────────────────────────────
_SECTOR_EMOJI = {
    "Tech":       "💻",
    "Media":      "🎬",
    "Finance":    "🏦",
    "Commerce":   "🛒",
    "Consumer":   "🍔",
    "Health":     "💊",
    "Energy":     "⛽",
    "Industrial": "⚙️",
    "Auto":       "🚗",
    "SaaS":       "☁️",
}

_RSI_LABEL = {
    (0,  25): "🔴 Sobreventa extrema",
    (25, 35): "🟠 Sobreventa",
    (35, 45): "🟡 Descuento",
    (45, 70): "🟢 Neutral",
    (70, 101): "⚪ Sobrecompra",
}


def _rsi_label(rsi: float) -> str:
    for (lo, hi), label in _RSI_LABEL.items():
        if lo <= rsi < hi:
            return label
    return "—"


def _score_bar(score: int, max_score: int = 10) -> str:
    """Barra visual de progreso para el opportunity score."""
    filled = round((score / max_score) * 10)
    return "█" * filled + "░" * (10 - filled)


def _format_price(value: Optional[float], prefix: str = "") -> str:
    if value is None:
        return "—"
    return f"{prefix}{value:,.2f}"


# ── Construcción del Mensaje ──────────────────────────────────────────────────

def build_message(signal: dict, news: dict) -> str:
    """
    Construye el texto Markdown del mensaje de alerta.
    signal: dict devuelto por scanner_tecnico.analyze_ticker()
    news:   dict devuelto por internet_search.get_news_context()
    """
    ticker      = signal["ticker"]
    name        = signal["name"]
    score       = signal["opportunity_score"]
    rsi         = signal["rsi"]
    precio_usd  = signal["precio_usd"]
    precio_ars  = signal.get("precio_ars")
    fuente_ars  = signal.get("fuente_ars", "—")
    tf          = signal.get("timeframe", "15m")
    sector      = next(
        (meta["sector"] for sym, meta in __import__("config").WATCHLIST.items() if sym == ticker),
        ""
    )
    sector_emoji = _SECTOR_EMOJI.get(sector, "📊")

    # ── Bloque CCL ────────────────────────────────────────────────────────────
    ccl_block = ""
    ccl_ind = signal.get("ccl_individual")
    ccl_ref = signal.get("ccl_referencia")
    discount = signal.get("discount_pct")
    if ccl_ind and discount is not None:
        trend = "🟢 Descuento" if discount > 0 else "🔴 Premio"
        ccl_block = (
            f"\n*💱 CCL Individual:* `${_format_price(ccl_ind)}`\n"
            f"*💱 CCL Mercado:*    `${_format_price(ccl_ref)}`\n"
            f"*📉 Descuento:*      `{discount:+.2f}%` {trend}\n"
            f"*⚠️ Precio ARS:*     `${_format_price(precio_ars, '$')}` _{fuente_ars}_"
        )
    elif precio_ars:
        ccl_block = f"\n*💵 Precio ARS:* `${_format_price(precio_ars)}` _{fuente_ars}_"

    # ── Bloque de señales ─────────────────────────────────────────────────────
    flags = []
    if signal.get("rsi_oversold"):         flags.append("RSI en descuento")
    if signal.get("precio_bajo_bb"):       flags.append("Precio bajo Banda Inferior BB")
    if signal.get("huella_institucional"): flags.append("🔔 Huella institucional detectada")
    if signal.get("precio_bajo_ema"):      flags.append("Precio bajo EMA 20")
    if signal.get("cotiza_con_descuento"): flags.append("CCL con descuento vs mercado")
    flags_text = "\n".join(f"   ✅ {f}" for f in flags) if flags else "   _Sin señales adicionales_"

    # ── Noticias ──────────────────────────────────────────────────────────────
    news_text = news.get("summary", "_Sin titulares recientes_")
    sources = ", ".join(news.get("sources_used", []))

    # ── Timestamp ─────────────────────────────────────────────────────────────
    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")

    msg = (
        f"🏛️👁️ *[BUFFETTEYE \\- ALERTA DE VALOR]*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sector_emoji} *{ticker}* — {name}\n"
        f"⏱ Timeframe: `{tf}` | 🕐 `{now}`\n\n"
        f"*📊 Score de Oportunidad:* `{score}/10`\n"
        f"`{_score_bar(score)}`\n\n"
        f"*📈 Datos de Precio*\n"
        f"*💵 Precio USD:*   `${_format_price(precio_usd)}`\n"
        f"{ccl_block}\n\n"
        f"*🔬 Indicadores Técnicos*\n"
        f"*RSI ({tf}):*   `{rsi}` — {_rsi_label(rsi)}\n"
        f"*EMA 20:*   `{_format_price(signal.get('ema_20'), '$')}`\n"
        f"*BB Inf:*   `{_format_price(signal.get('bb_lower'), '$')}`\n"
        f"*%B:*       `{signal.get('bb_pct_b', '—')}`\n"
        f"*Vol Spike:* `{'Sí 🚨' if signal.get('volumen_spike') else 'No'}`\n\n"
        f"*⚡ Señales Activas*\n"
        f"{flags_text}\n\n"
        f"*📰 Contexto de Mercado* _{sources}_\n"
        f"{news_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_BuffettEye v1\\.0 — Value Intelligence Engine_"
    )
    return msg


# ── Envío a Telegram ──────────────────────────────────────────────────────────

def send_alert(signal: dict, news: dict) -> bool:
    """
    Envía una alerta a Telegram.
    Retorna True si el mensaje fue enviado exitosamente, False si falló.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False

    message = build_message(signal, news)
    url = _TELEGRAM_API.format(token=TELEGRAM_TOKEN)

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ Alerta enviada: {signal['ticker']} (score={signal['opportunity_score']})")
            return True
        else:
            logger.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            # Reintento sin Markdown si el parse falla (error 400)
            if resp.status_code == 400:
                payload["parse_mode"] = None
                payload["text"] = _strip_markdown(message)
                resp2 = requests.post(url, json=payload, timeout=10)
                return resp2.status_code == 200
            return False
    except requests.RequestException as exc:
        logger.error(f"Error enviando alerta para {signal['ticker']}: {exc}")
        return False


def send_heartbeat(scan_count: int, alerts_sent: int) -> None:
    """Envía un estado periódico para confirmar que el bot sigue vivo."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    msg = (
        f"🤖 BuffettEye | Heartbeat\n"
        f"🕐 {now}\n"
        f"🔁 Ciclos ejecutados: {scan_count}\n"
        f"🚨 Alertas enviadas: {alerts_sent}\n"
        f"✅ Sistema operativo"
    )
    url = _TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        r = requests.post(url, json=payload, timeout=8)
        if not r.ok:
            logger.warning(f"Heartbeat error: {r.text[:100]}")
    except Exception as exc:
        logger.warning(f"Heartbeat no enviado: {exc}")


def send_startup_message() -> None:
    """Notifica el inicio del agente."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    msg = (
        f"🟢 BuffettEye iniciado\n"
        f"🕐 {now}\n"
        f"👁 Escaneando 40 CEDEARs BYMA...\n"
        f"📡 Alertas activas"
    )
    url = _TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        r = requests.post(url, json=payload, timeout=8)
        if not r.ok:
            logger.error(f"send_startup error: {r.text[:100]}")
    except Exception as exc:
        logger.error(f"send_startup no enviado: {exc}")


# ── Utilidades internas ───────────────────────────────────────────────────────

def _strip_markdown(text: str) -> str:
    """Elimina los caracteres de formato Markdown como fallback."""
    import re
    return re.sub(r"[*_`\[\]()~>#+\-=|{}.!\\]", "", text)
