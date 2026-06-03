# portfolio_manager.py
import pandas as pd
import streamlit as st
from typing import List, Dict, Any, Optional
import os

from config import Config
from data_sources import DataSourceManager
from error_handler import ErrorBoundary, DataValidator, ProgressTracker, ValidationError


class PortfolioManager:
    """Manages portfolio data loading and processing"""

    def __init__(self, config: Config):
        self.config = config
        self.data_sources = DataSourceManager(config)
        self.portfolio_file = config.get('data_sources.portfolio_file')
        self.start_row = config.get('data_sources.portfolio_start_row', 22)
        self.columns = config.get('data_sources.portfolio_columns', 'B:M')

    @ErrorBoundary.handle_errors(
        fallback_value=pd.DataFrame(),
        error_message="Failed to load portfolio file",
        show_details=True
    )
    def load_portfolio_file(self) -> pd.DataFrame:
        """Load and validate portfolio file"""
        if not os.path.exists(self.portfolio_file):
            raise ValidationError(f"Portfolio file not found: {self.portfolio_file}")

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

        # Process each holding
        all_stocks_data = []
        stocks_needing_ai_industry = {}

        # Create progress tracker
        tracker = ProgressTracker(len(holdings_df), "Processing portfolio holdings")

        for _, row in holdings_df.iterrows():
            stock_symbol = str(row['Symbol'])
            api_symbol = self._clean_symbol(stock_symbol)

            tracker.update(f"Analyzing {stock_symbol}")

            try:
                # Get basic stock info
                stock_info = {
                    'symbol': stock_symbol,
                    'qty': row['Quantity Available'],
                    'avg_price': row['Average Price']
                }

                # Check if it's an ETF
                if self.config.is_etf(api_symbol):
                    etf_info = self.config.get_etf_info(api_symbol)
                    stock_info.update({
                        'company_name': etf_info['name'],
                        'roe': 'N/A',
                        'roce': 'N/A',
                        'industry': etf_info['category'],
                        'about': etf_info['description'],
                        'current_price': None,
                        'day_change': None,
                        'supertrend': None,
                        'status': None,
                        'tech_error': None
                    })

                    # Still try to get price data for ETFs
                    try:
                        price_data = self.data_sources.yfinance.get_stock_analysis(api_symbol)
                        stock_info.update({
                            'current_price': price_data[0],
                            'day_change': price_data[1],
                            'supertrend': price_data[2],
                            'status': price_data[3],
                            'tech_error': price_data[4]
                        })
                    except Exception as e:
                        tracker.add_error(f"Price fetch failed: {str(e)}", stock_symbol)

                else:
                    # Regular stock - get comprehensive data
                    stock_data = self.data_sources.get_stock_data(api_symbol)
                    stock_info.update(stock_data)

                    # Mark for AI industry classification if needed
                    if (stock_info.get('industry') == 'N/A' and
                            stock_info.get('about') not in ['N/A', '']):
                        stocks_needing_ai_industry[stock_symbol] = stock_info['about']

                all_stocks_data.append(stock_info)

            except Exception as e:
                tracker.add_error(f"Processing failed: {str(e)}", stock_symbol)
                # Add minimal stock info even if processing fails
                all_stocks_data.append({
                    'symbol': stock_symbol,
                    'qty': row['Quantity Available'],
                    'avg_price': row['Average Price'],
                    'current_price': None,
                    'company_name': stock_symbol,
                    'tech_error': f"Processing failed: {str(e)}"
                })

        # AI industry classification if needed
        if stocks_needing_ai_industry and api_key:
            tracker.update("Running AI industry analysis")
            try:
                ai_industries = self.data_sources.ai.classify_industries_batch(
                    stocks_needing_ai_industry, api_key
                )

                # Update stocks with AI-classified industries
                for stock in all_stocks_data:
                    if stock['symbol'] in ai_industries:
                        stock['industry'] = ai_industries[stock['symbol']]

            except Exception as e:
                tracker.add_error(f"AI classification failed: {str(e)}")

        tracker.finish()
        return all_stocks_data

    def _clean_symbol(self, symbol: str) -> str:
        """Clean stock symbol for API usage"""
        if symbol.endswith(('-E', '-EQ')):
            return symbol.split('-')[0]
        return symbol

    def calculate_portfolio_summary(self, stocks_data: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate portfolio summary metrics"""
        if not stocks_data:
            return {'current_value': 0, 'total_pnl': 0, 'day_change': 0}

        df = pd.DataFrame(stocks_data)
        df['current_price'] = pd.to_numeric(df['current_price'], errors='coerce').fillna(0)
        df['day_change'] = pd.to_numeric(df['day_change'], errors='coerce').fillna(0)

        # Calculate metrics
        df['current_value'] = df['current_price'] * df['qty']
        df['pnl'] = (df['current_price'] - df['avg_price']) * df['qty']
        df['day_pnl'] = df['day_change'] * df['qty']

        # Filter valid prices for totals
        valid_df = df[df['current_price'] > 0]

        return {
            'current_value': valid_df['current_value'].sum(),
            'total_pnl': valid_df['pnl'].sum(),
            'day_change': valid_df['day_pnl'].sum(),
            'total_stocks': len(df),
            'valid_prices': len(valid_df)
        }

    def get_portfolio_display_data(self, stocks_data: List[Dict[str, Any]]) -> pd.DataFrame:
        """Prepare portfolio data for display"""
        if not stocks_data:
            return pd.DataFrame()

        df = pd.DataFrame(stocks_data)
        df['current_price'] = pd.to_numeric(df['current_price'], errors='coerce').fillna(0)
        df['day_change'] = pd.to_numeric(df['day_change'], errors='coerce').fillna(0)

        # Calculate display columns
        df['current_value'] = df['current_price'] * df['qty']
        df['pnl'] = (df['current_price'] - df['avg_price']) * df['qty']
        df['pnl_percent'] = ((df['current_price'] - df['avg_price']) / df['avg_price'] * 100).round(2)

        # Create status column with better error messages
        df['status_display'] = df.apply(self._create_status_display, axis=1)

        # Select and rename columns for display
        display_df = pd.DataFrame({
            'Symbol': df['symbol'],
            'Company': df.get('company_name', df['symbol']),
            'Qty': df['qty'].astype(int),
            'Avg. Price': df['avg_price'],
            'Current Price': df['current_price'],
            'Day Change': df['day_change'],
            'Current Value': df['current_value'],
            'P/L': df['pnl'],
            'P/L %': df['pnl_percent'],
            'Status': df['status_display']
        })

        return display_df.set_index('Symbol')

    def _create_status_display(self, row) -> str:
        """Create human-readable status for display"""
        if row.get('current_price', 0) <= 0:
            return "âŒ No Price Data"

        status = row.get('status')
        if status == "Above Supertrend":
            return "âœ… Above ST"
        elif status == "Below Supertrend":
            return "âš ï¸ Below ST"
        elif row.get('tech_error'):
            return "âš ï¸ Partial Data"
        else:
            return "âœ… OK"


class ETFManager:
    """Manages ETF configuration"""

    def __init__(self, config: Config):
        self.config = config

    def show_etf_management_ui(self):
        """Show ETF management interface in sidebar"""
        with st.sidebar.expander("ðŸ”§ Manage ETFs", expanded=False):
            st.subheader("Current ETFs")

            etf_data = self.config.get('etf_data', {})
            if etf_data:
                for symbol, info in etf_data.items():
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.text(f"{symbol}: {info['name']}")
                    with col2:
                        if st.button("ðŸ—‘ï¸", key=f"delete_{symbol}", help="Delete ETF"):
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