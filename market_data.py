# market_data.py — market-wide context: indices, macro gauges, sector pulse, regime read.
#
# Everything comes from one cached Yahoo batch. The "regime" is a rule-based
# read of the same signals an experienced investor eyeballs (VIX, trend vs
# moving averages, crude, rupee, overnight US). It explains; it does not predict.
import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import yfinance as yf

logger = logging.getLogger(__name__)

MARKET_TTL = 15 * 60

# label → (ticker, kind). kind drives formatting and which return rows are shown.
MACRO: Dict[str, Tuple[str, str]] = {
    "Nifty 50":     ("^NSEI",     "index"),
    "Sensex":       ("^BSESN",    "index"),
    "Nifty Midcap": ("^NSMIDCP",  "index"),
    "India VIX":    ("^INDIAVIX", "vix"),
    "USD/INR":      ("INR=X",     "fx"),
    "Brent crude":  ("BZ=F",      "commodity"),
    "Gold":         ("GC=F",      "commodity"),
    "S&P 500":      ("^GSPC",     "index"),
    "Nasdaq":       ("^IXIC",     "index"),
    "US VIX":       ("^VIX",      "vix"),
    "US 10Y yield": ("^TNX",      "yield"),
    "Dollar index": ("DX-Y.NYB",  "index"),
}

# Sector proxies. Real NSE sector indices where Yahoo serves history; the
# Nippon/Motilal sector ETFs (which track the same indices) elsewhere.
SECTORS: Dict[str, str] = {
    "Bank":            "^NSEBANK",
    "IT":              "^CNXIT",
    "Pharma":          "^CNXPHARMA",
    "Auto":            "AUTOBEES.NS",
    "FMCG":            "FMCGIETF.NS",
    "Metal":           "METALIETF.NS",
    "Realty":          "MOREALTY.NS",
    "PSU Bank":        "PSUBNKBEES.NS",
    "Consumption":     "CONSUMBEES.NS",
    "Infra":           "INFRABEES.NS",
    "Midcap":          "MIDCAPETF.NS",
    "Smallcap":        "SMALLCAP.NS",
}

RETURN_WINDOWS = {"1d": 1, "1w": 5, "1m": 21, "3m": 63}


@st.cache_data(ttl=MARKET_TTL, show_spinner=False)
def _download_market(tickers: Tuple[str, ...], period: str, timeout: int) -> Dict[str, pd.DataFrame]:
    raw = yf.download(list(tickers), period=period, group_by="ticker",
                      progress=False, threads=True, timeout=timeout)
    out: Dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(subset=['Close']) if 'Close' in df else df.dropna(how='all')
        if len(df) >= 2:
            out[t] = df.copy()
    return out


def _returns(close: pd.Series) -> Dict[str, Optional[float]]:
    """Percent change over the last N bars for each window."""
    close = close.astype(float).dropna()
    last = float(close.iloc[-1])
    out: Dict[str, Optional[float]] = {}
    for name, bars in RETURN_WINDOWS.items():
        if len(close) > bars and close.iloc[-1 - bars] > 0:
            out[name] = round((last / float(close.iloc[-1 - bars]) - 1) * 100, 2)
        else:
            out[name] = None
    return out


class MarketProvider:
    """Indices, macro gauges and sector returns from one batch call."""

    def __init__(self, config):
        self.config = config
        self.timeout = max(int(config.get('rate_limits.timeout', 10)), 30)

    def get_snapshot(self) -> Dict[str, Any]:
        tickers = tuple(dict.fromkeys(
            [t for t, _ in MACRO.values()] + list(SECTORS.values())
        ))
        try:
            hist = _download_market(tickers, "1y", self.timeout)
        except Exception as e:
            logger.warning(f"Market download failed: {e}")
            hist = {}

        macro: Dict[str, Dict[str, Any]] = {}
        for label, (ticker, kind) in MACRO.items():
            df = hist.get(ticker)
            if df is None:
                continue
            close = df['Close'].astype(float)
            row: Dict[str, Any] = {
                'ticker': ticker, 'kind': kind,
                'last': float(close.iloc[-1]),
                'as_of': pd.Timestamp(df.index[-1]).strftime('%d %b'),
                **_returns(close),
            }
            if kind == 'vix':
                # For VIX the absolute level matters more than % moves
                row['change_1w_pts'] = round(float(close.iloc[-1] - close.iloc[-6]), 2) if len(close) > 5 else None
            if len(close) >= 200:
                row['dma50'] = float(close.tail(50).mean())
                row['dma200'] = float(close.tail(200).mean())
            hi = float(df['High'].astype(float).tail(252).max()) if 'High' in df else float(close.tail(252).max())
            row['pct_from_52w_high'] = round((row['last'] / hi - 1) * 100, 2) if hi else None
            macro[label] = row

        sectors: List[Dict[str, Any]] = []
        for label, ticker in SECTORS.items():
            df = hist.get(ticker)
            if df is None:
                continue
            r = _returns(df['Close'])
            sectors.append({'sector': label, 'ticker': ticker, **r})

        snapshot = {
            'macro': macro,
            'sectors': sectors,
            'fetched_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        }
        snapshot['regime'] = market_regime(macro)
        return snapshot


# ---------------------------------------------------------------------------
# Regime read
# ---------------------------------------------------------------------------

def market_regime(macro: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Score risk 0–10 from a handful of signals and label it. Every point comes
    with a plain-English reason so the label is never a black box.
    """
    score = 0
    reasons: List[str] = []
    calm: List[str] = []

    nifty = macro.get('Nifty 50', {})
    vix = macro.get('India VIX', {})
    crude = macro.get('Brent crude', {})
    inr = macro.get('USD/INR', {})
    spx = macro.get('S&P 500', {})
    usvix = macro.get('US VIX', {})

    # --- Trend ---
    last = nifty.get('last')
    if last and nifty.get('dma50') and nifty.get('dma200'):
        if last < nifty['dma200']:
            score += 2; reasons.append(f"Nifty is below its 200-day average ({nifty['dma200']:,.0f}) — longer-term trend is down")
        elif last < nifty['dma50']:
            score += 1; reasons.append(f"Nifty is below its 50-day average ({nifty['dma50']:,.0f}) — short-term trend has weakened")
        else:
            calm.append("Nifty is above both its 50- and 200-day averages")
    if nifty.get('1w') is not None and nifty['1w'] <= -2:
        score += 1; reasons.append(f"Nifty fell {abs(nifty['1w']):.1f}% over the past week")
    if nifty.get('1m') is not None and nifty['1m'] <= -4:
        score += 1; reasons.append(f"Nifty is down {abs(nifty['1m']):.1f}% over the past month")
    if nifty.get('pct_from_52w_high') is not None and nifty['pct_from_52w_high'] <= -10:
        score += 1; reasons.append(f"Nifty is {abs(nifty['pct_from_52w_high']):.0f}% off its 52-week high — correction territory")

    # --- Fear gauge ---
    v = vix.get('last')
    if v is not None:
        if v >= 20:
            score += 2; reasons.append(f"India VIX at {v:.1f} — options market is pricing big swings")
        elif v >= 15:
            score += 1; reasons.append(f"India VIX at {v:.1f} — elevated nervousness")
        else:
            calm.append(f"India VIX at {v:.1f} — options market is calm")
        if vix.get('change_1w_pts') and vix['change_1w_pts'] >= 3:
            score += 1; reasons.append(f"VIX jumped {vix['change_1w_pts']:+.1f} points in a week — fear is rising fast")

    # --- External shocks ---
    if crude.get('1w') is not None and crude['1w'] >= 5:
        score += 1; reasons.append(f"Brent crude up {crude['1w']:.1f}% in a week (₹{crude['last']:.0f}) — bad for India's import bill and inflation")
    if crude.get('last') and crude['last'] >= 90:
        score += 1; reasons.append(f"Crude above $90 ({crude['last']:.0f}) — a persistent headwind for the rupee and rate cuts")
    if inr.get('1m') is not None and inr['1m'] >= 1.5:
        score += 1; reasons.append(f"Rupee weakened {inr['1m']:.1f}% in a month (₹{inr['last']:.2f}/$) — FIIs tend to sell into a falling rupee")
    if spx.get('1d') is not None and spx['1d'] <= -1.5:
        score += 1; reasons.append(f"S&P 500 fell {abs(spx['1d']):.1f}% overnight — weak global cue for today")
    if usvix.get('last') and usvix['last'] >= 25:
        score += 1; reasons.append(f"US VIX at {usvix['last']:.0f} — global risk-off")

    score = min(score, 10)
    if score >= 6:
        label, tone = "High risk / risk-off", "critical"
    elif score >= 3:
        label, tone = "Elevated caution", "warning"
    else:
        label, tone = "Calm / risk-on", "good"

    return {'score': score, 'label': label, 'tone': tone, 'reasons': reasons, 'calm': calm}


# ---------------------------------------------------------------------------
# Portfolio-side industry aggregation
# ---------------------------------------------------------------------------

def portfolio_industry_table(stocks: List[Dict[str, Any]]) -> pd.DataFrame:
    """Per-industry: value, weight, P/L %, day change %, 1Y vs index, Supertrend split."""
    rows = []
    for s in stocks:
        price, qty, avg = s.get('current_price') or 0, s.get('qty') or 0, s.get('avg_price') or 0
        if price <= 0 or qty <= 0:
            continue
        rows.append({
            'Industry': s.get('industry') or 'N/A',
            'symbol': s['symbol'],
            'value': price * qty,
            'invested': avg * qty,
            'day_pnl': (s.get('day_change') or 0) * qty,
            'vs_index': s.get('vs_benchmark_1y'),
            'above_st': 1 if s.get('status') == "Above Supertrend" else 0,
            'below_st': 1 if s.get('status') == "Below Supertrend" else 0,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    total = df['value'].sum()
    g = df.groupby('Industry').agg(
        Holdings=('symbol', lambda s: ', '.join(s)),
        n=('symbol', 'count'),
        Value=('value', 'sum'),
        invested=('invested', 'sum'),
        day_pnl=('day_pnl', 'sum'),
        vs_index=('vs_index', 'mean'),
        above=('above_st', 'sum'),
        below=('below_st', 'sum'),
    ).reset_index()
    g['Weight %'] = g['Value'] / total * 100
    g['P/L %'] = (g['Value'] - g['invested']) / g['invested'].where(g['invested'] > 0) * 100
    prev_value = g['Value'] - g['day_pnl']
    g['Today %'] = g['day_pnl'] / prev_value.where(prev_value != 0) * 100
    g['1Y vs Index %'] = g['vs_index']
    g['Trend'] = g.apply(lambda r: f"{int(r['above'])}▲ {int(r['below'])}▼", axis=1)
    g = g.sort_values('Value', ascending=False)
    return g[['Industry', 'Holdings', 'Weight %', 'Value', 'P/L %', 'Today %', '1Y vs Index %', 'Trend']] \
        .set_index('Industry')
