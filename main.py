# main.py - Refactored Stock Dashboard
import streamlit as st
import pandas as pd
from typing import List, Dict, Any

# Import our modular components
from config import Config
from portfolio_manager import PortfolioManager, ETFManager
from data_sources import DataSourceManager
from error_handler import ErrorBoundary, safe_execute

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

        # Initialize session state
        if 'portfolio_data' not in st.session_state:
            st.session_state.portfolio_data = []
        if 'total_investment' not in st.session_state:
            st.session_state.total_investment = 0

    def run(self):
        """Run the main application"""
        self._render_header()
        self._render_sidebar()
        self._render_main_content()

    def _render_header(self):
        """Render application header"""
        st.title("ðŸ“ˆ My Personal Stock Dashboard")
        st.markdown("*Real-time portfolio analysis with advanced screening and news sentiment*")

    def _render_sidebar(self):
        """Render sidebar with controls and configuration"""
        with st.sidebar:
            st.header("âš™ï¸ Configuration")

            # API Key input
            api_key = st.text_input(
                "Gemini API Key",
                type="password",
                help="Optional: For AI-based industry classification"
            )
            if api_key:
                st.session_state.gemini_api_key = api_key

            # Refresh controls
            st.header("ðŸ”„ Data Controls")

            refresh_button = st.button(
                "ðŸ”„ Refresh Portfolio",
                type="primary",
                help="Reload portfolio data from Excel file"
            )

            if refresh_button:
                self._refresh_portfolio_data()

            # Portfolio file status
            portfolio_file = self.config.get('data_sources.portfolio_file')
            if portfolio_file:
                st.info(f"ðŸ“„ Portfolio File: `{portfolio_file}`")
            else:
                st.error("âŒ No portfolio file configured")

            # ETF Management
            self.etf_manager.show_etf_management_ui()

            # Configuration display
            with st.expander("ðŸ“Š Current Settings", expanded=False):
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
        """Refresh portfolio data with error handling"""

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
                st.success(f"âœ… Refreshed {num_stocks} stocks successfully!")
                st.rerun()

    def _render_main_content(self):
        """Render main content tabs"""
        tab1, tab2, tab3, tab4 = st.tabs([
            "ðŸ“ˆ Portfolio Analysis",
            "ðŸ“° News & Sentiment",
            "ðŸ” Stock Screener",
            "âš™ï¸ Settings"
        ])

        with tab1:
            self._render_portfolio_tab()

        with tab2:
            self._render_news_tab()

        with tab3:
            self._render_screener_tab()

        with tab4:
            self._render_settings_tab()

    @ErrorBoundary.handle_errors(
        error_message="Error rendering portfolio tab",
        show_details=True
    )
    def _render_portfolio_tab(self):
        """Render portfolio analysis tab"""
        st.header("ðŸ“Š Portfolio Overview")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("ðŸ‘† Please refresh your portfolio data using the sidebar")
            self._show_sample_portfolio_preview()
            return

        # Portfolio summary metrics
        summary = self.portfolio_manager.calculate_portfolio_summary(portfolio_data)

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "ðŸ’° Total Investment",
                f"â‚¹{st.session_state.get('total_investment', 0):,.2f}"
            )

        with col2:
            current_value = summary['current_value']
            st.metric(
                "ðŸ“ˆ Current Value",
                f"â‚¹{current_value:,.2f}",
                delta=f"â‚¹{current_value - st.session_state.get('total_investment', 0):,.2f}"
            )

        with col3:
            total_pnl = summary['total_pnl']
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric(
                "ðŸ’µ Total P/L",
                f"â‚¹{total_pnl:,.2f}",
                delta_color=pnl_color
            )

        with col4:
            st.metric(
                "ðŸ“Š Data Coverage",
                f"{summary['valid_prices']}/{summary['total_stocks']} stocks"
            )

        # Holdings table
        st.header("ðŸ¢ Holdings Summary")
        display_df = self.portfolio_manager.get_portfolio_display_data(portfolio_data)

        if not display_df.empty:
            # Style the dataframe
            styled_df = display_df.style.format({
                'Avg. Price': 'â‚¹{:,.2f}',
                'Current Price': 'â‚¹{:,.2f}',
                'Day Change': '{:,.2f}',
                'Current Value': 'â‚¹{:,.2f}',
                'P/L': 'â‚¹{:,.2f}',
                'P/L %': '{:.2f}%'
            }).apply(self._color_pnl_columns, subset=['Day Change', 'P/L', 'P/L %'])

            st.dataframe(styled_df, use_container_width=True)

        # Individual stock details
        st.header("ðŸ” Individual Holdings Analysis")
        self._render_individual_stock_cards(portfolio_data)

    def _show_sample_portfolio_preview(self):
        """Show sample portfolio structure"""
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
        """Apply color styling to P/L columns"""

        def color_value(val):
            if pd.isna(val):
                return 'color: gray'
            try:
                num_val = float(str(val).replace('â‚¹', '').replace(',', '').replace('%', ''))
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
        """Render individual stock analysis cards"""
        if not portfolio_data:
            return

        # Display in 3-column grid
        num_columns = 3
        for i in range(0, len(portfolio_data), num_columns):
            cols = st.columns(num_columns)
            row_stocks = portfolio_data[i:i + num_columns]

            for j, stock in enumerate(row_stocks):
                with cols[j]:
                    self._render_stock_card(stock)

    def _render_stock_card(self, stock: Dict[str, Any]):
        """Render individual stock card"""
        with st.container(border=True):
            # Header
            st.subheader(f"ðŸ“Š {stock.get('symbol', 'N/A')}")
            st.caption(f"ðŸ¢ {stock.get('company_name', 'N/A')}")
            st.caption(f"ðŸ­ Industry: {stock.get('industry', 'N/A')}")

            current_price = stock.get('current_price')

            if current_price and current_price > 0:
                # Price metrics
                st.metric("ðŸ’° Current Price", f"â‚¹{current_price:,.2f}")

                day_change = stock.get('day_change')
                if day_change and day_change != 0:
                    prev_close = current_price - day_change
                    day_pct = (day_change / prev_close) * 100 if prev_close > 0 else 0
                    st.metric(
                        "ðŸ“ˆ Day's Change",
                        f"â‚¹{day_change:,.2f}",
                        delta=f"{day_pct:.2f}%"
                    )

                # P/L calculation
                avg_price = stock.get('avg_price', 0)
                if avg_price > 0:
                    pnl_pct = ((current_price - avg_price) / avg_price) * 100
                    pnl_color = "normal" if pnl_pct >= 0 else "inverse"
                    st.metric(
                        "ðŸ’µ Return %",
                        f"{pnl_pct:.2f}%",
                        delta_color=pnl_color
                    )

                # Supertrend status
                status = stock.get('status')
                if status == "Below Supertrend":
                    supertrend = stock.get('supertrend', 0)
                    if supertrend > 0:
                        pct_below = ((supertrend - current_price) / supertrend) * 100
                        st.warning(f"âš ï¸ Below Supertrend ({pct_below:.1f}%)")
                elif status == "Above Supertrend":
                    st.success("âœ… Above Supertrend")
            else:
                st.error("âŒ Price data unavailable")
                error_msg = stock.get('tech_error', 'Unknown error')
                st.caption(f"Error: {error_msg}")

            # Additional info
            with st.expander("â„¹ï¸ More Details"):
                about = stock.get('about', 'N/A')
                if about and about != 'N/A':
                    st.info(f"**About:** {about}")

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("ROE", stock.get('roe', 'N/A'))
                with col2:
                    st.metric("ROCE", stock.get('roce', 'N/A'))

    @ErrorBoundary.handle_errors(
        error_message="Error rendering news tab",
        show_details=True
    )
    def _render_news_tab(self):
        """Render news and sentiment tab"""
        st.header("ðŸ“° Latest Financial News & Sentiment")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("Please refresh your portfolio data first to get personalized news")
            return

        # News fetching controls
        col1, col2 = st.columns([1, 3])

        with col1:
            fetch_news = st.button(
                "ðŸ“° Fetch Latest News",
                type="primary",
                help="Get latest financial news with sentiment analysis"
            )

        with col2:
            if 'news_articles' in st.session_state:
                last_update = st.session_state.get('news_last_updated', 'Unknown')
                st.info(f"Last updated: {last_update}")

        if fetch_news:
            self._fetch_and_display_news(portfolio_data)

        # Display cached news if available
        if 'news_articles' in st.session_state:
            self._display_news_articles(st.session_state.news_articles)

    def _fetch_and_display_news(self, portfolio_data: List[Dict[str, Any]]):
        """Fetch and display news articles"""

        def fetch_operation():
            portfolio_stocks = [
                {
                    'symbol': stock['symbol'],
                    'company_name': stock.get('company_name', stock['symbol'])
                }
                for stock in portfolio_data
            ]

            articles = self.data_sources.news.get_news_from_rss(portfolio_stocks)
            st.session_state.news_articles = articles
            st.session_state.news_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            return len(articles)

        with st.spinner("ðŸ” Fetching and analyzing news..."):
            num_articles = safe_execute(
                fetch_operation,
                error_message="Failed to fetch news",
                default_return=0
            )

            if num_articles > 0:
                st.success(f"âœ… Found {num_articles} relevant articles")
                self._display_news_articles(st.session_state.news_articles)

    def _display_news_articles(self, articles: List[Dict[str, Any]]):
        """Display news articles with sentiment"""
        if not articles:
            st.info("No news articles found")
            return

        # Sentiment summary
        sentiment_counts = {}
        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1

        st.subheader("ðŸ“Š Sentiment Overview")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("ðŸŸ¢ Positive", sentiment_counts.get('Positive', 0))
        with col2:
            st.metric("âšª Neutral", sentiment_counts.get('Neutral', 0))
        with col3:
            st.metric("ðŸ”´ Negative", sentiment_counts.get('Negative', 0))

        # Articles display
        st.subheader("ðŸ“– Articles")

        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            icon = {"Positive": "ðŸŸ¢", "Negative": "ðŸ”´", "Neutral": "âšª"}.get(sentiment, "âšª")

            with st.container(border=True):
                headline = article.get('headline', 'No title')
                link = article.get('link', '#')

                st.markdown(
                    f"### {icon} [{headline}]({link})",
                    unsafe_allow_html=True
                )

                # Article metadata
                col1, col2 = st.columns(2)
                with col1:
                    st.caption(f"ðŸ“° Source: {article.get('source', 'Unknown')}")
                with col2:
                    st.caption(f"ðŸ•’ Published: {article.get('published', 'Unknown')}")

                # Affected stocks
                affected = article.get('affected', [])
                if affected:
                    st.markdown(f"**ðŸŽ¯ Relevant to:** `{'`, `'.join(affected)}`")

    @ErrorBoundary.handle_errors(
        error_message="Error rendering screener tab",
        show_details=True
    )
    def _render_screener_tab(self):
        """Render customizable stock screener tab"""
        st.header("ðŸ” Advanced Stock Screener")

        # Create tabs for different screener modes
        screener_tab1, screener_tab2, screener_tab3 = st.tabs([
            "ðŸ”§ Custom Builder",
            "ðŸ“‹ Quick Presets",
            "ðŸ“Š Results"
        ])

        with screener_tab1:
            st.markdown("""
                **ðŸŽ¯ Build Your Custom Screen**  
                Create sophisticated screening criteria using Chartink's powerful scan language.
                Combine multiple conditions with AND/OR logic to find stocks matching your strategy.
                """)

            # Render the screener builder
            scan_clause = self.screener_builder.render_screener_builder_ui()

            if scan_clause:
                self._execute_custom_scan(scan_clause)

        with screener_tab2:
            st.markdown("**âš¡ Quick Access to Popular Strategies**")
            self._render_quick_presets()

        with screener_tab3:
            self._render_scan_results_display()

    def _render_quick_presets(self):
        """Render quick preset selection"""
        preset_names = self.screener_builder.get_preset_names()

        # Display presets in a grid
        cols = st.columns(2)

        for i, preset_name in enumerate(preset_names):
            with cols[i % 2]:
                preset = self.screener_builder.presets[preset_name]

                with st.container(border=True):
                    st.subheader(f"ðŸ“ˆ {preset.name}")
                    st.write(preset.description)

                    # Show criteria count
                    enabled_count = sum(1 for c in preset.criteria if c.enabled)
                    st.caption(f"âœ… {enabled_count} active criteria")

                    col1, col2 = st.columns([2, 1])

                    with col1:
                        if st.button(f"ðŸš€ Run {preset.name}", key=f"run_{preset_name}"):
                            scan_clause = preset.to_chartink_scan()
                            if scan_clause:
                                self._execute_custom_scan(scan_clause, preset_name)

                    with col2:
                        if st.button("ðŸ”§", key=f"edit_{preset_name}", help="Edit this preset"):
                            st.info("Switch to Custom Builder tab to edit presets")

    def _execute_custom_scan(self, scan_clause: str, preset_name: str = "Custom"):
        """Execute a custom scan with the given clause"""

        def run_custom_scan():
            st.info(f"ðŸ” Running {preset_name} scan...")

            # Run Chartink scan
            scan_df = self.data_sources.chartink.run_scan(scan_clause)

            if scan_df.empty:
                st.warning("No stocks found matching the criteria")
                return pd.DataFrame()

            st.info(f"Found {len(scan_df)} stocks. Fetching fundamentals...")

            # Enhance with Screener data
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
                except Exception as e:
                    # Add stock even if fundamental data fails
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

            # Create and rank results
            results_df = pd.DataFrame(enhanced_stocks)

            if not results_df.empty:
                # Convert percentage strings to numbers for sorting
                results_df['ROCE_num'] = pd.to_numeric(
                    results_df['ROCE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                )
                results_df['ROE_num'] = pd.to_numeric(
                    results_df['ROE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                )

                # Sort by ROCE then ROE
                results_df = results_df.sort_values(
                    by=['ROCE_num', 'ROE_num'],
                    ascending=False,
                    na_position='last'
                ).drop(['ROCE_num', 'ROE_num'], axis=1).reset_index(drop=True)

            # Cache results
            st.session_state.scan_results = results_df
            st.session_state.scan_preset_name = preset_name
            st.session_state.scan_clause_used = scan_clause
            st.session_state.scan_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

            return results_df

        with st.spinner("ðŸ”„ Running comprehensive stock scan..."):
            results = safe_execute(
                run_custom_scan,
                error_message="Custom stock screener failed",
                default_return=pd.DataFrame()
            )

            if not results.empty:
                st.success(f"âœ… {preset_name} scan completed! Found {len(results)} ranked stocks")
                # Switch to results tab
                st.info("ðŸ“Š Check the 'Results' tab to view detailed results")

    def _render_scan_results_display(self):
        """Display scan results with enhanced features"""
        if 'scan_results' not in st.session_state or st.session_state.scan_results.empty:
            st.info("No scan results available. Run a scan to see results here.")
            return

        results_df = st.session_state.scan_results
        preset_name = st.session_state.get('scan_preset_name', 'Custom')
        scan_clause = st.session_state.get('scan_clause_used', '')
        last_updated = st.session_state.get('scan_last_updated', 'Unknown')

        # Results header
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("ðŸ“Š Total Results", len(results_df))
        with col2:
            st.metric("ðŸŽ¯ Strategy", preset_name)
        with col3:
            st.metric("ðŸ•’ Last Updated", last_updated)

        # Show scan clause used
        with st.expander("ðŸ” Scan Clause Used", expanded=False):
            st.code(scan_clause, language=None)

        # Filters for results
        st.subheader("ðŸ” Filter Results")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            min_market_cap = st.number_input(
                "Min Market Cap (Cr)",
                min_value=0,
                value=0,
                help="Filter by minimum market cap"
            )

        with col2:
            industries = ['All'] + sorted(results_df['Industry'].dropna().unique().tolist())
            selected_industry = st.selectbox("Industry", industries)

        with col3:
            min_roe = st.number_input(
                "Min ROE (%)",
                min_value=0.0,
                value=0.0,
                help="Minimum Return on Equity"
            )

        with col4:
            min_roce = st.number_input(
                "Min ROCE (%)",
                min_value=0.0,
                value=0.0,
                help="Minimum Return on Capital Employed"
            )

        # Apply filters
        filtered_df = results_df.copy()

        if min_market_cap > 0:
            # Try to filter by market cap if it's numeric
            try:
                filtered_df['Market Cap Num'] = pd.to_numeric(filtered_df['Market Cap'], errors='coerce')
                filtered_df = filtered_df[filtered_df['Market Cap Num'] >= min_market_cap]
                filtered_df = filtered_df.drop('Market Cap Num', axis=1)
            except:
                pass

        if selected_industry != 'All':
            filtered_df = filtered_df[filtered_df['Industry'] == selected_industry]

        if min_roe > 0:
            try:
                filtered_df['ROE_num'] = pd.to_numeric(
                    filtered_df['ROE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                )
                filtered_df = filtered_df[filtered_df['ROE_num'] >= min_roe]
                filtered_df = filtered_df.drop('ROE_num', axis=1)
            except:
                pass

        if min_roce > 0:
            try:
                filtered_df['ROCE_num'] = pd.to_numeric(
                    filtered_df['ROCE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                )
                filtered_df = filtered_df[filtered_df['ROCE_num'] >= min_roce]
                filtered_df = filtered_df.drop('ROCE_num', axis=1)
            except:
                pass

        # Display filtered results
        st.subheader(f"ðŸ“‹ Filtered Results ({len(filtered_df)} stocks)")

        if filtered_df.empty:
            st.warning("No stocks match the applied filters")
            return

        # Format and display results with enhanced styling
        def highlight_top_performers(s):
            """Highlight top performing stocks"""
            if s.name in ['ROE', 'ROCE']:
                try:
                    # Convert to numeric for comparison
                    numeric_values = pd.to_numeric(s.astype(str).str.replace('%', ''), errors='coerce')
                    styles = [''] * len(s)

                    # Highlight top 10% performers
                    top_threshold = numeric_values.quantile(0.9)
                    for i, val in enumerate(numeric_values):
                        if pd.notna(val) and val >= top_threshold:
                            styles[i] = 'background-color: lightgreen'

                    return styles
                except:
                    return [''] * len(s)
            return [''] * len(s)

        styled_results = filtered_df.style.format({
            'CMP': 'â‚¹{:,.2f}',
            'Volume': '{:,.0f}',
            'Market Cap': '{}'
        }).apply(highlight_top_performers)

        st.dataframe(styled_results, use_container_width=True, height=400)

        # Summary statistics
        col1, col2, col3 = st.columns(3)

        with col1:
            st.subheader("ðŸ“Š Industry Breakdown")
            if not filtered_df['Industry'].isna().all():
                industry_counts = filtered_df['Industry'].value_counts().head(5)
                for industry, count in industry_counts.items():
                    st.write(f"â€¢ {industry}: {count}")

        with col2:
            st.subheader("ðŸ’° Price Range")
            if 'CMP' in filtered_df.columns:
                try:
                    prices = pd.to_numeric(filtered_df['CMP'], errors='coerce').dropna()
                    if not prices.empty:
                        st.write(f"â€¢ Min: â‚¹{prices.min():.2f}")
                        st.write(f"â€¢ Max: â‚¹{prices.max():.2f}")
                        st.write(f"â€¢ Avg: â‚¹{prices.mean():.2f}")
                except:
                    st.write("Price data not available")

        with col3:
            st.subheader("ðŸ“ˆ Performance Stats")
            try:
                # Calculate average ROE and ROCE
                roe_values = pd.to_numeric(
                    filtered_df['ROE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                ).dropna()

                roce_values = pd.to_numeric(
                    filtered_df['ROCE'].astype(str).str.replace('%', ''),
                    errors='coerce'
                ).dropna()

                if not roe_values.empty:
                    st.write(f"â€¢ Avg ROE: {roe_values.mean():.1f}%")
                if not roce_values.empty:
                    st.write(f"â€¢ Avg ROCE: {roce_values.mean():.1f}%")

            except:
                st.write("Performance data not available")

        # Export and action buttons
        st.subheader("ðŸ“¥ Export & Actions")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            # Download filtered results
            csv = filtered_df.to_csv(index=False)
            st.download_button(
                label="ðŸ“¥ Download CSV",
                data=csv,
                file_name=f"{preset_name.lower().replace(' ', '_')}_scan_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv"
            )

        with col2:
            # Create watchlist
            if st.button("ðŸ‘ï¸ Create Watchlist"):
                watchlist_symbols = filtered_df['Symbol'].tolist()
                st.session_state.watchlist = watchlist_symbols
                st.success(f"Created watchlist with {len(watchlist_symbols)} stocks")

        with col3:
            # Save as new preset
            if st.button("ðŸ’¾ Save as Preset"):
                self._show_save_scan_as_preset_dialog(scan_clause, preset_name)

        with col4:
            # Clear results
            if st.button("ðŸ—‘ï¸ Clear Results"):
                if 'scan_results' in st.session_state:
                    del st.session_state.scan_results
                st.success("Results cleared")
                st.rerun()

        # Top picks highlight
        if len(filtered_df) > 0:
            st.subheader("â­ Top Picks")
            top_picks = filtered_df.head(3)

            cols = st.columns(min(3, len(top_picks)))

            for i, (_, stock) in enumerate(top_picks.iterrows()):
                with cols[i]:
                    with st.container(border=True):
                        st.write(f"**#{i + 1} {stock['Symbol']}**")
                        st.write(f"ðŸ¢ {stock['Company Name']}")
                        if stock['Industry'] != 'N/A':
                            st.write(f"ðŸ­ {stock['Industry']}")
                        st.write(f"ðŸ’° â‚¹{stock['CMP']:.2f}")
                        if stock['ROE'] != 'N/A':
                            st.write(f"ðŸ“Š ROE: {stock['ROE']}")
                        if stock['ROCE'] != 'N/A':
                            st.write(f"ðŸ“ˆ ROCE: {stock['ROCE']}")

    def _show_save_scan_as_preset_dialog(self, scan_clause: str, current_name: str):
        """Show dialog to save scan as new preset"""
        with st.form("save_scan_preset"):
            st.subheader("ðŸ’¾ Save Scan as Preset")

            new_preset_name = st.text_input(
                "Preset Name",
                value=f"{current_name} - Modified",
                placeholder="My Custom Scan"
            )

            new_description = st.text_area(
                "Description",
                placeholder="Description of this screening strategy"
            )

            if st.form_submit_button("Save Preset"):
                if new_preset_name and new_preset_name not in self.screener_builder.presets:
                    # This is a simplified save - in a full implementation,
                    # you'd parse the scan_clause back into individual criteria
                    st.info("Feature coming soon: Save complex scans as presets")
                    st.success(f"Preset '{new_preset_name}' would be saved")
                elif new_preset_name in self.screener_builder.presets:
                    st.error("Preset name already exists")
                else:
                    st.error("Please enter a preset name")  # main.py - Refactored Stock Dashboard


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

        # Initialize session state
        if 'portfolio_data' not in st.session_state:
            st.session_state.portfolio_data = []
        if 'total_investment' not in st.session_state:
            st.session_state.total_investment = 0

    def run(self):
        """Run the main application"""
        self._render_header()
        self._render_sidebar()
        self._render_main_content()

    def _render_header(self):
        """Render application header"""
        st.title("ðŸ“ˆ My Personal Stock Dashboard")
        st.markdown("*Real-time portfolio analysis with advanced screening and news sentiment*")

    def _render_sidebar(self):
        """Render sidebar with controls and configuration"""
        with st.sidebar:
            st.header("âš™ï¸ Configuration")

            # API Key input
            api_key = st.text_input(
                "Gemini API Key",
                type="password",
                help="Optional: For AI-based industry classification"
            )
            if api_key:
                st.session_state.gemini_api_key = api_key

            # Refresh controls
            st.header("ðŸ”„ Data Controls")

            refresh_button = st.button(
                "ðŸ”„ Refresh Portfolio",
                type="primary",
                help="Reload portfolio data from Excel file"
            )

            if refresh_button:
                self._refresh_portfolio_data()

            # Portfolio file status
            portfolio_file = self.config.get('data_sources.portfolio_file')
            if portfolio_file:
                st.info(f"ðŸ“„ Portfolio File: `{portfolio_file}`")
            else:
                st.error("âŒ No portfolio file configured")

            # ETF Management
            self.etf_manager.show_etf_management_ui()

            # Configuration display
            with st.expander("ðŸ“Š Current Settings", expanded=False):
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
        """Refresh portfolio data with error handling"""

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
                st.success(f"âœ… Refreshed {num_stocks} stocks successfully!")
                st.rerun()

    def _render_main_content(self):
        """Render main content tabs"""
        tab1, tab2, tab3, tab4 = st.tabs([
            "ðŸ“ˆ Portfolio Analysis",
            "ðŸ“° News & Sentiment",
            "ðŸ” Stock Screener",
            "âš™ï¸ Settings"
        ])

        with tab1:
            self._render_portfolio_tab()

        with tab2:
            self._render_news_tab()

        with tab3:
            self._render_screener_tab()

        with tab4:
            self._render_settings_tab()

    @ErrorBoundary.handle_errors(
        error_message="Error rendering portfolio tab",
        show_details=True
    )
    def _render_portfolio_tab(self):
        """Render portfolio analysis tab"""
        st.header("ðŸ“Š Portfolio Overview")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("ðŸ‘† Please refresh your portfolio data using the sidebar")
            self._show_sample_portfolio_preview()
            return

        # Portfolio summary metrics
        summary = self.portfolio_manager.calculate_portfolio_summary(portfolio_data)

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "ðŸ’° Total Investment",
                f"â‚¹{st.session_state.get('total_investment', 0):,.2f}"
            )

        with col2:
            current_value = summary['current_value']
            st.metric(
                "ðŸ“ˆ Current Value",
                f"â‚¹{current_value:,.2f}",
                delta=f"â‚¹{current_value - st.session_state.get('total_investment', 0):,.2f}"
            )

        with col3:
            total_pnl = summary['total_pnl']
            pnl_color = "normal" if total_pnl >= 0 else "inverse"
            st.metric(
                "ðŸ’µ Total P/L",
                f"â‚¹{total_pnl:,.2f}",
                delta_color=pnl_color
            )

        with col4:
            st.metric(
                "ðŸ“Š Data Coverage",
                f"{summary['valid_prices']}/{summary['total_stocks']} stocks"
            )

        # Holdings table
        st.header("ðŸ¢ Holdings Summary")
        display_df = self.portfolio_manager.get_portfolio_display_data(portfolio_data)

        if not display_df.empty:
            # Style the dataframe
            styled_df = display_df.style.format({
                'Avg. Price': 'â‚¹{:,.2f}',
                'Current Price': 'â‚¹{:,.2f}',
                'Day Change': '{:,.2f}',
                'Current Value': 'â‚¹{:,.2f}',
                'P/L': 'â‚¹{:,.2f}',
                'P/L %': '{:.2f}%'
            }).apply(self._color_pnl_columns, subset=['Day Change', 'P/L', 'P/L %'])

            st.dataframe(styled_df, use_container_width=True)

        # Individual stock details
        st.header("ðŸ” Individual Holdings Analysis")
        self._render_individual_stock_cards(portfolio_data)

    def _show_sample_portfolio_preview(self):
        """Show sample portfolio structure"""
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
        """Apply color styling to P/L columns"""

        def color_value(val):
            if pd.isna(val):
                return 'color: gray'
            try:
                num_val = float(str(val).replace('â‚¹', '').replace(',', '').replace('%', ''))
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
        """Render individual stock analysis cards"""
        if not portfolio_data:
            return

        # Display in 3-column grid
        num_columns = 3
        for i in range(0, len(portfolio_data), num_columns):
            cols = st.columns(num_columns)
            row_stocks = portfolio_data[i:i + num_columns]

            for j, stock in enumerate(row_stocks):
                with cols[j]:
                    self._render_stock_card(stock)

    def _render_stock_card(self, stock: Dict[str, Any]):
        """Render individual stock card"""
        with st.container(border=True):
            # Header
            st.subheader(f"ðŸ“Š {stock.get('symbol', 'N/A')}")
            st.caption(f"ðŸ¢ {stock.get('company_name', 'N/A')}")
            st.caption(f"ðŸ­ Industry: {stock.get('industry', 'N/A')}")

            current_price = stock.get('current_price')

            if current_price and current_price > 0:
                # Price metrics
                st.metric("ðŸ’° Current Price", f"â‚¹{current_price:,.2f}")

                day_change = stock.get('day_change')
                if day_change and day_change != 0:
                    prev_close = current_price - day_change
                    day_pct = (day_change / prev_close) * 100 if prev_close > 0 else 0
                    st.metric(
                        "ðŸ“ˆ Day's Change",
                        f"â‚¹{day_change:,.2f}",
                        delta=f"{day_pct:.2f}%"
                    )

                # P/L calculation
                avg_price = stock.get('avg_price', 0)
                if avg_price > 0:
                    pnl_pct = ((current_price - avg_price) / avg_price) * 100
                    pnl_color = "normal" if pnl_pct >= 0 else "inverse"
                    st.metric(
                        "ðŸ’µ Return %",
                        f"{pnl_pct:.2f}%",
                        delta_color=pnl_color
                    )

                # Supertrend status
                status = stock.get('status')
                if status == "Below Supertrend":
                    supertrend = stock.get('supertrend', 0)
                    if supertrend > 0:
                        pct_below = ((supertrend - current_price) / supertrend) * 100
                        st.warning(f"âš ï¸ Below Supertrend ({pct_below:.1f}%)")
                elif status == "Above Supertrend":
                    st.success("âœ… Above Supertrend")
            else:
                st.error("âŒ Price data unavailable")
                error_msg = stock.get('tech_error', 'Unknown error')
                st.caption(f"Error: {error_msg}")

            # Additional info
            with st.expander("â„¹ï¸ More Details"):
                about = stock.get('about', 'N/A')
                if about and about != 'N/A':
                    st.info(f"**About:** {about}")

                col1, col2 = st.columns(2)
                with col1:
                    st.metric("ROE", stock.get('roe', 'N/A'))
                with col2:
                    st.metric("ROCE", stock.get('roce', 'N/A'))

    @ErrorBoundary.handle_errors(
        error_message="Error rendering news tab",
        show_details=True
    )
    def _render_news_tab(self):
        """Render news and sentiment tab"""
        st.header("ðŸ“° Latest Financial News & Sentiment")

        portfolio_data = st.session_state.get('portfolio_data', [])

        if not portfolio_data:
            st.warning("Please refresh your portfolio data first to get personalized news")
            return

        # News fetching controls
        col1, col2 = st.columns([1, 3])

        with col1:
            fetch_news = st.button(
                "ðŸ“° Fetch Latest News",
                type="primary",
                help="Get latest financial news with sentiment analysis"
            )

        with col2:
            if 'news_articles' in st.session_state:
                last_update = st.session_state.get('news_last_updated', 'Unknown')
                st.info(f"Last updated: {last_update}")

        if fetch_news:
            self._fetch_and_display_news(portfolio_data)

        # Display cached news if available
        if 'news_articles' in st.session_state:
            self._display_news_articles(st.session_state.news_articles)

    def _fetch_and_display_news(self, portfolio_data: List[Dict[str, Any]]):
        """Fetch and display news articles"""

        def fetch_operation():
            portfolio_stocks = [
                {
                    'symbol': stock['symbol'],
                    'company_name': stock.get('company_name', stock['symbol'])
                }
                for stock in portfolio_data
            ]

            articles = self.data_sources.news.get_news_from_rss(portfolio_stocks)
            st.session_state.news_articles = articles
            st.session_state.news_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
            return len(articles)

        with st.spinner("ðŸ” Fetching and analyzing news..."):
            num_articles = safe_execute(
                fetch_operation,
                error_message="Failed to fetch news",
                default_return=0
            )

            if num_articles > 0:
                st.success(f"âœ… Found {num_articles} relevant articles")
                self._display_news_articles(st.session_state.news_articles)

    def _display_news_articles(self, articles: List[Dict[str, Any]]):
        """Display news articles with sentiment"""
        if not articles:
            st.info("No news articles found")
            return

        # Sentiment summary
        sentiment_counts = {}
        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1

        st.subheader("ðŸ“Š Sentiment Overview")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("ðŸŸ¢ Positive", sentiment_counts.get('Positive', 0))
        with col2:
            st.metric("âšª Neutral", sentiment_counts.get('Neutral', 0))
        with col3:
            st.metric("ðŸ”´ Negative", sentiment_counts.get('Negative', 0))

        # Articles display
        st.subheader("ðŸ“– Articles")

        for article in articles:
            sentiment = article.get('sentiment', 'Neutral')
            icon = {"Positive": "ðŸŸ¢", "Negative": "ðŸ”´", "Neutral": "âšª"}.get(sentiment, "âšª")

            with st.container(border=True):
                headline = article.get('headline', 'No title')
                link = article.get('link', '#')

                st.markdown(
                    f"### {icon} [{headline}]({link})",
                    unsafe_allow_html=True
                )

                # Article metadata
                col1, col2 = st.columns(2)
                with col1:
                    st.caption(f"ðŸ“° Source: {article.get('source', 'Unknown')}")
                with col2:
                    st.caption(f"ðŸ•’ Published: {article.get('published', 'Unknown')}")

                # Affected stocks
                affected = article.get('affected', [])
                if affected:
                    st.markdown(f"**ðŸŽ¯ Relevant to:** `{'`, `'.join(affected)}`")

    @ErrorBoundary.handle_errors(
        error_message="Error rendering screener tab",
        show_details=True
    )
    def _render_screener_tab(self):
        """Render stock screener tab"""
        st.header("ðŸ” Weekly Breakout Stock Screener")

        st.markdown("""
            **Screening Criteria:**
            - ðŸ“ˆ **Weekly Gain:** Stock is up â‰¥10% from 1 week ago
            - ðŸ’° **Market Cap:** Greater than â‚¹500 Cr  
            - ðŸ“Š **Technical:** Price above Supertrend (10, 7)
            - ðŸ“‹ **Ranking:** Sorted by ROCE and ROE
            """)

        # Screener controls
        col1, col2 = st.columns([1, 3])

        with col1:
            run_scan = st.button(
                "ðŸš€ Run Screener",
                type="primary",
                help="Run the weekly breakout scan (may take 1-2 minutes)"
            )

        with col2:
            if 'scan_results' in st.session_state:
                last_scan = st.session_state.get('scan_last_updated', 'Unknown')
                st.info(f"Last scan: {last_scan}")

        if run_scan:
            self._run_stock_screener()

        # Display cached results if available
        if 'scan_results' in st.session_state:
            self._display_scan_results(st.session_state.scan_results)

    def _run_stock_screener(self):
        """Run the stock screener with progress tracking"""

        def run_scan_operation():
            # Build scan clause from config
            min_cap = self.config.get('screener.min_market_cap', 500)
            gain_threshold = self.config.get('screener.weekly_gain_threshold', 1.1)
            st_length = self.config.get('technical_indicators.supertrend_length', 10)
            st_multiplier = self.config.get('technical_indicators.supertrend_multiplier', 7.0)

            scan_clause = f"""
                ( {{cash}} ( 
                    daily close >= 1 week ago close * {gain_threshold} and 
                    daily close > daily supertrend( {st_length}, {st_multiplier} ) and 
                    market cap > {min_cap} 
                ) )
                """

            # Run Chartink scan
            st.info("ðŸ” Running Chartink scan...")
            scan_df = self.data_sources.chartink.run_scan(scan_clause)

            if scan_df.empty:
                st.warning("No stocks found matching the criteria")
                return pd.DataFrame()

            st.info(f"Found {len(scan_df)} stocks. Fetching fundamentals...")

            # Enhance with Screener data
            enhanced_stocks = []
            progress_bar = st.progress(0, text="Analyzing stocks...")

            for i, row in enumerate(scan_df.itertuples()):
                progress_bar.progress(
                    (i + 1) / len(scan_df),
                    text=f"Analyzing {row.nsecode}..."
                )

                try:
                    _, roe, roce, _, _ = self.data_sources.screener.get_company_metrics(row.nsecode)

                    enhanced_stocks.append({
                        'Symbol': row.nsecode,
                        'Company Name': getattr(row, 'name', row.nsecode),
                        'CMP': getattr(row, 'close', 0),
                        'Volume': getattr(row, 'volume', 0),
                        'ROE': roe,
                        'ROCE': roce
                    })
                except Exception as e:
                    # Add stock even if fundamental data fails
                    enhanced_stocks.append({
                        'Symbol': row.nsecode,
                        'Company Name': getattr(row, 'name', row.nsecode),
                        'CMP': getattr(row, 'close', 0),
                        'Volume': getattr(row, 'volume', 0),
                        'ROE': 'N/A',
                        'ROCE': 'N/A'
                    })

            progress_bar.empty()

            # Create and rank results
            results_df = pd.DataFrame(enhanced_stocks)

            # Convert percentage strings to numbers for sorting
            results_df['ROCE_num'] = pd.to_numeric(
                results_df['ROCE'].astype(str).str.replace('%', ''),
                errors='coerce'
            )
            results_df['ROE_num'] = pd.to_numeric(
                results_df['ROE'].astype(str).str.replace('%', ''),
                errors='coerce'
            )

            # Sort by ROCE then ROE
            results_df = results_df.sort_values(
                by=['ROCE_num', 'ROE_num'],
                ascending=False,
                na_position='last'
            ).drop(['ROCE_num', 'ROE_num'], axis=1).reset_index(drop=True)

            # Cache results
            st.session_state.scan_results = results_df
            st.session_state.scan_last_updated = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

            return results_df

        with st.spinner("ðŸ”„ Running comprehensive stock scan..."):
            results = safe_execute(
                run_scan_operation,
                error_message="Stock screener failed",
                default_return=pd.DataFrame()
            )

            if not results.empty:
                st.success(f"âœ… Scan completed! Found {len(results)} ranked stocks")
                self._display_scan_results(results)

    def _display_scan_results(self, results_df: pd.DataFrame):
        """Display screener results"""
        if results_df.empty:
            st.info("No scan results available")
            return

        st.subheader(f"ðŸ“‹ Scan Results ({len(results_df)} stocks)")

        # Format and display results
        styled_results = results_df.style.format({
            'CMP': 'â‚¹{:,.2f}',
            'Volume': '{:,.0f}'
        })

        st.dataframe(styled_results, use_container_width=True)

        # Export options
        col1, col2 = st.columns(2)
        with col1:
            csv = results_df.to_csv(index=False)
            st.download_button(
                label="ðŸ“¥ Download CSV",
                data=csv,
                file_name=f"stock_scan_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv"
            )

        with col2:
            st.info(f"ðŸ’¡ Top stock by ROCE: **{results_df.iloc[0]['Symbol']}** ({results_df.iloc[0]['ROCE']})")

    def _render_settings_tab(self):
        """Render settings and configuration tab"""
        st.header("âš™ï¸ Dashboard Settings")

        # Rate limiting settings
        st.subheader("ðŸš¦ Rate Limiting")

        col1, col2 = st.columns(2)

        with col1:
            yf_delay = st.slider(
                "YFinance Delay (seconds)",
                min_value=0.1,
                max_value=5.0,
                value=self.config.get('rate_limits.yfinance_delay', 0.5),
                step=0.1,
                help="Minimum delay between YFinance API calls"
            )

            screener_delay = st.slider(
                "Screener Delay (seconds)",
                min_value=0.5,
                max_value=10.0,
                value=self.config.get('rate_limits.screener_delay', 1.0),
                step=0.5,
                help="Minimum delay between Screener.in requests"
            )

        with col2:
            max_retries = st.number_input(
                "Max Retries",
                min_value=1,
                max_value=10,
                value=self.config.get('rate_limits.max_retries', 3),
                help="Maximum retry attempts for failed requests"
            )

            timeout = st.number_input(
                "Request Timeout (seconds)",
                min_value=5,
                max_value=60,
                value=self.config.get('rate_limits.timeout', 10),
                help="Timeout for network requests"
            )

        # Technical indicators
        st.subheader("ðŸ“ˆ Technical Indicators")

        col1, col2 = st.columns(2)

        with col1:
            st_length = st.number_input(
                "Supertrend Length",
                min_value=5,
                max_value=50,
                value=self.config.get('technical_indicators.supertrend_length', 10),
                help="Period for Supertrend calculation"
            )

        with col2:
            st_multiplier = st.number_input(
                "Supertrend Multiplier",
                min_value=1.0,
                max_value=15.0,
                value=self.config.get('technical_indicators.supertrend_multiplier', 7.0),
                step=0.5,
                help="Multiplier for Supertrend calculation"
            )

        # Save settings
        if st.button("ðŸ’¾ Save Settings", type="primary"):
            # Update configuration
            self.config.set('rate_limits.yfinance_delay', yf_delay)
            self.config.set('rate_limits.screener_delay', screener_delay)
            self.config.set('rate_limits.max_retries', max_retries)
            self.config.set('rate_limits.timeout', timeout)
            self.config.set('technical_indicators.supertrend_length', st_length)
            self.config.set('technical_indicators.supertrend_multiplier', st_multiplier)

            st.success("âœ… Settings saved successfully!")
            st.rerun()

        # Portfolio file settings
        st.subheader("ðŸ“„ Portfolio File Configuration")

        current_file = self.config.get('data_sources.portfolio_file', '')
        portfolio_file = st.text_input(
            "Portfolio Excel File Path",
            value=current_file,
            help="Path to your portfolio Excel file"
        )

        col1, col2 = st.columns(2)
        with col1:
            start_row = st.number_input(
                "Header Row",
                min_value=0,
                max_value=50,
                value=self.config.get('data_sources.portfolio_start_row', 22),
                help="Row number where data headers are located"
            )

        with col2:
            columns = st.text_input(
                "Columns Range",
                value=self.config.get('data_sources.portfolio_columns', 'B:M'),
                help="Excel column range (e.g., 'B:M')"
            )

        if st.button("ðŸ’¾ Save Portfolio Settings"):
            self.config.set('data_sources.portfolio_file', portfolio_file)
            self.config.set('data_sources.portfolio_start_row', start_row)
            self.config.set('data_sources.portfolio_columns', columns)
            st.success("âœ… Portfolio settings saved!")

        # System information
        st.subheader("â„¹ï¸ System Information")

        with st.expander("ðŸ“Š Current Configuration", expanded=False):
            st.json(self.config.config)

        # Clear cache options
        st.subheader("ðŸ—‘ï¸ Cache Management")

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
    """Main application entry point"""
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