# error_handler.py
import functools
import traceback
from typing import Any, Callable, Optional, Union
import streamlit as st
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ErrorBoundary:
    """Error boundary decorator for Streamlit functions"""

    @staticmethod
    def handle_errors(
            fallback_value: Any = None,
            error_message: str = "An error occurred",
            show_details: bool = False,
            log_error: bool = True
    ):
        """
        Decorator to handle errors gracefully

        Args:
            fallback_value: Value to return on error
            error_message: User-friendly error message
            show_details: Whether to show error details to user
            log_error: Whether to log the error
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    error_details = f"Error in {func.__name__}: {str(e)}"

                    if log_error:
                        logger.error(f"{error_details}\n{traceback.format_exc()}")

                    # Show user-friendly error
                    if show_details:
                        st.error(f"{error_message}\n\nDetails: {error_details}")
                        with st.expander("Full Error Trace"):
                            st.code(traceback.format_exc())
                    else:
                        st.error(error_message)

                    return fallback_value

            return wrapper

        return decorator


class ValidationError(Exception):
    """Custom exception for data validation errors"""
    pass


class DataValidator:
    """Data validation utilities"""

    @staticmethod
    def validate_portfolio_data(df) -> bool:
        """Validate portfolio data structure"""
        required_columns = ['Symbol', 'Quantity Available', 'Average Price']

        if df is None or df.empty:
            raise ValidationError("Portfolio data is empty")

        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValidationError(f"Missing required columns: {missing_columns}")

        # Check for numeric columns
        numeric_columns = ['Quantity Available', 'Average Price']
        for col in numeric_columns:
            if not df[col].dtype.kind in 'biufc':  # numeric types
                try:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                except:
                    raise ValidationError(f"Column {col} contains invalid numeric data")

        return True

    @staticmethod
    def validate_api_response(response, expected_keys: list = None) -> bool:
        """Validate API response structure"""
        if response is None:
            raise ValidationError("API response is None")

        if expected_keys:
            if isinstance(response, dict):
                missing_keys = [key for key in expected_keys if key not in response]
                if missing_keys:
                    raise ValidationError(f"API response missing keys: {missing_keys}")
            else:
                raise ValidationError("Expected dict response")

        return True

    @staticmethod
    def validate_stock_symbol(symbol: str) -> str:
        """Validate and clean stock symbol"""
        if not symbol or not isinstance(symbol, str):
            raise ValidationError("Invalid symbol: must be non-empty string")

        symbol = symbol.strip().upper()
        if len(symbol) < 1:
            raise ValidationError("Symbol too short")

        return symbol


def safe_execute(func: Callable, error_message: str = "Operation failed",
                 default_return: Any = None) -> Any:
    """
    Safely execute a function with error handling

    Args:
        func: Function to execute
        error_message: Error message to display
        default_return: Default return value on error
    """
    try:
        return func()
    except Exception as e:
        logger.error(f"Safe execute failed: {str(e)}")
        st.error(f"{error_message}: {str(e)}")
        return default_return


def create_error_container():
    """Create a container for displaying errors"""
    return st.container()


class ProgressTracker:
    """Enhanced progress tracking with error reporting"""

    def __init__(self, total_items: int, description: str = "Processing"):
        self.total_items = total_items
        self.description = description
        self.current_item = 0
        self.errors = []
        self.progress_bar = st.progress(0, text=f"{description}...")
        self.error_container = st.container()

    def update(self, item_name: str = None, increment: int = 1):
        """Update progress"""
        self.current_item += increment
        progress = min(self.current_item / self.total_items, 1.0)

        status_text = f"{self.description}... ({self.current_item}/{self.total_items})"
        if item_name:
            status_text += f" - {item_name}"

        self.progress_bar.progress(progress, text=status_text)

    def add_error(self, error_message: str, item_name: str = None):
        """Add error to tracking"""
        error_info = {"message": error_message, "item": item_name}
        self.errors.append(error_info)

        with self.error_container:
            if len(self.errors) == 1:  # First error
                st.warning("Some items encountered errors:")
            st.caption(f"âŒ {item_name}: {error_message}" if item_name else f"âŒ {error_message}")

    def finish(self):
        """Clean up progress tracking"""
        self.progress_bar.empty()

        if self.errors:
            with st.expander(f"âš ï¸ {len(self.errors)} errors encountered", expanded=False):
                for error in self.errors:
                    st.text(f"â€¢ {error['item']}: {error['message']}" if error['item']
                            else f"â€¢ {error['message']}")


# Decorator for progress tracking
def with_progress(total_items: int, description: str = "Processing"):
    """Decorator to add progress tracking to functions"""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracker = ProgressTracker(total_items, description)
            try:
                result = func(tracker, *args, **kwargs)
                tracker.finish()
                return result
            except Exception as e:
                tracker.add_error(str(e))
                tracker.finish()
                raise

        return wrapper

    return decorator