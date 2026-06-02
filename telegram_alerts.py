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
    filled = round((score / max_score) * 10)
    return "█" * filled + "░" * (10 - filled)


def _format_price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _h(text: str) -> str:
    """Escapa caracteres especiales HTML: &, <, >"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Construcción del Mensaje (HTML) ──────────────────────────────────────────

def build_message(signal: dict, news: dict) -> str:
    ticker     = signal["ticker"]
    name       = signal["name"]
    score      = signal["opportunity_score"]
    rsi        = signal["rsi"]
    precio_usd = signal["precio_usd"]
    precio_ars = signal.get("precio_ars")
    fuente_ars = signal.get("fuente_ars", "—")
    tf         = signal.get("timeframe", "15m")
    sector     = next(
        (meta["sector"] for sym, meta in __import__("config").WATCHLIST.items() if sym == ticker), ""
    )
    sector_emoji = _SECTOR_EMOJI.get(sector, "📊")
    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")

    # ── CCL ───────────────────────────────────────────────────────────────────
    ccl_block = ""
    ccl_ind  = signal.get("ccl_individual")
    ccl_ref  = signal.get("ccl_referencia")
    discount = signal.get("discount_pct")
    if ccl_ind and discount is not None:
        trend = "🟢 Descuento" if discount > 0 else "🔴 Premio"
        ccl_block = (
            f"\n💱 <b>CCL Individual:</b> <code>${_h(_format_price(ccl_ind))}</code>\n"
            f"💱 <b>CCL Mercado:</b>    <code>${_h(_format_price(ccl_ref))}</code>\n"
            f"📉 <b>Descuento:</b>      <code>{discount:+.2f}%</code> {trend}\n"
            f"⚠️ <b>Precio ARS:</b>     <code>${_h(_format_price(precio_ars))}</code> <i>{_h(fuente_ars)}</i>"
        )
    elif precio_ars:
        ccl_block = f"\n💵 <b>Precio ARS:</b> <code>${_h(_format_price(precio_ars))}</code> <i>{_h(fuente_ars)}</i>"

    # ── Señales activas con explicación ──────────────────────────────────────
    flags = []
    if signal.get("rsi_oversold"):
        flags.append(f"RSI en <b>{rsi}</b> → El mercado vendió en exceso. Zona histórica de rebote.")
    if signal.get("huella_institucional"):
        flags.append("🔔 <b>Huella institucional</b> → Volumen anormal con precio en caída. Posible acumulación de grandes inversores.")
    if signal.get("precio_bajo_bb"):
        flags.append("Precio por debajo de la Banda Inferior de Bollinger → Estadísticamente el precio está en zona de reversión.")
    if signal.get("precio_bajo_ema"):
        flags.append("Precio por debajo de la EMA 20 → La tendencia de corto plazo favorece una recuperación.")
    if signal.get("cotiza_con_descuento"):
        flags.append(f"CCL con {signal.get('discount_pct', 0):+.1f}% de descuento vs el mercado → El CEDEAR cotiza más barato que su valor teórico.")
    flags_text = "\n".join(f"  ✅ {f}" for f in flags) if flags else "  <i>Sin señales adicionales</i>"

    # ── Noticias ──────────────────────────────────────────────────────────────
    news_text = _h(news.get("summary", "")) or "<i>Sin titulares recientes</i>"
    sources   = _h(", ".join(news.get("sources_used", [])))

    msg = (
        f"🏛️👁️ <b>BUFFETTEYE — ALERTA DE VALOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sector_emoji} <b>{_h(ticker)}</b> — {_h(name)}\n"
        f"📅 {now} | Timeframe: {tf}\n\n"
        f"⭐ <b>SCORE DE OPORTUNIDAD: {score}/10</b>\n"
        f"<code>{_score_bar(score)}</code>\n\n"
        f"💵 <b>PRECIO:</b> <code>${_h(_format_price(precio_usd))} USD</code>"
        f"{ccl_block}\n\n"
        f"📊 <b>POR QUÉ ES UNA OPORTUNIDAD:</b>\n"
        f"{flags_text}\n\n"
        f"🔢 <b>DATOS TÉCNICOS:</b>\n"
        f"  • RSI ({tf}): <code>{rsi}</code> → {_rsi_label(rsi)}\n"
        f"  • EMA 20: <code>${_h(_format_price(signal.get('ema_20')))}</code> — referencia de tendencia\n"
        f"  • Banda inferior BB: <code>${_h(_format_price(signal.get('bb_lower')))}</code>\n"
        f"  • Volumen institucional: <code>{'Sí 🚨' if signal.get('volumen_spike') else 'No detectado'}</code>\n\n"
        f"📰 <b>QUÉ ESTÁ PASANDO (últimas 2hs):</b>\n"
        f"{news_text}\n\n"
        f"⚠️ <i>Señal técnica, no recomendación de compra. Siempre verificá el contexto antes de operar.</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>BuffettEye v1.0</i>"
    )
    return msg


# ── Envío a Telegram ──────────────────────────────────────────────────────────

def send_alert(signal: dict, news: dict) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False

    message = build_message(signal, news)
    url = _TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"Alerta enviada: {signal['ticker']} (score={signal['opportunity_score']})")
            return True
        else:
            logger.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
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
