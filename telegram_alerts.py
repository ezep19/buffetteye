"""
BuffettEye — Módulo de Alertas Telegram
Envía mensajes con formato Markdown usando requests (sin dependencia de python-telegram-bot).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

from config import ALL_ASSETS, ARGENTINA_TZ, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

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
    # ── Cripto ────────────────────────────────────────────────────────────────
    "Crypto-L1":    "⛓️",
    "Crypto-Pagos": "💸",
    "Crypto-DeFi":  "🔗",
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
    filled = max(0, min(10, round((score / max_score) * 10)))
    return "█" * filled + "░" * (10 - filled)


def _format_price(value: Optional[float]) -> str:
    """
    Formatea un precio con decimales adaptativos.
    Necesario para cripto: DOGE a 0.073 se vería como '0.07' con 2 decimales fijos.
    """
    if value is None:
        return "—"
    abs_v = abs(value)
    if abs_v == 0:
        return "0.00"
    if abs_v < 0.01:
        return f"{value:,.6f}"
    if abs_v < 1:
        return f"{value:,.4f}"
    if abs_v < 100:
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return f"{value:,.2f}"


def _h(text: str) -> str:
    """Escapa caracteres especiales HTML: &, <, >"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Construcción del Mensaje (HTML) ──────────────────────────────────────────

def build_message(signal: dict, news: dict) -> str:
    ticker       = signal["ticker"]
    name         = signal["name"]
    score        = signal["opportunity_score"]
    rsi          = signal["rsi"]
    precio_usd   = signal["precio_usd"]
    precio_ars   = signal.get("precio_ars")
    fuente_ars   = signal.get("fuente_ars", "—")
    tf           = signal.get("timeframe", "15m")
    daily_change = signal.get("daily_change_pct")
    is_crypto    = signal.get("asset_class") == "crypto"
    sector       = ALL_ASSETS.get(ticker, {}).get("sector", "")
    sector_emoji = _SECTOR_EMOJI.get(sector, "📊")
    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    caida_str = f" | 📉 <b>{daily_change:+.2f}% hoy</b>" if daily_change is not None else ""

    # En cripto el ticker de yfinance viene como 'BTC-USD' — mostramos solo 'BTC'.
    ticker_label = ticker.replace("-USD", "") if is_crypto else ticker
    titulo   = "ALERTA CRIPTO" if is_crypto else "ALERTA DE VALOR"
    mercado  = "Mercado 24/7" if is_crypto else "BYMA"

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
    if signal.get("rsi_extremo"):
        flags.append(f"🔴 RSI en <b>{rsi}</b> → Sobreventa EXTREMA. Nivel históricamente escaso que suele preceder rebotes importantes.")
    elif signal.get("rsi_oversold"):
        flags.append(f"🟠 RSI en <b>{rsi}</b> → Sobreventa fuerte. El mercado vendió en exceso y el precio puede estar cerca de un piso.")
    if signal.get("precio_bajo_bb"):
        flags.append("📊 Precio perforó la Banda Inferior de Bollinger → Zona estadísticamente extrema. El 95% del tiempo el precio está por encima de este nivel.")
    # Los tramos de caída difieren: en cripto un -2% es ruido intradiario.
    if is_crypto:
        if signal.get("capitulacion"):
            flags.append(f"🩸 Capitulación: <b>{daily_change:.2f}%</b> en el día → Pánico vendedor. Históricamente donde se forman los pisos, y también donde más se pierde si el ciclo sigue bajando.")
        elif daily_change is not None and daily_change <= -8.0:
            flags.append(f"📉 Derrumbe de <b>{daily_change:.2f}%</b> en el día → Liquidaciones en cadena, típicas de mercados apalancados.")
        elif daily_change is not None and daily_change <= -4.0:
            flags.append(f"📉 Caída de <b>{daily_change:.2f}%</b> en el día → Presión vendedora fuerte en las últimas 24hs.")
    else:
        if daily_change is not None and daily_change <= -2.0:
            flags.append(f"📉 Caída de <b>{daily_change:.2f}%</b> en el día → Venta masiva en la sesión actual.")
        elif daily_change is not None and daily_change <= -1.0:
            flags.append(f"📉 Baja de <b>{daily_change:.2f}%</b> en el día → Presión vendedora sostenida.")
    if signal.get("huella_institucional"):
        detalle = (
            "Volumen inusualmente alto en caída. Puede ser acumulación de ballenas, pero también liquidación forzada de posiciones apalancadas."
            if is_crypto else
            "Volumen inusualmente alto en caída. Señal de posible acumulación por parte de grandes inversores."
        )
        flags.append(f"🔔 <b>Huella institucional</b> → {detalle}")
    if signal.get("precio_bajo_ema"):
        flags.append("📈 Precio por debajo de la EMA 20 → Tendencia de corto plazo extendida a la baja, candidata a recuperación.")
    if is_crypto and signal.get("regime") == "alcista":
        dist = signal.get("dist_sma_pct")
        dist_txt = f" (<b>{dist:+.1f}%</b> sobre su media de 200 días)" if dist is not None else ""
        flags.append(
            f"🛡️ Tendencia de fondo ALCISTA{dist_txt} → La caída ocurre dentro de un "
            f"mercado alcista, no de un derrumbe. Es el filtro que evita comprar en plena tendencia bajista."
        )
    if signal.get("cotiza_con_descuento"):
        flags.append(f"💱 CCL con <b>{signal.get('discount_pct', 0):+.1f}%</b> de descuento → El CEDEAR cotiza más barato que su valor implícito en dólares.")
    flags_text = "\n".join(f"  ✅ {f}" for f in flags) if flags else "  <i>Sin señales adicionales</i>"

    # ── Noticias ──────────────────────────────────────────────────────────────
    news_text = _h(news.get("summary", "")) or "<i>Sin titulares recientes</i>"
    sources   = _h(", ".join(news.get("sources_used", [])))

    disclaimer = (
        "⚠️ <i>Señal técnica, no recomendación de compra. La cripto no tiene balances ni "
        "flujo de caja que sostengan una valuación: puede seguir cayendo sin piso técnico. "
        "Nunca pongas plata que no puedas perder.</i>"
        if is_crypto else
        "⚠️ <i>Señal técnica, no recomendación de compra. Siempre verificá el contexto antes de operar.</i>"
    )

    msg = (
        f"🏛️👁️ <b>BUFFETTEYE — {titulo}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{sector_emoji} <b>{_h(ticker_label)}</b> — {_h(name)}\n"
        f"📅 {now} | {mercado} | Timeframe: {tf}{caida_str}\n\n"
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
        f"📰 <b>QUÉ ESTÁ PASANDO:</b>\n"
        f"{news_text}\n\n"
        f"{disclaimer}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>BuffettEye v1.1</i>"
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
    from config import CRYPTO_ENABLED, CRYPTO_WATCHLIST, WATCHLIST

    now = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y %H:%M ARS")
    cripto_line = (
        f"₿ {len(CRYPTO_WATCHLIST)} criptomonedas 24/7\n" if CRYPTO_ENABLED else ""
    )
    msg = (
        f"🟢 BuffettEye iniciado\n"
        f"🕐 {now}\n"
        f"👁 {len(WATCHLIST)} CEDEARs BYMA (11:00-17:00 ARS)\n"
        f"{cripto_line}"
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
