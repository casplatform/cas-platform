#!/usr/bin/env python3
"""CAS ML — Canonical Layer 1 retrain (reproducible pipeline).

raw Kelvins CSV -> EsaKelvinsMapper -> CanonicalCDM -> feature_extractor -> X
Reproduces the validated baseline recipe EXACTLY, with two deliberate, documented
deviations: mission_id dropped (no generalisation to real operators) and
object-type one-hot canonical 4-class (TBA folded into UNKNOWN) => 107 features.

Outputs are SEPARATE from the baseline (side-by-side; baseline untouched).
Success target: test ROC AUC >= 0.9533 (baseline test AUC).
"""
import os, sys, json, time
sys.path.insert(0, "/opt/cas/cas_api")
sys.path.insert(0, "/opt/cas/ml")

import numpy as np, pandas as pd, xgboost as xgb, sklearn
from sklearn.model_selection import train_test_split
from sklearn.metrics import (precision_score, recall_score, f1_score, roc_auc_score,
                             confusion_matrix, precision_recall_curve)
from mappers import EsaKelvinsMapper
from src.feature_extractor import extract_event_features

ML = "/opt/cas/ml"; MODELS = f"{ML}/models"
TRAIN = f"{ML}/datasets/esa_kelvins/train_data.csv"
TEST = f"{ML}/datasets/esa_kelvins/test_data.csv"
BASELINE_TEST_AUC = 0.9532597827376413

print("="*60); print("  CAS ML — Canonical Layer 1 Retrain"); print("="*60)
print(f"  xgboost {xgb.__version__}, pandas {pd.__version__}, numpy {np.__version__}, sklearn {sklearn.__version__}")

mapper = EsaKelvinsMapper()

def build(csv, tag):
    print(f"\n[{tag}] {csv}")
    t0 = time.time(); df = pd.read_csv(csv)
    print(f"    raw {df.shape}, {time.time()-t0:.1f}s")
    # LABEL from RAW (groupby.first risk >= -6) — exact baseline label, no canonical dependence
    df_last = df.sort_values(["event_id","time_to_tca"], ascending=[True,True]).groupby("event_id").first()
    y = (df_last["risk"] >= -6).astype(int)
    print(f"    events {len(y)}, positive {int(y.sum())} ({y.mean()*100:.2f}%)")
    # FEATURES via canonical pipeline
    t0 = time.time(); feats = {}; n = df["event_id"].nunique(); i = 0
    for ev, g in df.groupby("event_id"):
        cdms = [mapper.from_source(r) for r in g.to_dict("records")]
        feats[ev] = extract_event_features(cdms); i += 1
        if i % 2000 == 0: print(f"    map+extract {i}/{n}  ({time.time()-t0:.0f}s)", flush=True)
    X = pd.DataFrame.from_dict(feats, orient="index"); X.index.name = "event_id"
    print(f"    feature matrix {X.shape}, {time.time()-t0:.0f}s")
    return X, y.reindex(X.index)

def preprocess(X, cols):
    X = X.replace([np.inf, -np.inf], np.nan)
    for c in ["t_position_covariance_det", "c_position_covariance_det"]:
        if c in X.columns:
            X[c] = np.log10(X[c].abs().replace(0, np.nan))
    F32 = np.finfo(np.float32).max
    for c in X.select_dtypes(include=np.number).columns:
        if X[c].abs().max() > F32:
            X.loc[X[c].abs() > F32, c] = np.nan
    for f in cols:
        if f not in X.columns: X[f] = np.nan
    return X[cols]

# ── TRAIN ──
Xtr_raw, ytr = build(TRAIN, "TRAIN")
feature_cols = sorted(Xtr_raw.columns)
print(f"\n[features] n={len(feature_cols)}  (baseline 109; dropped: mission_id, obj_type_TBA)")
Xtr_all = preprocess(Xtr_raw, feature_cols)
spw = float((ytr == 0).sum() / max(int((ytr == 1).sum()), 1))
print(f"[imbalance] scale_pos_weight = {spw:.2f}")

Xtr, Xval, ytr2, yval = train_test_split(Xtr_all, ytr, test_size=0.2, random_state=42, stratify=ytr)
print(f"[split] train {Xtr.shape} pos={int(ytr2.sum())} | val {Xval.shape} pos={int(yval.sum())}")

model = xgb.XGBClassifier(n_estimators=500, max_depth=6, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, scale_pos_weight=spw,
                          eval_metric="aucpr", early_stopping_rounds=30,
                          random_state=42, n_jobs=-1, tree_method="hist")
t0 = time.time(); model.fit(Xtr, ytr2, eval_set=[(Xval, yval)], verbose=50)
print(f"[train] {time.time()-t0:.1f}s, best_iter={model.best_iteration}, best_aucpr={model.best_score:.4f}")

yv = model.predict(Xval); yvp = model.predict_proba(Xval)[:, 1]
val = dict(precision=float(precision_score(yval, yv)), recall=float(recall_score(yval, yv)),
           f1=float(f1_score(yval, yv)), roc_auc=float(roc_auc_score(yval, yvp)))
print(f"[val] P={val['precision']:.4f} R={val['recall']:.4f} F1={val['f1']:.4f} AUC={val['roc_auc']:.4f}")

model.save_model(f"{MODELS}/xgb_layer1_canonical.json")
with open(f"{MODELS}/xgb_layer1_canonical_features.json", "w") as f:
    json.dump({"features": feature_cols, "version": "canonical-v1",
               "dropped_vs_baseline": ["mission_id", "obj_type_TBA"]}, f, indent=2)
imp = sorted(zip(feature_cols, model.feature_importances_), key=lambda kv: -kv[1])[:20]
with open(f"{MODELS}/xgb_layer1_canonical_metrics.json", "w") as f:
    json.dump({"version": "canonical-v1", "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "n_events": int(len(ytr)), "n_features": len(feature_cols),
               "label_threshold": "log10(Pc) >= -6", "scale_pos_weight": spw,
               "val_metrics": val, "best_iteration": int(model.best_iteration),
               "top_20_features": [(n, float(i)) for n, i in imp]}, f, indent=2)
print("\n[top10 importance]")
for n, i in imp[:10]: print(f"    {n:30s} {i:.4f}")

# ── TEST ──
Xte_raw, yte = build(TEST, "TEST")
Xte = preprocess(Xte_raw, feature_cols)
tep = model.predict_proba(Xte)[:, 1]; ted = (tep >= 0.5).astype(int)
test = dict(precision=float(precision_score(yte, ted)), recall=float(recall_score(yte, ted)),
            f1=float(f1_score(yte, ted)), roc_auc=float(roc_auc_score(yte, tep)))
print(f"\n[TEST] P={test['precision']:.4f} R={test['recall']:.4f} F1={test['f1']:.4f} AUC={test['roc_auc']:.4f}")
print(f"[TEST] baseline {BASELINE_TEST_AUC:.4f} -> canonical {test['roc_auc']:.4f}  (delta {test['roc_auc']-BASELINE_TEST_AUC:+.4f})")

pd.DataFrame({"event_id": Xte.index, "high_risk_true": yte.values, "high_risk_proba": tep}).to_csv(
    f"{MODELS}/xgb_layer1_canonical_test_predictions.csv", index=False)
with open(f"{MODELS}/xgb_layer1_canonical_test_metrics.json", "w") as f:
    json.dump({"version": "canonical-v1", "tested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "n_events": int(len(yte)), "metrics": test,
               "confusion_matrix": confusion_matrix(yte, ted).tolist()}, f, indent=2)

# ── CALIBRATION (PR-curve; clean keys: red > yellow, RED stricter) ──
precs, recs, thr = precision_recall_curve(yte, tep)
def t4r(target):
    idx = int(np.argmin(np.abs(recs - target)))
    return float(recs[idx]), float(precs[idx]), (float(thr[min(idx, len(thr)-1)]) if len(thr) else 0.5)
red_rec, red_prec, red_thr = t4r(0.80)   # RED entry
yel_rec, yel_prec, yel_thr = t4r(0.95)   # YELLOW entry
if red_thr < yel_thr:
    red_thr, yel_thr = yel_thr, red_thr; red_rec, yel_rec = yel_rec, red_rec; red_prec, yel_prec = yel_prec, red_prec
print(f"\n[calib] RED  p>={red_thr:.4f} (recall {red_rec:.3f}, prec {red_prec:.3f})")
print(f"[calib] YELLOW p>={yel_thr:.4f} (recall {yel_rec:.3f}, prec {yel_prec:.3f})")

def tier(p): return "RED" if p >= red_thr else ("YELLOW" if p >= yel_thr else "GREEN")
tiers = np.array([tier(p) for p in tep]); tot_pos = int(yte.sum())
dist = {t: int((tiers == t).sum()) for t in ["RED","YELLOW","GREEN"]}
cap = {t: {"n_high_risk": int(yte.values[tiers == t].sum()),
           "capture_rate": round(float(yte.values[tiers == t].sum()/tot_pos), 4) if tot_pos else 0.0}
       for t in ["RED","YELLOW","GREEN"]}

bundle = {"model_path": "xgb_layer1_canonical.json", "features_path": "xgb_layer1_canonical_features.json",
          "model_version": "canonical-v1", "production_version": "canonical-v1-" + time.strftime("%Y%m%d"),
          "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
          "calibrated_from": "ESA Kelvins test_data.csv (canonical pipeline)",
          "thresholds": {"red": red_thr, "yellow": yel_thr},
          "tier_definition": {"RED": f"p >= {red_thr:.4f} (~recall {red_rec:.2f}, prec {red_prec:.2f})",
                              "YELLOW": f"{yel_thr:.4f} <= p < {red_thr:.4f}", "GREEN": f"p < {yel_thr:.4f}"},
          "test_set_metrics": {"n_events": int(len(yte)), "n_features": len(feature_cols),
                               "roc_auc": test["roc_auc"], "default_threshold_metrics": test,
                               "tier_distribution": dist, "high_risk_capture_by_tier": cap},
          "deviations_vs_baseline": ["mission_id dropped", "obj_type 4-class (TBA->UNKNOWN)"],
          "operational_notes": ["ML is decision SUPPORT, not autonomous decision",
                                "Always cross-check with deterministic Pc threshold",
                                "Score only when CDM feature coverage is sufficient (inference gate)"]}
with open(f"{MODELS}/xgb_layer1_canonical_production.json", "w") as f:
    json.dump(bundle, f, indent=2)

print(f"\n[tier dist] {dist}")
print(f"[capture]   {cap}")
print("\n" + "="*60)
print(f"  DONE — canonical test AUC {test['roc_auc']:.4f}  (target >= {BASELINE_TEST_AUC:.4f})")
print("="*60)
print("Saved: xgb_layer1_canonical{,_features,_metrics,_test_metrics,_production}.json + test_predictions.csv")
print("Baseline untouched.")
