# data_sources.py
import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote
import json
import feedparser
from nltk.sentiment.vader import SentimentIntensityAnalyzer
import streamlit as st
from typing import Dict, List, Tuple, Optional, Any

from config import Config
from rate_limiter import rate_limited_call
from error_handler import ErrorBoundary, DataValidator, ValidationError


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
            # Validate symbol
            symbol_clean = DataValidator.validate_stock_symbol(symbol)

            # Fetch recent data for price and day change
            stock_data = yf.download(f"{symbol_clean}.NS", period="2d",
                                     progress=False, timeout=self.timeout)

            if stock_data.empty or len(stock_data) < 2:
                raise ValidationError("Insufficient price data")

            # Clean multi-level columns
            if isinstance(stock_data.columns, pd.MultiIndex):
                stock_data.columns = stock_data.columns.droplevel(1)

            current_price = float(stock_data.iloc[-1]['Close'])
            day_change = float(current_price - stock_data.iloc[-2]['Close'])

            # Fetch longer-term data for Supertrend
            supertrend_data = yf.download(f"{symbol_clean}.NS", period="1y",
                                          progress=False, timeout=self.timeout)

            supertrend_value, status = None, None

            if not supertrend_data.empty:
                if isinstance(supertrend_data.columns, pd.MultiIndex):
                    supertrend_data.columns = supertrend_data.columns.droplevel(1)

                # Calculate Supertrend
                st_length = self.config.get('technical_indicators.supertrend_length', 10)
                st_multiplier = self.config.get('technical_indicators.supertrend_multiplier', 7.0)

                supertrend_data.ta.supertrend(length=st_length, multiplier=st_multiplier, append=True)
                st_col_name = f'SUPERT_{st_length}_{st_multiplier}'

                if st_col_name in supertrend_data.columns:
                    latest_st = supertrend_data.iloc[-1][st_col_name]
                    if not pd.isna(latest_st):
                        supertrend_value = float(latest_st)
                        status = "Above Supertrend" if current_price > supertrend_value else "Below Supertrend"

            return current_price, day_change, supertrend_value, status, None

        return rate_limited_call(
            service="yfinance",
            func=fetch_data,
            min_delay=self.rate_limit_delay,
            calls_per_minute=30,  # Conservative limit
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

                        # Extract company name
                        if company_name == symbol_clean:
                            name_element = soup.select_one("h1.show-from-tablet-landscape")
                            if name_element:
                                company_name = name_element.get_text(strip=True)

                        # Extract about section
                        if about == "N/A":
                            about_section = soup.select_one("div.about p")
                            if about_section:
                                about = about_section.get_text(strip=True).replace('...read more', '').strip()

                        # Extract financial ratios
                        if roe == "N/A" or roce == "N/A":
                            ratio_elements = soup.select("#top-ratios li")
                            for li in ratio_elements:
                                name_span = li.select_one(".name")
                                number_span = li.select_one(".number")

                                if name_span and number_span:
                                    ratio_name = name_span.get_text(strip=True)
                                    ratio_value = number_span.get_text(strip=True)

                                    if "ROE" in ratio_name and roe == "N/A":
                                        roe = ratio_value
                                    elif "ROCE" in ratio_name and roce == "N/A":
                                        roce = ratio_value

                        # Extract industry
                        if industry == "N/A":
                            industry_tag = soup.select_one(".company-info .flex-row a[href*='/industry/']")
                            if industry_tag:
                                industry = industry_tag.get_text(strip=True)

                        # Break if we have all the data
                        if all(val != "N/A" for val in [roe, roce, industry, about]):
                            break

                    except requests.RequestException:
                        continue

            return company_name, roe, roce, industry, about

        return rate_limited_call(
            service="screener",
            func=fetch_metrics,
            min_delay=self.rate_limit_delay,
            calls_per_minute=20,  # More conservative for scraping
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
                price_text = price_element.get_text(strip=True)
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

                # Get CSRF token
                screener_url = "https://chartink.com/screener/dashboard"
                response = session.get(screener_url, timeout=self.timeout)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, 'html.parser')
                csrf_token = soup.find('meta', {'name': 'csrf-token'})

                if not csrf_token:
                    raise ValidationError("Could not find CSRF token")

                csrf_token = csrf_token['content']
                session.headers.update({'X-CSRF-TOKEN': csrf_token})

                # Execute scan
                payload = {'scan_clause': scan_clause, '_token': csrf_token}
                process_url = "https://chartink.com/screener/process"

                post_response = session.post(process_url, data=payload, timeout=self.timeout)
                post_response.raise_for_status()

                scan_results = post_response.json().get('data', [])
                return pd.DataFrame(scan_results) if scan_results else pd.DataFrame()

        return rate_limited_call(
            service="chartink",
            func=execute_scan,
            min_delay=self.rate_limit_delay,
            calls_per_minute=10,  # Very conservative for complex scans
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
        """Fetch and analyze news with portfolio relevance"""

        query = '"Indian stock market" OR "NSE" OR "BSE" OR "Sensex"'
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"

        feed = feedparser.parse(url)
        if not feed.entries:
            raise ValidationError("No news articles found")

        articles = []
        stock_names = [s.get('company_name', s['symbol']) for s in portfolio_stocks]
        stock_symbols = [s['symbol'].split('-')[0] for s in portfolio_stocks]

        for entry in feed.entries[:self.max_articles]:
            sentiment = self._get_sentiment(entry.title)
            affected_stocks = []

            # Check which portfolio stocks are mentioned
            for i, name in enumerate(stock_names):
                symbol = stock_symbols[i]
                title_lower = entry.title.lower()

                if (name.lower() in title_lower or
                        symbol.lower() in title_lower):
                    affected_stocks.append(stock_symbols[i])

            articles.append({
                "headline": entry.title,
                "link": entry.link,
                "source": getattr(entry, 'source', {}).get('title', 'N/A'),
                "published": getattr(entry, 'published', 'N/A'),
                "sentiment": sentiment,
                "affected": list(set(affected_stocks))
            })

        return articles

    def _get_sentiment(self, text: str) -> str:
        """Analyze text sentiment"""
        try:
            score = self.analyzer.polarity_scores(text)['compound']
            if score >= 0.05:
                return "Positive"
            elif score <= -0.05:
                return "Negative"
            else:
                return "Neutral"
        except:
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

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={api_key}"
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

            content = result['candidates'][0]['content']['parts'][0]['text']
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
        """Create prompt for industry classification"""
        return f"""Analyze the following company descriptions. For each company, provide its primary industry.
Respond with ONLY a valid JSON object where the keys are the company symbols and the values are the identified industry.

Example response format:
{{"INFY": "Information Technology", "RELIANCE": "Oil & Gas"}}

Companies to analyze:
{json.dumps(stocks_data)}"""


class DataSourceManager:
    """Centralized data source management"""

    def __init__(self, config: Config):
        self.config = config
        self.yfinance = YFinanceProvider(config)
        self.screener = ScreenerProvider(config)
        self.chartink = ChartinkProvider(config)
        self.news = NewsProvider(config)
        self.ai = AIProvider(config)

    def get_stock_data(self, symbol: str) -> Dict[str, Any]:
        """Get comprehensive stock data from multiple sources"""
        # Try YFinance first
        current_price, day_change, supertrend, status, error = self.yfinance.get_stock_analysis(symbol)

        # If YFinance fails for price, try Screener fallback
        if current_price is None:
            current_price = self.screener.get_fallback_price(symbol)
            error = f"{error} (Price from Screener)" if error else "Price from Screener"

        # Get company fundamentals from Screener
        company_name, roe, roce, industry, about = self.screener.get_company_metrics(symbol)

        return {
            'symbol': symbol,
            'current_price': current_price,
            'day_change': day_change,
            'supertrend': supertrend,
            'status': status,
            'company_name': company_name,
            'roe': roe,
            'roce': roce,
            'industry': industry,
            'about': about,
            'tech_error': error
        }