"""
BuffettEye — Configuración Central
Fuente de verdad única para watchlist, umbrales y parámetros de mercado.
"""

import os
import pytz
from dotenv import load_dotenv

load_dotenv()

# ── Credenciales ──────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ── Parámetros de escaneo ─────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 15))
CCL_DISCOUNT_THRESHOLD = float(os.getenv("CCL_DISCOUNT_THRESHOLD", 2.0))

# ── Zona horaria y horario bursátil (BYMA) ────────────────────────────────────
ARGENTINA_TZ = pytz.timezone("America/Argentina/Buenos_Aires")
MARKET_OPEN = (11, 0)   # 11:00 ARS
MARKET_CLOSE = (17, 0)  # 17:00 ARS

# ── Umbrales técnicos ─────────────────────────────────────────────────────────
RSI_OVERSOLD     = 30       # Umbral duro: RSI debe estar DEBAJO de esto para calificar
RSI_EXTREME      = 25       # RSI extremo (señal más fuerte)
RSI_OVERBOUGHT   = 70
VOLUME_SPIKE_MULTIPLIER = 1.80
BB_PERIOD = 20
BB_STD    = 2
RSI_PERIOD = 14
EMA_PERIOD = 20

# ── Filtros duros (TODOS deben cumplirse para que una acción califique) ───────
# 1. RSI < RSI_OVERSOLD (30)
# 2. Precio por debajo de la Banda Inferior de Bollinger
# 3. Caída en el día >= MIN_DAILY_DROP_PCT
MIN_DAILY_DROP_PCT = -1.0   # la acción debe haber caído al menos 1% hoy

# ── Puntaje adicional (sobre los filtros duros) ───────────────────────────────
# RSI < 25:              +4 pts
# RSI 25–30:             +3 pts
# Volumen institucional: +3 pts
# Caída > 2% en el día:  +2 pts
# Caída > 1% en el día:  +1 pt
# Precio bajo EMA 20:    +1 pt
# CCL con descuento:     +1 pt

# ── Filtros de despacho ───────────────────────────────────────────────────────
ALERT_MIN_SCORE    = 5     # score mínimo DESPUÉS de pasar los filtros duros
ALERT_MAX_PER_CYCLE = 2    # máximo 2 alertas por ciclo (las mejores del mercado)

# ── Watchlist: 40 CEDEARs más líquidos de BYMA ───────────────────────────────
# ratio: cantidad de CEDEARs necesarios para representar 1 acción subyacente
# CCL_individual = (precio_ars × ratio) / precio_usd
# Ratios vigentes según BYMA — verificar en https://www.byma.com.ar/cedears/
WATCHLIST: dict[str, dict] = {
    # ── Big Tech ──────────────────────────────────────────────────────────────
    "AAPL":  {"ratio": 1,   "name": "Apple Inc.",                 "sector": "Tech"},
    "MSFT":  {"ratio": 1,   "name": "Microsoft Corp.",            "sector": "Tech"},
    "GOOGL": {"ratio": 10,  "name": "Alphabet Inc. (Google)",     "sector": "Tech"},
    "AMZN":  {"ratio": 10,  "name": "Amazon.com Inc.",            "sector": "Tech"},
    "META":  {"ratio": 1,   "name": "Meta Platforms Inc.",        "sector": "Tech"},
    "NVDA":  {"ratio": 10,  "name": "NVIDIA Corp.",               "sector": "Tech"},
    "AMD":   {"ratio": 1,   "name": "Advanced Micro Devices",     "sector": "Tech"},
    "INTC":  {"ratio": 1,   "name": "Intel Corp.",                "sector": "Tech"},
    "ORCL":  {"ratio": 1,   "name": "Oracle Corp.",               "sector": "Tech"},
    "IBM":   {"ratio": 1,   "name": "IBM Corp.",                  "sector": "Tech"},
    "CRM":   {"ratio": 1,   "name": "Salesforce Inc.",            "sector": "Tech"},
    # ── Streaming & Consumer ──────────────────────────────────────────────────
    "NFLX":  {"ratio": 1,   "name": "Netflix Inc.",               "sector": "Media"},
    "DIS":   {"ratio": 1,   "name": "Walt Disney Co.",            "sector": "Media"},
    "SPOT":  {"ratio": 1,   "name": "Spotify Technology",         "sector": "Media"},
    # ── Fintech & Pagos ───────────────────────────────────────────────────────
    "V":     {"ratio": 1,   "name": "Visa Inc.",                  "sector": "Finance"},
    "MA":    {"ratio": 1,   "name": "Mastercard Inc.",            "sector": "Finance"},
    "PYPL":  {"ratio": 1,   "name": "PayPal Holdings Inc.",       "sector": "Finance"},
    "COIN":  {"ratio": 1,   "name": "Coinbase Global Inc.",         "sector": "Finance"},
    "GS":    {"ratio": 1,   "name": "Goldman Sachs Group",        "sector": "Finance"},
    "JPM":   {"ratio": 1,   "name": "JPMorgan Chase & Co.",       "sector": "Finance"},
    # ── E-commerce & Retail ───────────────────────────────────────────────────
    "BABA":  {"ratio": 1,   "name": "Alibaba Group",             "sector": "Commerce"},
    "SHOP":  {"ratio": 10,  "name": "Shopify Inc.",               "sector": "Commerce"},
    "WMT":   {"ratio": 1,   "name": "Walmart Inc.",               "sector": "Commerce"},
    "UBER":  {"ratio": 1,   "name": "Uber Technologies Inc.",     "sector": "Commerce"},
    # ── Consumo masivo ────────────────────────────────────────────────────────
    "KO":    {"ratio": 1,   "name": "Coca-Cola Co.",              "sector": "Consumer"},
    "MCD":   {"ratio": 1,   "name": "McDonald's Corp.",           "sector": "Consumer"},
    "SBUX":  {"ratio": 1,   "name": "Starbucks Corp.",            "sector": "Consumer"},
    "NKE":   {"ratio": 1,   "name": "Nike Inc.",                  "sector": "Consumer"},
    # ── Salud & Farmacéutica ──────────────────────────────────────────────────
    "JNJ":   {"ratio": 1,   "name": "Johnson & Johnson",          "sector": "Health"},
    "PFE":   {"ratio": 1,   "name": "Pfizer Inc.",                "sector": "Health"},
    # ── Energía ───────────────────────────────────────────────────────────────
    "XOM":   {"ratio": 1,   "name": "Exxon Mobil Corp.",          "sector": "Energy"},
    "CVX":   {"ratio": 1,   "name": "Chevron Corp.",              "sector": "Energy"},
    # ── Industria & Manufactura ───────────────────────────────────────────────
    "BA":    {"ratio": 1,   "name": "Boeing Co.",                 "sector": "Industrial"},
    "CAT":   {"ratio": 1,   "name": "Caterpillar Inc.",           "sector": "Industrial"},
    "DE":    {"ratio": 1,   "name": "Deere & Co.",                "sector": "Industrial"},
    "GE":    {"ratio": 1,   "name": "General Electric Co.",       "sector": "Industrial"},
    "MMM":   {"ratio": 1,   "name": "3M Co.",                     "sector": "Industrial"},
    # ── Autos ─────────────────────────────────────────────────────────────────
    "TSLA":  {"ratio": 1,   "name": "Tesla Inc.",                 "sector": "Auto"},
    "F":     {"ratio": 1,   "name": "Ford Motor Co.",             "sector": "Auto"},
    # ── Videoconferencia / SaaS ───────────────────────────────────────────────
    "ZM":    {"ratio": 1,   "name": "Zoom Video Communications",  "sector": "SaaS"},
}
