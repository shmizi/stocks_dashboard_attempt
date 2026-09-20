# screen_builder.py
import streamlit as st
import json
import uuid
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
 
 
class TimeFrame(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
 
 
class Operator(Enum):
    GREATER = ">"
    LESS = "<"
    EQUAL = "="
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    NOT_EQUAL = "!="
 
 
class PriceReference(Enum):
    CURRENT_CLOSE = "daily close"
    CURRENT_HIGH = "daily high"
    CURRENT_LOW = "daily low"
    CURRENT_OPEN = "daily open"
    WEEK_AGO_CLOSE = "1 week ago close"
    MONTH_AGO_CLOSE = "1 month ago close"
    HIGH_52W = "52 week high"
    LOW_52W = "52 week low"
 
 
class TechnicalIndicator(Enum):
    RSI = "rsi"
    MACD = "macd"
    SUPERTREND = "supertrend"
    EMA = "ema"
    SMA = "sma"
    BOLLINGER_UPPER = "bollinger upper"
    BOLLINGER_LOWER = "bollinger lower"
 
 
@dataclass
class ScreenerCriteria:
    """Individual screening criteria"""
    id: str
    name: str
    left_operand: str
    operator: str
    right_operand: str
    enabled: bool = True
 
    def to_chartink_clause(self) -> str:
        """Convert criteria to Chartink scan language"""
        return f"( {self.left_operand} {self.operator} {self.right_operand} )"
 
 
@dataclass
class ScreenerPreset:
    """Preset collection of screening criteria"""
    name: str
    description: str
    criteria: List[ScreenerCriteria]
    logic: str = "AND"  # AND or OR
 
    def to_chartink_scan(self) -> str:
        """Convert entire preset to Chartink scan clause"""
        if not self.criteria:
            return ""
 
        enabled_criteria = [c for c in self.criteria if c.enabled]
        if not enabled_criteria:
            return ""
 
        clauses = [c.to_chartink_clause() for c in enabled_criteria]
 
        if self.logic == "AND":
            combined = " and ".join(clauses)
        else:  # OR
            combined = " or ".join(clauses)
 
        return f"( {{cash}} ( {combined} ) )"
 
 
class ScreenerBuilder:
    """Build and manage custom screening criteria"""
 
    def __init__(self, config):
        self.config = config
        self.presets = self._load_presets()
 
    def _load_presets(self) -> Dict[str, ScreenerPreset]:
        """Load saved presets from config"""
        preset_data = self.config.get('screener_presets', {})
        presets = {}
 
        for name, data in preset_data.items():
            criteria = [ScreenerCriteria(**c) for c in data.get('criteria', [])]
            presets[name] = ScreenerPreset(
                name=data.get('name', name),
                description=data.get('description', ''),
                criteria=criteria,
                logic=data.get('logic', 'AND')
            )

        # Seed defaults on a fresh install only. Deleted presets stay deleted;
        # the user can bring defaults back with "Restore default presets".
        if not presets:
            presets = self._create_default_presets()
            self._save_presets(presets)

        return presets

    def restore_default_presets(self) -> int:
        """Add any default preset that is missing. Returns how many were added."""
        added = 0
        for key, preset in self._create_default_presets().items():
            if key not in self.presets:
                self.presets[key] = preset
                added += 1
        if added:
            self._save_presets(self.presets)
        return added
 
    def _create_default_presets(self) -> Dict[str, ScreenerPreset]:
        """Create default screening presets"""
        presets = {}
 
        # Weekly Breakout (original)
        presets['weekly_breakout'] = ScreenerPreset(
            name="Weekly Breakout",
            description="Stocks up 10%+ from 1 week ago, above Supertrend, good market cap",
            criteria=[
                ScreenerCriteria("wb1", "Weekly Gain 10%+", "daily close", ">=", "1 week ago close * 1.1"),
                ScreenerCriteria("wb2", "Above Supertrend", "daily close", ">", "daily supertrend( 10, 7 )"),
                ScreenerCriteria("wb3", "Market Cap > 500Cr", "market cap", ">", "500")
            ]
        )
 
        # Momentum Stocks
        presets['momentum'] = ScreenerPreset(
            name="Momentum Stocks",
            description="High momentum stocks with strong volume and RSI",
            criteria=[
                ScreenerCriteria("mom1", "Price > 20 EMA", "daily close", ">", "daily ema( 20 )"),
                ScreenerCriteria("mom2", "Volume > 2x Avg", "daily volume", ">", "daily sma( daily volume, 20 ) * 2"),
                ScreenerCriteria("mom3", "RSI > 60", "daily rsi( 14 )", ">", "60"),
                ScreenerCriteria("mom4", "Market Cap > 100Cr", "market cap", ">", "100")
            ]
        )
 
        # Value Picks
        presets['value'] = ScreenerPreset(
            name="Value Picks",
            description="Undervalued stocks with good fundamentals",
            criteria=[
                ScreenerCriteria("val1", "PE < 15", "pe", "<", "15"),
                ScreenerCriteria("val2", "ROE > 15%", "roe", ">", "15"),
                ScreenerCriteria("val3", "Debt to Equity < 0.5", "debt to equity", "<", "0.5"),
                ScreenerCriteria("val4", "Market Cap > 500Cr", "market cap", ">", "500")
            ]
        )
 
        # Breakout from Consolidation
        presets['consolidation_breakout'] = ScreenerPreset(
            name="Consolidation Breakout",
            description="Stocks breaking out from sideways consolidation",
            criteria=[
                ScreenerCriteria("cb1", "Close > 20 day high", "daily close", ">", "daily max( 20, daily high )"),
                ScreenerCriteria("cb2", "Volume > 1.5x avg", "daily volume", ">",
                                 "daily sma( daily volume, 10 ) * 1.5"),
                ScreenerCriteria("cb3", "RSI between 50-70", "daily rsi( 14 )", ">", "50"),
                ScreenerCriteria("cb4", "RSI upper limit", "daily rsi( 14 )", "<", "70"),
                ScreenerCriteria("cb5", "Market Cap > 200Cr", "market cap", ">", "200")
            ]
        )
 
        return presets
 
    def _save_presets(self, presets: Dict[str, ScreenerPreset]):
        """Save presets to config"""
        preset_data = {}
        for name, preset in presets.items():
            preset_data[name] = {
                'name': preset.name,
                'description': preset.description,
                'logic': preset.logic,
                'criteria': [asdict(c) for c in preset.criteria]
            }
 
        self.config.set('screener_presets', preset_data)
 
    def render_screener_builder_ui(self) -> Optional[str]:
        """
        Render the screener builder UI and return scan clause when "Run Scan" is pressed.

        Every edit (toggle, logic, add, delete) is persisted immediately. The app
        object is rebuilt on each Streamlit rerun, so anything held only in memory
        would be lost before the next click.
        """
        st.header("🔧 Custom Stock Screener")

        # Preset selection
        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            preset_names = list(self.presets.keys())
            if not preset_names:
                st.error("No presets available")
                if st.button("♻️ Restore default presets"):
                    self.restore_default_presets()
                    st.rerun()
                return None

            selected_preset_name = st.selectbox(
                "📋 Select Preset",
                preset_names,
                format_func=lambda k: self.presets[k].name,
                help="Choose a pre-built screening strategy or customize your own"
            )

        with col2:
            st.write("")  # align with selectbox
            if st.button("➕ New Preset", width="stretch"):
                self._show_new_preset_dialog()

        with col3:
            st.write("")
            if st.button("♻️ Restore defaults", width="stretch",
                         help="Re-add any built-in preset you deleted"):
                added = self.restore_default_presets()
                st.toast(f"Restored {added} preset(s)" if added else "All defaults already present")
                if added:
                    st.rerun()

        if selected_preset_name not in self.presets:
            return None

        selected_preset = self.presets[selected_preset_name]
        key_prefix = f"preset_{selected_preset_name}"   # keep widget state per-preset

        # Display preset info
        st.info(f"**{selected_preset.name}**: {selected_preset.description}")

        # Logic selection — persisted on change
        col1, col2 = st.columns([1, 3])
        with col1:
            logic = st.radio(
                "🔗 Combine criteria with:",
                ["AND", "OR"],
                index=0 if selected_preset.logic == "AND" else 1,
                key=f"{key_prefix}_logic",
                help="AND = All conditions must be true, OR = Any condition can be true"
            )
            if logic != selected_preset.logic:
                selected_preset.logic = logic
                self._save_presets(self.presets)

        # Criteria management
        st.subheader("📊 Screening Criteria")

        if not selected_preset.criteria:
            st.caption("No criteria yet — add one below.")

        criteria_to_remove = []
        changed = False
        for i, criteria in enumerate(selected_preset.criteria):
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([1, 3, 3, 1])

                with col1:
                    enabled = st.checkbox(
                        "✓",
                        value=criteria.enabled,
                        key=f"{key_prefix}_enable_{criteria.id}",
                        help="Enable/disable this criteria"
                    )
                    if enabled != criteria.enabled:
                        criteria.enabled = enabled
                        changed = True

                with col2:
                    st.text_input(
                        "Name",
                        value=criteria.name,
                        key=f"{key_prefix}_name_{criteria.id}",
                        disabled=True
                    )

                with col3:
                    st.code(criteria.to_chartink_clause().strip("() "), language=None)

                with col4:
                    if st.button("🗑️", key=f"{key_prefix}_delete_{criteria.id}", help="Delete criteria"):
                        criteria_to_remove.append(i)

        if criteria_to_remove:
            for i in reversed(criteria_to_remove):
                selected_preset.criteria.pop(i)
            self._save_presets(self.presets)
            st.rerun()

        if changed:
            self._save_presets(self.presets)

        # Add new criteria
        with st.expander("➕ Add New Criteria", expanded=False):
            self._render_add_criteria_form(selected_preset)

        # Advanced options
        with st.expander("🔧 Advanced Options", expanded=False):
            self._render_advanced_options()

        # Generate and display scan clause
        scan_clause = selected_preset.to_chartink_scan()

        if scan_clause:
            st.subheader("🔍 Generated Scan Clause")
            st.code(scan_clause, language=None)
        else:
            st.warning("Enable or add at least one criterion to generate a scan.")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🚀 Run Scan", type="primary", width="stretch",
                         disabled=not scan_clause):
                return scan_clause

        with col2:
            if st.button("🗑️ Delete Preset", type="secondary", width="stretch"):
                if len(self.presets) > 1:  # Don't delete last preset
                    del self.presets[selected_preset_name]
                    self._save_presets(self.presets)
                    st.toast("Preset deleted")
                    st.rerun()
                else:
                    st.error("Cannot delete the last preset")

        return None
 
    def _render_add_criteria_form(self, preset: ScreenerPreset):
        """Render form to add new criteria"""
        with st.form("add_criteria"):
            criteria_name = st.text_input("Criteria Name", placeholder="e.g., Strong Volume")
 
            col1, col2, col3 = st.columns(3)
 
            with col1:
                # Left operand (what we're comparing)
                left_type = st.selectbox(
                    "Compare",
                    ["Price", "Technical", "Fundamental", "Volume", "Custom"],
                    help="What type of value to compare"
                )
 
                if left_type == "Price":
                    left_options = [
                        "daily close", "daily high", "daily low", "daily open",
                        "1 week ago close", "1 month ago close",
                        "52 week high", "52 week low"
                    ]
                elif left_type == "Technical":
                    left_options = [
                        "daily rsi( 14 )", "daily ema( 20 )", "daily sma( 50 )",
                        "daily supertrend( 10, 7 )", "daily macd()",
                        "daily bollinger upper( 20, 2 )", "daily bollinger lower( 20, 2 )"
                    ]
                elif left_type == "Fundamental":
                    left_options = [
                        "pe", "pb", "roe", "roce", "debt to equity",
                        "current ratio", "market cap", "sales", "profit"
                    ]
                elif left_type == "Volume":
                    left_options = [
                        "daily volume", "daily sma( daily volume, 20 )",
                        "weekly volume", "monthly volume"
                    ]
                else:  # Custom
                    left_options = [""]
 
                if left_options[0]:
                    left_operand = st.selectbox("Left Value", left_options)
                else:
                    left_operand = st.text_input("Custom Expression", placeholder="daily close")
 
            with col2:
                operator = st.selectbox(
                    "Operator",
                    [">", "<", ">=", "<=", "=", "!="],
                    help="Comparison operator"
                )
 
            with col3:
                # Right operand (what we're comparing against)
                right_type = st.selectbox(
                    "Against",
                    ["Number", "Price", "Technical", "Percentage", "Custom"]
                )
 
                if right_type == "Number":
                    right_operand = str(st.number_input("Value", value=0.0))
                elif right_type == "Price":
                    price_options = [
                        "daily close", "daily high", "daily low",
                        "1 week ago close", "1 month ago close"
                    ]
                    right_operand = st.selectbox("Price Reference", price_options)
                elif right_type == "Technical":
                    tech_options = [
                        "daily ema( 20 )", "daily sma( 50 )", "daily sma( 200 )",
                        "daily supertrend( 10, 7 )", "daily rsi( 14 )"
                    ]
                    right_operand = st.selectbox("Technical Indicator", tech_options)
                elif right_type == "Percentage":
                    base_value = st.selectbox("Base Value", ["daily close", "1 week ago close", "daily ema( 20 )"])
                    pct_change = st.number_input("Percentage Change", value=10.0)
                    right_operand = f"{base_value} * {1 + pct_change / 100}"
                else:  # Custom
                    right_operand = st.text_input("Custom Value", placeholder="100")
 
            if st.form_submit_button("Add Criteria"):
                if criteria_name and left_operand and right_operand:
                    new_criteria = ScreenerCriteria(
                        id=f"c_{uuid.uuid4().hex[:8]}",   # never collides after deletes
                        name=criteria_name,
                        left_operand=left_operand,
                        operator=operator,
                        right_operand=right_operand
                    )
                    preset.criteria.append(new_criteria)
                    self._save_presets(self.presets)
                    st.toast(f"Added criteria: {criteria_name}")
                    st.rerun()
                else:
                    st.error("Please fill all fields")
 
    def _render_advanced_options(self):
        """Render advanced screening options"""
        st.markdown("**🎯 Quick Filters**")
 
        col1, col2 = st.columns(2)
 
        with col1:
            if st.button("Add Market Cap Filter"):
                st.info("Market cap filter templates coming soon!")
 
        with col2:
            if st.button("Add Sector Filter"):
                st.info("Sector filtering coming soon!")
 
        st.markdown("**📖 Chartink Documentation**")
        st.markdown("""
        **Common Chartink Expressions:**
        - `daily close > daily ema( 20 )` - Price above 20-day EMA
        - `daily volume > daily sma( daily volume, 10 ) * 2` - Volume 2x average
        - `daily rsi( 14 ) > 70` - RSI above 70 (overbought)
        - `market cap > 500` - Market cap above 500 crores
        - `pe < 25` - P/E ratio less than 25
        - `1 week ago close * 1.1` - 10% above last week's close
        """)
 
    def _show_new_preset_dialog(self):
        """
        Open a modal to create a new preset.

        Must be a dialog: a form rendered inline under `if st.button(...)` only
        exists on the run where the button was clicked, so its submit could never
        be processed.
        """
        builder = self

        @st.dialog("Create New Preset")
        def dialog():
            preset_name = st.text_input("Preset Name", placeholder="My Custom Strategy")
            preset_desc = st.text_area("Description", placeholder="Description of this screening strategy")

            if st.button("Create Preset", type="primary"):
                key = preset_name.strip().lower().replace(" ", "_")
                if not key:
                    st.error("Please enter a preset name")
                elif key in builder.presets:
                    st.error("Preset name already exists")
                else:
                    builder.presets[key] = ScreenerPreset(
                        name=preset_name.strip(),
                        description=preset_desc.strip(),
                        criteria=[]
                    )
                    builder._save_presets(builder.presets)
                    st.toast(f"Created preset: {preset_name}")
                    st.rerun()

        dialog()
 
    def get_preset_names(self) -> List[str]:
        """Get list of available preset names"""
        return list(self.presets.keys())
 
    def get_preset_scan_clause(self, preset_name: str) -> str:
        """Get scan clause for a specific preset"""
        if preset_name in self.presets:
            return self.presets[preset_name].to_chartink_scan()
        return ""
 
 
# Preset templates for easy sharing
PRESET_TEMPLATES = {
    "momentum_breakout": {
        "name": "Momentum Breakout",
        "description": "Stocks with strong momentum breaking to new highs",
        "logic": "AND",
        "criteria": [
            {"id": "mb1", "name": "New 20-day high", "left_operand": "daily close", "operator": ">",
             "right_operand": "daily max( 20, daily high )", "enabled": True},
            {"id": "mb2", "name": "Above 50 EMA", "left_operand": "daily close", "operator": ">",
             "right_operand": "daily ema( 50 )", "enabled": True},
            {"id": "mb3", "name": "Strong volume", "left_operand": "daily volume", "operator": ">",
             "right_operand": "daily sma( daily volume, 20 ) * 1.5", "enabled": True},
            {"id": "mb4", "name": "Market cap > 100Cr", "left_operand": "market cap", "operator": ">",
             "right_operand": "100", "enabled": True}
        ]
    }
}