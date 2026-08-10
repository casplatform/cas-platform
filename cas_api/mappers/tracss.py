"""TraCSS CDM mapper — TraCSS-Spec-001 v2.1 (Office of Space Commerce).

TraCSS CDMs use STANDARD CCSDS 508.0-B-1 keyword names (verified against
tracss_fields.csv: 107 fields, zero SAT1_/SAT2_ prefixes — that prefixing only
applies to TraCSS's separate "JSON-st" space-track-compatibility flavour, not
the canonical TraCSS-unique format handled here). Because the field names are
CCSDS-standard, the CCSDS-common 83 fields (covariance, OD parameters, state
vectors, derived orbital elements, B-plane Mahalanobis) are handled by
CcsdsCdmMapper — this mapper COMPOSES that and layers ONLY the TraCSS-specific
relative-metadata fields that CCSDS does not define.

DRY: CcsdsCdmMapper does the heavy physics (orbital elements from state vectors,
B-plane Mahalanobis from covariance, sigma unit guard). This class adds the
~15 TraCSS-only conjunction-level fields, all of which already exist in
CDMRelativeMetadata / CDMHeader (schema source-tagged 'TraCSS-Spec-001').

Mahalanobis precedence: TraCSS supplies its own MAHALANOBIS_DISTANCE computed
from its OD covariance. That source value is AUTHORITATIVE and takes precedence
over CcsdsCdmMapper's derived B-plane value (which we keep only as a fallback
when the source omits it). No fabrication: if neither is present -> None.

Source-tagged honesty: every field here is read directly from the TraCSS CDM;
nothing is synthesised. Sparse input -> mostly-None canonical, never an error.
"""
import logging
from typing import Any, Dict, Optional

from schemas import CanonicalCDM
from mappers.base import SourceMapper, MapperError
from mappers.ccsds_cdm import (
    CcsdsCdmMapper, _to_float, _to_str, _to_datetime,
)

log = logging.getLogger(__name__)


class TraCSSMapper(SourceMapper):
    """Maps a TraCSS-Spec-001 v2.1 CDM (TraCSS-unique JSON) to CanonicalCDM by
    composing CcsdsCdmMapper and layering TraCSS-specific fields."""

    SOURCE_NAME = "tracss"
    SOURCE_VERSION = "TraCSS-Spec-001-v2.1"
    EXPECTED_FIELDS = {
        # CCSDS-common (delegated to CcsdsCdmMapper) + TraCSS-specific below
        "CONJUNCTION_ID", "MAHALANOBIS_DISTANCE", "APPROACH_ANGLE",
        "START_SCREEN_PERIOD", "STOP_SCREEN_PERIOD", "SCREEN_VOLUME_FRAME",
        "SCREEN_VOLUME_SHAPE", "SCREEN_VOLUME_X", "SCREEN_VOLUME_Y",
        "SCREEN_VOLUME_Z", "SCREEN_PC_THRESHOLD", "COLLISION_MAX_PROBABILITY",
        "COLLISION_MAX_PC_METHOD", "CLASSIFICATION", "MESSAGE_FOR",
    }

    def __init__(self):
        self._ccsds = CcsdsCdmMapper()

    def from_source(self, raw: Dict[str, Any]) -> CanonicalCDM:
        if not isinstance(raw, dict):
            raise MapperError(f"TraCSSMapper expects dict, got {type(raw).__name__}")

        # 1) Delegate the CCSDS-common heavy lifting (covariance, OD, state,
        #    orbital elements, derived B-plane Mahalanobis, sigma guard).
        cdm = self._ccsds.from_source(raw)

        # 2) Layer TraCSS-specific relative-metadata fields (Pydantic model is
        #    mutable; we set directly). All keys are CCSDS-standard names.
        rm = cdm.relative_metadata

        # CONJUNCTION_ID: TraCSS provides it explicitly; prefer it over the
        # MESSAGE_ID-derived fallback CcsdsCdmMapper set.
        conj_id = _to_str(raw.get("CONJUNCTION_ID"))
        if conj_id:
            rm.CONJUNCTION_ID = conj_id

        # MAHALANOBIS: source value is authoritative (TraCSS's own OD covariance).
        # Keep CcsdsCdmMapper's derived value only as fallback when source omits it.
        src_maha = _to_float(raw.get("MAHALANOBIS_DISTANCE"))
        if src_maha is not None:
            rm.MAHALANOBIS_DISTANCE = src_maha

        # Pure TraCSS-only fields (absent from CCSDS — CcsdsCdmMapper never set them).
        rm.APPROACH_ANGLE = _to_float(raw.get("APPROACH_ANGLE"))
        rm.CLASSIFICATION = _to_str(raw.get("CLASSIFICATION"))
        rm.COLLISION_MAX_PROBABILITY = _to_float(raw.get("COLLISION_MAX_PROBABILITY"))
        rm.COLLISION_MAX_PC_METHOD = _to_str(raw.get("COLLISION_MAX_PC_METHOD"))
        rm.SCREEN_PC_THRESHOLD = _to_float(raw.get("SCREEN_PC_THRESHOLD"))

        # Screening window + volume (CCSDS-defined but optional; CcsdsCdmMapper
        # does not currently read them, so we populate from TraCSS here).
        rm.START_SCREEN_PERIOD = _to_datetime(raw.get("START_SCREEN_PERIOD"))
        rm.STOP_SCREEN_PERIOD = _to_datetime(raw.get("STOP_SCREEN_PERIOD"))
        rm.SCREEN_VOLUME_FRAME = _to_str(raw.get("SCREEN_VOLUME_FRAME"))
        rm.SCREEN_VOLUME_SHAPE = _to_str(raw.get("SCREEN_VOLUME_SHAPE"))
        rm.SCREEN_VOLUME_X = _to_float(raw.get("SCREEN_VOLUME_X"))
        rm.SCREEN_VOLUME_Y = _to_float(raw.get("SCREEN_VOLUME_Y"))
        rm.SCREEN_VOLUME_Z = _to_float(raw.get("SCREEN_VOLUME_Z"))

        # MESSAGE_FOR lives on the header.
        msg_for = _to_str(raw.get("MESSAGE_FOR"))
        if msg_for and hasattr(cdm.header, "MESSAGE_FOR"):
            cdm.header.MESSAGE_FOR = msg_for

        # Tag originator if TraCSS didn't set it.
        if not cdm.header.ORIGINATOR:
            cdm.header.ORIGINATOR = "TraCSS"

        return cdm
