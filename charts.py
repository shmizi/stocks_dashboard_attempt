# charts.py — Altair charts for the dashboard.
#
# Colour rules (kept deliberately small):
#   * magnitude (allocation, value)  → one hue, blue
#   * a second series for context     → muted gray (emphasis, not a rainbow)
#   * good / warning / critical       → status colours, always with a label
# Chrome (axes, grid, text) is left to Streamlit's own Altair theme so charts
# match whichever theme the app is rendered in.
from typing import Dict, Optional

import altair as alt
import pandas as pd
import streamlit as st

_LIGHT = {
    'series':   '#2a78d6',
    'muted':    '#898781',
    'good':     '#0ca30c',
    'warning':  '#fab219',
    'critical': '#d03b3b',
}
_DARK = {
    'series':   '#3987e5',
    'muted':    '#898781',
    'good':     '#0ca30c',
    'warning':  '#fab219',
    'critical': '#d03b3b',
}


def palette() -> Dict[str, str]:
    try:
        return _DARK if st.context.theme.type == "dark" else _LIGHT
    except Exception:
        return _LIGHT


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

def allocation_chart(display_df: pd.DataFrame, by: str = "Symbol",
                     concentration_threshold: float = 15.0) -> Optional[alt.Chart]:
    """
    Horizontal bars of current value share. `by` is "Symbol" or "Industry".
    Bars above the concentration threshold are flagged in the warning colour
    (a status, so it also gets a text label — never colour alone).
    """
    df = display_df.reset_index()
    if 'Current Value' not in df or df['Current Value'].sum() <= 0:
        return None

    if by == "Industry":
        grouped = (df.groupby('Industry', dropna=False)['Current Value']
                     .sum().reset_index().rename(columns={'Industry': 'Label'}))
        grouped['Label'] = grouped['Label'].fillna('N/A')
        grouped['Detail'] = df.groupby('Industry', dropna=False)['Symbol'] \
                              .apply(lambda s: ', '.join(s)).values
    else:
        grouped = df[['Symbol', 'Company', 'Current Value']].rename(
            columns={'Symbol': 'Label', 'Company': 'Detail'})

    total = grouped['Current Value'].sum()
    grouped['Weight'] = grouped['Current Value'] / total * 100
    grouped['Concentrated'] = grouped['Weight'] >= concentration_threshold
    grouped['Flag'] = grouped['Concentrated'].map({True: f'≥ {concentration_threshold:.0f}%', False: ''})
    grouped = grouped.sort_values('Weight', ascending=False)
    order = grouped['Label'].tolist()     # explicit order: sort='-x' is ignored in layered charts

    p = palette()
    height = max(140, 26 * len(grouped) + 30)

    base = alt.Chart(grouped).encode(
        y=alt.Y('Label:N', sort=order, title=None,
                axis=alt.Axis(labelLimit=180, labelOverlap=False)),
        x=alt.X('Weight:Q', title='Share of portfolio value (%)',
                axis=alt.Axis(format='.0f', grid=True)),
        tooltip=[
            alt.Tooltip('Label:N', title=by),
            alt.Tooltip('Detail:N', title='Company' if by == 'Symbol' else 'Holdings'),
            alt.Tooltip('Current Value:Q', title='Value (₹)', format=',.0f'),
            alt.Tooltip('Weight:Q', title='Weight', format='.1f'),
        ],
    )

    bars = base.mark_bar(cornerRadiusEnd=4, height={'band': 0.7}).encode(
        color=alt.condition(
            alt.datum.Concentrated, alt.value(p['warning']), alt.value(p['series'])
        )
    )
    labels = base.mark_text(align='left', dx=4, fontSize=11).encode(
        text=alt.Text('Weight:Q', format='.1f'),
        color=alt.value(p['muted']),
    )
    flags = base.transform_filter(alt.datum.Concentrated).mark_text(
        align='left', dx=36, fontSize=11, fontWeight='bold'
    ).encode(text='Flag:N', color=alt.value(p['warning']))

    return (bars + labels + flags).properties(height=height)


# ---------------------------------------------------------------------------
# Price + Supertrend
# ---------------------------------------------------------------------------

def price_supertrend_chart(history: pd.DataFrame, supertrend: Optional[pd.DataFrame],
                           symbol: str, avg_price: Optional[float] = None,
                           months: int = 12) -> Optional[alt.Chart]:
    """
    Close price (blue) with the Supertrend line coloured by regime: green while
    it sits below price as support, red while it caps price as resistance.
    Optional dashed rule at the holding's average buy price.
    """
    if history is None or history.empty or 'Close' not in history:
        return None

    df = pd.DataFrame({'date': pd.to_datetime(history.index), 'close': history['Close'].astype(float).values})
    if supertrend is not None and not supertrend.empty:
        df['supertrend'] = supertrend['supertrend'].values
        df['direction'] = supertrend['direction'].values
    else:
        df['supertrend'] = None
        df['direction'] = None

    if months and months < 12:
        cutoff = df['date'].max() - pd.DateOffset(months=months)
        df = df[df['date'] >= cutoff]

    df = df.dropna(subset=['close'])
    if df.empty:
        return None

    p = palette()

    # Break the Supertrend line at every regime flip so segments don't join across gaps
    df['regime'] = df['direction'].map({1: 'ST support ▲', -1: 'ST resistance ▼'})
    df['segment'] = (df['direction'] != df['direction'].shift()).cumsum()

    hover = alt.selection_point(fields=['date'], nearest=True, on='mouseover', empty=False, clear='mouseout')

    x = alt.X('date:T', title=None, axis=alt.Axis(format='%b %y', grid=False))
    y_domain = [float(df[['close', 'supertrend']].min().min()) * 0.97,
                float(df[['close', 'supertrend']].max().max()) * 1.03]
    y = alt.Y('close:Q', title='₹', scale=alt.Scale(domain=y_domain, nice=False))

    close_line = alt.Chart(df).mark_line(strokeWidth=2, color=p['series']).encode(x=x, y=y)

    st_line = alt.Chart(df.dropna(subset=['supertrend'])).mark_line(strokeWidth=2).encode(
        x=x,
        y=alt.Y('supertrend:Q'),
        detail='segment:N',
        color=alt.Color('regime:N', title='Supertrend',
                        scale=alt.Scale(domain=['ST support ▲', 'ST resistance ▼'],
                                        range=[p['good'], p['critical']]),
                        legend=alt.Legend(orient='top', direction='vertical', title=None,
                                          labelLimit=160, padding=0)),
    )

    layers = [close_line, st_line]

    if avg_price and avg_price > 0 and y_domain[0] <= avg_price <= y_domain[1]:
        ref = pd.DataFrame({'y': [avg_price], 'x': [df['date'].max()],
                            'label': [f'avg buy ₹{avg_price:,.0f}']})
        rule = alt.Chart(ref).mark_rule(strokeDash=[4, 4], color=p['muted'], strokeWidth=1).encode(y='y:Q')
        rule_text = alt.Chart(ref).mark_text(align='right', dx=-2, dy=-7, fontSize=10, color=p['muted']) \
            .encode(x='x:T', y='y:Q', text='label:N')
        layers += [rule, rule_text]

    # Crosshair + tooltip
    selectors = alt.Chart(df).mark_point(opacity=0).encode(x=x, tooltip=[
        alt.Tooltip('date:T', title='Date', format='%d %b %Y'),
        alt.Tooltip('close:Q', title='Close', format=',.2f'),
        alt.Tooltip('supertrend:Q', title='Supertrend', format=',.2f'),
        alt.Tooltip('regime:N', title='Regime'),
    ]).add_params(hover)
    points = close_line.mark_point(size=60, filled=True, color=p['series']).transform_filter(hover)
    crosshair = alt.Chart(df).mark_rule(color=p['muted'], strokeWidth=1).encode(x=x).transform_filter(hover)

    layers += [crosshair, points, selectors]

    return alt.layer(*layers).properties(height=280, title=f"{symbol} — 1 year").interactive(bind_y=False)


# ---------------------------------------------------------------------------
# Portfolio value over time
# ---------------------------------------------------------------------------

def history_chart(history_df: pd.DataFrame) -> Optional[alt.Chart]:
    """Current value (blue) vs invested (gray) per refresh-day. Two series → legend."""
    if history_df is None or history_df.empty or len(history_df) < 1:
        return None

    df = history_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    long = df.melt(id_vars=['date'], value_vars=['current_value', 'invested'],
                   var_name='series', value_name='value')
    long['series'] = long['series'].map({'current_value': 'Current value', 'invested': 'Invested'})

    p = palette()
    hover = alt.selection_point(fields=['date'], nearest=True, on='mouseover', empty=False, clear='mouseout')

    x = alt.X('date:T', title=None, axis=alt.Axis(format='%d %b', grid=False))
    lines = alt.Chart(long).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
        x=x,
        y=alt.Y('value:Q', title='₹', axis=alt.Axis(format='~s')),
        color=alt.Color('series:N', title=None,
                        scale=alt.Scale(domain=['Current value', 'Invested'],
                                        range=[p['series'], p['muted']]),
                        legend=alt.Legend(orient='top', direction='horizontal')),
        tooltip=[
            alt.Tooltip('date:T', title='Date', format='%d %b %Y'),
            alt.Tooltip('series:N', title='Series'),
            alt.Tooltip('value:Q', title='₹', format=',.0f'),
        ],
    )
    crosshair = alt.Chart(long).mark_rule(color=p['muted'], strokeWidth=1).encode(x=x) \
        .transform_filter(hover)
    selectors = alt.Chart(long).mark_point(opacity=0).encode(x=x).add_params(hover)

    return (lines + crosshair + selectors).properties(height=220)
