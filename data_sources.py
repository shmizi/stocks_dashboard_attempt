# data_sources.py
import pandas as pd
import numpy as np
import yfinance as yf

import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
import streamlit as st
from typing import Dict, List, Tuple, Optional, Any

from config import Config
from rate_limiter import rate_limited_call
from error_handler import ErrorBoundary, DataValidator, ValidationError

logger = logging.getLogger(__name__)

# Cache TTLs (seconds). Prices go stale quickly; fundamentals barely move.
PRICE_TTL        = 15 * 60
FUNDAMENTALS_TTL = 24 * 60 * 60
SCAN_TTL         = 15 * 60

BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36')


def calculate_supertrend_series(df: pd.DataFrame, length: int = 10,
                                multiplier: float = 7.0) -> Optional[pd.DataFrame]:
    """
    Full Supertrend series — pure numpy/pandas, matches TradingView Pine Script.
    Returns a DataFrame indexed like `df` with columns ['supertrend', 'direction'],
    or None if there isn't enough data.

    direction == 1  → uptrend   (supertrend line is BELOW price, acting as support)
    direction == -1 → downtrend (supertrend line is ABOVE price, acting as resistance)

    Pine Script reference:
        lower := lower > prevLower or close[1] < prevLower ? lower : prevLower
        upper := upper < prevUpper or close[1] > prevUpper ? upper : prevUpper
        direction := close > prevUpper ? 1 : close < prevLower ? -1 : nz(direction[1], 1)
        superTrend := direction == 1 ? lower : upper
    """
    try:
        high  = df['High'].astype(float)
        low   = df['Low'].astype(float)
        close = df['Close'].astype(float)

        if len(df) < length + 1:
            return None

        # --- ATR via Wilder's RMA (alpha = 1/length) — matches TradingView ---
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs()
        ], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1.0 / length, adjust=False).mean()

        # --- Basic bands ---
        hl2         = (high + low) / 2.0
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr

        # Working arrays (numpy for speed)
        n           = len(df)
        upper_band  = basic_upper.to_numpy(dtype=float).copy()   # .copy() required for in-loop writes
        lower_band  = basic_lower.to_numpy(dtype=float).copy()
        close_arr   = close.to_numpy(dtype=float)
        supertrend  = np.full(n, np.nan)
        direction   = np.ones(n, dtype=int)   # 1=uptrend, -1=downtrend

        for i in range(1, n):
            prev_lower = lower_band[i - 1]
            prev_upper = upper_band[i - 1]
            prev_close = close_arr[i - 1]

            # Lower band only ratchets UP (Pine: lower > prevLower or close[1] < prevLower)
            if basic_lower.iloc[i] > prev_lower or prev_close < prev_lower:
                lower_band[i] = basic_lower.iloc[i]
            else:
                lower_band[i] = prev_lower

            # Upper band only ratchets DOWN (Pine: upper < prevUpper or close[1] > prevUpper)
            if basic_upper.iloc[i] < prev_upper or prev_close > prev_upper:
                upper_band[i] = basic_upper.iloc[i]
            else:
                upper_band[i] = prev_upper

            # Direction: compare CURRENT close against PREVIOUS bar's bands
            # (Pine: close > prevUpper → 1, close < prevLower → -1, else keep)
            cur_close = close_arr[i]
            if cur_close > prev_upper:
                direction[i] = 1
            elif cur_close < prev_lower:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]   # hold previous direction

            # Supertrend line follows the active band
            supertrend[i] = lower_band[i] if direction[i] == 1 else upper_band[i]

        return pd.DataFrame({'supertrend': supertrend, 'direction': direction}, index=df.index)

    except Exception:
        return None


def calculate_supertrend(df: pd.DataFrame, length: int = 10, multiplier: float = 7.0):
    """Latest (supertrend_value, direction), or (None, None) on failure."""
    series = calculate_supertrend_series(df, length, multiplier)
    if series is None or series.empty:
        return None, None
    last_st = series['supertrend'].iloc[-1]
    if pd.isna(last_st):
        return None, None
    return float(last_st), int(series['direction'].iloc[-1])


def calculate_rsi(close: pd.Series, length: int = 14) -> Optional[float]:
    """Wilder RSI of the last bar, or None if not enough data."""
    try:
        close = close.astype(float)
        if len(close) < length + 1:
            return None
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1.0 / length, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / length, adjust=False).mean()
        last_loss = float(loss.iloc[-1])
        if last_loss == 0:
            return 100.0
        rs = float(gain.iloc[-1]) / last_loss
        return round(100.0 - 100.0 / (1.0 + rs), 1)
    except Exception:
        return None


def calculate_indicators(history: pd.DataFrame) -> Dict[str, Optional[float]]:
    """
    Cheap extras from the same 1y OHLC frame: RSI(14), 50/200-day simple
    averages, 52-week high & distance from it, and 1-year return.
    Everything is None-safe so a short history never breaks the caller.
    """
    close = history['Close'].astype(float)
    out: Dict[str, Optional[float]] = {
        'rsi': calculate_rsi(close),
        'dma50': None, 'dma200': None,
        'high_52w': None, 'pct_from_52w_high': None,
        'return_1y': None,
    }
    try:
        last = float(close.iloc[-1])
        if len(close) >= 50:
            out['dma50'] = round(float(close.tail(50).mean()), 2)
        if len(close) >= 200:
            out['dma200'] = round(float(close.tail(200).mean()), 2)
        high_col = history['High'].astype(float) if 'High' in history else close
        hi = float(high_col.tail(252).max())
        out['high_52w'] = round(hi, 2)
        if hi > 0:
            out['pct_from_52w_high'] = round((last / hi - 1.0) * 100.0, 2)
        first = float(close.iloc[0])
        if first > 0 and len(close) >= 200:       # only call it "1y" with most of a year
            out['return_1y'] = round((last / first - 1.0) * 100.0, 2)
    except Exception:
        pass
    return out


def parse_rss_feed(url: str, timeout: int = 10) -> List[Dict]:
    """
    Fetch and parse an RSS/Atom feed using only requests + stdlib xml.
    Returns a list of dicts with keys: title, link, published, source.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; StockDashboard/1.0)'
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}

    entries = []

    # RSS 2.0
    for item in root.findall('.//item'):
        title = item.findtext('title') or ''
        link  = item.findtext('link') or ''
        pub   = item.findtext('pubDate') or ''
        source_el = item.find('source')
        source = source_el.text if source_el is not None else 'N/A'
        entries.append({'title': title, 'link': link, 'published': pub, 'source': source})

    # Atom
    if not entries:
        for entry in root.findall('atom:entry', ns):
            title = entry.findtext('atom:title', namespaces=ns) or ''
            link_el = entry.find("atom:link[@rel='alternate']", ns) or entry.find('atom:link', ns)
            link  = link_el.get('href', '') if link_el is not None else ''
            pub   = entry.findtext('atom:published', namespaces=ns) or ''
            entries.append({'title': title, 'link': link, 'published': pub, 'source': 'N/A'})

    return entries


@st.cache_data(ttl=PRICE_TTL, show_spinner=False)
def _download_price_history(symbol: str, period: str, timeout: int,
                            delay: float, max_retries: int) -> pd.DataFrame:
    """
    Rate-limited, cached yfinance download. Raises on failure so a bad result is
    never cached. One year of OHLC serves price, day change and Supertrend.
    """
    def fetch():
        df = yf.download(f"{symbol}.NS", period=period, progress=False, timeout=timeout)

        if df is None or df.empty or len(df) < 2:
            raise ValidationError("Insufficient price data")

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        # Extra safety: flatten any remaining tuple column names
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df

    return rate_limited_call(
        service="yfinance", func=fetch,
        min_delay=delay, calls_per_minute=30, max_retries=max_retries
    )


@st.cache_data(ttl=PRICE_TTL, show_spinner=False)
def _download_price_history_batch(symbols: Tuple[str, ...], period: str,
                                  timeout: int) -> Dict[str, pd.DataFrame]:
    """
    One yfinance request for the whole portfolio (cached 15 min per symbol set).
    Symbols Yahoo can't resolve are simply absent from the result; callers
    fall back to the single-symbol path for those.
    """
    if not symbols:
        return {}

    tickers = [f"{s}.NS" for s in symbols]
    raw = yf.download(tickers, period=period, group_by="ticker",
                      progress=False, threads=True, timeout=timeout)

    histories: Dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return histories

    for sym, ticker in zip(symbols, tickers):
        try:
            df = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(how='all')
        if len(df) >= 2 and 'Close' in df.columns:
            histories[sym] = df.copy()

    return histories


StockAnalysis = Tuple[Optional[float], Optional[float], Optional[float], Optional[str], Optional[str]]

ANALYSIS_KEYS = ('current_price', 'day_change', 'supertrend', 'status', 'tech_error',
                 'rsi', 'dma50', 'dma200', 'high_52w', 'pct_from_52w_high', 'return_1y')


def empty_analysis(error: Optional[str] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {k: None for k in ANALYSIS_KEYS}
    d['tech_error'] = error
    return d


class YFinanceProvider:
    """Yahoo Finance data provider with rate limiting"""

    def __init__(self, config: Config):
        self.config = config
        self.rate_limit_delay = config.get('rate_limits.yfinance_delay', 0.5)
        self.timeout = config.get('rate_limits.timeout', 10)
        self.max_retries = config.get('rate_limits.max_retries', 3)

    def analyse_history(self, history: pd.DataFrame) -> Dict[str, Any]:
        """Turn a 1y OHLC frame into price, day change, Supertrend status and indicators."""
        current_price = float(history['Close'].iloc[-1])
        day_change    = float(current_price - history['Close'].iloc[-2])

        st_length     = self.config.get('technical_indicators.supertrend_length', 10)
        st_multiplier = self.config.get('technical_indicators.supertrend_multiplier', 7.0)

        supertrend_value, direction = calculate_supertrend(
            history, length=st_length, multiplier=st_multiplier
        )

        # Use the direction flag as the definitive signal — NOT a raw price-vs-value
        # comparison. direction==1 means ST is acting as support below price (uptrend);
        # direction==-1 means it is resistance above price (downtrend).
        if supertrend_value is not None and direction is not None:
            status = "Above Supertrend" if direction == 1 else "Below Supertrend"
        else:
            status = None

        result = empty_analysis()
        result.update({
            'current_price': current_price,
            'day_change': day_change,
            'supertrend': supertrend_value,
            'status': status,
        })
        result.update(calculate_indicators(history))
        return result

    def get_history(self, symbol: str, batch_symbols: Optional[Tuple[str, ...]] = None
                    ) -> Optional[pd.DataFrame]:
        """
        1y OHLC for one symbol, for charting. Served from the batch cache when
        the symbol set of the last refresh is passed, otherwise a single cached
        download. Returns None if no data.
        """
        try:
            symbol_clean = DataValidator.validate_stock_symbol(symbol)
        except ValidationError:
            return None
        if batch_symbols and symbol_clean in batch_symbols:
            try:
                hist = _download_price_history_batch(tuple(batch_symbols), "1y",
                                                     max(self.timeout, 30)).get(symbol_clean)
                if hist is not None:
                    return hist
            except Exception:
                pass
        try:
            return _download_price_history(symbol_clean, "1y", self.timeout,
                                           self.rate_limit_delay, self.max_retries)
        except Exception:
            return None

    @ErrorBoundary.handle_errors(
        fallback_value=(None, None, None, None, "YFinance data unavailable"),
        error_message="Failed to fetch stock data from Yahoo Finance"
    )
    def get_stock_analysis(self, symbol: str) -> StockAnalysis:
        """(price, day_change, supertrend, status, error) for one symbol (cached 15 min)"""
        symbol_clean = DataValidator.validate_stock_symbol(symbol)
        history = _download_price_history(
            symbol_clean, "1y", self.timeout, self.rate_limit_delay, self.max_retries
        )
        a = self.analyse_history(history)
        return a['current_price'], a['day_change'], a['supertrend'], a['status'], a['tech_error']

    def get_batch_analysis(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Full analysis dicts for many symbols from one Yahoo request. Symbols
        missing from the batch response are retried individually (rate-limited),
        so the result always has an entry for every valid input symbol.
        """
        clean = []
        for s in symbols:
            try:
                clean.append(DataValidator.validate_stock_symbol(s))
            except ValidationError:
                pass
        unique = tuple(dict.fromkeys(clean))   # dedupe, keep order → stable cache key

        results: Dict[str, Dict[str, Any]] = {}
        try:
            histories = _download_price_history_batch(unique, "1y", max(self.timeout, 30))
        except Exception as e:
            logger.warning(f"Batch price download failed, falling back to per-symbol: {e}")
            histories = {}

        for sym in unique:
            history = histories.get(sym)
            if history is None:
                history = self.get_history(sym)
            if history is None:
                results[sym] = empty_analysis("No price data from Yahoo")
                continue
            try:
                results[sym] = self.analyse_history(history)
            except Exception as e:
                logger.warning(f"{sym}: analysis failed ({e})")
                results[sym] = empty_analysis(f"Analysis failed: {e}")

        return results


def _extract_industry(soup: BeautifulSoup) -> str:
    """
    Screener.in classifies companies as a breadcrumb of /market/ links:
        Sector > Industry > Basic industry > Sub-industry
        e.g. /market/IN02/ > /market/IN02/IN0201/ > /market/IN02/IN0201/IN020102/ ...
    The third level ("Auto Components") is the most useful label; fall back to
    the deepest one available. The legacy /industry/ link is checked last.
    """
    by_depth: Dict[int, str] = {}
    for a in soup.select("a[href^='/market/']"):
        depth = len([p for p in a['href'].split('/') if p]) - 1   # minus "market"
        text = a.get_text(strip=True)
        if depth >= 1 and text and depth not in by_depth:
            by_depth[depth] = text
    if by_depth:
        return by_depth.get(3) or by_depth[max(by_depth)]

    legacy = soup.select_one("a[href*='/industry/']")
    return legacy.get_text(strip=True) if legacy else "N/A"


@st.cache_data(ttl=FUNDAMENTALS_TTL, show_spinner=False)
def _scrape_company_metrics(symbol: str, timeout: int, delay: float,
                            max_retries: int) -> Tuple[str, str, str, str, str]:
    """Scrape name/ROE/ROCE/industry/about from Screener.in (cached 24h)."""

    def fetch_metrics():
        base_url = f"https://www.screener.in/company/{symbol}/"
        urls_to_check = [base_url + "consolidated/", base_url]

        company_name, roe, roce, industry, about = symbol, "N/A", "N/A", "N/A", "N/A"
        last_error: Optional[Exception] = None

        with requests.Session() as session:
            session.headers.update({'User-Agent': BROWSER_UA})

            for url in urls_to_check:
                try:
                    response = session.get(url, timeout=timeout)
                    response.raise_for_status()
                except requests.RequestException as e:
                    last_error = e
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')

                if company_name == symbol:
                    name_element = soup.select_one("h1.show-from-tablet-landscape")
                    if name_element:
                        company_name = name_element.get_text(strip=True)

                if about == "N/A":
                    about_section = soup.select_one("div.about p")
                    if about_section:
                        about = about_section.get_text(strip=True).replace('...read more', '').strip()

                if roe == "N/A" or roce == "N/A":
                    for li in soup.select("#top-ratios li"):
                        name_span   = li.select_one(".name")
                        number_span = li.select_one(".number")
                        if name_span and number_span:
                            ratio_name  = name_span.get_text(strip=True)
                            ratio_value = number_span.get_text(strip=True)
                            if "ROE" in ratio_name and roe == "N/A":
                                roe = ratio_value
                            elif "ROCE" in ratio_name and roce == "N/A":
                                roce = ratio_value

                if industry == "N/A":
                    industry = _extract_industry(soup)

                if all(val != "N/A" for val in [roe, roce, industry, about]):
                    break

        # Both URLs failed at the network level: raise so nothing is cached and
        # the retry handler gets a chance.
        if company_name == symbol and about == "N/A" and last_error is not None:
            raise last_error

        return company_name, roe, roce, industry, about

    return rate_limited_call(
        service="screener", func=fetch_metrics,
        min_delay=delay, calls_per_minute=20, max_retries=max_retries
    )


@st.cache_data(ttl=PRICE_TTL, show_spinner=False)
def _scrape_fallback_price(symbol: str, timeout: int, delay: float, max_retries: int) -> float:
    """Scrape the current price from Screener.in (cached 15 min)."""

    def fetch_price():
        url = f"https://www.screener.in/company/{symbol}/"
        response = requests.get(url, headers={'User-Agent': BROWSER_UA}, timeout=timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        price_element = soup.select_one(".company-info div.flex > h2")

        if price_element:
            price_match = re.search(r'[\d,.]+', price_element.get_text(strip=True))
            if price_match:
                return float(price_match.group().replace(",", ""))

        raise ValidationError("Price element not found")

    return rate_limited_call(
        service="screener_price", func=fetch_price,
        min_delay=delay, calls_per_minute=20, max_retries=max_retries
    )


class ScreenerProvider:
    """Screener.in data provider with rate limiting"""

    def __init__(self, config: Config):
        self.config = config
        self.rate_limit_delay = config.get('rate_limits.screener_delay', 1.0)
        self.timeout = config.get('rate_limits.timeout', 10)
        self.max_retries = config.get('rate_limits.max_retries', 3)

    @ErrorBoundary.handle_errors(
        fallback_value=("N/A", "N/A", "N/A", "N/A", "N/A"),
        error_message="Failed to fetch data from Screener.in"
    )
    def get_company_metrics(self, symbol: str) -> Tuple[str, str, str, str, str]:
        """Company name, ROE, ROCE, industry, about (cached 24h)"""
        symbol_clean = DataValidator.validate_stock_symbol(symbol)
        return _scrape_company_metrics(
            symbol_clean, self.timeout, self.rate_limit_delay, self.max_retries
        )

    @ErrorBoundary.handle_errors(
        fallback_value=None,
        error_message="Failed to get fallback price from Screener.in"
    )
    def get_fallback_price(self, symbol: str) -> Optional[float]:
        """Current price as fallback when YFinance fails (cached 15 min)"""
        symbol_clean = DataValidator.validate_stock_symbol(symbol)
        return _scrape_fallback_price(
            symbol_clean, self.timeout, self.rate_limit_delay, self.max_retries
        )


@st.cache_data(ttl=SCAN_TTL, show_spinner=False)
def _run_chartink_scan(scan_clause: str, timeout: int, delay: float,
                       max_retries: int) -> pd.DataFrame:
    """Execute a Chartink scan (cached 15 min per clause)."""

    def execute_scan():
        with requests.Session() as session:
            session.headers.update({'User-Agent': BROWSER_UA})

            response = session.get("https://chartink.com/screener/dashboard", timeout=timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            csrf_token = soup.find('meta', {'name': 'csrf-token'})
            if not csrf_token:
                raise ValidationError("Could not find CSRF token")

            csrf_token = csrf_token['content']
            session.headers.update({'X-CSRF-TOKEN': csrf_token})

            payload = {'scan_clause': scan_clause, '_token': csrf_token}
            post_response = session.post("https://chartink.com/screener/process",
                                         data=payload, timeout=timeout)
            post_response.raise_for_status()

            scan_results = post_response.json().get('data', [])
            return pd.DataFrame(scan_results) if scan_results else pd.DataFrame()

    return rate_limited_call(
        service="chartink", func=execute_scan,
        min_delay=delay, calls_per_minute=10, max_retries=max_retries
    )


class ChartinkProvider:
    """Chartink data provider with rate limiting"""

    def __init__(self, config: Config):
        self.config = config
        self.rate_limit_delay = config.get('rate_limits.chartink_delay', 2.0)
        self.timeout = config.get('rate_limits.timeout', 10)
        self.max_retries = config.get('rate_limits.max_retries', 3)

    @ErrorBoundary.handle_errors(
        fallback_value=pd.DataFrame(),
        error_message="Failed to fetch data from Chartink",
        show_details=True
    )
    def run_scan(self, scan_clause: str) -> pd.DataFrame:
        """Run Chartink scan (cached 15 min per clause)"""
        return _run_chartink_scan(
            scan_clause, self.timeout, self.rate_limit_delay, self.max_retries
        )


# VADER is a social-media lexicon: "Sensex crashes 800 points as banks slump"
# scores 0.0 out of the box. These finance terms are overlaid on top of it.
# Scale matches VADER (-4 … +4).
FINANCE_LEXICON: Dict[str, float] = {
    # --- negative ---
    'crash': -3.0, 'crashes': -3.0, 'crashed': -3.0,
    'plunge': -3.0, 'plunges': -3.0, 'plunged': -3.0,
    'rout': -3.0, 'recession': -3.0, 'bankruptcy': -3.5, 'fraud': -3.5, 'scam': -3.5,
    'slump': -2.5, 'slumps': -2.5, 'slumped': -2.5,
    'tumble': -2.5, 'tumbles': -2.5, 'tumbled': -2.5,
    'tank': -2.5, 'tanks': -2.5, 'tanked': -2.5,
    'sell-off': -2.5, 'selloff': -2.5, 'downgrade': -2.5, 'downgrades': -2.5, 'downgraded': -2.5,
    'default': -2.5, 'defaults': -2.5, 'layoffs': -2.5, 'penalty': -2.0, 'probe': -2.0,
    'sink': -2.0, 'sinks': -2.0, 'sank': -2.0, 'loss': -2.0, 'losses': -2.0,
    'miss': -2.0, 'misses': -2.0, 'missed': -2.0, 'bearish': -2.0, 'weak': -1.8, 'weakness': -1.8,
    'slide': -1.8, 'slides': -1.8, 'slid': -1.8, 'drag': -1.5, 'drags': -1.5, 'dragged': -1.5,
    'fall': -1.5, 'falls': -1.5, 'fell': -1.5, 'drop': -1.5, 'drops': -1.5, 'dropped': -1.5,
    'decline': -1.5, 'declines': -1.5, 'declined': -1.5, 'bear': -1.5,
    'outflow': -1.5, 'outflows': -1.5, 'correction': -1.5, 'cut': -1.2, 'cuts': -1.2,
    'npa': -2.5, 'npas': -2.5, 'writedown': -2.5, 'write-off': -2.5, 'impairment': -2.5,
    'dip': -1.0, 'dips': -1.0, 'dipped': -1.0, 'volatile': -1.0, 'volatility': -1.0,
    'inflation': -1.0, 'debt': -1.0, 'pressure': -1.0, 'lower': -1.0, 'low': -1.0, 'red': -1.0,
    # --- positive ---
    'surge': 3.0, 'surges': 3.0, 'surged': 3.0,
    'soar': 3.0, 'soars': 3.0, 'soared': 3.0,
    'rally': 2.5, 'rallies': 2.5, 'rallied': 2.5,
    'upgrade': 2.5, 'upgrades': 2.5, 'upgraded': 2.5, 'bullish': 2.5,
    'outperform': 2.5, 'outperforms': 2.5, 'outperformed': 2.5,
    'jump': 2.0, 'jumps': 2.0, 'jumped': 2.0, 'gain': 2.0, 'gains': 2.0, 'gained': 2.0,
    'beat': 2.0, 'beats': 2.0, 'rebound': 2.0, 'rebounds': 2.0, 'rebounded': 2.0,
    'breakout': 2.0, 'buyback': 2.0,
    'rise': 1.5, 'rises': 1.5, 'rose': 1.5, 'climb': 1.5, 'climbs': 1.5, 'climbed': 1.5,
    'profit': 1.5, 'profits': 1.5, 'growth': 1.5, 'strong': 1.5, 'dividend': 1.5,
    'inflow': 1.5, 'inflows': 1.5, 'recovery': 1.5, 'recovers': 1.5, 'boost': 1.5, 'boosts': 1.5,
    'bull': 1.5, 'higher': 1.0, 'high': 1.0, 'green': 1.0, 'upbeat': 1.5,
    # --- -ing forms (headlines love them: "Why is the market falling?") ---
    'falling': -1.8, 'dropping': -1.8, 'declining': -1.8, 'sliding': -1.8, 'sinking': -2.0,
    'slumping': -2.5, 'tumbling': -2.5, 'plunging': -3.0, 'crashing': -3.0, 'tanking': -2.5,
    'dumping': -2.0, 'selling': -1.0, 'weakening': -1.5, 'losing': -1.5, 'cutting': -1.0,
    'rising': 1.5, 'climbing': 1.5, 'gaining': 2.0, 'jumping': 2.0, 'rallying': 2.5,
    'surging': 3.0, 'soaring': 3.0, 'rebounding': 2.0, 'recovering': 1.5, 'buying': 1.0,
    'outperforming': 2.5, 'beating': 2.0, 'strengthening': 1.5,
    # --- finance nouns VADER reads emotionally ("share" = sharing, "trust", "gross") ---
    'share': 0.0, 'shares': 0.0, 'interest': 0.0, 'credit': 0.0, 'trust': 0.0,
    'security': 0.0, 'securities': 0.0, 'treasury': 0.0, 'value': 0.0, 'worth': 0.0,
    'gross': 0.0, 'asset': 0.0, 'assets': 0.0,
    'nifty': 0.0,          # the index, not "neat"
    # --- direction words VADER ignores ---
    'down': -1.2, 'up': 1.2, 'lower': -1.0, 'below': -0.5, 'above': 0.5,
    'red': -1.0, 'green': 1.0, 'flat': 0.0, 'record': 0.8, 'all-time': 0.5,
}


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_rss_entries(url: str, timeout: int) -> List[Dict]:
    """Fetch RSS entries (cached 30 min per URL, politely rate-limited)."""
    return rate_limited_call(
        service="google_news", func=lambda: parse_rss_feed(url, timeout=timeout),
        min_delay=0.5, calls_per_minute=40, max_retries=2
    )


def _google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"


def _parse_pub_date(text: str) -> Optional[datetime]:
    """RSS pubDate (RFC 2822) → aware datetime, or None."""
    try:
        return parsedate_to_datetime(text)
    except Exception:
        return None


# Suffixes that make a company name a worse search term than the bare name
_NAME_NOISE = re.compile(r'\b(ltd|limited|pvt|private|inc|corp|corporation|co)\b\.?', re.I)


def news_search_term(company_name: str, symbol: str) -> str:
    """'Banco Products (India) Ltd' → 'Banco Products'; falls back to the symbol."""
    name = company_name or ''
    name = re.sub(r'\(.*?\)', ' ', name)          # drop parentheticals
    name = _NAME_NOISE.sub(' ', name)
    name = re.sub(r'[^\w&\s-]', ' ', name)
    name = ' '.join(name.split())
    return name if len(name) >= 3 else symbol


class NewsProvider:
    """News data provider with sentiment analysis"""

    # Google News appends " - Publisher" to every headline; strip it before scoring
    _SOURCE_SUFFIX = re.compile(r'\s+-\s+[^-]+$')

    def __init__(self, config: Config):
        self.config = config
        self.max_articles = config.get('news.max_articles', 20)
        self.timeout = config.get('rate_limits.timeout', 10)
        self._analyzer = None          # created lazily — see _get_analyzer
        self._analyzer_failed = False

    def _get_analyzer(self):
        """
        Build the VADER analyzer on first use, downloading the lexicon if needed.
        Never raises: if VADER is unavailable, sentiment degrades to Neutral and
        the rest of the app keeps working.
        """
        if self._analyzer is not None or self._analyzer_failed:
            return self._analyzer
        try:
            import nltk
            from nltk.sentiment.vader import SentimentIntensityAnalyzer
            try:
                nltk.data.find('sentiment/vader_lexicon.zip')
            except LookupError:
                nltk.download('vader_lexicon', quiet=True)
            analyzer = SentimentIntensityAnalyzer()
            analyzer.lexicon.update(FINANCE_LEXICON)
            self._analyzer = analyzer
        except Exception as e:
            logger.warning(f"Sentiment analyzer unavailable, defaulting to Neutral: {e}")
            self._analyzer_failed = True
            st.warning("Sentiment analysis unavailable (VADER lexicon could not be loaded). "
                       "Headlines will be shown as Neutral.")
        return self._analyzer

    @ErrorBoundary.handle_errors(
        fallback_value=[],
        error_message="Failed to fetch news data"
    )
    def get_news_from_rss(self, portfolio_stocks: List[Dict],
                          include_holdings: bool = True,
                          per_holding: int = 3) -> List[Dict]:
        """
        Market-wide headlines plus (optionally) a targeted search per holding.
        Per-holding articles are tagged with that holding's symbol, so relevance
        is exact rather than substring luck. Newest first, de-duplicated by link.
        """
        market_query = '"Indian stock market" OR "NSE" OR "BSE" OR "Sensex"'
        jobs: List[Tuple[str, Optional[str], int]] = [(market_query, None, self.max_articles)]

        if include_holdings:
            for s in portfolio_stocks:
                sym = s['symbol'].split('-')[0]
                term = news_search_term(s.get('company_name', sym), sym)
                # "stock"/"share" keeps the search on the listed company rather than its products
                jobs.append((f'"{term}" (stock OR share OR shares OR NSE)', sym, per_holding))

        stock_names   = [s.get('company_name', s['symbol']) for s in portfolio_stocks]
        stock_symbols = [s['symbol'].split('-')[0] for s in portfolio_stocks]

        articles: Dict[str, Dict] = {}     # link → article (dedupe across searches)
        failures = 0
        for query, tagged_symbol, limit in jobs:
            try:
                entries = _fetch_rss_entries(_google_news_url(query), self.timeout)
            except Exception as e:
                failures += 1
                logger.warning(f"News search failed for {tagged_symbol or 'market'}: {e}")
                continue

            for entry in entries[:limit]:
                title    = entry.get('title', '')
                headline = self._SOURCE_SUFFIX.sub('', title).strip() or title
                link     = entry.get('link', '#')

                if link in articles:
                    if tagged_symbol and tagged_symbol not in articles[link]['affected']:
                        articles[link]['affected'].append(tagged_symbol)
                    continue

                sentiment, score = self._get_sentiment(headline)
                affected = [tagged_symbol] if tagged_symbol else []
                title_lower = headline.lower()
                for name, sym in zip(stock_names, stock_symbols):
                    if sym in affected:
                        continue
                    # Symbols shorter than 4 chars match inside ordinary words; require the name
                    if name.lower() in title_lower or (len(sym) >= 4 and sym.lower() in title_lower):
                        affected.append(sym)

                published_at = _parse_pub_date(entry.get('published', ''))
                articles[link] = {
                    "headline":     headline,
                    "link":         link,
                    "source":       entry.get('source', 'N/A'),
                    "published":    entry.get('published', 'N/A'),
                    "published_at": published_at.isoformat() if published_at else None,
                    "sentiment":    sentiment,
                    "score":        score,
                    "affected":     affected,
                }

        if not articles:
            raise ValidationError("No news articles found" + (f" ({failures} searches failed)" if failures else ""))

        return sorted(articles.values(), key=lambda a: a['published_at'] or '', reverse=True)

    MARKET_DRIVER_QUERIES = [
        'Sensex Nifty today',
        'stock market fall reason India',
        'FII selling India',
        'crude oil price India rupee',
        'Iran Israel US tensions markets',
        'RBI rate decision',
    ]

    @ErrorBoundary.handle_errors(fallback_value=[], error_message="Failed to fetch market news")
    def get_market_driver_news(self, per_query: int = 6) -> List[Dict]:
        """Headlines that usually explain index-level moves. Newest first, de-duped."""
        seen: Dict[str, Dict] = {}
        for q in self.MARKET_DRIVER_QUERIES:
            try:
                entries = _fetch_rss_entries(_google_news_url(q), self.timeout)
            except Exception as e:
                logger.warning(f"Market news search failed ({q}): {e}")
                continue
            for entry in entries[:per_query]:
                link = entry.get('link', '#')
                if link in seen:
                    continue
                title = entry.get('title', '')
                headline = self._SOURCE_SUFFIX.sub('', title).strip() or title
                sentiment, score = self._get_sentiment(headline)
                published_at = _parse_pub_date(entry.get('published', ''))
                seen[link] = {
                    'headline': headline, 'link': link, 'source': entry.get('source', 'N/A'),
                    'published': entry.get('published', 'N/A'),
                    'published_at': published_at.isoformat() if published_at else None,
                    'sentiment': sentiment, 'score': score, 'query': q,
                }
        return sorted(seen.values(), key=lambda a: a['published_at'] or '', reverse=True)

    @staticmethod
    def rule_based_tldr(articles: List[Dict], holdings: List[str]) -> str:
        """No-LLM summary: counts plus the negative items about holdings."""
        if not articles:
            return "No articles."
        n = len(articles)
        pos = sum(a.get('sentiment') == 'Positive' for a in articles)
        neg = sum(a.get('sentiment') == 'Negative' for a in articles)
        mood = "mostly positive" if pos > 2 * neg else "mostly negative" if neg > 2 * pos else "mixed"
        tagged = [a for a in articles if a.get('affected')]
        neg_tagged = sorted((a for a in tagged if a.get('sentiment') == 'Negative'),
                            key=lambda a: a.get('score', 0))
        lines = [f"**Market mood:** {mood} — {pos} positive / {neg} negative of {n} headlines.",
                 f"**About my holdings:** {len(tagged)} headlines mention "
                 f"{len({s for a in tagged for s in a['affected']})} of your stocks."]
        if neg_tagged:
            lines.append("**Worth a closer look:**")
            for a in neg_tagged[:3]:
                lines.append(f"- {', '.join(a['affected'])}: [{a['headline']}]({a['link']})")
        else:
            lines.append("No negative headlines about your holdings in this batch.")
        return "\n".join(lines)

    def _get_sentiment(self, text: str) -> Tuple[str, float]:
        """Classify text; returns (label, compound score)."""
        analyzer = self._get_analyzer()
        if analyzer is None:
            return "Neutral", 0.0
        try:
            score = analyzer.polarity_scores(text)['compound']
        except Exception:
            return "Neutral", 0.0
        if score >= 0.05:
            return "Positive", score
        if score <= -0.05:
            return "Negative", score
        return "Neutral", score


class AIProvider:
    """AI-based analysis provider (Google Gemini)"""

    MODEL = "gemini-2.5-flash"

    def __init__(self, config: Config):
        self.config = config
        self.model = config.get('ai.gemini_model', self.MODEL)
        # LLM calls are slower than scrapes; give them more headroom
        self.timeout = max(config.get('rate_limits.timeout', 10), 45)

    @ErrorBoundary.handle_errors(
        fallback_value={},
        error_message="AI industry classification failed"
    )
    def classify_industries_batch(self, stocks_data: Dict[str, str], api_key: str) -> Dict[str, str]:
        """Classify industries using AI with rate limiting"""

        if not api_key or not stocks_data:
            return {}

        def make_ai_request():
            prompt = self._create_industry_prompt(stocks_data)
            url    = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }

            # Key goes in a header, never the URL: requests embeds the URL in
            # exception messages, which would otherwise leak the key into the UI/logs.
            response = requests.post(
                url,
                headers={'Content-Type': 'application/json', 'x-goog-api-key': api_key},
                json=payload,
                timeout=self.timeout
            )
            if response.status_code in (400, 401, 403):
                raise ValidationError(f"Gemini rejected the request ({response.status_code}): "
                                      "check the API key and model name")
            response.raise_for_status()

            result = response.json()
            if 'candidates' not in result or not result['candidates']:
                raise ValidationError("Invalid AI response structure")

            content    = result['candidates'][0]['content']['parts'][0]['text']
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if not json_match:
                raise ValidationError("Could not extract JSON from AI response")

            parsed = json.loads(json_match.group(0))
            if not isinstance(parsed, dict):
                raise ValidationError("AI response was not a JSON object")
            return {str(k): str(v) for k, v in parsed.items()}

        return rate_limited_call(
            service="gemini_ai",
            func=make_ai_request,
            min_delay=2.0,
            calls_per_minute=15,
            max_retries=2
        )

    # ---- generic text generation -----------------------------------------

    def generate(self, prompt: str, api_key: str, max_tokens: int = 600) -> Optional[str]:
        """One Gemini call → plain text, or None on any failure (never raises)."""
        if not api_key or not prompt:
            return None

        def call():
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{self.model}:generateContent")
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
            }
            response = requests.post(
                url, headers={'Content-Type': 'application/json', 'x-goog-api-key': api_key},
                json=payload, timeout=self.timeout)
            if response.status_code in (400, 401, 403):
                raise ValidationError(f"Gemini rejected the request ({response.status_code})")
            response.raise_for_status()
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text'].strip()

        try:
            return rate_limited_call(service="gemini_ai", func=call,
                                     min_delay=2.0, calls_per_minute=15, max_retries=1)
        except Exception as e:
            logger.warning(f"Gemini generate failed: {e}")
            return None

    def summarize_market(self, snapshot: Dict[str, Any], headlines: List[Dict],
                         api_key: str) -> Optional[str]:
        """3–5 bullets: what moved the Indian market and why, grounded in the data given."""
        macro = snapshot.get('macro', {})
        gauges = "\n".join(
            f"- {k}: {v['last']:,.2f} (1d {v.get('1d') or 0:+.2f}%, 1w {v.get('1w') or 0:+.2f}%, 1m {v.get('1m') or 0:+.2f}%)"
            for k, v in macro.items())
        sectors = ", ".join(f"{s['sector']} {s.get('1d') or 0:+.1f}%" for s in snapshot.get('sectors', []))
        regime = snapshot.get('regime', {})
        heads = "\n".join(f"- {h['headline']} ({h.get('source', '')})" for h in headlines[:25])
        prompt = f"""You are a market analyst writing a short morning note for an Indian retail investor.
Use ONLY the data below. Do not invent numbers. Do not give buy/sell advice or predictions.

Market gauges (latest close):
{gauges}

Sector moves today: {sectors}

Rule-based risk read: {regime.get('label')} (score {regime.get('score')}/10). Reasons: {'; '.join(regime.get('reasons') or ['none'])}

Recent headlines:
{heads}

Write:
1. One sentence: where the Indian market is today (direction and size of move).
2. 3–5 bullets explaining WHY, linking specific headlines/geopolitics/macro to the gauges (e.g. crude, rupee, FII flows, global cues, US-Iran tensions if present in headlines).
3. One bullet: what to watch next (an event or level), phrased neutrally.
Plain text, markdown bullets, under 180 words."""
        return self.generate(prompt, api_key, max_tokens=500)

    def summarize_news(self, articles: List[Dict], holdings: List[str], api_key: str) -> Optional[str]:
        """TL;DR of a headline batch, with a separate line for the user's holdings."""
        heads = "\n".join(
            f"- [{a.get('sentiment', 'Neutral')}] {a['headline']}"
            + (f"  (about: {', '.join(a['affected'])})" if a.get('affected') else "")
            for a in articles[:60])
        prompt = f"""Summarise these Indian market headlines for a retail investor who holds: {', '.join(holdings)}.
Use ONLY the headlines. No advice, no predictions.

{heads}

Write:
- **Market mood:** one sentence.
- **Big themes:** 2–3 bullets.
- **About my holdings:** 1–3 bullets naming the specific stocks mentioned and what the news says. If nothing material, say so in one line.
- **Worth a closer look:** at most 2 negative items about holdings, one line each.
Markdown, under 160 words."""
        return self.generate(prompt, api_key, max_tokens=450)

    def _create_industry_prompt(self, stocks_data: Dict[str, str]) -> str:
        return f"""Analyze the following company descriptions. For each company, provide its primary industry.
Respond with ONLY a valid JSON object where the keys are the company symbols and the values are the identified industry.

Example response format:
{{"INFY": "Information Technology", "RELIANCE": "Oil & Gas"}}

Companies to analyze:
{json.dumps(stocks_data)}"""


class DataSourceManager:
    """Centralised data source management"""

    def __init__(self, config: Config):
        self.config   = config
        self.yfinance = YFinanceProvider(config)
        self.screener = ScreenerProvider(config)
        self.chartink = ChartinkProvider(config)
        self.news     = NewsProvider(config)
        self.ai       = AIProvider(config)
        from market_data import MarketProvider     # local import: market_data imports nothing from here
        self.market   = MarketProvider(config)

    # --- Thread-safe raw fetchers -------------------------------------------
    # These call the cached scrapers directly and make NO Streamlit calls, so
    # they can run inside a ThreadPoolExecutor. They raise on failure; the
    # caller (on the script thread) decides how to report it.

    def fetch_company_metrics_raw(self, symbol: str) -> Tuple[str, str, str, str, str]:
        s = self.screener
        return _scrape_company_metrics(
            DataValidator.validate_stock_symbol(symbol), s.timeout, s.rate_limit_delay, s.max_retries
        )

    def fetch_fallback_price_raw(self, symbol: str) -> float:
        s = self.screener
        return _scrape_fallback_price(
            DataValidator.validate_stock_symbol(symbol), s.timeout, s.rate_limit_delay, s.max_retries
        )

    def get_stock_data(self, symbol: str) -> Dict[str, Any]:
        """Get comprehensive stock data from multiple sources (single-stock path)"""
        current_price, day_change, supertrend, status, error = self.yfinance.get_stock_analysis(symbol)

        if current_price is None:
            current_price = self.screener.get_fallback_price(symbol)
            error = f"{error} (Price from Screener)" if error else "Price from Screener"

        company_name, roe, roce, industry, about = self.screener.get_company_metrics(symbol)

        return {
            'symbol':        symbol,
            'current_price': current_price,
            'day_change':    day_change,
            'supertrend':    supertrend,
            'status':        status,
            'company_name':  company_name,
            'roe':           roe,
            'roce':          roce,
            'industry':      industry,
            'about':         about,
            'tech_error':    error
        }