# main.py - Stock Dashboard
import io
import streamlit as st
import pandas as pd
from typing import List, Dict, Any

# Import our modular components
import charts
from config import Config
from portfolio_manager import PortfolioManager, ETFManager, PortfolioStore, PortfolioHistory
from data_sources import DataSourceManager, calculate_supertrend_series
from error_handler import ErrorBoundary, safe_execute
from screen_builder import ScreenerBuilder

# Configure Streamlit
st.set_page_config(
    page_title="Stock Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)


class StockDashboardApp:
    """Main application class"""

    def __init__(self):
        self.config = Config()
        self.portfolio_manager = PortfolioManager(self.config)
        self.etf_manager = ETFManager(self.config)
        self.data_sources = DataSourceManager(self.config)
        self.screener_builder = ScreenerBuilder(self.config)

        if 'portfolio_data' not in st.session_state:
            # Open with the last refresh instead of an empty page
            saved = PortfolioStore.load()
            if saved:
                st.session_state.portfolio_data = saved['stocks']
                st.session_state.total_investment = saved.get('total_investment') or 0
                st.session_state.portfolio_saved_at = saved.get('saved_at', 'Unknown')
                st.session_state.signal_changes = saved.get('signal_changes', [])
                meta = saved.get('meta') or {}
                if meta.get('benchmark_symbol'):
                    st.session_state.benchmark_info = {'symbol': meta['benchmark_symbol'],
                                                       'return_1y': meta.get('benchmark_return_1y')}
                if meta.get('price_batch_symbols'):
                    st.session_state.price_batch_symbols = tuple(meta['price_batch_symbols'])
            else:
                st.session_state.portfolio_data = []
        if 'total_investment' not in st.session_state:
            st.session_state.total_investment = 0

    def run(self):
        self._render_header()
        self._render_sidebar()
        self._render_main_content()

    def _render_header(self):
        st.title("📈 My Personal Stock Dashboard")
        st.markdown("*Real-time portfolio analysis with advanced screening and news sentiment*")

    def _render_sidebar(self):
        with st.sidebar:
            st.header("⚙️ Configuration")

            secret_key = self._secret("GEMINI_API_KEY")
            if secret_key:
                st.session_state.gemini_api_key = secret_key
                st.caption("🔑 Gemini key loaded from `.streamlit/secrets.toml`")
            else:
                api_key = st.text_input(
                    "Gemini API Key",
                    type="password",
                    help="Optional: for AI-based industry classification. "
                         "Put GEMINI_API_KEY in .streamlit/secrets.toml to skip this box."
                )
                if api_key:
                    st.session_state.gemini_api_key = api_key

            st.header("🔄 Data Controls")

            refresh_button = st.button(
                "🔄 Refresh Portfolio",
                type="primary",
                help="Fetch fresh prices and fundamentals for every holding",
                width="stretch"
            )

            if refresh_button:
                self._refresh_portfolio_data()

            saved_at = st.session_state.get('portfolio_saved_at')
            if saved_at and st.session_state.get('portfolio_data'):
                st.caption(f"🕒 Data as of {saved_at}")

            uploaded = st.file_uploader(
                "📤 Upload holdings (.xlsx)",
                type=["xlsx", "xls"],
                help="Your broker's holdings export. Saved locally and reused next time."
            )
            if uploaded is not None:
                signature = (uploaded.name, uploaded.size)
                if st.session_state.get('uploaded_signature') != signature:
                    path = self.portfolio_manager.save_uploaded_file(uploaded)
                    st.session_state.uploaded_signature = signature
                    st.toast(f"Saved {uploaded.name} → {path}. Click Refresh Portfolio.")
                    st.rerun()

            portfolio_file = self.config.get('data_sources.portfolio_file')
            if portfolio_file:
                st.caption(f"📄 Using `{portfolio_file}`")
            else:
                st.error("❌ No portfolio file configured")

            self.etf_manager.show_etf_management_ui()

            with st.expander("📊 Current Settings", expanded=False):
                st.json({
                    "Rate Limits": {
                        "YFinance Delay": f"{self.config.get('rate_limits.yfinance_delay')}s",
                        "Screener Delay": f"{self.config.get('rate_limits.screener_delay')}s",
                        "Max Retries": self.config.get('rate_limits.max_retries')
                    },
                    "Technical Indicators": {
                        "Supertrend Length": self.config.get('technical_indicators.supertrend_length'),
                        "Supertrend Multiplier": self.config.get('technical_indicators.supertrend_multiplier')
                    }
                })

    @staticmethod
    def _secret(name: str):
        """Read a value from st.secrets; None if no secrets file or key."""
        try:
            return st.secrets.get(name)
        except Exception:
            return None

    def _refresh_portfolio_data(self):
        def refresh_operation():
            api_key = st.session_state.get('gemini_api_key')
            portfolio_data = self.portfolio_manager.process_portfolio_holdings(api_key)
            st.session_state.portfolio_data = portfolio_data
            return len(portfolio_data)

        with st.spinner("Refreshing portfolio data..."):
            num_stocks = safe_execute(
                refresh_operation,
                error_message="Failed to refresh portfolio data",
                default_return=0
            )
            if num_stocks > 0:
                st.success(f"✅ Refreshed {num_stocks} stocks successfully!")
                st.rerun()

    def _render_main_content(self):
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📈 Portfolio Analysis",
            "👁️ Watchlist",
            "📰 News & Sentiment",
            "🔍 Stock Screener",
            "⚙️ Settings"
        ])

        with tab1:
            self._render_portfolio_tab()
        with tab2:
            self._render_watchlist_tab()
        with tab3:
            self._render_news_tab()
        with tab4:
            self._render_screener_tab()
        with tab5:
            self._render_settings_tab()

    # ------------------------------------------------------------------ helpers

    def _held_symbols(self) -> set:
        return {s.get('api_symbol') or s['symbol'].split('-')[0]
                for s in st.session_state.get('portfolio_data', [])}

    def _watchlist(self) -> List[str]:
        return list(self.config.get('watchlist', []) or [])

    def _save_watchlist(self, symbols: List[str]):
        cleaned = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
        self.config.set('watchlist', cleaned)

    def _render_signal_alerts(self):
        changes = st.session_state.get('signal_changes') or []
        if not changes:
            return
        st.subheader("🔔 Supertrend flips since last refresh")
        for c in changes:
            up = c['to'] == "Above Supertrend"
            text = (f"**{c['symbol']}** flipped {'🟢 above' if up else '🔴 below'} Supertrend "
                    f"(was *{c['from']}* at {c['since']})")
            (st.success if up else st.error)(text)
        if st.button("Dismiss alerts", key="dismiss_alerts"):
            st.session_state.signal_changes = []
            saved = PortfolioStore.load()
            if saved:
                PortfolioStore.save(saved['stocks'], saved.get('total_investment') or 0, [])
            st.rerun()

    def _render_allocation(self, display_df: pd.DataFrame):
        st.subheader("🥧 Allocation")
        threshold = float(self.config.get('portfolio.concentration_threshold', 15))
        by = st.radio("Group by", ["Symbol", "Industry"], horizontal=True,
                      label_visibility="collapsed", key="alloc_by")
        chart = charts.allocation_chart(display_df, by=by, concentration_threshold=threshold)
        if chart is None:
            st.info("No priced holdings to chart yet.")
            return
        st.altair_chart(chart, width="stretch")
        priced = display_df[display_df['Current Value'] > 0]
        heavy = priced[priced['Weight %'] >= threshold]
        if not heavy.empty:
            st.caption(f"⚠️ {len(heavy)} holding(s) at or above {threshold:.0f}% of portfolio value: "
                       f"{', '.join(heavy.index)}")

    def _render_value_history(self):
        hist = PortfolioHistory.load()
        if hist.empty:
            return
        with st.expander(f"📉 Value over time · {len(hist)} refresh day(s)", expanded=len(hist) > 1):
            if len(hist) < 2:
                st.caption("Refresh on another day to start seeing a trend. Each day's last refresh is logged.")
            chart = charts.history_chart(hist)
            if chart is not None:
                st.altair_chart(chart, width="stretch")
            first, last = hist.iloc[0], hist.iloc[-1]
            if len(hist) > 1 and first['current_value']:
                delta = last['current_value'] - first['current_value']
                st.caption(f"Since {pd.to_datetime(first['date']).strftime('%d %b %Y')}: "
                           f"₹{delta:+,.0f} ({delta / first['current_value'] * 100:+.1f}%)")

    def _render_export(self, display_df: pd.DataFrame, filename_stem: str):
        stamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M')
        col1, col2, _ = st.columns([1, 1, 4])
        with col1:
            st.download_button("📥 CSV", data=display_df.to_csv().encode('utf-8'),
                               file_name=f"{filename_stem}_{stamp}.csv", mime="text/csv",
                               width="stretch")
        with col2:
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as xw:
                display_df.to_excel(xw, sheet_name=filename_stem[:31])
                ws = xw.sheets[filename_stem[:31]]
                for column in ws.columns:
                    width = max(len(str(c.value)) if c.value is not None else 0 for c in column)
                    ws.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 45)
            st.download_button("📥 Excel", data=buf.getvalue(),
                               file_name=f"{filename_stem}_{stamp}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               width="stretch")

    @ErrorBoundary.handle_errors(error_message="Error rendering portfolio tab", show_details=True)
    def _render_portfolio_tab(self):
        st.header("📊 Portfolio Overview")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("👆 Please refresh your portfolio data using the sidebar")
            self._show_sample_portfolio_preview()
            return

        summary = self.portfolio_manager.calculate_portfolio_summary(portfolio_data)
        partial = summary['valid_prices'] < summary['total_stocks']

        saved_at = st.session_state.get('portfolio_saved_at')
        if saved_at:
            st.caption(f"🕒 Data as of {saved_at} — use the sidebar to refresh")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("💰 Total Investment", f"₹{summary['invested_total']:,.2f}",
                      help="Avg price × qty across all holdings")
        with col2:
            current_value = summary['current_value']
            # Compare against what was invested in the *priced* stocks only,
            # otherwise a missing price shows up as a loss.
            st.metric(
                "📈 Current Value",
                f"₹{current_value:,.2f}",
                delta=f"₹{current_value - summary['invested_priced']:,.2f}",
                help="Sum over holdings with a live price" + (" (partial)" if partial else "")
            )
        with col3:
            total_pnl = summary['total_pnl']
            pnl_pct = (total_pnl / summary['invested_priced'] * 100) if summary['invested_priced'] else 0
            st.metric("💵 Total P/L", f"₹{total_pnl:,.2f}", delta=f"{pnl_pct:+.2f}%")
        with col4:
            day = summary['day_change']
            st.metric("📅 Today", f"₹{day:,.2f}", delta=f"{day:+,.0f}",
                      help="Sum of (day change × qty) over priced holdings")

        if partial:
            missing = summary['total_stocks'] - summary['valid_prices']
            st.warning(f"⚠️ {missing} of {summary['total_stocks']} holdings have no price data — "
                       f"totals above cover ₹{summary['invested_priced']:,.0f} of "
                       f"₹{summary['invested_total']:,.0f} invested.")

        bench = st.session_state.get('benchmark_info') or {}
        if bench.get('return_1y') is not None:
            beating = sum(1 for s in portfolio_data if (s.get('vs_benchmark_1y') or 0) > 0)
            comparable = sum(1 for s in portfolio_data if s.get('vs_benchmark_1y') is not None)
            st.caption(f"📐 Benchmark {bench['symbol']} 1-year return: {bench['return_1y']:+.1f}% · "
                       f"{beating}/{comparable} holdings beat it over the same window")

        self._render_signal_alerts()
        self._render_value_history()

        st.header("🏢 Holdings Summary")
        display_df = self.portfolio_manager.get_portfolio_display_data(portfolio_data)

        if not display_df.empty:
            self._render_allocation(display_df)

            st.subheader("📋 Holdings")
            fcol1, fcol2, fcol3 = st.columns([1, 1, 2])
            with fcol1:
                status_filter = st.selectbox(
                    "Show", ["All", "Below Supertrend", "Above Supertrend", "No price data",
                             "Losing", "Gaining", "Beating index", "Overbought (RSI>70)", "Oversold (RSI<30)"],
                    label_visibility="collapsed"
                )
            with fcol2:
                show_indicators = st.toggle("Indicator columns", value=False,
                                            help="RSI, distance from 52-week high, 1Y return vs index")
            filtered = display_df
            if status_filter == "Below Supertrend":
                filtered = display_df[display_df['Status'].str.contains("Below", na=False)]
            elif status_filter == "Above Supertrend":
                filtered = display_df[display_df['Status'].str.contains("Above", na=False)]
            elif status_filter == "No price data":
                filtered = display_df[display_df['Status'].str.contains("No Price", na=False)]
            elif status_filter == "Losing":
                filtered = display_df[display_df['P/L'] < 0]
            elif status_filter == "Gaining":
                filtered = display_df[display_df['P/L'] > 0]
            elif status_filter == "Beating index":
                filtered = display_df[display_df['1Y vs Index %'] > 0]
            elif status_filter == "Overbought (RSI>70)":
                filtered = display_df[display_df['RSI'] > 70]
            elif status_filter == "Oversold (RSI<30)":
                filtered = display_df[display_df['RSI'] < 30]
            with fcol3:
                st.caption(f"{len(filtered)} of {len(display_df)} holdings · click a column header to sort")

            indicator_cols = ['RSI', 'From 52w High %', '1Y vs Index %']
            table = filtered if show_indicators else filtered.drop(columns=indicator_cols)

            styled_df = table.style.format({
                'Qty': '{:,.0f}',
                'Avg. Price': '₹{:,.2f}',
                'Current Price': '₹{:,.2f}',
                'Day Change': '{:+,.2f}',
                'Current Value': '₹{:,.2f}',
                'Weight %': '{:.1f}%',
                'P/L': '₹{:,.2f}',
                'P/L %': '{:+.2f}%',
                'RSI': '{:.0f}',
                'From 52w High %': '{:+.1f}%',
                '1Y vs Index %': '{:+.1f}%',
            }, na_rep='—').apply(
                self._color_pnl_columns,
                subset=[c for c in ['Day Change', 'P/L', 'P/L %', '1Y vs Index %'] if c in table.columns]
            )
            st.dataframe(styled_df, width="stretch")
            self._render_export(filtered, "holdings")

        st.header("📈 Chart a holding")
        priced = [s for s in portfolio_data if s.get('current_price')]
        if priced:
            options = {f"{s['symbol']} — {s.get('company_name', '')}": s for s in priced}
            pick = st.selectbox("Holding", list(options.keys()), label_visibility="collapsed", key="chart_pick")
            self._render_price_chart(options[pick], key_suffix="_main")

        st.header("🔍 Individual Holdings Analysis")
        self._render_individual_stock_cards(portfolio_data)

    def _show_sample_portfolio_preview(self):
        st.info("**Expected Portfolio File Structure:**")
        st.code("""
Excel file with columns:
- Symbol (e.g., 'RELIANCE-EQ', 'INFY')
- Quantity Available
- Average Price
- ... (other columns)

Starting from row 23 (configurable)
        """)

    def _color_pnl_columns(self, values):
        def color_value(val):
            if pd.isna(val):
                return 'color: gray'
            try:
                num_val = float(str(val).replace('₹', '').replace(',', '').replace('%', ''))
                if num_val > 0:
                    return 'color: green'
                elif num_val < 0:
                    return 'color: red'
                else:
                    return 'color: gray'
            except:
                return 'color: gray'
        return values.apply(color_value)

    def _render_individual_stock_cards(self, portfolio_data: List[Dict[str, Any]]):
        if not portfolio_data:
            return
        num_columns = 3
        for i in range(0, len(portfolio_data), num_columns):
            cols = st.columns(num_columns)
            row_stocks = portfolio_data[i:i + num_columns]
            for j, stock in enumerate(row_stocks):
                with cols[j]:
                    self._render_stock_card(stock)

    def _render_stock_card(self, stock: Dict[str, Any]):
        with st.container(border=True):
            st.subheader(f"📊 {stock.get('symbol', 'N/A')}")
            st.caption(f"🏢 {stock.get('company_name', 'N/A')}")
            st.caption(f"🏭 Industry: {stock.get('industry', 'N/A')}")

            current_price = stock.get('current_price')

            if current_price and current_price > 0:
                st.metric("💰 Current Price", f"₹{current_price:,.2f}")

                day_change = stock.get('day_change')
                if day_change and day_change != 0:
                    prev_close = current_price - day_change
                    day_pct = (day_change / prev_close) * 100 if prev_close > 0 else 0
                    st.metric("📈 Day's Change", f"₹{day_change:,.2f}", delta=f"{day_pct:.2f}%")

                avg_price = stock.get('avg_price', 0)
                if avg_price > 0:
                    pnl_pct = ((current_price - avg_price) / avg_price) * 100
                    pnl_color = "normal" if pnl_pct >= 0 else "inverse"
                    st.metric("💵 Return %", f"{pnl_pct:.2f}%", delta_color=pnl_color)

                status = stock.get('status')
                if status == "Below Supertrend":
                    supertrend = stock.get('supertrend') or 0
                    if supertrend > 0:
                        pct_below = ((supertrend - current_price) / supertrend) * 100
                        st.warning(f"⚠️ Below Supertrend ({pct_below:.1f}%)")
                elif status == "Above Supertrend":
                    st.success("✅ Above Supertrend")

                st.markdown(self._indicator_badges(stock))
            else:
                st.error("❌ Price data unavailable")
                error_msg = stock.get('tech_error', 'Unknown error')
                st.caption(f"Error: {error_msg}")

            with st.expander("📈 Chart & details"):
                self._render_price_chart(stock)
                about = stock.get('about', 'N/A')
                if about and about != 'N/A':
                    st.info(f"**About:** {about}")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("ROE", stock.get('roe', 'N/A'))
                with col2:
                    st.metric("ROCE", stock.get('roce', 'N/A'))

    @staticmethod
    def _indicator_badges(stock: Dict[str, Any]) -> str:
        """Compact RSI / DMA / 52w-high / vs-index line for a card."""
        parts = []
        rsi = stock.get('rsi')
        if rsi is not None:
            tag = " 🔥" if rsi > 70 else (" 🧊" if rsi < 30 else "")
            parts.append(f"RSI **{rsi:.0f}**{tag}")
        price = stock.get('current_price') or 0
        dma50, dma200 = stock.get('dma50'), stock.get('dma200')
        if dma50 and dma200:
            above = (price > dma50) + (price > dma200)
            parts.append(f"DMA {'▲▲' if above == 2 else '▲▽' if above == 1 else '▽▽'} 50/200")
        off_high = stock.get('pct_from_52w_high')
        if off_high is not None:
            parts.append(f"52w-high **{off_high:+.0f}%**")
        vs = stock.get('vs_benchmark_1y')
        if vs is not None:
            parts.append(f"vs index **{vs:+.0f}pp**")
        return " · ".join(parts) if parts else ""

    def _render_price_chart(self, stock: Dict[str, Any], key_suffix: str = ""):
        symbol = stock.get('api_symbol') or stock['symbol'].split('-')[0]
        batch = st.session_state.get('price_batch_symbols')
        history = self.data_sources.yfinance.get_history(symbol, batch)
        if history is None or history.empty:
            st.caption("No price history available for a chart.")
            return
        st_series = calculate_supertrend_series(
            history,
            length=self.config.get('technical_indicators.supertrend_length', 10),
            multiplier=self.config.get('technical_indicators.supertrend_multiplier', 7.0),
        )
        months = st.segmented_control("Range", [3, 6, 12], default=12,
                                      format_func=lambda m: f"{m}m",
                                      key=f"range_{stock['symbol']}{key_suffix}",
                                      label_visibility="collapsed") or 12
        chart = charts.price_supertrend_chart(history, st_series, symbol,
                                              avg_price=stock.get('avg_price') or None,
                                              months=months)
        if chart is not None:
            st.altair_chart(chart, width="stretch")

    # ---------------------------------------------------------------- watchlist

    @ErrorBoundary.handle_errors(error_message="Error rendering watchlist tab", show_details=True)
    def _render_watchlist_tab(self):
        st.header("👁️ Watchlist")
        st.caption("Stocks you're tracking but don't hold. Saved in dashboard_config.json. "
                   "Screener results can be added from the Results tab.")

        watchlist = self._watchlist()

        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            new_syms = st.text_input("Add symbols", placeholder="INFY, TCS, RELIANCE",
                                     label_visibility="collapsed", key="wl_add_input")
        with col2:
            if st.button("➕ Add", width="stretch", key="wl_add_btn") and new_syms:
                added = [s for s in new_syms.replace(';', ',').split(',') if s.strip()]
                self._save_watchlist(watchlist + added)
                st.rerun()
        with col3:
            if st.button("🔄 Refresh", type="primary", width="stretch", key="wl_refresh",
                         disabled=not watchlist):
                with st.spinner("Analyzing watchlist..."):
                    data = safe_execute(
                        lambda: self.portfolio_manager.process_symbols(watchlist, "Analyzing watchlist"),
                        error_message="Watchlist refresh failed", default_return=[])
                    st.session_state.watchlist_data = data
                    st.session_state.watchlist_saved_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
                st.rerun()

        if not watchlist:
            st.info("Your watchlist is empty. Add symbols above or send screener results here.")
            return

        # Chips with remove buttons
        held = self._held_symbols()
        chip_cols = st.columns(min(6, len(watchlist)) or 1)
        for i, sym in enumerate(watchlist):
            with chip_cols[i % len(chip_cols)]:
                label = f"✕ {sym}" + (" (held)" if sym in held else "")
                if st.button(label, key=f"wl_rm_{sym}", help=f"Remove {sym}", width="stretch"):
                    self._save_watchlist([s for s in watchlist if s != sym])
                    st.session_state.watchlist_data = [
                        d for d in st.session_state.get('watchlist_data', []) if d['symbol'] != sym]
                    st.rerun()

        data = st.session_state.get('watchlist_data') or []
        if not data:
            st.caption("Click **Refresh** to fetch prices, Supertrend and fundamentals.")
            return

        saved_at = st.session_state.get('watchlist_saved_at')
        if saved_at:
            st.caption(f"🕒 As of {saved_at}")

        rows = []
        for s in data:
            rows.append({
                'Symbol': s['symbol'],
                'Company': s.get('company_name', s['symbol']),
                'Industry': s.get('industry', 'N/A'),
                'Price': s.get('current_price'),
                'Day Change': s.get('day_change'),
                'RSI': s.get('rsi'),
                'From 52w High %': s.get('pct_from_52w_high'),
                '1Y vs Index %': s.get('vs_benchmark_1y'),
                'ROE': s.get('roe', 'N/A'),
                'ROCE': s.get('roce', 'N/A'),
                'Status': self.portfolio_manager._create_status_display(pd.Series(s)),
                'Held': '✅' if s['symbol'] in held else '',
            })
        wl_df = pd.DataFrame(rows).set_index('Symbol')
        for c in ['Price', 'Day Change', 'RSI', 'From 52w High %', '1Y vs Index %']:
            wl_df[c] = pd.to_numeric(wl_df[c], errors='coerce')
        styled = wl_df.style.format({
            'Price': '₹{:,.2f}', 'Day Change': '{:+,.2f}', 'RSI': '{:.0f}',
            'From 52w High %': '{:+.1f}%', '1Y vs Index %': '{:+.1f}%',
        }, na_rep='—').apply(self._color_pnl_columns, subset=['Day Change', '1Y vs Index %'])
        st.dataframe(styled, width="stretch")
        self._render_export(wl_df, "watchlist")

        st.subheader("📈 Charts")
        for stock in data:
            with st.expander(f"{stock['symbol']} — {stock.get('company_name', '')}"):
                if stock.get('current_price'):
                    st.markdown(self._indicator_badges(stock))
                self._render_price_chart(stock)

    # --------------------------------------------------------------------- news

    @ErrorBoundary.handle_errors(error_message="Error rendering news tab", show_details=True)
    def _render_news_tab(self):
        st.header("📰 Latest Financial News & Sentiment")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("Please refresh your portfolio data first to get personalized news")
            return

        col1, col2, col3 = st.columns([1, 2, 2])
        with col1:
            fetch_news = st.button("📰 Fetch Latest News", type="primary",
                                   help="Market headlines plus a targeted search per holding")
        with col2:
            per_holding = st.toggle("Search each holding", value=True,
                                    help=f"One Google News search per holding ({len(portfolio_data)} extra "
                                         "requests, cached 30 min). Off = market headlines only.")
        with col3:
            if 'news_articles' in st.session_state:
                st.caption(f"🕒 Last updated: {st.session_state.get('news_last_updated', 'Unknown')}")

        if fetch_news:
            self._fetch_news(portfolio_data, per_holding)

        if 'news_articles' in st.session_state:
            self._display_news_articles(st.session_state.news_articles)

    def _fetch_news(self, portfolio_data: List[Dict[str, Any]], per_holding: bool):
        def fetch_operation():
            portfolio_stocks = [
                {'symbol': stock['symbol'], 'company_name': stock.get('company_name', stock['symbol'])}
                for stock in portfolio_data
            ]
            articles = self.data_sources.news.get_news_from_rss(portfolio_stocks, include_holdings=per_holding)
            st.session_state.news_articles = articles
            st.session_state.news_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            return len(articles)

        with st.spinner("🔍 Fetching and analyzing news..."):
            num_articles = safe_execute(fetch_operation, error_message="Failed to fetch news", default_return=0)
            if num_articles > 0:
                st.toast(f"Found {num_articles} articles")

    def _display_news_articles(self, articles: List[Dict[str, Any]]):
        if not articles:
            st.info("No news articles found")
            return

        sentiment_counts = {}
        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1

        tagged = [a for a in articles if a.get('affected')]
        neg_tagged = [a for a in tagged if a.get('sentiment') == 'Negative']

        st.subheader("📊 Sentiment Overview")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("🟢 Positive", sentiment_counts.get('Positive', 0))
        with col2:
            st.metric("⚪ Neutral", sentiment_counts.get('Neutral', 0))
        with col3:
            st.metric("🔴 Negative", sentiment_counts.get('Negative', 0))
        with col4:
            st.metric("🎯 About my holdings", len(tagged),
                      delta=f"{len(neg_tagged)} negative" if neg_tagged else None, delta_color="inverse")

        # Filters
        symbols = sorted({s for a in articles for s in a.get('affected', [])})
        f1, f2, f3 = st.columns([1, 1, 2])
        with f1:
            sent_filter = st.selectbox("Sentiment", ["All", "Positive", "Negative", "Neutral"],
                                       label_visibility="collapsed", key="news_sent")
        with f2:
            scope = st.selectbox("Scope", ["All articles", "My holdings only"] + symbols,
                                 label_visibility="collapsed", key="news_scope")
        with f3:
            order = st.radio("Order", ["Newest", "Most negative", "Most positive"], horizontal=True,
                             label_visibility="collapsed", key="news_order")

        shown = articles
        if sent_filter != "All":
            shown = [a for a in shown if a.get('sentiment') == sent_filter]
        if scope == "My holdings only":
            shown = [a for a in shown if a.get('affected')]
        elif scope not in ("All articles",):
            shown = [a for a in shown if scope in a.get('affected', [])]
        if order == "Most negative":
            shown = sorted(shown, key=lambda a: a.get('score', 0))
        elif order == "Most positive":
            shown = sorted(shown, key=lambda a: -a.get('score', 0))

        st.subheader(f"📖 Articles ({len(shown)})")
        for article in shown:
            sentiment = article.get('sentiment', 'Neutral')
            icon = {"Positive": "🟢", "Negative": "🔴", "Neutral": "⚪"}.get(sentiment, "⚪")

            with st.container(border=True):
                headline = article.get('headline', 'No title')
                link = article.get('link', '#')
                st.markdown(f"#### {icon} [{headline}]({link})")

                when = article.get('published_at')
                try:
                    when_text = pd.Timestamp(when).tz_convert('Asia/Kolkata').strftime('%d %b %Y, %H:%M') if when else article.get('published', 'Unknown')
                except Exception:
                    when_text = article.get('published', 'Unknown')
                bits = [f"📰 {article.get('source', 'Unknown')}", f"🕒 {when_text}"]
                affected = article.get('affected', [])
                if affected:
                    bits.append("🎯 " + ", ".join(f"`{s}`" for s in affected))
                st.caption(" · ".join(bits))

    @ErrorBoundary.handle_errors(error_message="Error rendering screener tab", show_details=True)
    def _render_screener_tab(self):
        st.header("🔍 Advanced Stock Screener")

        screener_tab1, screener_tab2, screener_tab3 = st.tabs([
            "🔧 Custom Builder",
            "📋 Quick Presets",
            "📊 Results"
        ])

        with screener_tab1:
            st.markdown("""
            **🎯 Build Your Custom Screen**
            Create sophisticated screening criteria using Chartink's powerful scan language.
            Combine multiple conditions with AND/OR logic to find stocks matching your strategy.
            """)
            scan_clause = self.screener_builder.render_screener_builder_ui()
            if scan_clause:
                self._execute_custom_scan(scan_clause)

        with screener_tab2:
            st.markdown("**⚡ Quick Access to Popular Strategies**")
            self._render_quick_presets()

        with screener_tab3:
            self._render_scan_results_display()

    def _render_quick_presets(self):
        preset_names = self.screener_builder.get_preset_names()
        cols = st.columns(2)

        for i, preset_name in enumerate(preset_names):
            with cols[i % 2]:
                preset = self.screener_builder.presets[preset_name]
                with st.container(border=True):
                    st.subheader(f"📈 {preset.name}")
                    st.write(preset.description)
                    enabled_count = sum(1 for c in preset.criteria if c.enabled)
                    st.caption(f"✅ {enabled_count} active criteria")

                    col1, col2 = st.columns([2, 1])
                    with col1:
                        if st.button(f"🚀 Run {preset.name}", key=f"run_{preset_name}"):
                            scan_clause = preset.to_chartink_scan()
                            if scan_clause:
                                self._execute_custom_scan(scan_clause, preset_name)
                    with col2:
                        if st.button("🔧", key=f"edit_{preset_name}", help="Edit this preset"):
                            st.info("Switch to Custom Builder tab to edit presets")

    def _execute_custom_scan(self, scan_clause: str, preset_name: str = "Custom"):
        def run_custom_scan():
            st.info(f"🔍 Running {preset_name} scan...")
            scan_df = self.data_sources.chartink.run_scan(scan_clause)

            if scan_df.empty:
                st.warning("No stocks found matching the criteria")
                return pd.DataFrame()

            st.info(f"Found {len(scan_df)} stocks. Fetching fundamentals...")

            enhanced_stocks = []
            progress_bar = st.progress(0, text="Analyzing stocks...")
            max_results = min(len(scan_df), self.config.get('screener.max_results', 50))

            for i, row in enumerate(scan_df.head(max_results).itertuples()):
                progress_bar.progress(
                    (i + 1) / max_results,
                    text=f"Analyzing {row.nsecode}... ({i + 1}/{max_results})"
                )
                try:
                    _, roe, roce, industry, _ = self.data_sources.screener.get_company_metrics(row.nsecode)
                    enhanced_stocks.append({
                        'Symbol': row.nsecode,
                        'Company Name': getattr(row, 'name', row.nsecode),
                        'Industry': industry,
                        'CMP': getattr(row, 'close', 0),
                        'Volume': getattr(row, 'volume', 0),
                        'ROE': roe,
                        'ROCE': roce,
                        'Market Cap': getattr(row, 'market_cap', 'N/A')
                    })
                except Exception:
                    enhanced_stocks.append({
                        'Symbol': row.nsecode,
                        'Company Name': getattr(row, 'name', row.nsecode),
                        'Industry': 'N/A',
                        'CMP': getattr(row, 'close', 0),
                        'Volume': getattr(row, 'volume', 0),
                        'ROE': 'N/A',
                        'ROCE': 'N/A',
                        'Market Cap': getattr(row, 'market_cap', 'N/A')
                    })

            progress_bar.empty()
            results_df = pd.DataFrame(enhanced_stocks)

            if not results_df.empty:
                results_df['ROCE_num'] = pd.to_numeric(
                    results_df['ROCE'].astype(str).str.replace('%', ''), errors='coerce'
                )
                results_df['ROE_num'] = pd.to_numeric(
                    results_df['ROE'].astype(str).str.replace('%', ''), errors='coerce'
                )
                results_df = results_df.sort_values(
                    by=['ROCE_num', 'ROE_num'], ascending=False, na_position='last'
                ).drop(['ROCE_num', 'ROE_num'], axis=1).reset_index(drop=True)

            st.session_state.scan_results = results_df
            st.session_state.scan_preset_name = preset_name
            st.session_state.scan_clause_used = scan_clause
            st.session_state.scan_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            return results_df

        with st.spinner("🔄 Running comprehensive stock scan..."):
            results = safe_execute(run_custom_scan, error_message="Custom stock screener failed",
                                   default_return=pd.DataFrame())
            if results is not None and not results.empty:
                st.success(f"✅ {preset_name} scan completed! Found {len(results)} ranked stocks")
                st.info("📊 Check the 'Results' tab to view detailed results")

    def _render_scan_results_display(self):
        if 'scan_results' not in st.session_state or st.session_state.scan_results.empty:
            st.info("No scan results available. Run a scan to see results here.")
            return

        results_df = st.session_state.scan_results.copy()
        # Mark what you already hold / watch so a scan result isn't a "new idea" by mistake
        held, watched = self._held_symbols(), set(self._watchlist())
        results_df.insert(1, 'Held', results_df['Symbol'].map(
            lambda s: '💼 held' if s in held else ('👁️ watching' if s in watched else '')))
        preset_name = st.session_state.get('scan_preset_name', 'Custom')
        scan_clause = st.session_state.get('scan_clause_used', '')
        last_updated = st.session_state.get('scan_last_updated', 'Unknown')

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📊 Total Results", len(results_df))
        with col2:
            st.metric("🎯 Strategy", preset_name)
        with col3:
            st.metric("🕒 Last Updated", last_updated)

        with st.expander("🔍 Scan Clause Used", expanded=False):
            st.code(scan_clause, language=None)

        st.subheader("🔍 Filter Results")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            min_market_cap = st.number_input("Min Market Cap (Cr)", min_value=0, value=0)
        with col2:
            industries = ['All'] + sorted(results_df['Industry'].dropna().unique().tolist())
            selected_industry = st.selectbox("Industry", industries)
        with col3:
            min_roe = st.number_input("Min ROE (%)", min_value=0.0, value=0.0)
        with col4:
            min_roce = st.number_input("Min ROCE (%)", min_value=0.0, value=0.0)

        filtered_df = results_df.copy()

        if min_market_cap > 0:
            try:
                filtered_df['_mc'] = pd.to_numeric(filtered_df['Market Cap'], errors='coerce')
                filtered_df = filtered_df[filtered_df['_mc'] >= min_market_cap].drop('_mc', axis=1)
            except Exception:
                pass

        if selected_industry != 'All':
            filtered_df = filtered_df[filtered_df['Industry'] == selected_industry]

        if min_roe > 0:
            try:
                filtered_df['_roe'] = pd.to_numeric(
                    filtered_df['ROE'].astype(str).str.replace('%', ''), errors='coerce')
                filtered_df = filtered_df[filtered_df['_roe'] >= min_roe].drop('_roe', axis=1)
            except Exception:
                pass

        if min_roce > 0:
            try:
                filtered_df['_roce'] = pd.to_numeric(
                    filtered_df['ROCE'].astype(str).str.replace('%', ''), errors='coerce')
                filtered_df = filtered_df[filtered_df['_roce'] >= min_roce].drop('_roce', axis=1)
            except Exception:
                pass

        st.subheader(f"📋 Filtered Results ({len(filtered_df)} stocks)")

        if filtered_df.empty:
            st.warning("No stocks match the applied filters")
            return

        def highlight_top_performers(s):
            if s.name in ['ROE', 'ROCE']:
                try:
                    numeric_values = pd.to_numeric(s.astype(str).str.replace('%', ''), errors='coerce')
                    styles = [''] * len(s)
                    top_threshold = numeric_values.quantile(0.9)
                    for i, val in enumerate(numeric_values):
                        if pd.notna(val) and val >= top_threshold:
                            styles[i] = 'background-color: lightgreen'
                    return styles
                except Exception:
                    return [''] * len(s)
            return [''] * len(s)

        styled_results = filtered_df.style.format({
            'CMP': '₹{:,.2f}',
            'Volume': '{:,.0f}',
            'Market Cap': '{}'
        }).apply(highlight_top_performers)

        st.dataframe(styled_results, width="stretch", height=400)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.subheader("📊 Industry Breakdown")
            if not filtered_df['Industry'].isna().all():
                for industry, count in filtered_df['Industry'].value_counts().head(5).items():
                    st.write(f"• {industry}: {count}")
        with col2:
            st.subheader("💰 Price Range")
            try:
                prices = pd.to_numeric(filtered_df['CMP'], errors='coerce').dropna()
                if not prices.empty:
                    st.write(f"• Min: ₹{prices.min():.2f}")
                    st.write(f"• Max: ₹{prices.max():.2f}")
                    st.write(f"• Avg: ₹{prices.mean():.2f}")
            except Exception:
                st.write("Price data not available")
        with col3:
            st.subheader("📈 Performance Stats")
            try:
                roe_vals = pd.to_numeric(
                    filtered_df['ROE'].astype(str).str.replace('%', ''), errors='coerce').dropna()
                roce_vals = pd.to_numeric(
                    filtered_df['ROCE'].astype(str).str.replace('%', ''), errors='coerce').dropna()
                if not roe_vals.empty:
                    st.write(f"• Avg ROE: {roe_vals.mean():.1f}%")
                if not roce_vals.empty:
                    st.write(f"• Avg ROCE: {roce_vals.mean():.1f}%")
            except Exception:
                st.write("Performance data not available")

        st.subheader("📥 Export & Actions")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            csv = filtered_df.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"{preset_name.lower().replace(' ', '_')}_scan_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv"
            )
        with col2:
            new_syms = [s for s in filtered_df['Symbol'].tolist() if s not in held]
            if st.button(f"👁️ Add {len(new_syms)} to Watchlist", disabled=not new_syms,
                         help="Adds every filtered result you don't already hold"):
                self._save_watchlist(self._watchlist() + new_syms)
                st.toast(f"Added {len(new_syms)} symbols — see the Watchlist tab")
                st.rerun()
        with col3:
            if st.button("💾 Save as Preset"):
                st.info("Feature coming soon: Save complex scans as presets")
        with col4:
            if st.button("🗑️ Clear Results"):
                if 'scan_results' in st.session_state:
                    del st.session_state.scan_results
                st.success("Results cleared")
                st.rerun()

        if len(filtered_df) > 0:
            st.subheader("⭐ Top Picks")
            top_picks = filtered_df.head(3)
            cols = st.columns(min(3, len(top_picks)))
            for i, (_, stock) in enumerate(top_picks.iterrows()):
                with cols[i]:
                    with st.container(border=True):
                        st.write(f"**#{i + 1} {stock['Symbol']}**")
                        st.write(f"🏢 {stock['Company Name']}")
                        if stock['Industry'] != 'N/A':
                            st.write(f"🏭 {stock['Industry']}")
                        st.write(f"💰 ₹{stock['CMP']:.2f}")
                        if stock['ROE'] != 'N/A':
                            st.write(f"📊 ROE: {stock['ROE']}")
                        if stock['ROCE'] != 'N/A':
                            st.write(f"📈 ROCE: {stock['ROCE']}")

    def _render_settings_tab(self):
        st.header("⚙️ Dashboard Settings")

        st.subheader("🚦 Rate Limiting")
        col1, col2 = st.columns(2)

        with col1:
            yf_delay = st.slider("YFinance Delay (seconds)", 0.1, 5.0,
                                 self.config.get('rate_limits.yfinance_delay', 0.5), 0.1)
            screener_delay = st.slider("Screener Delay (seconds)", 0.5, 10.0,
                                       self.config.get('rate_limits.screener_delay', 1.0), 0.5)
        with col2:
            max_retries = st.number_input("Max Retries", 1, 10,
                                          self.config.get('rate_limits.max_retries', 3))
            timeout = st.number_input("Request Timeout (seconds)", 5, 60,
                                      self.config.get('rate_limits.timeout', 10))
            workers = st.slider("Parallel fetch workers", 1, 8,
                                int(self.config.get('rate_limits.parallel_workers', 4)),
                                help="Screener.in scrapes run concurrently; the per-service "
                                     "delay above still applies across all workers.")

        st.subheader("📈 Technical Indicators")
        col1, col2 = st.columns(2)
        with col1:
            st_length = st.number_input("Supertrend Length", 5, 50,
                                        self.config.get('technical_indicators.supertrend_length', 10))
        with col2:
            st_multiplier = st.number_input("Supertrend Multiplier", 1.0, 15.0,
                                            self.config.get('technical_indicators.supertrend_multiplier', 7.0),
                                            step=0.5)

        st.subheader("📐 Portfolio")
        col1, col2 = st.columns(2)
        with col1:
            benchmark = st.text_input("Benchmark symbol (NSE)",
                                      value=self.config.get('benchmark.symbol', 'NIFTYBEES'),
                                      help="1-year return of each holding is compared to this")
        with col2:
            concentration = st.number_input("Concentration alert (% of value)", 5, 50,
                                            int(self.config.get('portfolio.concentration_threshold', 15)))

        if st.button("💾 Save Settings", type="primary"):
            self.config.set('benchmark.symbol', benchmark.strip().upper() or 'NIFTYBEES')
            self.config.set('portfolio.concentration_threshold', int(concentration))
            self.config.set('rate_limits.yfinance_delay', yf_delay)
            self.config.set('rate_limits.screener_delay', screener_delay)
            self.config.set('rate_limits.max_retries', int(max_retries))
            self.config.set('rate_limits.timeout', int(timeout))
            self.config.set('rate_limits.parallel_workers', int(workers))
            self.config.set('technical_indicators.supertrend_length', int(st_length))
            self.config.set('technical_indicators.supertrend_multiplier', float(st_multiplier))
            st.success("✅ Settings saved successfully!")
            st.rerun()

        st.subheader("📄 Portfolio File Configuration")
        current_file = self.config.get('data_sources.portfolio_file', '')
        portfolio_file = st.text_input("Portfolio Excel File Path", value=current_file)

        col1, col2 = st.columns(2)
        with col1:
            start_row = st.number_input("Header Row", 0, 50,
                                        self.config.get('data_sources.portfolio_start_row', 22))
        with col2:
            columns = st.text_input("Columns Range",
                                    value=self.config.get('data_sources.portfolio_columns', 'B:M'))

        if st.button("💾 Save Portfolio Settings"):
            self.config.set('data_sources.portfolio_file', portfolio_file)
            self.config.set('data_sources.portfolio_start_row', int(start_row))
            self.config.set('data_sources.portfolio_columns', columns)
            st.success("✅ Portfolio settings saved!")

        st.subheader("ℹ️ System Information")
        with st.expander("📊 Current Configuration", expanded=False):
            st.json(self.config.config)

        st.subheader("🗑️ Cache Management")
        st.caption("Prices are cached 15 min, fundamentals 24 h, scans and news 15–30 min. "
                   "Clear the data cache to force a fresh fetch from every source.")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("Clear Portfolio Cache"):
                st.session_state.portfolio_data = []
                st.session_state.pop('portfolio_saved_at', None)
                PortfolioStore.clear()
                st.success("Portfolio cache cleared")
        with col2:
            if st.button("Clear News Cache"):
                if 'news_articles' in st.session_state:
                    del st.session_state.news_articles
                st.success("News cache cleared")
        with col3:
            if st.button("Clear Scan Cache"):
                if 'scan_results' in st.session_state:
                    del st.session_state.scan_results
                st.success("Scan cache cleared")
        with col4:
            if st.button("Clear Data Cache", type="primary",
                         help="Drop cached yfinance / Screener.in / Chartink / RSS responses"):
                st.cache_data.clear()
                st.success("Data cache cleared — next refresh will re-fetch everything")


def main():
    try:
        app = StockDashboardApp()
        app.run()
    except Exception as e:
        st.error(f"Application failed to start: {str(e)}")
        with st.expander("Error Details"):
            import traceback
            st.code(traceback.format_exc())


if __name__ == "__main__":
    main()