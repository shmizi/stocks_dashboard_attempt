# 📈 Stock Dashboard

A modular Streamlit app for real-time Indian stock portfolio analysis, news sentiment tracking, and custom stock screening using Chartink.

--

## 📁 Project Structure

```
.
├── main.py                  # ← Streamlit entry point (fixed)
├── config.py                # Configuration management (JSON-backed)
├── data_sources.py          # YFinance, Screener.in, Chartink, News, AI providers
├── portfolio_manager.py     # Portfolio loading, processing, display
├── screen_builder.py        # Custom Chartink screener builder UI
├── rate_limiter.py          # Thread-safe rate limiting + retry with backoff
├── error_handler.py         # Error boundaries, validation, progress tracking
└── dashboard_config.json    # Runtime config (auto-created on first run)
```

---

## 🚀 Setup & Installation

### 1. Install dependencies

```bash
pip install streamlit pandas yfinance pandas-ta requests beautifulsoup4 feedparser nltk
```

### 2. Download NLTK sentiment data (one-time)

```python
import nltk
nltk.download('vader_lexicon')
```

### 3. Place your portfolio Excel file

Put your holdings file (e.g., `holdings-SOH330.xlsx`) in the project root, or update the path in Settings.

**Expected Excel structure:**
- Headers starting at row 23 (row index 22, configurable)
- Columns B–M (configurable)
- Required columns: `Symbol`, `Quantity Available`, `Average Price`
- Symbol format: `RELIANCE-EQ` or `INFY` (the `-EQ` suffix is stripped automatically)

### 4. Run the app

```bash
streamlit run main.py
```

---

## ✨ Features

### 📊 Portfolio Analysis
- Loads holdings from your broker's Excel export
- Fetches live prices via Yahoo Finance (with Screener.in as fallback)
- Calculates P/L, day change, and portfolio-level totals
- Supertrend (10, 7) signal for each holding — bullish/bearish status
- ETF-aware: ETFs skip the Screener.in fundamentals scrape
- AI-powered industry classification via Gemini API (optional)

### 📰 News & Sentiment
- Fetches Indian market news from Google News RSS
- VADER sentiment analysis (Positive / Neutral / Negative)
- Highlights articles mentioning your portfolio stocks
- Sentiment summary dashboard

### 🔍 Stock Screener (Chartink-powered)
Three sub-tabs:

| Tab | What it does |
|-----|-------------|
| **Custom Builder** | Build criteria visually (price, technical, fundamental, volume), combine with AND/OR |
| **Quick Presets** | One-click runs of saved strategies (Weekly Breakout, Momentum, Value Picks, Consolidation Breakout) |
| **Results** | Filter, sort, export results; highlights top ROCE/ROE performers |

Results are ranked by ROCE then ROE, with CSV export and watchlist creation (This is my personal preference).

### ⚙️ Settings
- Rate limit tuning (per-service delays, max retries, timeout)
- Supertrend parameters
- Portfolio file path and Excel range
- Cache clearing (portfolio, news, scan results)

---

## ⚙️ Configuration

`dashboard_config.json` is auto-created on first run. Key sections:

```json
{
  "etf_data": {
    "GOLDBEES": { "name": "...", "description": "...", "category": "Gold ETF" }
  },
  "rate_limits": {
    "yfinance_delay": 0.5,
    "screener_delay": 1.0,
    "chartink_delay": 2.0,
    "max_retries": 3,
    "timeout": 10
  },
  "technical_indicators": {
    "supertrend_length": 10,
    "supertrend_multiplier": 7.0
  },
  "data_sources": {
    "portfolio_file": "holdings-SOH330.xlsx",
    "portfolio_start_row": 22,
    "portfolio_columns": "B:M"
  }
}
```

You can edit this file directly or use the Settings tab in the UI.

---

## 🚦 Rate Limiting

| Service | Default delay | Calls/min | Notes |
|---------|--------------|-----------|-------|
| YFinance | 0.5s | 30 | Free tier |
| Screener.in | 1.0s | 20 | Web scraping |
| Chartink | 2.0s | 10 | Complex scans |
| Gemini AI | 2.0s | 15 | API quota |

All calls use exponential backoff on failure. Tune delays in Settings if you hit blocks.

---

## 🛠️ Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Application failed to start" | Check terminal for import errors; ensure all pip packages installed |
| "Portfolio file not found" | Set correct path in Settings → Portfolio File Configuration |
| Prices show as unavailable | Check internet; verify symbol format (e.g., `INFY` not `INFY.NS`) |
| Chartink scan returns empty | Loosen criteria; check Chartink is accessible from your network |
| AI classification fails | Verify Gemini API key; industry classification is optional and skipped gracefully |
| Rate limit / 429 errors | Increase delays in Settings → Rate Limiting |

---

## 📝 Notes

- Data is fetched live on demand; nothing is persisted between sessions except `dashboard_config.json`.
- The Gemini API key is session-only (not saved to config for security).
- Screener.in scraping respects a 1s delay; heavy portfolio loads may take 1–2 minutes.
- This app is for personal/educational use. Respect each data provider's terms of service.
