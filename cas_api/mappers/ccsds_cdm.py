"""CCSDS Conjunction Data Message mapper (full CDM with covariance).

Maps a CCSDS 508.0-B-1 compliant CDM into CanonicalCDM -- the format produced by
Starlink Space Safety / Stargaze, TraCSS, and Space-Track operator-tier CDMs (all
CCSDS-aligned, standard CCSDS keyword names). Unlike the 16-field public tier
(SpaceTrackPublicMapper -> ML gate UNAVAILABLE), a full CDM carries covariance,
relative state, OD parameters and object state vectors -> the gate passes -> ML
fires (RED/YELLOW/GREEN + SHAP).

Verified against the validated EsaKelvinsMapper (same canonical target fields):
  * Covariance: 6x6 RTN lower-triangular pos/vel submatrix, SI (m**2 / m**2*s /
    m**2*s**2). Canonical CDMCovarianceMatrix uses identical CCSDS names+units ->
    IDENTITY, no unit conversion. Non-RTN source frame is flagged, never silently
    mis-mapped.
  * Orbital elements (SMA/ECC/INC/apo/peri) are not CDM fields; derived from the
    state vector (X,Y,Z,X_DOT,Y_DOT,Z_DOT in km,km/s) via two-body mechanics
    -> cas_extensions.SEMI_MAJOR_AXIS/ECCENTRICITY + additional.INCLINATION/
    APOAPSIS_ALTITUDE/PERIAPSIS_ALTITUDE (km altitude, Re=6378.137).
  * Object size (RISK_COMPUTATION_SIZE / *_span): 2*HBR if HBR present, else
    equivalent diameter 2*sqrt(AREA_PC/pi). RCS_ESTIMATE := AREA_PC (m**2).
  * NOT fabricated (absent from a standard CDM; gate-handled as missing):
    MAHALANOBIS_DISTANCE (needs cross-frame covariance combination -- deferred),
    space weather (F10/F3M/SSN/AP), conjunction geometry angles.

Input: CCSDS CDM JSON. Objects at segment[0]/segment[1] or object1/object2; each
object's metadata/od/additional/state/covariance subdicts are flattened by CCSDS
field name (globally unique -> no collision). Header + relative metadata read from
the top level. Field NAMES are CCSDS-standard regardless of source flavour; only
container nesting can vary (confirmed against a real Starlink sample pre-prod).
"""
import logging
import math
import numpy as np
from datetime import datetime
from typing import Any, Dict, Optional

from schemas import (
    CanonicalCDM, CDMHeader, CDMRelativeMetadata, CDMObject, CDMObjectMetadata,
    CDMODParameters, CDMAdditionalParameters, CDMCovarianceMatrix,
    CDMCASExtensions, CDMCASObjectExtensions, ObjectClass,
)
from mappers.base import SourceMapper, MapperError

log = logging.getLogger(__name__)

MU_EARTH = 398600.4418   # km^3/s^2
R_EARTH = 6378.137       # km
_SIGMA_MIN_M = 1e-2
_SIGMA_MAX_M = 1e7

_COV_KEYS = [
    "CR_R", "CT_R", "CT_T", "CN_R", "CN_T", "CN_N",
    "CRDOT_R", "CRDOT_T", "CRDOT_N", "CRDOT_RDOT",
    "CTDOT_R", "CTDOT_T", "CTDOT_N", "CTDOT_RDOT", "CTDOT_TDOT",
    "CNDOT_R", "CNDOT_T", "CNDOT_N", "CNDOT_RDOT", "CNDOT_TDOT", "CNDOT_NDOT",
]


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if (f != f or f in (float("inf"), float("-inf"))) else f
    except (ValueError, TypeError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def _to_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _to_datetime(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%jT%H:%M:%S.%f", "%Y-%jT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_object_class(v):
    if v is None or v == "":
        return None
    s = str(v).strip().upper()
    return {
        "PAYLOAD": ObjectClass.PAYLOAD, "ROCKET BODY": ObjectClass.ROCKET_BODY,
        "ROCKET_BODY": ObjectClass.ROCKET_BODY, "R/B": ObjectClass.ROCKET_BODY,
        "DEBRIS": ObjectClass.DEBRIS, "DEB": ObjectClass.DEBRIS,
        "TBA": ObjectClass.UNKNOWN, "UNKNOWN": ObjectClass.UNKNOWN,
        "OTHER": ObjectClass.OTHER,
    }.get(s, ObjectClass.OTHER)


def _eci2rtn(r, v):
    """ECI->RTN rotation (rows R,T,W). None if degenerate."""
    r = np.asarray(r, float); v = np.asarray(v, float)
    nr = np.linalg.norm(r); h = np.cross(r, v); nh = np.linalg.norm(h)
    if nr == 0 or nh == 0:
        return None
    R = r / nr; W = h / nh; T = np.cross(W, R)
    return np.vstack([R, T, W])


def _bplane_mahalanobis(r1, v1, C1_rtn, r2, v2, C2_rtn):
    """Standard 2D encounter-plane Mahalanobis (NASA CARA / CCSDS method).
    r,v: km, km/s ECI. C_rtn: 3x3 RTN position covariance, m^2. Returns dimensionless.
    Combine per-object covariances in ECI (uncorrelated -> sum), project onto the plane
    perpendicular to relative velocity. None if inputs degenerate/singular."""
    M1 = _eci2rtn(r1, v1); M2 = _eci2rtn(r2, v2)
    if M1 is None or M2 is None:
        return None
    C = M1.T @ C1_rtn @ M1 + M2.T @ C2_rtn @ M2
    dr = (np.asarray(r2, float) - np.asarray(r1, float)) * 1000.0
    dv = np.asarray(v2, float) - np.asarray(v1, float)
    ndv = np.linalg.norm(dv)
    if ndv == 0:
        return None
    dvn = dv / ndv
    a = np.array([1.0, 0.0, 0.0]) if abs(dvn[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = a - np.dot(a, dvn) * dvn
    n1 = np.linalg.norm(e1)
    if n1 == 0:
        return None
    e1 = e1 / n1; e2 = np.cross(dvn, e1)
    P = np.vstack([e1, e2])
    C2d = P @ C @ P.T; d2d = P @ dr
    try:
        val = float(d2d @ np.linalg.solve(C2d, d2d))
    except np.linalg.LinAlgError:
        return None
    return float(np.sqrt(val)) if val > 0 else None


class CcsdsCdmMapper(SourceMapper):
    """Maps a CCSDS 508.0-B-1 full CDM (JSON) to CanonicalCDM."""

    SOURCE_NAME = "ccsds_cdm"
    SOURCE_VERSION = "v1-508.0-B-1"

    def from_source(self, raw: Dict[str, Any]) -> CanonicalCDM:
        if not isinstance(raw, dict):
            raise MapperError(f"CcsdsCdmMapper expects dict, got {type(raw).__name__}")

        conj = self._flatten({k: v for k, v in raw.items()
                              if k not in ("segment", "segments", "object1", "object2")})
        o1 = self._flatten(self._obj_block(raw, 0))
        o2 = self._flatten(self._obj_block(raw, 1))

        header = CDMHeader(
            CCSDS_CDM_VERS=_to_str(conj.get("CCSDS_CDM_VERS")) or "1.0",
            CREATION_DATE=_to_datetime(conj.get("CREATION_DATE")),
            ORIGINATOR=_to_str(conj.get("ORIGINATOR")),
            MESSAGE_ID=_to_str(conj.get("MESSAGE_ID")),
        )
        object1 = self._build_object(o1, "OBJECT1")
        object2 = self._build_object(o2, "OBJECT2")
        self._sigma_guard(object1, object2)

        relative_metadata = CDMRelativeMetadata(
            TCA=_to_datetime(conj.get("TCA")),
            MISS_DISTANCE=_to_float(conj.get("MISS_DISTANCE")),
            RELATIVE_SPEED=_to_float(conj.get("RELATIVE_SPEED")),
            RELATIVE_POSITION_R=_to_float(conj.get("RELATIVE_POSITION_R")),
            RELATIVE_POSITION_T=_to_float(conj.get("RELATIVE_POSITION_T")),
            RELATIVE_POSITION_N=_to_float(conj.get("RELATIVE_POSITION_N")),
            RELATIVE_VELOCITY_R=_to_float(conj.get("RELATIVE_VELOCITY_R")),
            RELATIVE_VELOCITY_T=_to_float(conj.get("RELATIVE_VELOCITY_T")),
            RELATIVE_VELOCITY_N=_to_float(conj.get("RELATIVE_VELOCITY_N")),
            COLLISION_PROBABILITY=_to_float(conj.get("COLLISION_PROBABILITY")),
            COLLISION_PROBABILITY_METHOD=_to_str(conj.get("COLLISION_PROBABILITY_METHOD")),
            CONJUNCTION_ID=_to_str(conj.get("MESSAGE_ID")),
            MAHALANOBIS_DISTANCE=self._compute_mahalanobis(object1, object2),
        )

        return CanonicalCDM(
            header=header,
            relative_metadata=relative_metadata,
            object1=object1,
            object2=object2,
            cas_extensions=CDMCASExtensions(),  # conjunction-level (space weather absent in CDM)
        )

    def _obj_block(self, raw, idx):
        seg = raw.get("segment") or raw.get("segments")
        if isinstance(seg, list) and len(seg) > idx and isinstance(seg[idx], dict):
            return seg[idx]
        b = raw.get(f"object{idx+1}")
        return b if isinstance(b, dict) else {}

    def _flatten(self, block):
        flat: Dict[str, Any] = {}

        def merge(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, dict):
                        merge(v)
                    else:
                        flat[k] = v
        merge(block)
        return flat

    def _build_object(self, of, obj_label):
        x, y, z = _to_float(of.get("X")), _to_float(of.get("Y")), _to_float(of.get("Z"))
        vx, vy, vz = _to_float(of.get("X_DOT")), _to_float(of.get("Y_DOT")), _to_float(of.get("Z_DOT"))
        sma = ecc = inc = apo_alt = per_alt = None
        if None not in (x, y, z, vx, vy, vz):
            sma, ecc, inc, apo_alt, per_alt = self._orbital_elements(x, y, z, vx, vy, vz)

        area_pc = _to_float(of.get("AREA_PC"))
        hbr = _to_float(of.get("HBR"))
        if hbr is not None:
            span = 2.0 * hbr
        elif area_pc and area_pc > 0:
            span = 2.0 * math.sqrt(area_pc / math.pi)
        else:
            span = None

        return CDMObject(
            metadata=CDMObjectMetadata(
                OBJECT=obj_label,
                OBJECT_DESIGNATOR=_to_str(of.get("OBJECT_DESIGNATOR")),
                OBJECT_NAME=_to_str(of.get("OBJECT_NAME")),
                OBJECT_TYPE=_to_object_class(of.get("OBJECT_TYPE")),
                INTERNATIONAL_DESIGNATOR=_to_str(of.get("INTERNATIONAL_DESIGNATOR")),
                REF_FRAME=_to_str(of.get("REF_FRAME")) or "EME2000",
                ORBIT_CENTER=_to_str(of.get("ORBIT_CENTER")) or "EARTH",
                COVARIANCE_METHOD=_to_str(of.get("COVARIANCE_METHOD")) or "CALCULATED",
                MANEUVERABLE=_to_str(of.get("MANEUVERABLE")),
                CATALOG_NAME=_to_str(of.get("CATALOG_NAME")) or "SATCAT",
            ),
            od_parameters=CDMODParameters(
                RECOMMENDED_OD_SPAN=_to_float(of.get("RECOMMENDED_OD_SPAN")),
                ACTUAL_OD_SPAN=_to_float(of.get("ACTUAL_OD_SPAN")),
                OBS_AVAILABLE=_to_int(of.get("OBS_AVAILABLE")),
                OBS_USED=_to_int(of.get("OBS_USED")),
                TRACKS_AVAILABLE=_to_int(of.get("TRACKS_AVAILABLE")),
                TRACKS_USED=_to_int(of.get("TRACKS_USED")),
                RESIDUALS_ACCEPTED=_to_float(of.get("RESIDUALS_ACCEPTED")),
                WEIGHTED_RMS=_to_float(of.get("WEIGHTED_RMS")),
                TIME_LASTOB_START=_to_datetime(of.get("TIME_LASTOB_START")),
                TIME_LASTOB_END=_to_datetime(of.get("TIME_LASTOB_END")),
            ),
            additional=CDMAdditionalParameters(
                AREA_PC=area_pc,
                AREA_DRG=_to_float(of.get("AREA_DRG")),
                AREA_SRP=_to_float(of.get("AREA_SRP")),
                MASS=_to_float(of.get("MASS")),
                HBR=hbr,
                CD_AREA_OVER_MASS=_to_float(of.get("CD_AREA_OVER_MASS")),
                CR_AREA_OVER_MASS=_to_float(of.get("CR_AREA_OVER_MASS")),
                SEDR=_to_float(of.get("SEDR")),
                THRUST_ACCELERATION=_to_float(of.get("THRUST_ACCELERATION")),
                X=x, Y=y, Z=z, X_DOT=vx, Y_DOT=vy, Z_DOT=vz,
                INCLINATION=inc,
                APOAPSIS_ALTITUDE=apo_alt,
                PERIAPSIS_ALTITUDE=per_alt,
            ),
            covariance=self._covariance(of),
            cas_extensions=CDMCASObjectExtensions(
                SEMI_MAJOR_AXIS=sma,
                ECCENTRICITY=ecc,
                RCS_ESTIMATE=area_pc,
                RISK_COMPUTATION_SIZE=span,
            ),
        )

    def _covariance(self, of):
        vals = {k: _to_float(of.get(k)) for k in _COV_KEYS}
        if all(v is None for v in vals.values()):
            return None
        frame = _to_str(of.get("COV_REF_FRAME") or of.get("COVARIANCE_REF_FRAME"))
        if frame and frame.upper().replace(" ", "") not in ("RTN", "UVW", "UWV"):
            log.warning("CcsdsCdmMapper: covariance frame %r not RTN; mapped as-is "
                        "(no frame conversion applied)", frame)
        return CDMCovarianceMatrix(**vals)

    def _orbital_elements(self, x, y, z, vx, vy, vz):
        r = math.sqrt(x*x + y*y + z*z)
        v2 = vx*vx + vy*vy + vz*vz
        if r <= 0:
            return None, None, None, None, None
        energy = v2 / 2.0 - MU_EARTH / r
        if energy >= 0:
            return None, None, None, None, None
        sma = -MU_EARTH / (2.0 * energy)
        hx, hy, hz = y*vz - z*vy, z*vx - x*vz, x*vy - y*vx
        h = math.sqrt(hx*hx + hy*hy + hz*hz)
        ex = (vy*hz - vz*hy) / MU_EARTH - x / r
        ey = (vz*hx - vx*hz) / MU_EARTH - y / r
        ez = (vx*hy - vy*hx) / MU_EARTH - z / r
        ecc = math.sqrt(ex*ex + ey*ey + ez*ez)
        inc = math.degrees(math.acos(max(-1.0, min(1.0, hz / h)))) if h > 0 else None
        apo_alt = sma * (1.0 + ecc) - R_EARTH
        per_alt = sma * (1.0 - ecc) - R_EARTH
        return sma, ecc, inc, apo_alt, per_alt

    def _sigma_guard(self, *objects):
        for i, obj in enumerate(objects, 1):
            cov = obj.covariance
            if not cov:
                continue
            for key in ("CR_R", "CT_T", "CN_N"):
                var = getattr(cov, key, None)
                if var is None:
                    continue
                if var < 0:
                    log.warning("CcsdsCdmMapper: object%d %s negative (%g) -- non-physical", i, key, var)
                    continue
                sig = math.sqrt(var)
                if not (_SIGMA_MIN_M <= sig <= _SIGMA_MAX_M):
                    log.warning("CcsdsCdmMapper: object%d sigma(%s)=%g m outside [%g,%g] -- "
                                "possible unit mismatch (expected m**2)", i, key, sig, _SIGMA_MIN_M, _SIGMA_MAX_M)

    def _position_cov_rtn(self, cov):
        """3x3 RTN position covariance (m^2) from canonical covariance; None if incomplete."""
        if cov is None:
            return None
        keys = ["CR_R", "CT_R", "CT_T", "CN_R", "CN_T", "CN_N"]
        v = {k: getattr(cov, k, None) for k in keys}
        if any(v[k] is None for k in keys):
            return None
        return np.array([[v["CR_R"], v["CT_R"], v["CN_R"]],
                         [v["CT_R"], v["CT_T"], v["CN_T"]],
                         [v["CN_R"], v["CN_T"], v["CN_N"]]], dtype=float)

    def _state(self, obj):
        a = obj.additional
        r = [a.X, a.Y, a.Z]; vv = [a.X_DOT, a.Y_DOT, a.Z_DOT]
        if any(c is None for c in r + vv):
            return None, None
        return r, vv

    def _compute_mahalanobis(self, object1, object2):
        """Standard B-plane Mahalanobis from both objects' ECI states + RTN position
        covariances (NASA CARA / CCSDS method). None if incomplete/degenerate -> the ML
        inference gate treats it as missing (never fabricated)."""
        try:
            r1, v1 = self._state(object1); r2, v2 = self._state(object2)
            C1 = self._position_cov_rtn(object1.covariance)
            C2 = self._position_cov_rtn(object2.covariance)
            if r1 is None or r2 is None or C1 is None or C2 is None:
                return None
            return _bplane_mahalanobis(r1, v1, C1, r2, v2, C2)
        except Exception as exc:
            log.warning("CcsdsCdmMapper: mahalanobis computation failed: %s", exc)
            return None
