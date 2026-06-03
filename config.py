# config.py
import json
import os
from typing import Dict, List, Any
import streamlit as st


class Config:
    """Configuration management for the stock dashboard"""

    DEFAULT_CONFIG = {
        "etf_data": {
            "GOLDBEES": {
                "name": "Nippon India ETF Gold Bees",
                "description": "This is an Exchange-Traded Fund that tracks the price of gold.",
                "category": "Gold ETF"
            },
            "NIFTYBEES": {
                "name": "Nippon India ETF Nifty BeES",
                "description": "ETF tracking the Nifty 50 index",
                "category": "Equity ETF"
            },
            "BANKBEES": {
                "name": "Nippon India ETF Bank BeES",
                "description": "ETF tracking the Nifty Bank index",
                "category": "Sector ETF"
            }
        },
        "rate_limits": {
            "yfinance_delay": 0.5,
            "screener_delay": 1.0,
            "chartink_delay": 2.0,
            "max_retries": 3,
            "timeout": 10
        },
        "data_sources": {
            "portfolio_file": "holdings-SOH330.xlsx",
            "portfolio_start_row": 22,
            "portfolio_columns": "B:M"
        },
        "technical_indicators": {
            "supertrend_length": 10,
            "supertrend_multiplier": 7.0
        },
        "news": {
            "max_articles": 20,
            "cache_ttl": 1800
        },
        "screener": {
            "min_market_cap": 500,
            "weekly_gain_threshold": 1.1,
            "max_results": 50
        },
        "screener_presets": {
            "weekly_breakout": {
                "name": "Weekly Breakout",
                "description": "Stocks up 10%+ from 1 week ago, above Supertrend, good market cap",
                "logic": "AND",
                "criteria": [
                    {"id": "wb1", "name": "Weekly Gain 10%+", "left_operand": "daily close", "operator": ">=",
                     "right_operand": "1 week ago close * 1.1", "enabled": True},
                    {"id": "wb2", "name": "Above Supertrend", "left_operand": "daily close", "operator": ">",
                     "right_operand": "daily supertrend( 10, 7 )", "enabled": True},
                    {"id": "wb3", "name": "Market Cap > 500Cr", "left_operand": "market cap", "operator": ">",
                     "right_operand": "500", "enabled": True}
                ]
            }
        }
    }

    def __init__(self, config_file: str = "dashboard_config.json"):
        self.config_file = config_file
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                # Merge with defaults to ensure all keys exist
                return self._merge_config(self.DEFAULT_CONFIG, config)
            except Exception as e:
                st.warning(f"Could not load config file: {e}. Using defaults.")

        # Create default config file
        self._save_config(self.DEFAULT_CONFIG)
        return self.DEFAULT_CONFIG.copy()

    def _merge_config(self, default: Dict, user: Dict) -> Dict:
        """Recursively merge user config with defaults"""
        result = default.copy()
        for key, value in user.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _save_config(self, config: Dict[str, Any]):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            st.error(f"Could not save config file: {e}")

    def get(self, key: str, default=None):
        """Get configuration value using dot notation (e.g., 'rate_limits.timeout')"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """Set configuration value and save to file"""
        keys = key.split('.')
        config_ref = self.config
        for k in keys[:-1]:
            if k not in config_ref:
                config_ref[k] = {}
            config_ref = config_ref[k]

        config_ref[keys[-1]] = value
        self._save_config(self.config)

    def get_etf_info(self, symbol: str) -> Dict[str, str]:
        """Get ETF information"""
        etf_data = self.get('etf_data', {})
        return etf_data.get(symbol, {
            "name": symbol,
            "description": "ETF information not available",
            "category": "ETF"
        })

    def is_etf(self, symbol: str) -> bool:
        """Check if symbol is an ETF"""
        return symbol in self.get('etf_data', {})

    def add_etf(self, symbol: str, name: str, description: str, category: str = "ETF"):
        """Add new ETF to configuration"""
        etf_data = self.get('etf_data', {})
        etf_data[symbol] = {
            "name": name,
            "description": description,
            "category": category
        }
        self.set('etf_data', etf_data)
        st.success(f"Added {symbol} to ETF list")