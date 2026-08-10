"""ESA Kelvins Collision Avoidance Challenge dataset mapper.

Maps one Kelvins CSV row (103 columns; Uriot et al. 2020) to CanonicalCDM.
Built for ML retraining: the canonical CDM is the input the feature extractor
reads, so every Kelvins quantity the model uses is preserved here.

Field semantics verified against ESA's published column descriptions
(https://kelvins.esa.int/collision-avoidance-challenge/data/). Key facts:

  * Covariance is given as sigmas (std-dev, diagonal) + correlation
    coefficients (off-diagonal). CCSDS wants covariance VALUES, so:
        diagonal     C_xx = sigma_x ** 2
        off-diagonal C_ij = corr_ij * sigma_i * sigma_j
    Units already align (sigma_pos [m], sigma_vel [m/s] -> m^2, m^2/s, m^2/s^2).
  * Orbital elements: h_apo/h_per are altitudes (km, fixed-Re); j2k_sma/ecc are
    stored explicitly in CAS extensions (lossless, source-agnostic) rather than
    derived from h_apo/h_per (derivation inherits rounding + Re ambiguity).
  * The dataset is anonymised: no absolute datetimes. time_to_tca and
    time_lastob_* are relative scalars [days]. We synthesise datetimes against a
    FIXED anchor so only the deltas (which is all that is meaningful) survive;
    the extractor recovers them identically at train and inference time.
        time_to_tca       = TCA - CREATION_DATE        (days)
        x_time_lastob_*   = CREATION_DATE - TIME_LASTOB_*   (days, epoch-ref)
  * risk = log10(Pc) at the CDM epoch  -> COLLISION_PROBABILITY = 10**risk.
  * Target ('t_') = ESA satellite = Object1 (PAYLOAD); Chaser ('c_') = Object2.
  * 'position_covariance_det' is NOT stored (exact function of the covariance
    matrix; the extractor recomputes it).

CAUTION (handled downstream, not here): COLLISION_PROBABILITY,
COLLISION_MAX_PROBABILITY and OBJECT_DESIGNATOR(=mission_id) are stored for
fidelity/traceability but MUST be excluded from the ML feature set
(label-leakage / non-generalising identifier).
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from schemas import (
    CanonicalCDM,
    CDMHeader,
    CDMRelativeMetadata,
    CDMObject,
    CDMObjectMetadata,
    CDMODParameters,
    CDMAdditionalParameters,
    CDMCovarianceMatrix,
    CDMCASExtensions,
    CDMCASObjectExtensions,
    ObjectClass,
)
from mappers.base import SourceMapper, MapperError

log = logging.getLogger(__name__)

# Fixed reference epoch for the anonymised dataset. Only deltas are meaningful;
# the absolute value is arbitrary and never interpreted as a real calendar time.
KELVINS_TCA_ANCHOR = datetime(2000, 1, 1, 0, 0, 0)


# ────────────────────────────────────────────────────────────────
# Type-conversion helpers (NaN/empty -> None; never raise on sparse data)
# ────────────────────────────────────────────────────────────────
def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / Inf
            return None
        return f
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _log10_to_pc(v: Any) -> Optional[float]:
    """risk / max_risk_estimate are base-10 logs of a probability -> 10**x."""
    f = _to_float(v)
    if f is None:
        return None
    try:
        return 10.0 ** f
    except OverflowError:
        return None


def _to_object_class(v: Any) -> Optional[ObjectClass]:
    if v is None or v == "":
        return None
    s = str(v).strip().upper()
    mapping = {
        "PAYLOAD": ObjectClass.PAYLOAD,
        "ROCKET BODY": ObjectClass.ROCKET_BODY,
        "ROCKET_BODY": ObjectClass.ROCKET_BODY,
        "R/B": ObjectClass.ROCKET_BODY,
        "DEBRIS": ObjectClass.DEBRIS,
        "DEB": ObjectClass.DEBRIS,
        "TBA": ObjectClass.UNKNOWN,
        "UNKNOWN": ObjectClass.UNKNOWN,
        "OTHER": ObjectClass.OTHER,
    }
    return mapping.get(s, ObjectClass.OTHER)


# ────────────────────────────────────────────────────────────────
# Mapper
# ────────────────────────────────────────────────────────────────
_SHARED_FIELDS = [
    "time_lastob_start", "time_lastob_end", "recommended_od_span", "actual_od_span",
    "obs_available", "obs_used", "residuals_accepted", "weighted_rms", "rcs_estimate",
    "cd_area_over_mass", "cr_area_over_mass", "sedr", "j2k_sma", "j2k_ecc", "j2k_inc",
    "ct_r", "cn_r", "cn_t", "crdot_r", "crdot_t", "crdot_n", "ctdot_r", "ctdot_t",
    "ctdot_n", "ctdot_rdot", "cndot_r", "cndot_t", "cndot_n", "cndot_rdot", "cndot_tdot",
    "span", "h_apo", "h_per", "sigma_r", "sigma_t", "sigma_n", "sigma_rdot",
    "sigma_tdot", "sigma_ndot", "position_covariance_det",
]
_UNIQUE_FIELDS = [
    "event_id", "time_to_tca", "mission_id", "risk", "max_risk_estimate",
    "max_risk_scaling", "miss_distance", "relative_speed",
    "relative_position_r", "relative_position_t", "relative_position_n",
    "relative_velocity_r", "relative_velocity_t", "relative_velocity_n",
    "geocentric_latitude", "azimuth", "elevation", "mahalanobis_distance",
    "F10", "F3M", "SSN", "AP", "c_object_type",
]


class EsaKelvinsMapper(SourceMapper):
    """Maps a single ESA Kelvins CDM row (103 cols) to CanonicalCDM."""

    SOURCE_NAME = "esa_kelvins"
    SOURCE_VERSION = "v2019-103col"
    EXPECTED_FIELDS = set(_UNIQUE_FIELDS) | {
        f"{p}_{f}" for p in ("t", "c") for f in _SHARED_FIELDS
    }

    def from_source(self, raw: Dict[str, Any]) -> CanonicalCDM:
        if not isinstance(raw, dict):
            raise MapperError(
                f"EsaKelvinsMapper expects dict, got {type(raw).__name__}"
            )

        unknown = set(raw.keys()) - self.EXPECTED_FIELDS
        if unknown:
            log.debug("Unexpected Kelvins fields ignored: %s", sorted(unknown))

        # ── Synthesised timeline (anonymised data -> deltas only) ──────────
        time_to_tca = _to_float(raw.get("time_to_tca"))  # days
        creation_date = (
            KELVINS_TCA_ANCHOR - timedelta(days=time_to_tca)
            if time_to_tca is not None else None
        )

        header = CDMHeader(
            CCSDS_CDM_VERS="1.0",
            CREATION_DATE=creation_date,
            ORIGINATOR="ESA_KELVINS",
            MESSAGE_ID=_to_str(raw.get("event_id")),  # event-level (no per-CDM id)
        )

        # ── Relative metadata ──────────────────────────────────────────────
        relative_metadata = CDMRelativeMetadata(
            TCA=KELVINS_TCA_ANCHOR,
            MISS_DISTANCE=_to_float(raw.get("miss_distance")),
            RELATIVE_SPEED=_to_float(raw.get("relative_speed")),
            RELATIVE_POSITION_R=_to_float(raw.get("relative_position_r")),
            RELATIVE_POSITION_T=_to_float(raw.get("relative_position_t")),
            RELATIVE_POSITION_N=_to_float(raw.get("relative_position_n")),
            RELATIVE_VELOCITY_R=_to_float(raw.get("relative_velocity_r")),
            RELATIVE_VELOCITY_T=_to_float(raw.get("relative_velocity_t")),
            RELATIVE_VELOCITY_N=_to_float(raw.get("relative_velocity_n")),
            MAHALANOBIS_DISTANCE=_to_float(raw.get("mahalanobis_distance")),
            COLLISION_PROBABILITY=_log10_to_pc(raw.get("risk")),
            COLLISION_MAX_PROBABILITY=_log10_to_pc(raw.get("max_risk_estimate")),
            CONJUNCTION_ID=_to_str(raw.get("event_id")),
        )

        # ── Conjunction-level CAS extension (space weather + geometry) ──────
        cas_extensions = CDMCASExtensions(
            F10=_to_float(raw.get("F10")),
            F3M=_to_float(raw.get("F3M")),
            SSN=_to_float(raw.get("SSN")),
            AP=_to_float(raw.get("AP")),
            GEOCENTRIC_LATITUDE=_to_float(raw.get("geocentric_latitude")),
            AZIMUTH=_to_float(raw.get("azimuth")),
            ELEVATION=_to_float(raw.get("elevation")),
        )

        object1 = self._build_object(
            raw, "t", "OBJECT1", ObjectClass.PAYLOAD,
            _to_str(raw.get("mission_id")), creation_date,
        )
        object2 = self._build_object(
            raw, "c", "OBJECT2", _to_object_class(raw.get("c_object_type")),
            None, creation_date,
        )

        return CanonicalCDM(
            header=header,
            relative_metadata=relative_metadata,
            object1=object1,
            object2=object2,
            cas_extensions=cas_extensions,
        )

    # ── Per-object assembly ────────────────────────────────────────────────
    def _build_object(self, raw, p, obj_label, obj_type, designator, creation_date):
        return CDMObject(
            metadata=CDMObjectMetadata(
                OBJECT=obj_label,
                OBJECT_TYPE=obj_type,
                OBJECT_DESIGNATOR=designator,
                REF_FRAME="EME2000",          # j2k -> J2000 / EME2000
                ORBIT_CENTER="EARTH",
                COVARIANCE_METHOD="CALCULATED",
            ),
            od_parameters=self._od_params(raw, p, creation_date),
            additional=self._additional(raw, p),
            covariance=self._covariance(raw, p),
            cas_extensions=self._cas_object(raw, p),
        )

    def _od_params(self, raw, p, creation_date):
        ls = _to_float(raw.get(f"{p}_time_lastob_start"))  # days before epoch
        le = _to_float(raw.get(f"{p}_time_lastob_end"))
        t_start = (
            creation_date - timedelta(days=ls)
            if (creation_date is not None and ls is not None) else None
        )
        t_end = (
            creation_date - timedelta(days=le)
            if (creation_date is not None and le is not None) else None
        )
        return CDMODParameters(
            RECOMMENDED_OD_SPAN=_to_float(raw.get(f"{p}_recommended_od_span")),
            ACTUAL_OD_SPAN=_to_float(raw.get(f"{p}_actual_od_span")),
            OBS_AVAILABLE=_to_int(raw.get(f"{p}_obs_available")),
            OBS_USED=_to_int(raw.get(f"{p}_obs_used")),
            RESIDUALS_ACCEPTED=_to_float(raw.get(f"{p}_residuals_accepted")),  # %
            WEIGHTED_RMS=_to_float(raw.get(f"{p}_weighted_rms")),
            TIME_LASTOB_START=t_start,
            TIME_LASTOB_END=t_end,
        )

    def _additional(self, raw, p):
        return CDMAdditionalParameters(
            CD_AREA_OVER_MASS=_to_float(raw.get(f"{p}_cd_area_over_mass")),
            CR_AREA_OVER_MASS=_to_float(raw.get(f"{p}_cr_area_over_mass")),
            SEDR=_to_float(raw.get(f"{p}_sedr")),
            APOAPSIS_ALTITUDE=_to_float(raw.get(f"{p}_h_apo")),
            PERIAPSIS_ALTITUDE=_to_float(raw.get(f"{p}_h_per")),
            INCLINATION=_to_float(raw.get(f"{p}_j2k_inc")),
        )

    def _cas_object(self, raw, p):
        return CDMCASObjectExtensions(
            SEMI_MAJOR_AXIS=_to_float(raw.get(f"{p}_j2k_sma")),
            ECCENTRICITY=_to_float(raw.get(f"{p}_j2k_ecc")),
            RCS_ESTIMATE=_to_float(raw.get(f"{p}_rcs_estimate")),
            RISK_COMPUTATION_SIZE=_to_float(raw.get(f"{p}_span")),
        )

    def _covariance(self, raw, p):
        """sigmas (diagonal std-dev) + correlations (off-diagonal) -> CCSDS covariance."""
        s_r = _to_float(raw.get(f"{p}_sigma_r"))
        s_t = _to_float(raw.get(f"{p}_sigma_t"))
        s_n = _to_float(raw.get(f"{p}_sigma_n"))
        s_rd = _to_float(raw.get(f"{p}_sigma_rdot"))
        s_td = _to_float(raw.get(f"{p}_sigma_tdot"))
        s_nd = _to_float(raw.get(f"{p}_sigma_ndot"))

        def var(s):
            return s * s if s is not None else None

        def cov(corr_field, si, sj):
            c = _to_float(raw.get(corr_field))
            if c is None or si is None or sj is None:
                return None
            return c * si * sj

        return CDMCovarianceMatrix(
            # diagonal: variance = sigma^2
            CR_R=var(s_r), CT_T=var(s_t), CN_N=var(s_n),
            CRDOT_RDOT=var(s_rd), CTDOT_TDOT=var(s_td), CNDOT_NDOT=var(s_nd),
            # position vs position
            CT_R=cov(f"{p}_ct_r", s_t, s_r),
            CN_R=cov(f"{p}_cn_r", s_n, s_r),
            CN_T=cov(f"{p}_cn_t", s_n, s_t),
            # velocity vs position
            CRDOT_R=cov(f"{p}_crdot_r", s_rd, s_r),
            CRDOT_T=cov(f"{p}_crdot_t", s_rd, s_t),
            CRDOT_N=cov(f"{p}_crdot_n", s_rd, s_n),
            CTDOT_R=cov(f"{p}_ctdot_r", s_td, s_r),
            CTDOT_T=cov(f"{p}_ctdot_t", s_td, s_t),
            CTDOT_N=cov(f"{p}_ctdot_n", s_td, s_n),
            CNDOT_R=cov(f"{p}_cndot_r", s_nd, s_r),
            CNDOT_T=cov(f"{p}_cndot_t", s_nd, s_t),
            CNDOT_N=cov(f"{p}_cndot_n", s_nd, s_n),
            # velocity vs velocity
            CTDOT_RDOT=cov(f"{p}_ctdot_rdot", s_td, s_rd),
            CNDOT_RDOT=cov(f"{p}_cndot_rdot", s_nd, s_rd),
            CNDOT_TDOT=cov(f"{p}_cndot_tdot", s_nd, s_td),
        )
