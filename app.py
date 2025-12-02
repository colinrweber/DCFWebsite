import math
from typing import Dict, Optional

import streamlit as st
import yfinance as yf


def _normalize_label(label: str) -> str:
    """Normalize balance sheet labels for easier matching."""
    return label.strip().lower().replace(" ", "").replace("_", "")


def _extract_total_debt(balance_sheet) -> Optional[float]:
    if balance_sheet is None or balance_sheet.empty:
        return None

    debt_labels = {
        "totaldebt",
        "longtermdebt",
        "shortlongtermdebttotal",
        "longtermdebttotal",
        "shorttermdebttotal",
    }

    for raw_label in balance_sheet.index:
        normalized = _normalize_label(str(raw_label))
        if normalized in debt_labels:
            first_column = balance_sheet.columns[0]
            value = balance_sheet.loc[raw_label, first_column]
            return float(value) if not math.isnan(value) else None

    return None


def _get_first_balance_sheet(ticker: yf.Ticker):
    for frame in (ticker.balance_sheet, ticker.quarterly_balance_sheet):
        if frame is not None and not frame.empty:
            return frame
    return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_ticker_metrics(ticker_symbol: str) -> Dict[str, Optional[float]]:
    ticker = yf.Ticker(ticker_symbol)
    fast_info = ticker.fast_info or {}

    price = fast_info.get("last_price") or fast_info.get("previous_close")
    if price is None:
        recent = ticker.history(period="5d")
        if not recent.empty:
            price = float(recent["Close"].iloc[-1])

    shares_outstanding = fast_info.get("shares_outstanding")
    market_cap = fast_info.get("market_cap")
    if market_cap is None and price is not None and shares_outstanding:
        market_cap = price * shares_outstanding

    info = ticker.get_info() or {}
    beta = info.get("beta") or fast_info.get("beta")

    balance_sheet = _get_first_balance_sheet(ticker)
    total_debt = _extract_total_debt(balance_sheet)

    return {
        "price": price,
        "shares_outstanding": shares_outstanding,
        "market_cap": market_cap,
        "beta": beta,
        "total_debt": total_debt,
    }


def calculate_wacc(
    market_cap: float,
    total_debt: float,
    risk_free_rate: float,
    market_risk_premium: float,
    cost_of_debt: float,
    tax_rate: float,
    beta: float,
) -> Dict[str, float]:
    equity_value = market_cap
    debt_value = max(total_debt, 0)
    total_capital = equity_value + debt_value

    equity_weight = equity_value / total_capital if total_capital else 0
    debt_weight = debt_value / total_capital if total_capital else 0

    cost_of_equity = risk_free_rate + beta * market_risk_premium
    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)

    wacc = equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt

    return {
        "equity_weight": equity_weight,
        "debt_weight": debt_weight,
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "wacc": wacc,
    }


st.set_page_config(page_title="WACC Calculator", layout="centered")
st.title("Weighted Average Cost of Capital (WACC)")
st.write(
    "Enter a ticker and your assumptions to compute WACC. "
    "All rates are entered as percentages. Risk-free rate is manual (no FRED), "
    "and equity inputs leverage yfinance data."
)
st.caption(
    "yfinance calls are cached for 5 minutes to reduce Yahoo Finance throttling. "
    "If you get rate-limited, wait briefly or supply manual overrides."
)

with st.form("wacc_form"):
    ticker_symbol = st.text_input("Ticker", value="AAPL").strip().upper()
    risk_free_rate_pct = st.number_input(
        "Risk-free rate (%)", min_value=0.0, max_value=50.0, value=4.0, step=0.1
    )
    market_risk_premium_pct = st.number_input(
        "Market risk premium (%)", min_value=0.0, max_value=50.0, value=5.0, step=0.1
    )
    cost_of_debt_pct = st.number_input(
        "Cost of debt (%)", min_value=0.0, max_value=50.0, value=4.0, step=0.1
    )
    tax_rate_pct = st.number_input(
        "Marginal tax rate (%)", min_value=0.0, max_value=100.0, value=25.0, step=0.1
    )
    manual_market_cap = st.number_input(
        "Override market cap ($) [optional]",
        min_value=0.0,
        value=0.0,
        step=1_000_000.0,
        help=(
            "Use this if yfinance is rate limited or missing data. "
            "Leave at 0 to use the API value."
        ),
    )
    submitted = st.form_submit_button("Calculate WACC")

if submitted:
    if not ticker_symbol:
        st.error("Please enter a ticker symbol.")
    else:
        metrics = fetch_ticker_metrics(ticker_symbol)

        market_cap = manual_market_cap or metrics.get("market_cap")
        if market_cap is None or market_cap <= 0:
            st.error(
                "Unable to retrieve market capitalization for this ticker. "
                "If Yahoo Finance is rate limiting, try again in a minute or set an override."
            )
            st.info(
                "Tips: avoid rapid repeat requests, use a VPN/static IP to reduce throttling, "
                "or provide the market cap manually above."
            )
        else:
            beta = metrics.get("beta") or 1.0
            total_debt = metrics.get("total_debt") or 0.0

            wacc_inputs = calculate_wacc(
                market_cap=market_cap,
                total_debt=total_debt,
                risk_free_rate=risk_free_rate_pct / 100,
                market_risk_premium=market_risk_premium_pct / 100,
                cost_of_debt=cost_of_debt_pct / 100,
                tax_rate=tax_rate_pct / 100,
                beta=beta,
            )

            st.success(f"WACC for {ticker_symbol}: {wacc_inputs['wacc'] * 100:.2f}%")

            st.subheader("Inputs")
            st.write(
                f"Market cap: {market_cap:,.0f}\n\n"
                f"Total debt: {total_debt:,.0f}\n\n"
                f"Beta (yfinance, default 1 if missing): {beta:.2f}\n\n"
                f"Equity weight: {wacc_inputs['equity_weight']:.2%}\n\n"
                f"Debt weight: {wacc_inputs['debt_weight']:.2%}"
            )

            st.subheader("Cost Components")
            st.write(
                f"Cost of equity (CAPM): {wacc_inputs['cost_of_equity'] * 100:.2f}%\n\n"
                f"After-tax cost of debt: {wacc_inputs['after_tax_cost_of_debt'] * 100:.2f}%"
            )

            with st.expander("Raw yfinance fetch"):
                st.json(metrics)
