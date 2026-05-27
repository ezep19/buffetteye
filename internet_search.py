"""
BuffettEye — Módulo de Búsqueda en Internet
Obtiene titulares recientes para contextualizar picos de volumen o caídas.
Fuentes: yfinance ticker.news → Google News RSS → Yahoo Finance RSS (fallback).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests
import yfinance as yf

logger = logging.getLogger("buffetteye.internet")

_MAX_HEADLINES = 5
_MAX_AGE_HOURS = 2
_REQUEST_TIMEOUT = 6


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Elimina caracteres de control y espacios sobrantes."""
    return re.sub(r"\s+", " ", text).strip()


def _is_recent(pub_ts: Optional[int], max_hours: int = _MAX_AGE_HOURS) -> bool:
    """True si el artículo fue publicado en las últimas max_hours horas."""
    if pub_ts is None:
        return True  # si no hay timestamp lo incluimos por defecto
    age_hours = (time.time() - pub_ts) / 3600
    return age_hours <= max_hours


def _format_headline(title: str, source: str = "", pub_ts: Optional[int] = None) -> str:
    """Formatea un titular para incluir en el mensaje Telegram."""
    time_str = ""
    if pub_ts:
        dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
        time_str = f" [{dt.strftime('%H:%M UTC')}]"
    src = f" — _{source}_" if source else ""
    return f"• {_clean_text(title)}{time_str}{src}"


# ── Fuente 1: yfinance ticker.news ───────────────────────────────────────────

def _fetch_yfinance_news(ticker: str) -> list[str]:
    """
    Obtiene noticias desde yfinance (Yahoo Finance bajo el capó).
    Retorna lista de titulares formateados.
    """
    headlines = []
    try:
        t = yf.Ticker(ticker)
        news_items = t.news or []
        for item in news_items[:_MAX_HEADLINES * 2]:
            pub_ts = item.get("providerPublishTime")
            if not _is_recent(pub_ts):
                continue
            title = item.get("title", "")
            publisher = item.get("publisher", "")
            if title:
                headlines.append(_format_headline(title, publisher, pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"{ticker} yfinance news error: {exc}")
    return headlines


# ── Fuente 2: Google News RSS ────────────────────────────────────────────────

def _fetch_google_news_rss(query: str) -> list[str]:
    """
    Scrape RSS de Google News (sin API key).
    query: ej. 'AAPL Apple stock'
    """
    headlines = []
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": "BuffettEye/1.0"})
        if resp.status_code != 200:
            return headlines
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:_MAX_HEADLINES * 2]:
            title = entry.get("title", "")
            source = entry.get("source", {}).get("title", "Google News")
            # feedparser devuelve published_parsed como struct_time UTC
            pub_ts = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                import calendar
                pub_ts = int(calendar.timegm(entry.published_parsed))
            if not _is_recent(pub_ts):
                continue
            if title:
                headlines.append(_format_headline(title, source, pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"Google News RSS error para '{query}': {exc}")
    return headlines


# ── Fuente 3: Yahoo Finance RSS (fallback) ───────────────────────────────────

def _fetch_yahoo_finance_rss(ticker: str) -> list[str]:
    """Feed RSS público de Yahoo Finance para un ticker."""
    headlines = []
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": "BuffettEye/1.0"})
        if resp.status_code != 200:
            return headlines
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:_MAX_HEADLINES]:
            title = entry.get("title", "")
            pub_ts = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                import calendar
                pub_ts = int(calendar.timegm(entry.published_parsed))
            if not _is_recent(pub_ts):
                continue
            if title:
                headlines.append(_format_headline(title, "Yahoo Finance", pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"Yahoo Finance RSS error para {ticker}: {exc}")
    return headlines


# ── Función Principal ─────────────────────────────────────────────────────────

def get_news_context(ticker: str, company_name: str) -> dict:
    """
    Obtiene titulares recientes para `ticker` usando todas las fuentes disponibles.
    Retorna:
      {
        "headlines": [...],           # lista de strings formateados
        "summary": str,               # resumen compacto para el mensaje
        "sources_used": [...],        # qué fuentes respondieron
        "headline_count": int,
      }
    """
    all_headlines: list[str] = []
    sources_used: list[str] = []

    # — Fuente 1: yfinance —————————————————————————————————————————————————————
    yf_headlines = _fetch_yfinance_news(ticker)
    if yf_headlines:
        all_headlines.extend(yf_headlines)
        sources_used.append("Yahoo Finance")

    # — Fuente 2: Google News RSS (si hace falta más contexto) ────────────────
    if len(all_headlines) < 3:
        query = f"{ticker} {company_name} stock"
        gn_headlines = _fetch_google_news_rss(query)
        if gn_headlines:
            # Deduplicar por primeras 40 chars del título
            existing_prefixes = {h[:40] for h in all_headlines}
            for h in gn_headlines:
                if h[:40] not in existing_prefixes:
                    all_headlines.append(h)
                    existing_prefixes.add(h[:40])
            if gn_headlines:
                sources_used.append("Google News")

    # — Fuente 3: Yahoo RSS (último fallback) ─────────────────────────────────
    if len(all_headlines) < 2:
        yah_headlines = _fetch_yahoo_finance_rss(ticker)
        if yah_headlines:
            all_headlines.extend(yah_headlines)
            if yah_headlines:
                sources_used.append("Yahoo RSS")

    final = all_headlines[:_MAX_HEADLINES]

    if final:
        summary = "\n".join(final)
    else:
        summary = "_No se encontraron titulares recientes (últimas 2h)_"

    return {
        "headlines": final,
        "summary": summary,
        "sources_used": sources_used,
        "headline_count": len(final),
    }
