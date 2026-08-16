import numpy as np
from datetime import datetime, timedelta, timezone
from config import BUFFER_PATH, DB_PATH
from data.circling_prior import ClimbPrior, NO_DATA

with np.load(BUFFER_PATH, allow_pickle=False) as d:
    X, y = d["X"], d["y"].astype(int)

def decode(sin_v, cos_v, period):
    return (np.arctan2(sin_v, cos_v) % (2*np.pi)) / (2*np.pi) * period

hour = decode(X[:,15], X[:,16], 24)
doy  = decode(X[:,17], X[:,18], 365)
base = datetime(2026,1,1,tzinfo=timezone.utc)
whens = [base + timedelta(days=float(d)-1, hours=float(h)) for d,h in zip(doy,hour)]
print("sample time range:", min(whens).isoformat(), "->", max(whens).isoformat())

for lag in (24, 12, 6):
    p = ClimbPrior(DB_PATH, lag_hours=lag)
    v = p.values(X[:,0], X[:,1], whens)
    have = ~np.isnan(v)
    print(f"\nlag={lag}h: coverage {have.mean()*100:5.1f}%  ({have.sum()}/{len(v)} rows)")
    if have.any():
        print(f"   prior  mean={v[have].mean():.4f}  min={v[have].min():.4f} max={v[have].max():.4f}")
        for lbl in (0,1):
            m = have & (y==lbl)
            if m.any():
                print(f"   label={lbl}: n={m.sum():5d} mean prior={v[m].mean():.4f}")
