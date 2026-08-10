"""Canonical CDM -> Layer 1 feature extractor.

Reproduces the original (patch_ml1_xgboost_baseline.py) event-level feature
engineering, reading from CanonicalCDM objects instead of raw Kelvins columns.
SINGLE bridge used at training (Kelvins->mapper->canonical->here) AND inference
(production CDM->mapper->canonical->here).

Reproduction contract (verified field-by-field vs the original recipe):
  * Representative per event = pandas groupby.first() over time_to_tca-ascending
    CDMs  ==>  per-feature first-non-null across the sorted CDMs.
  * Aggregates over ALL event CDMs (skipna): agg_miss_distance{min,max,mean},
    agg_time_to_tca{min,max,count}.
  * Covariance inverse-transform to the model's native form:
        sigma_x = sqrt(C_xx);   corr_ij = C_ij/(sigma_i*sigma_j)
        x_position_covariance_det = det(3x3 RTN position covariance)  [raw;
            model preprocessing applies log10 downstream]
  * time_to_tca = (TCA-CREATION) [d]; x_time_lastob_* = (CREATION-TIME_LASTOB_*) [d]
  * object-type one-hot from object2 (canonical 4-class). DELIBERATE deviation:
    TBA folded into UNKNOWN (CCSDS has no TBA) -> no obj_type_TBA.
  * mission_id DELIBERATELY DROPPED (anonymised id; does not generalise to
    production NORAD designators).

Output: dict {feature: value-or-None}. None == missing (XGBoost native NaN).
"""
import math
from typing import List

OBJ_TYPE_COLUMNS = ["obj_type_DEBRIS", "obj_type_PAYLOAD", "obj_type_ROCKET BODY", "obj_type_UNKNOWN"]


def _sqrt(v):
    if v is None or v < 0:
        return None
    return math.sqrt(v)


def _corr(off, si, sj):
    if off is None or si is None or sj is None or si == 0 or sj == 0:
        return None
    return off / (si * sj)


def _det3(a, b, c, d, e, f):
    # det [[a,b,c],[b,d,e],[c,e,f]]
    if None in (a, b, c, d, e, f):
        return None
    return a * (d * f - e * e) - b * (b * f - e * c) + c * (b * e - d * c)


def _days(creation, t):
    if creation is None or t is None:
        return None
    return (creation - t).total_seconds() / 86400.0


def _ttca(cdm):
    rm, h = cdm.relative_metadata, cdm.header
    if rm is not None and h is not None and rm.TCA is not None and h.CREATION_DATE is not None:
        return (rm.TCA - h.CREATION_DATE).total_seconds() / 86400.0
    return None


def _is_missing(v):
    return v is None or (isinstance(v, float) and v != v)


def _object_features(f, p, obj, creation):
    if obj is None:
        return
    od = obj.od_parameters
    if od is not None:
        f[f"{p}_recommended_od_span"] = od.RECOMMENDED_OD_SPAN
        f[f"{p}_actual_od_span"] = od.ACTUAL_OD_SPAN
        f[f"{p}_obs_available"] = od.OBS_AVAILABLE
        f[f"{p}_obs_used"] = od.OBS_USED
        f[f"{p}_residuals_accepted"] = od.RESIDUALS_ACCEPTED
        f[f"{p}_weighted_rms"] = od.WEIGHTED_RMS
        f[f"{p}_time_lastob_start"] = _days(creation, od.TIME_LASTOB_START)
        f[f"{p}_time_lastob_end"] = _days(creation, od.TIME_LASTOB_END)
    add = obj.additional
    if add is not None:
        f[f"{p}_cd_area_over_mass"] = add.CD_AREA_OVER_MASS
        f[f"{p}_cr_area_over_mass"] = add.CR_AREA_OVER_MASS
        f[f"{p}_sedr"] = add.SEDR
        f[f"{p}_h_apo"] = add.APOAPSIS_ALTITUDE
        f[f"{p}_h_per"] = add.PERIAPSIS_ALTITUDE
        f[f"{p}_j2k_inc"] = add.INCLINATION
    ce = obj.cas_extensions
    if ce is not None:
        f[f"{p}_j2k_sma"] = ce.SEMI_MAJOR_AXIS
        f[f"{p}_j2k_ecc"] = ce.ECCENTRICITY
        f[f"{p}_rcs_estimate"] = ce.RCS_ESTIMATE
        f[f"{p}_span"] = ce.RISK_COMPUTATION_SIZE
    cov = obj.covariance
    if cov is not None:
        sr, st, sn = _sqrt(cov.CR_R), _sqrt(cov.CT_T), _sqrt(cov.CN_N)
        srd, std, snd = _sqrt(cov.CRDOT_RDOT), _sqrt(cov.CTDOT_TDOT), _sqrt(cov.CNDOT_NDOT)
        f[f"{p}_sigma_r"], f[f"{p}_sigma_t"], f[f"{p}_sigma_n"] = sr, st, sn
        f[f"{p}_sigma_rdot"], f[f"{p}_sigma_tdot"], f[f"{p}_sigma_ndot"] = srd, std, snd
        f[f"{p}_ct_r"] = _corr(cov.CT_R, st, sr)
        f[f"{p}_cn_r"] = _corr(cov.CN_R, sn, sr)
        f[f"{p}_cn_t"] = _corr(cov.CN_T, sn, st)
        f[f"{p}_crdot_r"] = _corr(cov.CRDOT_R, srd, sr)
        f[f"{p}_crdot_t"] = _corr(cov.CRDOT_T, srd, st)
        f[f"{p}_crdot_n"] = _corr(cov.CRDOT_N, srd, sn)
        f[f"{p}_ctdot_r"] = _corr(cov.CTDOT_R, std, sr)
        f[f"{p}_ctdot_t"] = _corr(cov.CTDOT_T, std, st)
        f[f"{p}_ctdot_n"] = _corr(cov.CTDOT_N, std, sn)
        f[f"{p}_ctdot_rdot"] = _corr(cov.CTDOT_RDOT, std, srd)
        f[f"{p}_cndot_r"] = _corr(cov.CNDOT_R, snd, sr)
        f[f"{p}_cndot_t"] = _corr(cov.CNDOT_T, snd, st)
        f[f"{p}_cndot_n"] = _corr(cov.CNDOT_N, snd, sn)
        f[f"{p}_cndot_rdot"] = _corr(cov.CNDOT_RDOT, snd, srd)
        f[f"{p}_cndot_tdot"] = _corr(cov.CNDOT_TDOT, snd, std)
        f[f"{p}_position_covariance_det"] = _det3(cov.CR_R, cov.CT_R, cov.CN_R,
                                                  cov.CT_T, cov.CN_T, cov.CN_N)


def _single_cdm_features(cdm) -> dict:
    f = {"time_to_tca": _ttca(cdm)}
    rm = cdm.relative_metadata
    if rm is not None:
        f["miss_distance"] = rm.MISS_DISTANCE
        f["relative_speed"] = rm.RELATIVE_SPEED
        f["relative_position_r"] = rm.RELATIVE_POSITION_R
        f["relative_position_t"] = rm.RELATIVE_POSITION_T
        f["relative_position_n"] = rm.RELATIVE_POSITION_N
        f["relative_velocity_r"] = rm.RELATIVE_VELOCITY_R
        f["relative_velocity_t"] = rm.RELATIVE_VELOCITY_T
        f["relative_velocity_n"] = rm.RELATIVE_VELOCITY_N
        f["mahalanobis_distance"] = rm.MAHALANOBIS_DISTANCE
    cas = cdm.cas_extensions
    if cas is not None:
        f["geocentric_latitude"] = cas.GEOCENTRIC_LATITUDE
        f["azimuth"] = cas.AZIMUTH
        f["elevation"] = cas.ELEVATION
        f["F10"], f["F3M"], f["SSN"], f["AP"] = cas.F10, cas.F3M, cas.SSN, cas.AP
    creation = cdm.header.CREATION_DATE if cdm.header is not None else None
    _object_features(f, "t", cdm.object1, creation)
    _object_features(f, "c", cdm.object2, creation)
    return f


def _obj2_class(cdm):
    o2 = cdm.object2
    if o2 is not None and o2.metadata is not None and o2.metadata.OBJECT_TYPE is not None:
        ot = o2.metadata.OBJECT_TYPE
        return ot.value if hasattr(ot, "value") else str(ot)
    return None


def extract_event_features(cdms: List) -> dict:
    """One conjunction's CanonicalCDMs -> Layer 1 feature dict."""
    if not cdms:
        return {}
    # closest-to-TCA first (ascending time_to_tca; None last; stable)
    idx = sorted(range(len(cdms)),
                 key=lambda i: (_ttca(cdms[i]) is None,
                                _ttca(cdms[i]) if _ttca(cdms[i]) is not None else 0.0))
    cdms_sorted = [cdms[i] for i in idx]
    per = [_single_cdm_features(c) for c in cdms_sorted]

    # representative = per-feature first non-null (groupby.first semantics)
    feat = {}
    keys = set()
    for d in per:
        keys.update(d.keys())
    for k in keys:
        v_out = None
        for d in per:
            v = d.get(k)
            if not _is_missing(v):
                v_out = v
                break
        feat[k] = v_out

    # aggregates over ALL CDMs (skipna)
    miss = [d.get("miss_distance") for d in per if not _is_missing(d.get("miss_distance"))]
    tts = [d.get("time_to_tca") for d in per if not _is_missing(d.get("time_to_tca"))]
    feat["agg_miss_distance_min"] = min(miss) if miss else None
    feat["agg_miss_distance_max"] = max(miss) if miss else None
    feat["agg_miss_distance_mean"] = (sum(miss) / len(miss)) if miss else None
    feat["agg_time_to_tca_min"] = min(tts) if tts else None
    feat["agg_time_to_tca_max"] = max(tts) if tts else None
    feat["agg_time_to_tca_count"] = len(tts)

    # object-type one-hot (canonical 4-class, representative object2)
    cls = None
    for c in cdms_sorted:
        cls = _obj2_class(c)
        if cls is not None:
            break
    for col in OBJ_TYPE_COLUMNS:
        feat[col] = 1 if (cls is not None and f"obj_type_{cls}" == col) else 0

    return feat
