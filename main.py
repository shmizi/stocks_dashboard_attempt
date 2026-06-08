# main.py - Stock Dashboard
import streamlit as st
import pandas as pd
from typing import List, Dict, Any

# Import our modular components
from config import Config
from portfolio_manager import PortfolioManager, ETFManager
from data_sources import DataSourceManager
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

            api_key = st.text_input(
                "Gemini API Key",
                type="password",
                help="Optional: For AI-based industry classification"
            )
            if api_key:
                st.session_state.gemini_api_key = api_key

            st.header("🔄 Data Controls")

            refresh_button = st.button(
                "🔄 Refresh Portfolio",
                type="primary",
                help="Reload portfolio data from Excel file"
            )

            if refresh_button:
                self._refresh_portfolio_data()

            portfolio_file = self.config.get('data_sources.portfolio_file')
            if portfolio_file:
                st.info(f"📄 Portfolio File: `{portfolio_file}`")
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
        tab1, tab2, tab3, tab4 = st.tabs([
            "📈 Portfolio Analysis",
            "📰 News & Sentiment",
            "🔍 Stock Screener",
            "⚙️ Settings"
        ])

        with tab1:
            self._render_portfolio_tab()
        with tab2:
            self._render_news_tab()
        with tab3:
            self._render_screener_tab()
        with tab4:
            self._render_settings_tab()

    @ErrorBoundary.handle_errors(error_message="Error rendering portfolio tab", show_details=True)
    def _render_portfolio_tab(self):
        st.header("📊 Portfolio Overview")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("👆 Please refresh your portfolio data using the sidebar")
            self._show_sample_portfolio_preview()
            return

        summary = self.portfolio_manager.calculate_portfolio_summary(portfolio_data)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("💰 Total Investment", f"₹{st.session_state.get('total_investment', 0):,.2f}")
        with col2:
            current_value = summary['current_value']
            st.metric(
                "📈 Current Value",
                f"₹{current_value:,.2f}",
                delta=f"₹{current_value - st.session_state.get('total_investment', 0):,.2f}"
            )
        with col3:
            total_pnl = summary['total_pnl']
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric("💵 Total P/L", f"₹{total_pnl:,.2f}", delta_color=pnl_color)
        with col4:
            st.metric("📊 Data Coverage", f"{summary['valid_prices']}/{summary['total_stocks']} stocks")

        st.header("🏢 Holdings Summary")
        display_df = self.portfolio_manager.get_portfolio_display_data(portfolio_data)

        if not display_df.empty:
            styled_df = display_df.style.format({
                'Avg. Price': '₹{:,.2f}',
                'Current Price': '₹{:,.2f}',
                'Day Change': '{:,.2f}',
                'Current Value': '₹{:,.2f}',
                'P/L': '₹{:,.2f}',
                'P/L %': '{:.2f}%'
            }).apply(self._color_pnl_columns, subset=['Day Change', 'P/L', 'P/L %'])
            st.dataframe(styled_df, use_container_width=True)

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
                    supertrend = stock.get('supertrend', 0)
                    if supertrend > 0:
                        pct_below = ((supertrend - current_price) / supertrend) * 100
                        st.warning(f"⚠️ Below Supertrend ({pct_below:.1f}%)")
                elif status == "Above Supertrend":
                    st.success("✅ Above Supertrend")
            else:
                st.error("❌ Price data unavailable")
                error_msg = stock.get('tech_error', 'Unknown error')
                st.caption(f"Error: {error_msg}")

            with st.expander("ℹ️ More Details"):
                about = stock.get('about', 'N/A')
                if about and about != 'N/A':
                    st.info(f"**About:** {about}")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("ROE", stock.get('roe', 'N/A'))
                with col2:
                    st.metric("ROCE", stock.get('roce', 'N/A'))

    @ErrorBoundary.handle_errors(error_message="Error rendering news tab", show_details=True)
    def _render_news_tab(self):
        st.header("📰 Latest Financial News & Sentiment")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("Please refresh your portfolio data first to get personalized news")
            return

        col1, col2 = st.columns([1, 3])
        with col1:
            fetch_news = st.button("📰 Fetch Latest News", type="primary",
                                   help="Get latest financial news with sentiment analysis")
        with col2:
            if 'news_articles' in st.session_state:
                last_update = st.session_state.get('news_last_updated', 'Unknown')
                st.info(f"Last updated: {last_update}")

        if fetch_news:
            self._fetch_and_display_news(portfolio_data)

        if 'news_articles' in st.session_state:
            self._display_news_articles(st.session_state.news_articles)

    def _fetch_and_display_news(self, portfolio_data: List[Dict[str, Any]]):
        def fetch_operation():
            portfolio_stocks = [
                {'symbol': stock['symbol'], 'company_name': stock.get('company_name', stock['symbol'])}
                for stock in portfolio_data
            ]
            articles = self.data_sources.news.get_news_from_rss(portfolio_stocks)
            st.session_state.news_articles = articles
            st.session_state.news_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            return len(articles)

        with st.spinner("🔍 Fetching and analyzing news..."):
            num_articles = safe_execute(fetch_operation, error_message="Failed to fetch news", default_return=0)
            if num_articles > 0:
                st.success(f"✅ Found {num_articles} relevant articles")
                self._display_news_articles(st.session_state.news_articles)

    def _display_news_articles(self, articles: List[Dict[str, Any]]):
        if not articles:
            st.info("No news articles found")
            return

        sentiment_counts = {}
        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1

        st.subheader("📊 Sentiment Overview")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("🟢 Positive", sentiment_counts.get('Positive', 0))
        with col2:
            st.metric("⚪ Neutral", sentiment_counts.get('Neutral', 0))
        with col3:
            st.metric("🔴 Negative", sentiment_counts.get('Negative', 0))

        st.subheader("📖 Articles")
        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            icon = {"Positive": "🟢", "Negative": "🔴", "Neutral": "⚪"}.get(sentiment, "⚪")

            with st.container(border=True):
                headline = article.get('headline', 'No title')
                link = article.get('link', '#')
                st.markdown(f"### {icon} [{headline}]({link})", unsafe_allow_html=True)

                col1, col2 = st.columns(2)
                with col1:
                    st.caption(f"📰 Source: {article.get('source', 'Unknown')}")
                with col2:
                    st.caption(f"🕒 Published: {article.get('published', 'Unknown')}")

                affected = article.get('affected', [])
                if affected:
                    st.markdown(f"**🎯 Relevant to:** `{'`, `'.join(affected)}`")

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

        results_df = st.session_state.scan_results
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

        st.dataframe(styled_results, use_container_width=True, height=400)

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
            if st.button("👁️ Create Watchlist"):
                st.session_state.watchlist = filtered_df['Symbol'].tolist()
                st.success(f"Created watchlist with {len(st.session_state.watchlist)} stocks")
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

        st.subheader("📈 Technical Indicators")
        col1, col2 = st.columns(2)
        with col1:
            st_length = st.number_input("Supertrend Length", 5, 50,
                                        self.config.get('technical_indicators.supertrend_length', 10))
        with col2:
            st_multiplier = st.number_input("Supertrend Multiplier", 1.0, 15.0,
                                            self.config.get('technical_indicators.supertrend_multiplier', 7.0),
                                            step=0.5)

        if st.button("💾 Save Settings", type="primary"):
            self.config.set('rate_limits.yfinance_delay', yf_delay)
            self.config.set('rate_limits.screener_delay', screener_delay)
            self.config.set('rate_limits.max_retries', int(max_retries))
            self.config.set('rate_limits.timeout', int(timeout))
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
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Clear Portfolio Cache"):
                st.session_state.portfolio_data = []
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