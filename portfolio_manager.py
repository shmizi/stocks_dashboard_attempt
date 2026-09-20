# portfolio_manager.py
import json
import logging
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import pandas as pd
import streamlit as st

from config import Config
from data_sources import DataSourceManager, empty_analysis
from error_handler import ErrorBoundary, DataValidator, ProgressTracker, ValidationError

logger = logging.getLogger(__name__)

UPLOADED_PORTFOLIO_FILE = "uploaded_holdings.xlsx"   # git-ignored via *.xlsx


def _json_safe(value):
    """Make numpy scalars / NaN JSON-serialisable."""
    if hasattr(value, 'item'):          # numpy scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


class PortfolioStore:
    """
    Persists the last processed portfolio to disk so the app opens with data
    instead of an empty page. Plain JSON next to the config file.
    """
    FILE = ".portfolio_cache.json"

    @classmethod
    def save(cls, stocks_data: List[Dict[str, Any]], total_investment: float,
             signal_changes: Optional[List[Dict[str, Any]]] = None,
             meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "saved_at": saved_at,
            "total_investment": _json_safe(total_investment),
            "signal_changes": signal_changes or [],
            "meta": {k: _json_safe(v) for k, v in (meta or {}).items()},
            "stocks": [{k: _json_safe(v) for k, v in s.items()} for s in stocks_data],
        }
        try:
            with open(cls.FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=1, ensure_ascii=False)
            return saved_at
        except Exception as e:
            logger.warning(f"Could not persist portfolio: {e}")
            return None

    @classmethod
    def load(cls) -> Optional[Dict[str, Any]]:
        if not os.path.exists(cls.FILE):
            return None
        try:
            with open(cls.FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            if not isinstance(payload.get("stocks"), list):
                return None
            return payload
        except Exception as e:
            logger.warning(f"Could not load persisted portfolio: {e}")
            return None

    @classmethod
    def clear(cls):
        try:
            os.remove(cls.FILE)
        except FileNotFoundError:
            pass


class PortfolioHistory:
    """
    One row per refresh-day of portfolio totals, so value can be plotted over
    time. A second refresh on the same day replaces that day's row.
    """
    FILE = "portfolio_history.csv"
    COLUMNS = ['date', 'saved_at', 'invested', 'current_value', 'total_pnl', 'day_change', 'priced', 'total']

    @classmethod
    def load(cls) -> pd.DataFrame:
        if not os.path.exists(cls.FILE):
            return pd.DataFrame(columns=cls.COLUMNS)
        try:
            df = pd.read_csv(cls.FILE, parse_dates=['date'])
            return df.sort_values('date').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Could not read portfolio history: {e}")
            return pd.DataFrame(columns=cls.COLUMNS)

    @classmethod
    def append(cls, summary: Dict[str, Any]) -> None:
        if not summary.get('valid_prices'):
            return                      # nothing priced — don't log a zero
        now = datetime.now()
        row = {
            'date': now.strftime("%Y-%m-%d"),
            'saved_at': now.strftime("%Y-%m-%d %H:%M:%S"),
            'invested': round(summary['invested_priced'], 2),
            'current_value': round(summary['current_value'], 2),
            'total_pnl': round(summary['total_pnl'], 2),
            'day_change': round(summary['day_change'], 2),
            'priced': summary['valid_prices'],
            'total': summary['total_stocks'],
        }
        try:
            df = cls.load()
            df['date'] = pd.to_datetime(df['date']).dt.strftime("%Y-%m-%d")
            df = df[df['date'] != row['date']]
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            df.to_csv(cls.FILE, index=False)
        except Exception as e:
            logger.warning(f"Could not write portfolio history: {e}")


class PortfolioManager:
    """Manages portfolio data loading and processing"""

    def __init__(self, config: Config):
        self.config = config
        self.data_sources = DataSourceManager(config)
        self.portfolio_file = config.get('data_sources.portfolio_file')
        self.start_row = config.get('data_sources.portfolio_start_row', 22)
        self.columns = config.get('data_sources.portfolio_columns', 'B:M')
        self.workers = int(config.get('rate_limits.parallel_workers', 4))

    def save_uploaded_file(self, uploaded) -> str:
        """
        Persist a file from st.file_uploader and point the config at it, so the
        upload survives restarts. Returns the saved path.
        """
        with open(UPLOADED_PORTFOLIO_FILE, 'wb') as f:
            f.write(uploaded.getbuffer())
        self.config.set('data_sources.portfolio_file', UPLOADED_PORTFOLIO_FILE)
        self.portfolio_file = UPLOADED_PORTFOLIO_FILE
        return UPLOADED_PORTFOLIO_FILE

    @ErrorBoundary.handle_errors(
        fallback_value=pd.DataFrame(),
        error_message="Failed to load portfolio file",
        show_details=True
    )
    def load_portfolio_file(self) -> pd.DataFrame:
        """Load and validate portfolio file"""
        if not self.portfolio_file or not os.path.exists(self.portfolio_file):
            raise ValidationError(
                f"Portfolio file not found: {self.portfolio_file!r}. "
                "Upload one from the sidebar or set the path in Settings."
            )

        # Load the Excel file
        df = pd.read_excel(
            self.portfolio_file,
            header=self.start_row,
            usecols=self.columns
        )

        # Clean column names
        df.columns = df.columns.str.strip()

        # Validate the data structure
        DataValidator.validate_portfolio_data(df)

        # Convert numeric columns
        df['Quantity Available'] = pd.to_numeric(df['Quantity Available'], errors='coerce')
        df['Average Price'] = pd.to_numeric(df['Average Price'], errors='coerce')

        # Filter valid holdings
        df = df.dropna(subset=['Symbol', 'Quantity Available'])
        df = df[df['Quantity Available'] > 0]

        return df

    def process_portfolio_holdings(self, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Process all portfolio holdings with progress tracking"""
        holdings_df = self.load_portfolio_file()

        if holdings_df.empty:
            st.warning("No valid holdings found in portfolio file")
            return []

        # Calculate total investment
        total_investment = (holdings_df['Average Price'] * holdings_df['Quantity Available']).sum()
        st.session_state.total_investment = total_investment

        rows = [
            (str(r['Symbol']), self._clean_symbol(str(r['Symbol'])),
             r['Quantity Available'], r['Average Price'])
            for _, r in holdings_df.iterrows()
        ]

        # +1 for the batch price step
        tracker = ProgressTracker(len(rows) + 1, "Processing portfolio holdings")

        # ---- Stage 1: all prices in ONE Yahoo request -----------------------
        # The benchmark rides along so "vs index" needs no extra call.
        tracker.update("Fetching prices for all holdings")
        benchmark = self.config.get('benchmark.symbol', 'NIFTYBEES')
        batch_symbols = [r[1] for r in rows] + [benchmark]
        prices = self.data_sources.yfinance.get_batch_analysis(batch_symbols)
        st.session_state.price_batch_symbols = tuple(dict.fromkeys(batch_symbols))

        bench = prices.get(benchmark, {})
        benchmark_return = bench.get('return_1y')
        st.session_state.benchmark_info = {'symbol': benchmark, 'return_1y': benchmark_return}

        # ---- Stage 2: fundamentals / fallback prices in parallel ------------
        # Workers make no Streamlit calls; errors come back as data and are
        # reported here on the script thread.
        ctx = self._script_ctx()
        results: Dict[str, Dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            futures = {}
            for stock_symbol, api_symbol, qty, avg in rows:
                is_etf = self.config.is_etf(api_symbol)
                analysis = prices.get(api_symbol) or empty_analysis("No price data")
                need_fallback = analysis.get('current_price') is None
                fut = pool.submit(self._fetch_extras, api_symbol, is_etf, need_fallback, ctx)
                futures[fut] = (stock_symbol, api_symbol, qty, avg, is_etf, analysis)

            for fut in as_completed(futures):
                stock_symbol, api_symbol, qty, avg, is_etf, analysis = futures[fut]
                tracker.update(f"Analyzed {stock_symbol}")

                stock_info: Dict[str, Any] = {
                    'symbol': stock_symbol,
                    'api_symbol': api_symbol,
                    'qty': qty,
                    'avg_price': avg,
                }
                stock_info.update(analysis)
                if benchmark_return is not None and analysis.get('return_1y') is not None:
                    stock_info['vs_benchmark_1y'] = round(analysis['return_1y'] - benchmark_return, 2)
                else:
                    stock_info['vs_benchmark_1y'] = None

                if is_etf:
                    etf_info = self.config.get_etf_info(api_symbol)
                    stock_info.update({
                        'company_name': etf_info['name'],
                        'roe': 'N/A', 'roce': 'N/A',
                        'industry': etf_info['category'],
                        'about': etf_info['description'],
                    })
                else:
                    stock_info.update({
                        'company_name': api_symbol,
                        'roe': 'N/A', 'roce': 'N/A', 'industry': 'N/A', 'about': 'N/A',
                    })

                try:
                    extras = fut.result()
                except Exception as e:          # should not happen; _fetch_extras never raises
                    extras = {'errors': [f"Processing failed: {e}"]}

                for err in extras.get('errors', []):
                    tracker.add_error(err, stock_symbol)

                if 'metrics' in extras:
                    name, roe, roce, industry, about = extras['metrics']
                    stock_info.update({'company_name': name, 'roe': roe, 'roce': roce,
                                       'industry': industry, 'about': about})

                if extras.get('fallback_price') is not None:
                    stock_info['current_price'] = extras['fallback_price']
                    stock_info['tech_error'] = "Price from Screener (no Yahoo data)"

                results[stock_symbol] = stock_info

        # Preserve the order of the holdings file
        all_stocks_data = [results[r[0]] for r in rows if r[0] in results]

        # ---- Stage 3: AI industry classification for gaps -------------------
        stocks_needing_ai_industry = {
            s['symbol']: s['about'] for s in all_stocks_data
            if s.get('industry') == 'N/A' and s.get('about') not in ('N/A', '', None)
        }
        if stocks_needing_ai_industry and api_key:
            tracker.update("Running AI industry analysis")
            try:
                ai_industries = self.data_sources.ai.classify_industries_batch(
                    stocks_needing_ai_industry, api_key
                )
                for stock in all_stocks_data:
                    if stock['symbol'] in ai_industries:
                        stock['industry'] = ai_industries[stock['symbol']]
            except Exception as e:
                tracker.add_error(f"AI classification failed: {str(e)}")

        tracker.finish()

        # ---- Stage 4: compare with the previous snapshot, then persist -------
        previous = PortfolioStore.load()
        signal_changes = self._detect_signal_changes(previous, all_stocks_data)
        st.session_state.signal_changes = signal_changes

        saved_at = PortfolioStore.save(
            all_stocks_data, total_investment, signal_changes,
            meta={'benchmark_symbol': benchmark, 'benchmark_return_1y': benchmark_return,
                  'price_batch_symbols': list(st.session_state.price_batch_symbols)}
        )
        if saved_at:
            st.session_state.portfolio_saved_at = saved_at

        summary = self.calculate_portfolio_summary(all_stocks_data)
        PortfolioHistory.append(summary)

        return all_stocks_data

    @staticmethod
    def _detect_signal_changes(previous: Optional[Dict[str, Any]],
                               current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Supertrend flips since the last snapshot. Carries forward earlier
        unacknowledged flips so a change isn't lost after one refresh.
        """
        if not previous:
            return []
        prev_status = {s['symbol']: s.get('status') for s in previous.get('stocks', [])}
        prev_saved_at = previous.get('saved_at', 'previous refresh')
        changes: List[Dict[str, Any]] = []

        for s in current:
            before, after = prev_status.get(s['symbol']), s.get('status')
            if before and after and before != after:
                changes.append({
                    'symbol': s['symbol'],
                    'from': before,
                    'to': after,
                    'since': prev_saved_at,
                    'detected_at': datetime.now().strftime("%Y-%m-%d %H:%M"),
                })

        # Keep older flips (max 7 days) that are still consistent with the current status
        cutoff = datetime.now() - timedelta(days=7)
        current_status = {s['symbol']: s.get('status') for s in current}
        seen = {c['symbol'] for c in changes}
        for old in previous.get('signal_changes', []):
            try:
                detected = datetime.strptime(old['detected_at'], "%Y-%m-%d %H:%M")
            except Exception:
                continue
            if (old['symbol'] not in seen and detected >= cutoff
                    and current_status.get(old['symbol']) == old.get('to')):
                changes.append(old)

        return changes

    def process_symbols(self, symbols: List[str], description: str = "Analyzing") -> List[Dict[str, Any]]:
        """
        Same pipeline as the portfolio (batch prices → parallel fundamentals)
        for an arbitrary symbol list — used by the watchlist. No qty/avg.
        """
        symbols = [self._clean_symbol(s) for s in symbols if s]
        if not symbols:
            return []

        tracker = ProgressTracker(len(symbols) + 1, description)
        tracker.update("Fetching prices")
        benchmark = self.config.get('benchmark.symbol', 'NIFTYBEES')
        prices = self.data_sources.yfinance.get_batch_analysis(symbols + [benchmark])
        benchmark_return = (prices.get(benchmark) or {}).get('return_1y')
        ctx = self._script_ctx()
        results: Dict[str, Dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            futures = {}
            for sym in symbols:
                analysis = prices.get(sym) or empty_analysis("No price data")
                fut = pool.submit(self._fetch_extras, sym, self.config.is_etf(sym),
                                  analysis.get('current_price') is None, ctx)
                futures[fut] = (sym, analysis)

            for fut in as_completed(futures):
                sym, analysis = futures[fut]
                tracker.update(sym)
                info: Dict[str, Any] = {'symbol': sym, 'api_symbol': sym, 'qty': 0, 'avg_price': 0,
                                        'company_name': sym, 'roe': 'N/A', 'roce': 'N/A',
                                        'industry': 'N/A', 'about': 'N/A'}
                info.update(analysis)
                info['vs_benchmark_1y'] = (
                    round(analysis['return_1y'] - benchmark_return, 2)
                    if benchmark_return is not None and analysis.get('return_1y') is not None else None
                )
                if self.config.is_etf(sym):
                    etf = self.config.get_etf_info(sym)
                    info.update({'company_name': etf['name'], 'industry': etf['category'],
                                 'about': etf['description']})
                try:
                    extras = fut.result()
                except Exception as e:
                    extras = {'errors': [str(e)]}
                for err in extras.get('errors', []):
                    tracker.add_error(err, sym)
                if 'metrics' in extras:
                    name, roe, roce, industry, about = extras['metrics']
                    info.update({'company_name': name, 'roe': roe, 'roce': roce,
                                 'industry': industry, 'about': about})
                if extras.get('fallback_price') is not None:
                    info['current_price'] = extras['fallback_price']
                    info['tech_error'] = "Price from Screener (no Yahoo data)"
                results[sym] = info

        tracker.finish()
        return [results[s] for s in symbols if s in results]

    @staticmethod
    def _script_ctx():
        """Streamlit script-run context, attached to worker threads so cache_data works there."""
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            return get_script_run_ctx(suppress_warning=True)
        except Exception:
            return None

    def _fetch_extras(self, api_symbol: str, is_etf: bool, need_fallback: bool, ctx) -> Dict[str, Any]:
        """
        Worker-thread job: Screener.in fundamentals (non-ETF) and a fallback
        price when Yahoo had nothing. Never raises and never touches Streamlit
        UI — errors are returned in the 'errors' list.
        """
        if ctx is not None:
            try:
                from streamlit.runtime.scriptrunner import add_script_run_ctx
                add_script_run_ctx(threading.current_thread(), ctx)
            except Exception:
                pass

        out: Dict[str, Any] = {'errors': []}

        if not is_etf:
            try:
                out['metrics'] = self.data_sources.fetch_company_metrics_raw(api_symbol)
            except Exception as e:
                out['errors'].append(f"Fundamentals unavailable: {e}")

        if need_fallback:
            try:
                out['fallback_price'] = self.data_sources.fetch_fallback_price_raw(api_symbol)
            except Exception as e:
                out['errors'].append(f"No price from Yahoo or Screener: {e}")

        return out

    _SERIES_SUFFIX = re.compile(r'-[A-Z]{1,2}$')   # NSE series: -EQ, -BE, -BZ, -SM, -ST, -F, -E …

    def _clean_symbol(self, symbol: str) -> str:
        """Strip the NSE series suffix broker exports append (LIQUIDCASE-F → LIQUIDCASE)."""
        return self._SERIES_SUFFIX.sub('', symbol.strip().upper())

    def calculate_portfolio_summary(self, stocks_data: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate portfolio summary metrics"""
        empty = {'current_value': 0.0, 'total_pnl': 0.0, 'day_change': 0.0,
                 'invested_total': 0.0, 'invested_priced': 0.0,
                 'total_stocks': 0, 'valid_prices': 0}
        if not stocks_data:
            return empty

        df = pd.DataFrame(stocks_data)
        df['current_price'] = pd.to_numeric(df['current_price'], errors='coerce').fillna(0)
        df['day_change'] = pd.to_numeric(df['day_change'], errors='coerce').fillna(0)
        df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
        df['avg_price'] = pd.to_numeric(df['avg_price'], errors='coerce').fillna(0)

        # Calculate metrics
        df['invested'] = df['avg_price'] * df['qty']
        df['current_value'] = df['current_price'] * df['qty']
        df['pnl'] = (df['current_price'] - df['avg_price']) * df['qty']
        df['day_pnl'] = df['day_change'] * df['qty']

        # Totals only make sense over stocks that actually have a price; expose
        # the matching invested amount so P/L and deltas compare like with like.
        valid_df = df[df['current_price'] > 0]

        return {
            'current_value': float(valid_df['current_value'].sum()),
            'total_pnl': float(valid_df['pnl'].sum()),
            'day_change': float(valid_df['day_pnl'].sum()),
            'invested_total': float(df['invested'].sum()),
            'invested_priced': float(valid_df['invested'].sum()),
            'total_stocks': int(len(df)),
            'valid_prices': int(len(valid_df))
        }

    def get_portfolio_display_data(self, stocks_data: List[Dict[str, Any]]) -> pd.DataFrame:
        """Prepare portfolio data for display"""
        if not stocks_data:
            return pd.DataFrame()

        df = pd.DataFrame(stocks_data)
        df['current_price'] = pd.to_numeric(df['current_price'], errors='coerce').fillna(0)
        df['day_change'] = pd.to_numeric(df['day_change'], errors='coerce').fillna(0)
        df['qty'] = pd.to_numeric(df['qty'], errors='coerce').fillna(0)
        df['avg_price'] = pd.to_numeric(df['avg_price'], errors='coerce').fillna(0)

        # Calculate display columns
        df['current_value'] = df['current_price'] * df['qty']
        df['pnl'] = (df['current_price'] - df['avg_price']) * df['qty']
        safe_avg = df['avg_price'].where(df['avg_price'] > 0)          # 0 → NaN, no divide-by-zero
        df['pnl_percent'] = ((df['current_price'] - df['avg_price']) / safe_avg * 100).round(2)
        # Unpriced rows would otherwise show a -100% loss
        df.loc[df['current_price'] <= 0, ['pnl', 'pnl_percent']] = float('nan')

        # Create status column with better error messages
        df['status_display'] = df.apply(self._create_status_display, axis=1)

        def col(name, default=None):
            return pd.to_numeric(df[name], errors='coerce') if name in df else pd.Series(default, index=df.index)

        total_value = df['current_value'].sum()
        weight = (df['current_value'] / total_value * 100).round(1) if total_value else col('_none')

        # Select and rename columns for display
        display_df = pd.DataFrame({
            'Symbol': df['symbol'],
            'Company': df.get('company_name', df['symbol']),
            'Industry': df.get('industry', 'N/A'),
            'Qty': df['qty'],
            'Avg. Price': df['avg_price'],
            'Current Price': df['current_price'],
            'Day Change': df['day_change'],
            'Current Value': df['current_value'],
            'Weight %': weight,
            'P/L': df['pnl'],
            'P/L %': df['pnl_percent'],
            'RSI': col('rsi'),
            'From 52w High %': col('pct_from_52w_high'),
            '1Y vs Index %': col('vs_benchmark_1y'),
            'Status': df['status_display']
        })

        return display_df.set_index('Symbol')

    def _create_status_display(self, row) -> str:
        """Create human-readable status for display"""
        if row.get('current_price', 0) <= 0:
            return "❌ No Price Data"

        status = row.get('status')
        if status == "Above Supertrend":
            return "✅ Above ST"
        elif status == "Below Supertrend":
            return "⚠️ Below ST"
        elif row.get('tech_error'):
            return "⚠️ Partial Data"
        elif status is None:
            # Supertrend calculation failed — do not show a misleading green tick
            return "❓ ST Unavailable"
        else:
            return "✅ OK"


class ETFManager:
    """Manages ETF configuration"""

    def __init__(self, config: Config):
        self.config = config

    def show_etf_management_ui(self):
        """ETF list editor (rendered wherever it is called — Settings tab)"""
        with st.expander("Manage ETFs", expanded=False):
            st.caption("ETFs skip the Screener.in fundamentals scrape and use the category below as their industry.")
            st.subheader("Current ETFs")

            etf_data = self.config.get('etf_data', {})
            if etf_data:
                for symbol, info in etf_data.items():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.text(f"{symbol}: {info['name']}")
                    with col2:
                        if st.button("🗑️", key=f"delete_{symbol}", help="Delete ETF"):
                            self._delete_etf(symbol)
                            st.rerun()
            else:
                st.info("No ETFs configured")

            st.subheader("Add New ETF")
            with st.form("add_etf_form"):
                new_symbol = st.text_input("Symbol", placeholder="NIFTYBEES")
                new_name = st.text_input("Name", placeholder="Nippon India ETF Nifty BeES")
                new_description = st.text_area("Description", placeholder="ETF tracking Nifty 50 index")
                new_category = st.selectbox("Category", ["ETF", "Gold ETF", "Equity ETF", "Sector ETF", "Bond ETF"])

                if st.form_submit_button("Add ETF"):
                    if new_symbol and new_name:
                        self.config.add_etf(new_symbol.upper(), new_name, new_description, new_category)
                        st.rerun()
                    else:
                        st.error("Symbol and Name are required")

    def _delete_etf(self, symbol: str):
        """Delete an ETF from configuration"""
        etf_data = self.config.get('etf_data', {})
        if symbol in etf_data:
            del etf_data[symbol]
            self.config.set('etf_data', etf_data)
            st.success(f"Deleted {symbol} from ETF list")