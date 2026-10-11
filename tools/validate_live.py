"""Representative live-provider validation, isolated from personal runs.

Fresh acquisition requires a new output directory. Replays use those immutable
provider records and the recorded FX observations; they never refresh markets.
This checks coverage and execution, not filing accuracy or predictive value.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import pickle
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from screener import fetch, inputs, metrics, scoring
from screener.__main__ import load_class_overrides

COHORT = {
    "ADBE": "US mature software",
    "NVDA": "US high-growth cyclical semiconductors",
    "KO": "US defensive consumer staples",
    "XOM": "US integrated energy cycle",
    "RIVN": "US loss-making manufacturer and funding needs",
    "SHOP.TO": "Canadian growth platform; CAD quote, USD statements",
    "TSM": "Taiwan semiconductor ADR",
    "2330.TW": "Taiwan local listing; ADR-equivalence control",
    "ASML.AS": "Dutch semiconductor capital-equipment cycle",
    "7203.T": "Japanese manufacturer with financing subsidiary",
    "RIO.L": "UK mining; pence quote, USD statements, dual-listed structure",
    "BHP.AX": "Australian mining; AUD quote, USD statements",
    "JPM": "US regulated bank",
    "PLD": "US property REIT",
    "NEE": "US utility with regulated and competitive businesses",
    "MUV2.DE": "German reinsurer",
    "0700.HK": "Chinese platform and investment portfolio; HKD quote, CNY accounts",
    "NU": "Brazil-focused financial platform; USD accounts and share-class scope",
}


class RecordedFX:
    def __init__(self, observations):
        self.saved = observations

    def rate(self, a, b):
        a, b = fetch.major_currency(a)[0], fetch.major_currency(b)[0]
        if a == b:
            return 1.0
        observation = self.saved.get(f"{a}/{b}")
        return observation["rate"] if observation else None

    def observations(self):
        return self.saved


def replay_record(record):
    path=Path(record["source_snapshot"])
    payload=path.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=record["source_sha256"]:
        raise ValueError("immutable snapshot SHA-256 differs from acquisition receipt")
    data=pickle.loads(payload)  # Only the user's own recorded provider snapshot.
    return {**data,"source_snapshot":str(path.resolve()),"source_sha256":record["source_sha256"],"cache_state":"replay"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--valuation-date", type=date.fromisoformat, required=True)
    parser.add_argument("--replay", type=Path, help="prior acquisition directory; no network")
    parser.add_argument("--symbols", nargs="+", help="Yahoo symbols; defaults to the representative cohort")
    parser.add_argument("--no-issuer-overrides", action="store_true", help="Evaluate general models without company-specific inputs or class overrides")
    args = parser.parse_args(argv)
    if any((args.output_dir / name).exists() for name in ("cache", "acquisition.json", "results.csv", "results.json")):
        parser.error("output already contains an acquisition or results; choose a new directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    cfg["issuer_inputs"] = inputs.load(ROOT / cfg["inputs"].get("valuation_overrides", "overrides/valuation.toml"))
    overrides = load_class_overrides(ROOT / cfg["inputs"]["classification_overrides"])
    if args.no_issuer_overrides:
        cfg['issuer_inputs']={}
        overrides={}
    source = args.replay or args.output_dir
    prior = json.loads((source / "acquisition.json").read_text()) if args.replay else None
    cohort = args.symbols or (list(prior["cohort"]) if prior else list(COHORT))
    fx = RecordedFX(prior["fx"]) if prior else fetch.FX()
    records, failures, acquisition = [], [], {}
    for symbol in cohort:
        print(f"{'Replay' if prior else 'Fetch'} {symbol}", flush=True)
        try:
            data = replay_record(prior["records"][symbol]) if prior else fetch.get(symbol, source / "cache", 0, 0, force=True)[0]
        except (KeyError,ValueError,OSError) as exc:
            failures.append({"symbol":symbol,"stage":"replay_integrity","error":str(exc)})
            continue
        if data is None:
            failures.append({"symbol": symbol, "stage": "acquisition", "error": "provider returned no record"})
            continue
        data["source_snapshot"]=str(Path(data["source_snapshot"]).resolve())
        acquisition[symbol] = {k: data.get(k) for k in ("fetched_at", "source_snapshot", "source_sha256", "errors")}
        try:
            m = metrics.compute(data, None, fx, cfg, overrides.get(symbol), valuation_date=args.valuation_date)
            records.append({"symbol": symbol, "name": "", "owned": False, "lists": "", **m})
        except Exception as exc:
            failures.append({"symbol": symbol, "stage": "compute", "error": f"{type(exc).__name__}: {exc}"})
    frame = scoring.score(pd.DataFrame(records), cfg) if records else pd.DataFrame()
    frame.to_csv(args.output_dir / "results.csv", index=False)
    (args.output_dir / "results.json").write_text(frame.to_json(orient="records", indent=2), encoding="utf-8")
    receipt = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "valuation_date": str(args.valuation_date),
               "mode": "replay" if prior else "live", "cohort": {s: COHORT.get(s,'additional validation issuer') for s in cohort},
               "records": acquisition, "fx": fx.observations(), "failures": failures,
               "classification_overrides": overrides, "issuer_inputs": cfg["issuer_inputs"],
               "no_issuer_overrides": args.no_issuer_overrides,
               "parent_acquisition": {"path":str((source/"acquisition.json").resolve()),"sha256":hashlib.sha256((source/"acquisition.json").read_bytes()).hexdigest()} if prior else None,
               "config_sha256": hashlib.sha256((ROOT / "config.toml").read_bytes()).hexdigest(),
               "implementation_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "screener").glob("*.py")}}
    receipt['resource_sha256']={str(p.relative_to(ROOT/'screener')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'screener/resources').glob('*.json')}
    (args.output_dir / "acquisition.json").write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"records": len(frame), "failures": failures, "statuses": frame["status"].value_counts().to_dict() if len(frame) else {}}, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
