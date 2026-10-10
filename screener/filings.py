"""Optional SEC companyfacts evidence, preserving publication time and units.

An explicit issuer CIK and SEC_USER_AGENT are required. This is not a universal
XBRL mapper: custom concepts and segment/regulatory disclosures remain separate
issuer inputs. Conflicting facts are never averaged or selected for valuation.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request
import pandas as pd

# Standard whole-entity US-GAAP balance concepts only. Debt components are kept
# separate until the statement normalizer can establish a complete pair.
BALANCE_TAGS={
    "Assets":"Total Assets","StockholdersEquity":"Stockholders Equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest":"Total Equity Gross Minority Interest",
    "MinorityInterest":"Minority Interest","CashAndCashEquivalentsAtCarryingValue":"Cash And Cash Equivalents",
    "LongTermDebtCurrent":"Current Portion Of Long Term Debt","LongTermDebtNoncurrent":"Long Term Debt",
    "LongTermDebtAndFinanceLeaseObligationsCurrent":"Current Portion Of Long Term Debt And Finance Leases",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent":"Long Term Debt And Capital Lease Obligation",
    "Goodwill":"Goodwill","PreferredStockValue":"Preferred Stock Equity",
}

def balance_facts(payload:dict,currency:str,as_of:date) -> tuple[pd.DataFrame,list[dict]]:
    rows={};ledger=[]
    for tag,label in BALANCE_TAGS.items():
        facts=payload.get("facts",{}).get("us-gaap",{}).get(tag,{}).get("units",{}).get(currency,[])
        by_period={}
        for fact in facts:
            if fact.get("start") or not fact.get("filed") or not fact.get("end"):
                continue
            if date.fromisoformat(fact["filed"])>as_of or date.fromisoformat(fact["end"])>as_of:
                continue
            by_period.setdefault(fact["end"],[]).append(fact)
        for period,observations in by_period.items():
            newest=max(x["filed"] for x in observations)
            chosen=[x for x in observations if x["filed"]==newest]
            values={x["val"] for x in chosen}
            entry={"tag":tag,"period_end":period,"filed":newest,"currency":currency,
                   "accessions":sorted({x.get("accn","") for x in chosen}),"status":"selected" if len(values)==1 else "conflicting"}
            if len(values)==1:
                value=next(iter(values));rows.setdefault(label,{})[pd.Timestamp(period)]=value
                entry["value"]=value
            ledger.append(entry)
    return pd.DataFrame.from_dict(rows,orient="index").sort_index(axis=1,ascending=False),ledger

def enrich(data:dict,cik:str,cache_dir:Path,as_of:date|None=None) -> dict:
    as_of=as_of or date.today()
    if not str(cik).isdigit() or len(str(cik))>10:
        raise ValueError("SEC CIK must contain at most ten digits")
    agent=os.environ.get("SEC_USER_AGENT")
    if not agent:
        return {**data,"filing_error":"filing verification unavailable: SEC_USER_AGENT not configured"}
    url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    request=urllib.request.Request(url,headers={"User-Agent":agent,"Accept":"application/json"})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            raw=response.read()
        payload=json.loads(raw)
        if int(payload.get("cik",-1))!=int(cik):raise ValueError("SEC issuer identity mismatch")
        digest=hashlib.sha256(raw).hexdigest()
        destination=Path(cache_dir)/"filings"/str(int(cik))
        destination.mkdir(parents=True,exist_ok=True)
        file=destination/f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}-{digest[:12]}.json"
        with file.open('xb') as fh:fh.write(raw)
        currency=(data.get('info')or{}).get('financialCurrency')
        frame,ledger=balance_facts(payload,currency,as_of)
        return {**data,"filing_balance_sheet":frame,"filing_ledger":ledger,"filing_source":url,
                "filing_snapshot":str(file),"filing_sha256":digest,"filing_entity":payload.get('entityName')}
    except Exception as exc:
        return {**data,"filing_error":f"filing verification unavailable: {type(exc).__name__}: {exc}"}
