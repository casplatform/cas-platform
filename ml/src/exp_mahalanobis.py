#!/usr/bin/env python3
"""Experiment: marginal value of mahalanobis_distance (drop vs keep).
Reuses train_canonical's EXACT build/preprocess/hyperparams. Caches feature
matrices (pickle) for fast re-runs. Baseline untouched; writes nothing to prod."""
import os, sys, time, pickle
sys.path.insert(0, "/opt/cas/cas_api"); sys.path.insert(0, "/opt/cas/ml")
import numpy as np, pandas as pd, xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from mappers import EsaKelvinsMapper
from src.feature_extractor import extract_event_features

ML="/opt/cas/ml"; CACHE=f"{ML}/cache"; os.makedirs(CACHE, exist_ok=True)
TRAIN=f"{ML}/datasets/esa_kelvins/train_data.csv"; TEST=f"{ML}/datasets/esa_kelvins/test_data.csv"
CANON_RED=0.15557056665420532; CANON_YEL=0.05681183189153671; CANON_TEST_AUC=0.9484
mapper=EsaKelvinsMapper()

def build(csv, tag):
    df=pd.read_csv(csv)
    df_last=df.sort_values(["event_id","time_to_tca"]).groupby("event_id").first()
    y=(df_last["risk"]>=-6).astype(int)
    feats={}; n=df["event_id"].nunique(); i=0; t0=time.time()
    for ev,g in df.groupby("event_id"):
        cdms=[mapper.from_source(r) for r in g.to_dict("records")]
        feats[ev]=extract_event_features(cdms); i+=1
        if i%3000==0: print(f"    [{tag}] {i}/{n} ({time.time()-t0:.0f}s)", flush=True)
    X=pd.DataFrame.from_dict(feats,orient="index"); X.index.name="event_id"
    return X, y.reindex(X.index)

def get_cached():
    f=f"{CACHE}/exp_maha_matrices.pkl"
    if os.path.exists(f):
        print("loading cached matrices...")
        with open(f,"rb") as fh: return pickle.load(fh)
    print("building matrices (one-time)...")
    Xtr,ytr=build(TRAIN,"train"); print(f"  train {Xtr.shape}")
    Xte,yte=build(TEST,"test"); print(f"  test {Xte.shape}")
    with open(f,"wb") as fh: pickle.dump((Xtr,ytr,Xte,yte),fh)
    print("cached.")
    return Xtr,ytr,Xte,yte

def preprocess(X, cols):
    X=X.replace([np.inf,-np.inf],np.nan)
    for c in ["t_position_covariance_det","c_position_covariance_det"]:
        if c in X.columns: X[c]=np.log10(X[c].abs().replace(0,np.nan))
    F32=np.finfo(np.float32).max
    for c in X.select_dtypes(include=np.number).columns:
        if X[c].abs().max()>F32: X.loc[X[c].abs()>F32,c]=np.nan
    for f in cols:
        if f not in X.columns: X[f]=np.nan
    return X[cols]

def train_eval(Xtr_raw,ytr,Xte_raw,yte,cols,tag):
    Xtr_all=preprocess(Xtr_raw.copy(),cols); Xte=preprocess(Xte_raw.copy(),cols)
    spw=float((ytr==0).sum()/max(int((ytr==1).sum()),1))
    Xa,Xv,ya,yv=train_test_split(Xtr_all,ytr,test_size=0.2,random_state=42,stratify=ytr)
    m=xgb.XGBClassifier(n_estimators=500,max_depth=6,learning_rate=0.05,subsample=0.8,
        colsample_bytree=0.8,scale_pos_weight=spw,eval_metric="aucpr",
        early_stopping_rounds=30,random_state=42,n_jobs=-1,tree_method="hist")
    m.fit(Xa,ya,eval_set=[(Xv,yv)],verbose=False)
    vp=m.predict_proba(Xv)[:,1]; tp=m.predict_proba(Xte)[:,1]
    val_auc=roc_auc_score(yv,vp); test_auc=roc_auc_score(yte,tp)
    tiers=np.where(tp>=CANON_RED,"RED",np.where(tp>=CANON_YEL,"YELLOW","GREEN"))
    pos=int(yte.sum())
    cap={t:(int(yte.values[tiers==t].sum()), round(float(yte.values[tiers==t].sum()/pos),4) if pos else 0.0) for t in ["RED","YELLOW","GREEN"]}
    ry=cap["RED"][1]+cap["YELLOW"][1]
    print(f"\n[{tag}] n_features={len(cols)} best_iter={m.best_iteration}")
    print(f"  val_AUC={val_auc:.4f}  test_AUC={test_auc:.4f}")
    print(f"  capture (canonical-v1 thr): RED={cap['RED']} YELLOW={cap['YELLOW']} GREEN={cap['GREEN']}")
    print(f"  RED+YELLOW capture={ry*100:.1f}%  (high-risk missed in GREEN={cap['GREEN'][0]}/{pos})")
    return dict(tag=tag,n=len(cols),val_auc=val_auc,test_auc=test_auc,cap=cap,ry=ry)

Xtr,ytr,Xte,yte=get_cached()
print(f"events: train={len(ytr)} (pos {int(ytr.sum())}) test={len(yte)} (pos {int(yte.sum())})")
allcols=sorted(Xtr.columns)
print(f"total features={len(allcols)}; mahalanobis present={'mahalanobis_distance' in allcols}")

r_full=train_eval(Xtr,ytr,Xte,yte,allcols,"FULL 107 (sanity vs canonical-v1)")
dropcols=[c for c in allcols if c!="mahalanobis_distance"]
r_drop=train_eval(Xtr,ytr,Xte,yte,dropcols,"DROP mahalanobis (106)")

print("\n"+"="*64)
print(f"  canonical-v1 recorded test AUC = {CANON_TEST_AUC}")
print(f"  FULL(107)  test AUC = {r_full['test_auc']:.4f}  <- sanity: should ~match canonical-v1")
print(f"  DROP(106)  test AUC = {r_drop['test_auc']:.4f}")
print(f"  >>> mahalanobis MARGINAL value = {r_full['test_auc']-r_drop['test_auc']:+.4f} test-AUC")
print(f"  RED+YELLOW capture: FULL={r_full['ry']*100:.1f}%  DROP={r_drop['ry']*100:.1f}%  (delta {(r_full['ry']-r_drop['ry'])*100:+.1f}pp)")
print("="*64)
print("If marginal AUC tiny + capture ~unchanged -> DROP permanently (safest, parity-guaranteed).")
print("Else -> implement CARA-standard B-plane mahalanobis for CCSDS (rigorous, validatable, no retrain).")
