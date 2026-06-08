# data_sources.py
import pandas as pd
import numpy as np
import yfinance as yf

import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote
import json
import xml.etree.ElementTree as ET
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import streamlit as st
from typing import Dict, List, Tuple, Optional, Any

from config import Config
from rate_limiter import rate_limited_call
from error_handler import ErrorBoundary, DataValidator, ValidationError


def calculate_supertrend(df: pd.DataFrame, length: int = 10, multiplier: float = 7.0):
    """
    Pure numpy/pandas Supertrend calculation — no pandas_ta required.
    Returns (supertrend_value, direction) or (None, None) on failure.
    """
    try:
        high = df['High'].astype(float)
        low  = df['Low'].astype(float)
        close = df['Close'].astype(float)

        # Average True Range (ATR)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs()
        ], axis=1).max(axis=1)

        atr = tr.ewm(span=length, adjust=False).mean()

        hl2 = (high + low) / 2
        upper_band = hl2 + multiplier * atr
        lower_band = hl2 - multiplier * atr

        supertrend = pd.Series(index=df.index, dtype=float)
        direction  = pd.Series(index=df.index, dtype=int)   # 1 = uptrend, -1 = downtrend

        for i in range(1, len(df)):
            # Lower band: only moves up
            if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < supertrend.iloc[i - 1]:
                lb = lower_band.iloc[i]
            else:
                lb = lower_band.iloc[i - 1]

            # Upper band: only moves down
            if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > supertrend.iloc[i - 1]:
                ub = upper_band.iloc[i]
            else:
                ub = upper_band.iloc[i - 1]

            lower_band.iloc[i] = lb
            upper_band.iloc[i] = ub

            prev_st = supertrend.iloc[i - 1] if i > 1 else ub

            if pd.isna(prev_st) or prev_st == ub:
                if close.iloc[i] <= ub:
                    supertrend.iloc[i] = ub
                    direction.iloc[i]  = -1
                else:
                    supertrend.iloc[i] = lb
                    direction.iloc[i]  = 1
            else:
                if close.iloc[i] >= lb:
                    supertrend.iloc[i] = lb
                    direction.iloc[i]  = 1
                else:
                    supertrend.iloc[i] = ub
                    direction.iloc[i]  = -1

        last_st = supertrend.iloc[-1]
        if pd.isna(last_st):
            return None, None
        return float(last_st), int(direction.iloc[-1])

    except Exception:
        return None, None


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


class YFinanceProvider:
    """Yahoo Finance data provider with rate limiting"""

    def __init__(self, config: Config):
        self.config = config
        self.rate_limit_delay = config.get('rate_limits.yfinance_delay', 0.5)
        self.timeout = config.get('rate_limits.timeout', 10)
        self.max_retries = config.get('rate_limits.max_retries', 3)

    @ErrorBoundary.handle_errors(
        fallback_value=(None, None, None, None, "YFinance data unavailable"),
        error_message="Failed to fetch stock data from Yahoo Finance"
    )
    def get_stock_analysis(self, symbol: str) -> Tuple[Optional[float], Optional[float],
    Optional[float], Optional[str], Optional[str]]:
        """Fetch stock analysis data with rate limiting"""

        def fetch_data():
            symbol_clean = DataValidator.validate_stock_symbol(symbol)

            # Fetch recent data for price and day change
            stock_data = yf.download(f"{symbol_clean}.NS", period="2d",
                                     progress=False, timeout=self.timeout)

            if stock_data.empty or len(stock_data) < 2:
                raise ValidationError("Insufficient price data")

            if isinstance(stock_data.columns, pd.MultiIndex):
                stock_data.columns = stock_data.columns.droplevel(1)

            current_price = float(stock_data.iloc[-1]['Close'])
            day_change    = float(current_price - stock_data.iloc[-2]['Close'])

            # Fetch longer-term data for Supertrend
            supertrend_data = yf.download(f"{symbol_clean}.NS", period="1y",
                                          progress=False, timeout=self.timeout)

            supertrend_value, status = None, None

            if not supertrend_data.empty:
                if isinstance(supertrend_data.columns, pd.MultiIndex):
                    supertrend_data.columns = supertrend_data.columns.droplevel(1)

                st_length     = self.config.get('technical_indicators.supertrend_length', 10)
                st_multiplier = self.config.get('technical_indicators.supertrend_multiplier', 7.0)

                supertrend_value, direction = calculate_supertrend(
                    supertrend_data, length=st_length, multiplier=st_multiplier
                )

                if supertrend_value is not None:
                    status = "Above Supertrend" if current_price > supertrend_value else "Below Supertrend"

            return current_price, day_change, supertrend_value, status, None

        return rate_limited_call(
            service="yfinance",
            func=fetch_data,
            min_delay=self.rate_limit_delay,
            calls_per_minute=30,
            max_retries=self.max_retries
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
        """Scrape company metrics from Screener.in with rate limiting"""

        def fetch_metrics():
            symbol_clean = DataValidator.validate_stock_symbol(symbol)

            base_url = f"https://www.screener.in/company/{symbol_clean}/"
            urls_to_check = [base_url + "consolidated/", base_url]

            company_name, roe, roce, industry, about = symbol_clean, "N/A", "N/A", "N/A", "N/A"

            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }

            with requests.Session() as session:
                session.headers.update(headers)

                for url in urls_to_check:
                    try:
                        response = session.get(url, timeout=self.timeout)
                        response.raise_for_status()

                        soup = BeautifulSoup(response.text, 'html.parser')

                        if company_name == symbol_clean:
                            name_element = soup.select_one("h1.show-from-tablet-landscape")
                            if name_element:
                                company_name = name_element.get_text(strip=True)

                        if about == "N/A":
                            about_section = soup.select_one("div.about p")
                            if about_section:
                                about = about_section.get_text(strip=True).replace('...read more', '').strip()

                        if roe == "N/A" or roce == "N/A":
                            ratio_elements = soup.select("#top-ratios li")
                            for li in ratio_elements:
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
                            industry_tag = soup.select_one(".company-info .flex-row a[href*='/industry/']")
                            if industry_tag:
                                industry = industry_tag.get_text(strip=True)

                        if all(val != "N/A" for val in [roe, roce, industry, about]):
                            break

                    except requests.RequestException:
                        continue

            return company_name, roe, roce, industry, about

        return rate_limited_call(
            service="screener",
            func=fetch_metrics,
            min_delay=self.rate_limit_delay,
            calls_per_minute=20,
            max_retries=self.max_retries
        )

    @ErrorBoundary.handle_errors(
        fallback_value=None,
        error_message="Failed to get fallback price from Screener.in"
    )
    def get_fallback_price(self, symbol: str) -> Optional[float]:
        """Get current price as fallback when YFinance fails"""

        def fetch_price():
            symbol_clean = DataValidator.validate_stock_symbol(symbol)
            url = f"https://www.screener.in/company/{symbol_clean}/"

            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            price_element = soup.select_one(".company-info div.flex > h2")

            if price_element:
                price_text  = price_element.get_text(strip=True)
                price_match = re.search(r'[\d,.]+', price_text)
                if price_match:
                    return float(price_match.group().replace(",", ""))

            raise ValidationError("Price element not found")

        return rate_limited_call(
            service="screener_price",
            func=fetch_price,
            min_delay=self.rate_limit_delay,
            calls_per_minute=20,
            max_retries=self.max_retries
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
        """Run Chartink scan with rate limiting"""

        def execute_scan():
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }

            with requests.Session() as session:
                session.headers.update(headers)

                screener_url = "https://chartink.com/screener/dashboard"
                response = session.get(screener_url, timeout=self.timeout)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                csrf_token = soup.find('meta', {'name': 'csrf-token'})

                if not csrf_token:
                    raise ValidationError("Could not find CSRF token")

                csrf_token = csrf_token['content']
                session.headers.update({'X-CSRF-TOKEN': csrf_token})

                payload      = {'scan_clause': scan_clause, '_token': csrf_token}
                process_url  = "https://chartink.com/screener/process"
                post_response = session.post(process_url, data=payload, timeout=self.timeout)
                post_response.raise_for_status()

                scan_results = post_response.json().get('data', [])
                return pd.DataFrame(scan_results) if scan_results else pd.DataFrame()

        return rate_limited_call(
            service="chartink",
            func=execute_scan,
            min_delay=self.rate_limit_delay,
            calls_per_minute=10,
            max_retries=self.max_retries
        )


class NewsProvider:
    """News data provider with sentiment analysis"""

    def __init__(self, config: Config):
        self.config = config
        self.max_articles = config.get('news.max_articles', 20)
        self.analyzer = SentimentIntensityAnalyzer()

    @ErrorBoundary.handle_errors(
        fallback_value=[],
        error_message="Failed to fetch news data"
    )
    def get_news_from_rss(self, portfolio_stocks: List[Dict]) -> List[Dict]:
        """Fetch and analyse news with portfolio relevance — no feedparser needed."""

        query = '"Indian stock market" OR "NSE" OR "BSE" OR "Sensex"'
        url   = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"

        raw_entries = parse_rss_feed(url, timeout=10)
        if not raw_entries:
            raise ValidationError("No news articles found")

        stock_names   = [s.get('company_name', s['symbol']) for s in portfolio_stocks]
        stock_symbols = [s['symbol'].split('-')[0] for s in portfolio_stocks]

        articles = []
        for entry in raw_entries[:self.max_articles]:
            title     = entry.get('title', '')
            sentiment = self._get_sentiment(title)
            affected_stocks = []

            title_lower = title.lower()
            for i, name in enumerate(stock_names):
                sym = stock_symbols[i]
                if name.lower() in title_lower or sym.lower() in title_lower:
                    affected_stocks.append(sym)

            articles.append({
                "headline": title,
                "link":      entry.get('link', '#'),
                "source":    entry.get('source', 'N/A'),
                "published": entry.get('published', 'N/A'),
                "sentiment": sentiment,
                "affected":  list(set(affected_stocks))
            })

        return articles

    def _get_sentiment(self, text: str) -> str:
        """Analyse text sentiment"""
        try:
            score = self.analyzer.polarity_scores(text)['compound']
            if score >= 0.05:
                return "Positive"
            elif score <= -0.05:
                return "Negative"
            else:
                return "Neutral"
        except Exception:
            return "Neutral"


class AIProvider:
    """AI-based analysis provider"""

    def __init__(self, config: Config):
        self.config = config
        self.timeout = config.get('rate_limits.timeout', 45)

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
                f"gemini-1.5-flash-latest:generateContent?key={api_key}"
            )
            payload = {"contents": [{"parts": [{"text": prompt}]}]}

            response = requests.post(
                url,
                headers={'Content-Type': 'application/json'},
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()

            result = response.json()
            if 'candidates' not in result or not result['candidates']:
                raise ValidationError("Invalid AI response structure")

            content    = result['candidates'][0]['content']['parts'][0]['text']
            json_match = re.search(r'\{.*\}', content, re.DOTALL)

            if not json_match:
                raise ValidationError("Could not extract JSON from AI response")

            return json.loads(json_match.group(0))

        return rate_limited_call(
            service="gemini_ai",
            func=make_ai_request,
            min_delay=2.0,
            calls_per_minute=15,
            max_retries=2
        )

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

    def get_stock_data(self, symbol: str) -> Dict[str, Any]:
        """Get comprehensive stock data from multiple sources"""
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