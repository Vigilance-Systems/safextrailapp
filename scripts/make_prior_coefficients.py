"""Write risk_engine/coefficients.json with EXPERT PRIOR values, so the engine works before the
notebook has trained anything. The notebook overwrites this file with real, data-derived values.
The logit quantile table is simulated from standard-normal features (fixed seed)."""
import json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from risk_engine.engine import FEATURES, PRIOR_COEFS

means = dict(valley_feels_max_c=32.0, summit_feels_min_c=2.0, summit_gust_max_kph=40.0,
             summit_snow_cm=0.5, precip_mm=0.5)
stds = dict(valley_feels_max_c=12.0, summit_feels_min_c=10.0, summit_gust_max_kph=20.0,
            summit_snow_cm=3.0, precip_mm=2.5)
coefs = dict(PRIOR_COEFS)
intercept = -4.0
rng = np.random.default_rng(0)
z = rng.standard_normal((20000, len(FEATURES)))
logits = intercept + z @ np.array([coefs[f] for f in FEATURES])
out = dict(is_prior=True, synthetic=False, n_incidents=0, n_days=0, intercept=intercept,
           means=means, stds=stds, coefs=coefs,
           logit_quantiles=[round(float(q), 4) for q in np.quantile(logits, np.linspace(0, 1, 101))])
Path(__file__).resolve().parent.parent.joinpath("risk_engine", "coefficients.json").write_text(json.dumps(out, indent=1))
print("wrote prior coefficients")
