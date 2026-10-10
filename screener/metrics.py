"""Turn raw Yahoo data for one symbol into screening metrics and an entry ceiling.

All amounts are in the issuer's reporting currency. Two equity values are computed:
  reasonable case: earnings grow at the estimate (capped at the return on new capital), fading to the
                   currency's terminal growth; reinvestment at the current return on capital; discounted at
                   the currency hurdle (risk-free + equity risk premium). This is the entry ceiling.
  adverse case:    lower growth; the return on new capital fades to the hurdle (excess returns competed away);
                   cyclicals on the trough margin; discounted at the floor rate. Reported as a separate downside
                   check, not mixed into the ceiling: taking the lower of the two stacked every adverse assumption
                   onto the timing signal and made the ceiling a stress-test price for most names.
Entry score = 100 x (1 - market cap / ceiling): +20 means the price is 20% below the ceiling.
Adverse score = the same formula against the adverse value.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import valuation as V
from .fetch import FX, major_currency

FINANCIAL_INDUSTRIES = ("Banks", "Insurance - ", "Asset Management", "Capital Markets", "Mortgage Finance",
                        "Credit Services", "Financial Conglomerates", "Shell Companies")
CYCLICAL_SECTORS = {"Basic Materials", "Energy"}
CYCLICAL_INDUSTRIES = {"Semiconductors", "Semiconductor Equipment & Materials", "Auto Manufacturers",
                       "Farm & Heavy Construction Machinery", "Airlines", "Marine Shipping", "Steel",
                       "Auto Parts", "Trucking", "Railroads", "Building Materials", "Residential Construction",
                       "Lumber & Wood Production", "Agricultural Inputs", "Recreational Vehicles"}

ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross": ["Gross Profit"],
    "ebit": ["Operating Income", "EBIT"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "pretax": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "da": ["Reconciled Depreciation", "Depreciation And Amortization", "Depreciation Amortization Depletion"],
    "ocf": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure"],
    "sbc": ["Stock Based Compensation"],
    "shares_avg": ["Diluted Average Shares", "Basic Average Shares"],
    "shares_out": ["Ordinary Shares Number", "Share Issued"],
    "debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "minority": ["Minority Interest"],
    "assets": ["Total Assets"],
    "goodwill": ["Goodwill"],
}
DEBT_LIKE_ROWS = ("Total Debt", "Long Term Debt", "Current Debt", "Long Term Debt And Capital Lease Obligation",
                  "Current Debt And Capital Lease Obligation", "Net Debt")


def _row(df: pd.DataFrame | None, key: str) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for name in ROWS[key]:
        if name in df.index:
            s = pd.to_numeric(df.loc[name], errors="coerce")
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            if s.notna().any():
                return s
    return None


def _latest(df, key) -> float:
    s = _row(df, key)
    if s is None:
        return math.nan
    s = s.dropna()
    return float(s.iloc[0]) if len(s) else math.nan  # Yahoo orders columns newest first


def _annual(df, key) -> pd.Series:
    """Annual series oldest -> newest (NaN-dropped)."""
    s = _row(df, key)
    if s is None:
        return pd.Series(dtype=float)
    s = s.dropna()
    return s.sort_index()


def _finite(s: pd.Series) -> pd.Series:
    """Drop NaN and +/-inf (a zero denominator year)."""
    return s.replace([np.inf, -np.inf], np.nan).dropna()


def _nz(x) -> float:
    return 0.0 if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _isnum(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def _date(df) -> pd.Timestamp | None:
    return pd.Timestamp(df.columns[0]) if df is not None and not df.empty else None


def _cagr(series: pd.Series, max_years: int = 3) -> float:
    s = series.dropna()
    if len(s) < 2:
        return math.nan
    s = s.iloc[-(max_years + 1):]
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    years = (s.index[-1] - s.index[0]).days / 365.25
    if first <= 0 or last <= 0 or years < 0.9:
        return math.nan
    return (last / first) ** (1 / years) - 1


UNIT_TOLERANCE = 0.15  # holdings-file euro price vs Yahoo's price at today's FX; beyond this the units differ (GDR ratio)


def cost_basis(price: float, ceiling_px: float, quote_ccy: str, shares: float, cost_eur: float,
               value_eur: float, fx) -> dict:
    """Your average buy price against today's max entry price, in the quote currency at today's FX.

    cost_px = euro cost per share converted at today's rate, so the comparison is "is the business worth more today,
    in euro, than the euro you paid" (currency moves since purchase included). cost_score uses the entry-score formula.
    Dropped with a flag when the holdings file's euro price per share and Yahoo's price disagree beyond UNIT_TOLERANCE."""
    if not (_isnum(cost_eur) and cost_eur > 0 and _isnum(shares) and shares > 0 and _isnum(price) and price > 0):
        return {}
    rate = fx.rate(quote_ccy, "EUR") if quote_ccy else None
    if not rate:
        return {"cost_flag": f"no FX rate {quote_ccy}->EUR"}
    if _isnum(value_eur) and value_eur > 0 and abs((value_eur / shares) / (price * rate) - 1) > UNIT_TOLERANCE:
        return {"cost_flag": "holdings-file and Yahoo prices disagree (units or listing); cost score skipped"}
    cost_px = cost_eur / shares / rate
    return {"cost_px": cost_px, "cost_score": entry_score(cost_px, ceiling_px) if _isnum(ceiling_px) else math.nan}


def entry_score(mcap: float, ceiling: float) -> float:
    """100 x (1 - price / ceiling). NaN when there is no positive ceiling."""
    if not _isnum(ceiling) or ceiling <= 0 or not _isnum(mcap) or mcap <= 0 or math.isinf(ceiling):
        return math.nan
    return max(-100.0, 100.0 * (1.0 - mcap / ceiling))  # floored: beyond 2x the ceiling the exact gap says nothing


def classify(info: dict, override: str | None) -> str:
    if override:
        return override
    if (info.get("quoteType") or "EQUITY") != "EQUITY":
        return "fund"
    industry, sector = info.get("industry") or "", info.get("sector") or ""
    if industry.startswith(FINANCIAL_INDUSTRIES) and industry != "Insurance Brokers":
        return "financial"
    if sector in CYCLICAL_SECTORS or industry in CYCLICAL_INDUSTRIES:
        return "cyclical"
    return "operating"


def compute(data: dict, price_now: float | None, fx: FX, cfg: dict, class_override: str | None) -> dict:
    vcfg = cfg["valuation"]
    info = data.get("info") or {}
    flags: list[str] = []
    blockers: list[str] = []  # reasons the name cannot get an entry ceiling
    m: dict = {"symbol": data["symbol"], "yahoo_name": info.get("longName") or info.get("shortName") or "",
               "sector": info.get("sector") or "", "industry": info.get("industry") or "",
               "country": info.get("country") or "", "not_rankable": ""}
    cls = classify(info, class_override)
    m["class"] = cls
    if cls == "fund":
        m["flags"] = "fund/ETF - not screened"
        return m

    # ---------- market data (quote currency -> financial currency)
    q_ccy, q_div = major_currency(info.get("currency"))
    f_ccy = major_currency(info.get("financialCurrency") or info.get("currency"))[0]
    info_price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    mcap_q = info.get("marketCap")
    shares_info = info.get("sharesOutstanding")
    if mcap_q and info_price and shares_info:
        ratio = mcap_q / (info_price * shares_info)  # detect market cap reported in minor units
        if q_div == 100 and ratio > 50:
            mcap_q /= 100
    elif not mcap_q and info_price and shares_info:
        mcap_q = info_price * shares_info / q_div  # e.g. GDRs without a Yahoo market cap
        flags.append("mcap = price x shares")
    if price_now and info_price and mcap_q:
        mcap_q = mcap_q * price_now / info_price  # refresh cached market cap with the latest close
    fx_q_to_f = fx.rate(q_ccy, f_ccy) if q_ccy and f_ccy else None
    if q_ccy != f_ccy:
        flags.append(f"fx {q_ccy}->{f_ccy}")
    if not mcap_q or fx_q_to_f is None:
        m["flags"] = "no market cap or FX"
        m["not_rankable"] = "no market cap or FX"
        return m
    mcap = mcap_q * fx_q_to_f
    m["currency"] = f_ccy
    m["mcap_fc"] = mcap
    eur = fx.rate(f_ccy, "EUR")
    m["mcap_eur_bn"] = mcap * eur / 1e9 if eur else math.nan
    m["price"] = (price_now or info_price or math.nan) / q_div
    m["quote_ccy"] = q_ccy

    # ---------- required return and terminal growth for the reporting currency
    caps: list[str] = []  # parameters that bound this name's result (visibility, not a gate)
    all_rates = cfg.get("rates") or {}
    rates = all_rates.get(f_ccy)
    base_rates = all_rates.get(vcfg.get("base_currency", "EUR"))
    if rates is None:
        blockers.append(f"no rates for {f_ccy} in config [rates]")
        hurdle = floor = gT = math.nan
    else:
        gT = rates["g"]
        crp = rates.get("crp", 0.0)  # country risk premium on top of the market premium, in both cases
        least = gT + vcfg.get("min_rate_over_terminal_growth", 0.02)  # the terminal model needs r - g > 0
        hurdle = max(rates["rf"] + vcfg["equity_risk_premium"] + crp, least)
        if hurdle < vcfg.get("min_hurdle", 0.0):
            hurdle = vcfg["min_hurdle"]
            caps.append("min hurdle")
        floor = min(max(rates["rf"] + vcfg.get("adverse_return_spread", 0.0) + crp, least), hurdle)
    m.update(hurdle=hurdle, floor_rate=floor, terminal_growth=gT)

    # ---------- statements: one source per statement, so fields within a statement share a period
    ti, tc = data.get("ttm_income_stmt"), data.get("ttm_cashflow")
    ai, ac, ab = data.get("income_stmt"), data.get("cashflow"), data.get("balance_sheet")
    qb = data.get("quarterly_balance_sheet")
    bal = qb if qb is not None and not qb.empty else ab
    inc = ti if _row(ti, "revenue") is not None and _row(ti, "ebit") is not None else ai
    if inc is ai and ai is not None and not ai.empty:
        flags.append("income: annual not TTM")
    cfs = tc if _row(tc, "ocf") is not None and _row(tc, "capex") is not None else ac
    if cfs is ac and ac is not None and not ac.empty:
        flags.append("cash flow: annual not TTM")

    rev, ebit, ni, gross = (_latest(inc, k) for k in ("revenue", "ebit", "net_income", "gross"))
    pretax, tax = _latest(inc, "pretax"), _latest(inc, "tax")
    ocf, capex, da, sbc = (_latest(cfs, k) for k in ("ocf", "capex", "da", "sbc"))
    if math.isnan(sbc):
        if cls not in ("financial", "holdco"):
            flags.append("no SBC data (FCF not SBC-adjusted)")
        sbc = 0.0
    tax_rate = min(max(tax / pretax, 0.0), 0.35) if pretax and pretax > 0 and not math.isnan(tax) else 0.21

    debt, cash = _latest(bal, "debt"), _latest(bal, "cash")
    equity, minority, assets = _latest(bal, "equity"), _nz(_latest(bal, "minority")), _latest(bal, "assets")
    if math.isnan(debt):
        has_debt_rows = bal is not None and not bal.empty and any(r in bal.index for r in DEBT_LIKE_ROWS)
        if not has_debt_rows and not math.isnan(assets) and not math.isnan(equity):
            debt = 0.0  # Yahoo omits every debt row for debt-free issuers
            flags.append("no debt rows: assumed debt-free")
        else:
            blockers.append("no debt figure")
    if math.isnan(cash):
        blockers.append("no cash figure")
    net_debt = debt - cash  # NaN if either is missing
    ev = mcap + net_debt + minority
    m.update(revenue=rev, ebit=ebit, net_income=ni, ev_fc=ev, net_debt_fc=net_debt)

    today = pd.Timestamp(datetime.now(timezone.utc).date())
    core = [d for d in (_date(inc), _date(bal)) if d is not None]  # statements the ceiling depends on
    periods = core + ([_date(cfs)] if _date(cfs) is not None else [])
    if periods:
        m["data_as_of"] = min(periods).date().isoformat()
    if core and (today - min(core)).days > vcfg["stale_after_days"]:
        flags.append("stale financials")
    elif _date(cfs) is not None and (today - _date(cfs)).days > vcfg["stale_after_days"]:
        flags.append("cash flow stale")
    ttm_date = _date(inc)

    # ---------- annual history
    a_rev, a_ebit, a_ni = _annual(ai, "revenue"), _annual(ai, "ebit"), _annual(ai, "net_income")
    a_eq, a_debt, a_cash, a_gw = _annual(ab, "equity"), _annual(ab, "debt"), _annual(ab, "cash"), _annual(ab, "goodwill")
    if len(a_rev) < 3:
        flags.append("short history")

    fcf = ocf + capex if not (math.isnan(ocf) or math.isnan(capex)) else math.nan
    fcf_adj = fcf - sbc if not math.isnan(fcf) else math.nan

    # growth inputs
    rev_cagr = _cagr(a_rev)
    if not math.isnan(rev) and len(a_rev) >= 1 and ttm_date is not None and ttm_date > a_rev.index[-1]:
        rev_cagr = _cagr(pd.concat([a_rev, pd.Series([rev], index=[ttm_date])]))
    analyst = math.nan
    re_ = data.get("revenue_estimate")
    if isinstance(re_, pd.DataFrame) and "growth" in re_.columns:
        for period in ("+1y", "0y"):
            if period in re_.index and pd.notna(re_.loc[period, "growth"]):
                analyst = float(re_.loc[period, "growth"])
                break
    growth_inputs = [g for g in (rev_cagr, analyst) if not math.isnan(g)]
    g_raw = float(np.mean(growth_inputs)) if growth_inputs else math.nan
    g_est = float(np.clip(g_raw, vcfg["growth_floor"], vcfg["growth_cap"])) if growth_inputs else math.nan
    if g_raw == g_raw and g_raw > vcfg["growth_cap"]:
        caps.append("growth cap")
    elif g_raw == g_raw and g_raw < vcfg["growth_floor"]:
        caps.append("growth floor")
    m.update(rev_cagr_3y=rev_cagr, analyst_growth=analyst)

    sh = _annual(ai, "shares_avg")
    m["share_cagr"] = _cagr(sh) if len(sh) >= 2 else math.nan
    m["evs"] = ev / rev if rev and rev > 0 and not math.isnan(ev) else math.nan
    m["evs_vs_hist"] = (_evs_vs_history(data, mcap, price_now or info_price, a_rev, ab, ai, m["evs"])
                        if cls in ("operating", "cyclical") else math.nan)

    # ---------- base earnings and return on capital per model family
    years, R_cap = vcfg["years"], vcfg["return_cap"]
    base = base_adv = R0 = R_avg = math.nan
    if cls in ("financial", "holdco"):
        industry = m["industry"]
        if industry.startswith("Banks"):
            fin_type = "bank"
        elif industry.startswith("Insurance"):
            fin_type = "insurer"
        elif class_override == "financial":
            fin_type = "platform"  # fintech / marketplace hybrid forced to the equity model by the override file
        else:
            fin_type = "other"
        m["fin_type"] = fin_type
        base = base_adv = ni
        if fin_type == "insurer" and vcfg.get("insurer_smooth_earnings", False) and len(a_ni) >= 2 and ni == ni:
            smoothed = float(a_ni.iloc[-3:].mean())  # a catastrophe-light year is not the base
            if smoothed < ni:
                base = base_adv = smoothed
                flags.append("insurer: 3y average earnings used")
        value_base, bridge = mcap, 0.0
        m["earnings_yield"] = ni / mcap if ni == ni else math.nan
        m["book_yield"] = equity / mcap if equity and equity > 0 else math.nan
        m["roe"] = ni / equity if equity and equity > 0 else math.nan
        roe_hist = _finite(a_ni / a_eq.reindex(a_ni.index))
        m["roe_stability"] = float(roe_hist.std()) if len(roe_hist) >= 3 else math.nan
        m["equity_to_assets"] = equity / assets if assets and assets > 0 else math.nan
        R0 = m["roe"]
        R_avg = float(roe_hist.iloc[-3:].mean()) if len(roe_hist) >= 2 else math.nan
        if not (equity and equity > 0):
            blockers.append("no positive book equity")
        if fin_type == "bank" and m["equity_to_assets"] == m["equity_to_assets"] and \
                m["equity_to_assets"] < vcfg.get("bank_min_equity_to_assets", 0.04):
            flags.append(f"thin capital (equity/assets < {vcfg.get('bank_min_equity_to_assets', 0.04):.0%})")
        if fin_type == "platform":
            flags.append("platform: growth capped at ROE on cash-inflated equity; read the score as a conservative floor")
        loss_making = math.isnan(ni) or ni <= 0
    else:
        value_base, bridge = ev, _nz(net_debt) + minority
        ebit_used = ebit_adv = ebit
        if cls == "cyclical" and len(a_rev) >= 3 and not math.isnan(rev) and rev > 0:
            margins = list((a_ebit / a_rev.reindex(a_ebit.index)).dropna())
            if not math.isnan(ebit):
                margins.append(ebit / rev)
            if len(margins) >= 3:
                mid = float(np.median(margins))
                adv_margin = min(margins) if vcfg.get("cyclical_adverse_margin", "median") == "min" else mid
                ebit_used = mid * rev
                ebit_adv = adv_margin * min(rev, float(np.mean(list(a_rev.iloc[-3:]) + [rev])))
                flags.append("EBIT normalised (median margin; adverse: %s margin)" % ("trough" if adv_margin != mid or vcfg.get("cyclical_adverse_margin") == "min" else "median"))
        base, base_adv = ebit_used * (1 - tax_rate), ebit_adv * (1 - tax_rate)
        nopat_ttm = ebit * (1 - tax_rate) if ebit == ebit else math.nan
        # invested capital = equity + debt - cash, averaged over the last two year-ends when available.
        # The return on NEW capital excludes goodwill: acquisition premiums are sunk and do not fund organic growth.
        a_debt_f = a_debt.reindex(a_eq.index).fillna(0.0) if not math.isnan(debt) else a_debt.reindex(a_eq.index)
        inv_hist = (a_eq + a_debt_f - a_cash.reindex(a_eq.index)).dropna()
        gw_hist = a_gw.reindex(inv_hist.index).fillna(0.0)
        goodwill = _nz(_latest(bal, "goodwill"))
        invested = _nz(equity) + _nz(debt) - _nz(cash)
        invested_ex = invested - goodwill
        if len(inv_hist) >= 2 and inv_hist.iloc[-1] > 0 and inv_hist.iloc[-2] > 0:
            invested = float(inv_hist.iloc[-2:].mean())
            invested_ex = float((inv_hist - gw_hist).iloc[-2:].mean())
        m["roic"] = nopat_ttm / invested if invested > 0 and nopat_ttm == nopat_ttm else math.nan
        m["roic_ex_goodwill"] = nopat_ttm / invested_ex if invested_ex > 0 and nopat_ttm == nopat_ttm else math.nan
        R0 = m["roic_ex_goodwill"]
        if invested_ex <= 0 and ebit == ebit and ebit > 0:
            R0 = R_cap
            flags.append("invested capital ex goodwill <= 0: reinvestment at cap")
            caps.append("invested <= 0")
        inv_ex_hist = (inv_hist - gw_hist)
        roic_hist = _finite(a_ebit.reindex(inv_ex_hist.index) * (1 - tax_rate) / inv_ex_hist.where(inv_ex_hist > 0))
        R_avg = float(roic_hist.iloc[-3:].mean()) if len(roic_hist) >= 2 else math.nan
        m["nopat_yield"] = base / ev if ev > 0 and base == base else math.nan
        m["fcf_yield"] = fcf_adj / ev if ev > 0 and fcf_adj == fcf_adj else math.nan
        m["ebit_yield"] = ebit / ev if ev > 0 and ebit == ebit else math.nan
        m["gross_profitability"] = gross / assets if assets and assets > 0 and gross == gross else math.nan
        margins_hist = _finite(a_ebit / a_rev.reindex(a_ebit.index))
        m["margin_stability"] = float(margins_hist.std()) if len(margins_hist) >= 3 else math.nan
        m["cash_conversion"] = float(np.clip(fcf / ni, -1, 2)) if ni and ni > 0 and fcf == fcf else math.nan
        ebitda = _nz(ebit) + _nz(da)
        m["net_debt_ebitda"] = (net_debt / ebitda if ebitda > 0 and net_debt == net_debt
                                else (math.nan if net_debt != net_debt or net_debt <= 0 else 99.0))
        if fcf_adj == fcf_adj and fcf_adj < 0:
            flags.append("negative FCF-SBC (TTM)")
        loss_making = math.isnan(ebit) or ebit <= 0
        if loss_making:
            m["class"] = cls = "preprofit"
            burn = -(fcf_adj if fcf_adj == fcf_adj else 0)
            m["cash_runway_years"] = cash / burn if burn > 0 and cash == cash else math.nan

    for y in ("fcf_yield", "earnings_yield", "nopat_yield"):
        if m.get(y, 0) == m.get(y, 0) and (m.get(y) or 0) > 0.25:
            flags.append(f"{y} > 25%: check one-offs")
    m["base_earnings"] = base
    if cls == "holdco":
        flags.append("holding company: value via NAV, not screened")
    if cls in ("holdco", "preprofit") or (cls == "financial" and loss_making):
        if cls == "financial":
            m["class"] = "preprofit"
        m["flags"] = "; ".join(flags)
        return m

    # ---------- scenarios
    if R0 == R0 and R0 > 0:
        if vcfg.get("return_smoothing", False) and R_avg == R_avg and R_avg > 0 and R_avg < R0:
            R0 = R_avg  # one strong year does not set ten years of reinvestment
            caps.append("R smoothed (3y avg)")
        if R0 > R_cap:
            R0 = R_cap
            caps.append("R cap")
    else:
        blockers.append("no return on capital")
    if g_est == g_est:
        g0 = g_est
    else:
        g0 = gT
        flags.append("no growth estimate: terminal growth assumed")
    if R0 == R0 and g0 == g0 and g0 > R0:
        g0 = R0
        flags.append("growth capped at return on capital")
        caps.append("growth <= R")
    adverse_inputs = [g for g in (rev_cagr, analyst, g0) if g == g]
    if not adverse_inputs:
        g_adv = math.nan
    elif vcfg.get("adverse_growth_mode", "half") == "haircut":
        g_adv = min(adverse_inputs) - vcfg["adverse_growth_haircut"]
    else:
        low = min(adverse_inputs)
        g_adv = 0.5 * low if low > 0 else low  # half the lowest input; a decline is kept, not halved
    if g_adv == g_adv and cls == "cyclical" and vcfg.get("cyclical_adverse_growth_at_terminal", False):
        g_adv = min(g_adv, gT)  # a cycle reverts to trend
    g_adv = float(np.clip(g_adv, vcfg["growth_floor"], R0 if R0 == R0 else g_adv)) if g_adv == g_adv else math.nan
    m.update(est_growth=g0, adverse_growth=g_adv, return_on_capital=R0, base_earnings_adverse=base_adv,
             reinvest_rate=(g0 / R0 if R0 == R0 and R0 > 0 and g0 == g0 else math.nan), caps_hit="; ".join(caps))
    if not (base == base and base > 0):
        blockers.append("no positive base earnings")
    if blockers:
        m["not_rankable"] = "; ".join(blockers)
        m["flags"] = "; ".join(flags)
        return m

    v_reas = V.present_value(base, g0, hurdle, gT, years, R0, R0) - bridge
    v_adv = V.present_value(base_adv, g_adv, floor, gT, years, R0, hurdle) - bridge
    ceiling = v_reas
    m.update(value_reasonable=v_reas, value_adverse=v_adv, entry_ceiling=ceiling)
    if not math.isfinite(ceiling):
        m["not_rankable"] = "ceiling not finite (rate at or below terminal growth)"
        m["flags"] = "; ".join(flags)
        return m
    if ceiling <= 0:
        m["not_rankable"] = (f"ceiling <= 0: at {R0:.0%} on new capital the business is worth less than its net debt "
                             f"in the reasonable case")
        m["flags"] = "; ".join(flags)
        return m
    m["entry_score"] = entry_score(mcap, ceiling)
    # downside check: equity worth nothing in the adverse case reads as the floor; an unbounded value as no score
    m["adverse_score"] = -100.0 if _isnum(v_adv) and v_adv <= 0 else entry_score(mcap, v_adv)
    px = m["price"]
    if px == px and mcap > 0:
        m["ceiling_px"] = px * ceiling / mcap
        m["reasonable_px"] = px * v_reas / mcap
        m["adverse_px"] = px * v_adv / mcap
    m["implied_return"] = V.implied_return(base, value_base, g0, gT, years, R0, R0)
    m["implied_growth"] = V.implied_growth(base, value_base, hurdle, gT, years, R0, R0)
    if m["implied_return"] is not None and base_rates is not None:
        # rate-differential conversion (uncovered parity): what the local return is worth in the base currency
        m["implied_return_base"] = m["implied_return"] - (rates["rf"] - base_rates["rf"])
    m["flags"] = "; ".join(flags)
    return m


def _evs_vs_history(data, mcap_now, price_now, a_rev, ab, ai, evs_now) -> float:
    """Current EV/Sales divided by the median EV/Sales at past fiscal year-ends (context only, not a gate).
    Historical market cap = current market cap x price ratio x share-count ratio (ADR-ratio invariant)."""
    hist = data.get("history")
    if hist is None or hist.empty or not price_now or len(a_rev) < 2 or math.isnan(evs_now):
        return math.nan
    close = hist["Close"].dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    shares = _annual(ai, "shares_avg")
    debt, cash = _annual(ab, "debt"), _annual(ab, "cash")
    if len(shares) < 2:
        return math.nan
    s_now = float(shares.iloc[-1])
    vals = []
    for d, r in a_rev.items():
        d = pd.Timestamp(d)
        p = close[close.index <= d]
        if not len(p) or r <= 0 or d not in shares.index:
            continue
        mc = mcap_now * float(p.iloc[-1]) / price_now * float(shares[d]) / s_now
        nd = _nz(debt.get(d)) - _nz(cash.get(d))
        vals.append((mc + nd) / r)
    return evs_now / float(np.median(vals)) if len(vals) >= 2 else math.nan
