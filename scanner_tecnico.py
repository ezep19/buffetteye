"""
BuffettEye — Módulo de Análisis Técnico
Motor cuantitativo: Bollinger Bands, RSI, EMA, volumen institucional y CCL.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from config import (
    ALERT_MAX_PER_CYCLE,
    ALERT_MIN_SCORE,
    ARGENTINA_TZ,
    BB_PERIOD,
    BB_STD,
    CCL_DISCOUNT_THRESHOLD,
    EMA_PERIOD,
    MIN_DAILY_DROP_PCT,
    RSI_EXTREME,
    RSI_OVERSOLD,
    RSI_PERIOD,
    VOLUME_SPIKE_MULTIPLIER,
    WATCHLIST,
)

logger = logging.getLogger("buffetteye.scanner")


# ── Indicadores Técnicos ──────────────────────────────────────────────────────

def calculate_bollinger(close: pd.Series, period: int = BB_PERIOD, std_dev: float = BB_STD) -> pd.DataFrame:
    """Bandas de Bollinger (period, std_dev)."""
    sma = close.rolling(window=period).mean()
    sigma = close.rolling(window=period).std(ddof=0)
    return pd.DataFrame({
        "bb_upper": sma + std_dev * sigma,
        "bb_middle": sma,
        "bb_lower": sma - std_dev * sigma,
        "bb_width": (2 * std_dev * sigma) / sma,         # volatilidad normalizada
        "bb_pct_b": (close - (sma - std_dev * sigma)) / (2 * std_dev * sigma),  # posición en banda
    })


def calculate_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """RSI con suavizado de Wilder (EMA α=1/period)."""
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)
    alpha = 1 / period
    avg_gain = gains.ewm(alpha=alpha, adjust=False).mean()
    avg_loss = losses.ewm(alpha=alpha, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).rename("rsi")


def calculate_ema(close: pd.Series, period: int = EMA_PERIOD) -> pd.Series:
    """EMA estándar con suavizado 2/(period+1)."""
    return close.ewm(span=period, adjust=False).mean().rename(f"ema_{period}")


def detect_volume_spike(volume: pd.Series, window: int = 20, multiplier: float = VOLUME_SPIKE_MULTIPLIER) -> pd.Series:
    """True si el volumen de la vela supera multiplier × media de las últimas `window` velas."""
    vol_mean = volume.rolling(window=window).mean()
    return (volume > multiplier * vol_mean).rename("volume_spike")


# ── Descarga de Datos ─────────────────────────────────────────────────────────

def download_ohlcv(ticker: str, interval: str = "15m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Descarga datos OHLCV desde yfinance.
    interval: '15m' | '1h' | '1d'
    period:   '5d' | '1mo' | '3mo'
    Retorna DataFrame con columnas [Open, High, Low, Close, Volume] o None si falla.
    """
    try:
        df = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if df.empty or len(df) < 25:
            logger.warning(f"{ticker}: datos insuficientes para {interval}")
            return None
        # Aplanar MultiIndex si yfinance lo devuelve con columnas multinivel
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df[["Open", "High", "Low", "Close", "Volume"]].copy()
    except Exception as exc:
        logger.error(f"{ticker}: error descargando datos — {exc}")
        return None


def get_current_price_usd(ticker: str) -> Optional[float]:
    """Precio spot en USD desde yfinance (fast_info)."""
    try:
        info = yf.Ticker(ticker).fast_info
        return float(info.last_price)
    except Exception:
        return None


# ── Variación Diaria ─────────────────────────────────────────────────────────

def get_daily_change_pct(ticker: str) -> Optional[float]:
    """
    Retorna la variación porcentual del precio en el día actual.
    Positivo = subió, negativo = bajó.
    """
    try:
        info = yf.Ticker(ticker).fast_info
        prev_close = float(info.previous_close)
        last = float(info.last_price)
        if prev_close and prev_close > 0:
            return round((last - prev_close) / prev_close * 100, 2)
    except Exception:
        pass
    return None


# ── Dólar CCL de Referencia ───────────────────────────────────────────────────

def get_ccl_reference() -> Optional[float]:
    """
    Obtiene el tipo de cambio CCL de mercado desde dolarapi.com (API pública, sin auth).
    Retorna el valor de venta (ASK) del CCL. Devuelve None si la API no responde.
    """
    url = "https://dolarapi.com/v1/dolares/contadoconliqui"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("venta") or data.get("compra", 0))
    except Exception as exc:
        logger.warning(f"No se pudo obtener CCL de referencia: {exc}")
        return None


# ── Precio CEDEAR en ARS ──────────────────────────────────────────────────────

def get_cedear_price_ars(ticker: str, usd_price: float, ratio: int, ccl_ref: float) -> tuple[Optional[float], str]:
    """
    Intenta obtener el precio real del CEDEAR en ARS.

    Estrategia:
      1. Intenta la API pública de RAVA (rava.com) — endpoint no autenticado.
      2. Si falla, calcula el precio TEÓRICO como:
             precio_teorico = (usd_price / ratio) × ccl_ref
         y lo devuelve marcado como "teórico".

    Para conectar tu broker (IOL, Balanz, PPI, etc.) reemplaza el bloque de
    rava por la llamada a tu API con auth y retorna ("real", precio).

    Returns: (precio_ars, fuente) donde fuente ∈ {"rava", "teorico", "error"}
    """
    # — Intento 1: RAVA API ————————————————————————————————————————————————————
    try:
        url = f"https://api.rava.com/v1/security/{ticker}"
        resp = requests.get(url, timeout=4, headers={"User-Agent": "BuffettEye/1.0"})
        if resp.status_code == 200:
            data = resp.json()
            precio = data.get("last") or data.get("close")
            if precio and float(precio) > 0:
                return float(precio), "rava"
    except Exception:
        pass

    # — Fallback: precio teórico vía CCL de referencia ────────────────────────
    if ccl_ref and ccl_ref > 0 and usd_price and usd_price > 0:
        precio_teorico = (usd_price / ratio) * ccl_ref
        return precio_teorico, "teorico"

    return None, "error"


# ── Cálculo de Descuento CCL Individual ──────────────────────────────────────

def calculate_ccl_discount(
    cedear_ars: float,
    ratio: int,
    usd_price: float,
    ccl_ref: float,
) -> dict:
    """
    Calcula el CCL implícito del CEDEAR y su descuento/premio vs CCL de mercado.

    CCL_individual = (precio_ars × ratio) / precio_usd
    Descuento (%) = (CCL_ref - CCL_individual) / CCL_ref × 100
    Positivo = cotiza barato vs CCL de referencia (oportunidad de compra).
    """
    if not all([cedear_ars, ratio, usd_price, ccl_ref]):
        return {"ccl_individual": None, "discount_pct": None}

    ccl_individual = (cedear_ars * ratio) / usd_price
    discount_pct = ((ccl_ref - ccl_individual) / ccl_ref) * 100

    return {
        "ccl_individual": round(ccl_individual, 2),
        "ccl_referencia": round(ccl_ref, 2),
        "discount_pct": round(discount_pct, 2),
        "cotiza_con_descuento": discount_pct >= CCL_DISCOUNT_THRESHOLD,
    }


# ── Análisis Completo de un Ticker ───────────────────────────────────────────

def analyze_ticker(ticker: str, meta: dict) -> Optional[dict]:
    """
    Ejecuta el pipeline completo de análisis para un ticker:
      - Descarga OHLCV en 15m y 1h
      - Calcula Bollinger, RSI, EMA, volumen institucional
      - Calcula CCL individual y descuento
      - Retorna un dict con señales o None si no hay suficientes datos.
    """
    ratio: int = meta["ratio"]
    name: str = meta["name"]

    # ─ Descarga de datos ─────────────────────────────────────────────────────
    df_15m = download_ohlcv(ticker, interval="15m", period="5d")
    df_1h  = download_ohlcv(ticker, interval="1h",  period="1mo")

    if df_15m is None and df_1h is None:
        return None

    # Usamos el timeframe disponible con más resolución
    df = df_15m if df_15m is not None else df_1h
    tf_label = "15m" if df_15m is not None else "1h"

    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    # ─ Indicadores ───────────────────────────────────────────────────────────
    bb = calculate_bollinger(close)
    rsi = calculate_rsi(close)
    ema = calculate_ema(close)
    vol_spike = detect_volume_spike(volume)

    last_close = float(close.iloc[-1])
    last_rsi   = float(rsi.iloc[-1])
    last_ema   = float(ema.iloc[-1])
    last_bb_lower  = float(bb["bb_lower"].iloc[-1])
    last_bb_pct_b  = float(bb["bb_pct_b"].iloc[-1])
    last_vol_spike = bool(vol_spike.iloc[-1])

    # ─ Indicadores binarios ───────────────────────────────────────────────────
    rsi_oversold        = last_rsi < RSI_OVERSOLD          # RSI < 30
    rsi_extremo         = last_rsi < RSI_EXTREME           # RSI < 25
    precio_bajo_bb      = last_close < last_bb_lower
    precio_bajo_ema     = last_close < last_ema
    huella_institucional = last_vol_spike and rsi_oversold

    # ─ Caída diaria del precio ────────────────────────────────────────────────
    usd_price   = get_current_price_usd(ticker) or last_close
    daily_change = get_daily_change_pct(ticker)  # ej: -2.5 = bajó 2.5% hoy

    # ─ FILTROS DUROS: los 3 deben cumplirse para que la acción califique ──────
    # 1. RSI realmente oversold (< 30)
    # 2. Precio perforó la Banda Inferior de Bollinger
    # 3. La acción cayó al menos MIN_DAILY_DROP_PCT% hoy
    daily_ok = (daily_change is not None and daily_change <= MIN_DAILY_DROP_PCT)
    filtros_duros = rsi_oversold and precio_bajo_bb and daily_ok

    if not filtros_duros:
        # No califica — retornamos sin alerta para no desperdiciar tiempo en CCL/noticias
        return {
            "ticker": ticker, "name": name, "timeframe": tf_label,
            "timestamp": datetime.now(ARGENTINA_TZ).isoformat(),
            "precio_usd": round(usd_price, 4), "precio_ars": None, "fuente_ars": "—",
            "rsi": round(last_rsi, 1), "ema_20": round(last_ema, 4),
            "bb_lower": round(last_bb_lower, 4), "bb_pct_b": round(last_bb_pct_b, 3),
            "volumen_spike": last_vol_spike, "daily_change_pct": daily_change,
            "rsi_oversold": rsi_oversold, "precio_bajo_bb": precio_bajo_bb,
            "precio_bajo_ema": precio_bajo_ema, "huella_institucional": huella_institucional,
            "ratio": ratio, "opportunity_score": 0, "alerta_activa": False,
        }

    # ─ CCL (solo si pasa filtros duros) ──────────────────────────────────────
    ccl_ref = get_ccl_reference()
    cedear_ars, fuente_ars = get_cedear_price_ars(ticker, usd_price, ratio, ccl_ref)
    ccl_data = {}
    if cedear_ars and ccl_ref:
        ccl_data = calculate_ccl_discount(cedear_ars, ratio, usd_price, ccl_ref)

    # ─ Score de calidad (sobre la base de los filtros duros ya aprobados) ─────
    score = 0
    if rsi_extremo:                              score += 4   # RSI < 25
    elif rsi_oversold:                           score += 3   # RSI 25-30
    if huella_institucional:                     score += 3   # vol spike + RSI bajo
    if daily_change is not None:
        if daily_change <= -2.0:                 score += 2   # caída fuerte hoy
        elif daily_change <= -1.0:               score += 1   # caída moderada hoy
    if precio_bajo_ema:                          score += 1
    if ccl_data.get("cotiza_con_descuento"):     score += 1

    resultado = {
        "ticker": ticker,
        "name": name,
        "timeframe": tf_label,
        "timestamp": datetime.now(ARGENTINA_TZ).isoformat(),
        # ── Precios ───────────────────────────────────────────────────────────
        "precio_usd": round(usd_price, 4),
        "precio_ars": round(cedear_ars, 2) if cedear_ars else None,
        "fuente_ars": fuente_ars,
        "daily_change_pct": daily_change,
        # ── Indicadores ───────────────────────────────────────────────────────
        "rsi": round(last_rsi, 1),
        "ema_20": round(last_ema, 4),
        "bb_lower": round(last_bb_lower, 4),
        "bb_pct_b": round(last_bb_pct_b, 3),
        "volumen_spike": last_vol_spike,
        # ── Señales binarias ──────────────────────────────────────────────────
        "rsi_oversold": rsi_oversold,
        "rsi_extremo": rsi_extremo,
        "precio_bajo_bb": precio_bajo_bb,
        "precio_bajo_ema": precio_bajo_ema,
        "huella_institucional": huella_institucional,
        # ── CCL ───────────────────────────────────────────────────────────────
        **ccl_data,
        "ratio": ratio,
        # ── Score ─────────────────────────────────────────────────────────────
        "opportunity_score": score,
        "alerta_activa": score >= ALERT_MIN_SCORE,
    }

    return resultado


# ── Escaneo Completo del Mercado ──────────────────────────────────────────────

def scan_market(watchlist: dict | None = None, delay_between: float = 1.5) -> list[dict]:
    """
    Escanea todos los tickers del watchlist.
    delay_between: segundos de espera entre llamadas a yfinance (evita rate-limit).
    Retorna lista de resultados con alerta_activa=True, ordenada por score desc.
    """
    if watchlist is None:
        watchlist = WATCHLIST

    alertas = []
    total = len(watchlist)

    for i, (ticker, meta) in enumerate(watchlist.items(), start=1):
        logger.info(f"[{i:02d}/{total}] Analizando {ticker} ({meta['name']})...")
        try:
            resultado = analyze_ticker(ticker, meta)
            if resultado and resultado["alerta_activa"]:
                alertas.append(resultado)
                logger.info(
                    f"  ✅ SEÑAL: score={resultado['opportunity_score']} "
                    f"RSI={resultado['rsi']} spike={resultado['volumen_spike']}"
                )
            elif resultado:
                logger.debug(f"  ─ Sin señal: score={resultado['opportunity_score']}")
        except Exception as exc:
            logger.error(f"{ticker}: excepción en analyze_ticker — {exc}")

        if i < total:
            time.sleep(delay_between)

    alertas.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return alertas[:ALERT_MAX_PER_CYCLE]
