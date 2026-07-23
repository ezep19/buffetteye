"""
BuffettEye — Módulo de Búsqueda de Noticias en Español
Obtiene titulares recientes en español para contextualizar movimientos de precio.
Fuentes: Google News AR (español) → Ambito Financiero → Yahoo Finance España → Yahoo Finance EN (fallback)
"""

from __future__ import annotations

import calendar
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import requests

logger = logging.getLogger("buffetteye.internet")

_MAX_HEADLINES = 4
_MAX_AGE_HOURS_ES = 720  # noticias en español: ventana de 30 días
_MAX_AGE_HOURS_EN = 24   # noticias en inglés: últimas 24h
_REQUEST_TIMEOUT = 6
_HEADERS = {"User-Agent": "Mozilla/5.0 BuffettEye/1.0"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _ts_from_struct(entry) -> Optional[int]:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return int(calendar.timegm(entry.published_parsed))
    return None


def _is_recent(pub_ts: Optional[int], max_hours: int = _MAX_AGE_HOURS_ES) -> bool:
    if pub_ts is None:
        return True
    return (time.time() - pub_ts) / 3600 <= max_hours


def _fmt_hora(pub_ts: Optional[int]) -> str:
    if not pub_ts:
        return ""
    dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
    age_h = (time.time() - pub_ts) / 3600
    if age_h < 24:
        return f" [hace {int(age_h)}h]"
    else:
        return f" [{dt.strftime('%d/%m/%Y')}]"


def _fmt_headline(title: str, source: str = "", pub_ts: Optional[int] = None) -> str:
    return f"• {_clean(title)}{_fmt_hora(pub_ts)} — <i>{_clean(source)}</i>"


# ── Fuente 1: Google News RSS en español (Argentina) ─────────────────────────

def _google_news_es(query: str) -> list[str]:
    """
    Google News RSS en español con localización argentina.
    Ejemplo de query: 'Apple acciones caída bolsa'
    """
    headlines = []
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=es-419&gl=AR&ceid=AR:es"
    )
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=_HEADERS)
        if resp.status_code != 200:
            return headlines
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:_MAX_HEADLINES * 2]:
            title = entry.get("title", "")
            source = entry.get("source", {}).get("title", "Google News")
            pub_ts = _ts_from_struct(entry)
            if not _is_recent(pub_ts):
                continue
            if title:
                headlines.append(_fmt_headline(title, source, pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"Google News ES error: {exc}")
    return headlines


# ── Fuente 2: Ambito Financiero RSS ──────────────────────────────────────────

def _ambito_rss(company_name: str, asset_class: str = "cedear") -> list[str]:
    """
    RSS de Ambito Financiero (mercados). Filtra por nombre de empresa/activo.
    """
    headlines = []
    urls = [
        "https://www.ambito.com/rss/pages/economia-y-finanzas.xml",
        "https://www.ambito.com/rss/pages/mercados.xml",
    ]
    keywords = [w.lower() for w in company_name.split() if len(w) > 3]
    # Términos genéricos que hacen pasar una nota como "contexto de mercado"
    generic = (
        ["bitcoin", "cripto", "criptomoneda", "blockchain", "ethereum"]
        if asset_class == "crypto" else
        ["wall street", "nasdaq", "s&p", "bolsa", "acciones", "mercado"]
    )

    for url in urls:
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=_HEADERS)
            if resp.status_code != 200:
                continue
            feed = feedparser.parse(resp.text)
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                pub_ts = _ts_from_struct(entry)
                if not _is_recent(pub_ts):
                    continue
                # Filtrar por activo o contexto de mercado
                title_lower = title.lower()
                if any(kw in title_lower for kw in keywords) or \
                   any(w in title_lower for w in generic):
                    headlines.append(_fmt_headline(title, "Ámbito Financiero", pub_ts))
                if len(headlines) >= _MAX_HEADLINES:
                    break
        except Exception as exc:
            logger.debug(f"Ambito RSS error: {exc}")
        if headlines:
            break
    return headlines


# ── Fuente 3: Yahoo Finance España RSS ───────────────────────────────────────

def _yahoo_es_rss(ticker: str) -> list[str]:
    """Yahoo Finance en español — noticias del ticker en castellano."""
    headlines = []
    url = f"https://es.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=ES&lang=es-ES"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=_HEADERS)
        if resp.status_code != 200:
            return headlines
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:_MAX_HEADLINES]:
            title = entry.get("title", "")
            pub_ts = _ts_from_struct(entry)
            if not _is_recent(pub_ts):
                continue
            if title:
                headlines.append(_fmt_headline(title, "Yahoo Finanzas", pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"Yahoo ES RSS error: {exc}")
    return headlines


# ── Fuente 4: Yahoo Finance EN (fallback traducido en texto) ─────────────────

def _yahoo_en_rss(ticker: str) -> list[str]:
    """Fallback en inglés si no hay noticias en español."""
    headlines = []
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT, headers=_HEADERS)
        if resp.status_code != 200:
            return headlines
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:_MAX_HEADLINES]:
            title = entry.get("title", "")
            pub_ts = _ts_from_struct(entry)
            if not _is_recent(pub_ts, max_hours=_MAX_AGE_HOURS_EN):
                continue
            if title:
                headlines.append(_fmt_headline(f"[EN] {title}", "Yahoo Finance", pub_ts))
            if len(headlines) >= _MAX_HEADLINES:
                break
    except Exception as exc:
        logger.debug(f"Yahoo EN RSS error: {exc}")
    return headlines


# ── Función Principal ─────────────────────────────────────────────────────────

def get_news_context(ticker: str, company_name: str, asset_class: str = "cedear") -> dict:
    """
    Obtiene titulares recientes en español para `ticker`.
    Prioridad: Google News AR → Ambito → Yahoo ES → Yahoo EN (fallback).

    asset_class: 'cedear' o 'crypto'. Cambia los términos de búsqueda — buscar
    "Bitcoin acciones bolsa" no devuelve nada útil.
    """
    is_crypto = asset_class == "crypto"
    # yfinance usa 'BTC-USD'; para buscar noticias sirve el símbolo pelado.
    simbolo = ticker.replace("-USD", "") if is_crypto else ticker

    all_headlines: list[str] = []
    sources_used: list[str] = []

    def _add(new_items: list[str], source_label: str):
        existing = {h[:50] for h in all_headlines}
        for h in new_items:
            if h[:50] not in existing:
                all_headlines.append(h)
                existing.add(h[:50])
        if new_items:
            sources_used.append(source_label)

    # 1. Google News en español
    query_es = (
        f"{company_name} criptomoneda precio" if is_crypto
        else f"{company_name} acciones bolsa"
    )
    _add(_google_news_es(query_es), "Google News AR")

    # 2. Google News con símbolo
    if len(all_headlines) < 2:
        query_sym = f"{simbolo} cripto" if is_crypto else f"{ticker} acciones"
        _add(_google_news_es(query_sym), "Google News")

    # 3. Ambito Financiero
    if len(all_headlines) < 2:
        _add(_ambito_rss(company_name, asset_class), "Ámbito Financiero")

    # 4. Yahoo Finance España
    if len(all_headlines) < 2:
        _add(_yahoo_es_rss(ticker), "Yahoo Finanzas ES")

    # 5. Yahoo Finance EN (fallback)
    if len(all_headlines) < 1:
        _add(_yahoo_en_rss(ticker), "Yahoo Finance")

    final = all_headlines[:_MAX_HEADLINES]

    if final:
        summary = "\n".join(final)
    else:
        summary = "<i>Sin noticias recientes en las últimas horas.</i>"

    return {
        "headlines": final,
        "summary": summary,
        "sources_used": sources_used,
        "headline_count": len(final),
    }
