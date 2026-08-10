"""Space-Track public CDM tier mapper (16 fields).

Maps the current Space-Track public-tier API response into CanonicalCDM.
This is what CAS ingests today via cas_engine.py's parse_cdm.

The 16 fields available without Owner/Operator credentials:
    CDM_ID, CREATED, EMERGENCY_REPORTABLE, MIN_RNG, PC, TCA,
    SAT_1_ID, SAT_1_NAME, SAT1_OBJECT_TYPE, SAT1_RCS, SAT_1_EXCL_VOL,
    SAT_2_ID, SAT_2_NAME, SAT2_OBJECT_TYPE, SAT2_RCS, SAT_2_EXCL_VOL

After mapping, ~190 of CanonicalCDM's ~238 addressable fields will be None
(no covariance, no OD parameters, no full state vectors, no J2K elements).
This is expected for the public tier.

Non-CCSDS fields preserved as TraCSS USER_DEFINED_* (no information loss):
- EMERGENCY_REPORTABLE  -> relative_metadata (custom Yes/No flag)
- SAT*_RCS              -> object.tracss_extensions (radar cross-section bin)
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from schemas import (
    CanonicalCDM,
    CDMHeader,
    CDMRelativeMetadata,
    CDMObject,
    CDMObjectMetadata,
    CDMTraCSSObjectExtensions,
    ObjectClass,
)
from mappers.base import SourceMapper, MapperError


log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Tip dönüşüm yardımcıları
# ────────────────────────────────────────────────────────────────

def _to_float(v: Any) -> Optional[float]:
    """Safely convert to float. Returns None if not parseable."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        # NaN / Inf reddedilir
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (ValueError, TypeError):
        log.debug("Could not parse float: %r", v)
        return None


def _to_str(v: Any) -> Optional[str]:
    """Convert to string, treating empty/None as missing."""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _to_datetime(v: Any) -> Optional[datetime]:
    """Parse common CDM datetime formats. Returns None if unparseable.

    Accepted formats (CCSDS spec + Space-Track variants):
        2026-06-04T10:00:00.000000
        2026-06-04T10:00:00
        2026-06-04 10:00:00
        2026-156T10:00:00 (DOY format)
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    # Try ISO formats first
    candidates = [
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%jT%H:%M:%S.%f",  # CCSDS DOY
        "%Y-%jT%H:%M:%S",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    log.debug("Could not parse datetime: %r", s)
    return None


def _to_object_class(v: Any) -> Optional[ObjectClass]:
    """Map Space-Track object type strings to ObjectClass enum.

    Space-Track variants seen in production:
        'PAYLOAD', 'DEBRIS', 'ROCKET BODY', 'TBA' (unknown), '' (empty)
    """
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
# Mapper sınıfı
# ────────────────────────────────────────────────────────────────


class SpaceTrackPublicMapper(SourceMapper):
    """Maps Space-Track public-tier 16-field CDM responses to CanonicalCDM."""

    SOURCE_NAME = "spacetrack_public"
    SOURCE_VERSION = "v1-16fields"
    EXPECTED_FIELDS = {
        "CDM_ID", "CREATED", "EMERGENCY_REPORTABLE", "MIN_RNG", "PC", "TCA",
        "SAT_1_ID", "SAT_1_NAME", "SAT1_OBJECT_TYPE", "SAT1_RCS", "SAT_1_EXCL_VOL",
        "SAT_2_ID", "SAT_2_NAME", "SAT2_OBJECT_TYPE", "SAT2_RCS", "SAT_2_EXCL_VOL",
    }

    def from_source(self, raw: Dict[str, Any]) -> CanonicalCDM:
        """Convert a Space-Track public CDM dict to CanonicalCDM."""
        if not isinstance(raw, dict):
            raise MapperError(
                f"SpaceTrackPublicMapper expects dict, got {type(raw).__name__}"
            )

        # Track unknown fields (logged for visibility, not dropped silently)
        unknown_keys = set(raw.keys()) - self.EXPECTED_FIELDS
        if unknown_keys:
            log.debug("Unexpected Space-Track fields ignored: %s", sorted(unknown_keys))

        # ── HEADER ─────────────────────────────────────────────────
        header = CDMHeader(
            CCSDS_CDM_VERS="1.0",
            CREATION_DATE=_to_datetime(raw.get("CREATED")),
            ORIGINATOR="SPACETRACK",
            MESSAGE_ID=_to_str(raw.get("CDM_ID")),
        )

        # ── RELATIVE METADATA ──────────────────────────────────────
        # SCREEN_VOLUME_X: Space-Track public sadece "exclusion volume"
        # değerini her object için ayrı veriyor (SAT_1_EXCL_VOL, SAT_2_EXCL_VOL).
        # CCSDS RelativeMetadata bir tek "SCREEN_VOLUME_X" tanıyor — daha
        # büyüğünü ortak değer olarak alıyoruz (en muhafazakar yaklaşım).
        ev1 = _to_float(raw.get("SAT_1_EXCL_VOL"))
        ev2 = _to_float(raw.get("SAT_2_EXCL_VOL"))
        screen_vol_x = max(v for v in (ev1, ev2) if v is not None) if (ev1 or ev2) else None

        relative_metadata = CDMRelativeMetadata(
            TCA=_to_datetime(raw.get("TCA")),
            MISS_DISTANCE=_to_float(raw.get("MIN_RNG")),
            COLLISION_PROBABILITY=_to_float(raw.get("PC")),
            SCREEN_VOLUME_X=screen_vol_x,
        )

        # ── OBJECT 1 ───────────────────────────────────────────────
        object1 = CDMObject(
            metadata=CDMObjectMetadata(
                OBJECT="OBJECT1",
                OBJECT_DESIGNATOR=_to_str(raw.get("SAT_1_ID")),
                OBJECT_NAME=_to_str(raw.get("SAT_1_NAME")),
                OBJECT_TYPE=_to_object_class(raw.get("SAT1_OBJECT_TYPE")),
                CATALOG_NAME="SATCAT",
            ),
            tracss_extensions=self._build_object_extensions(
                rcs=raw.get("SAT1_RCS"),
                excl_vol=raw.get("SAT_1_EXCL_VOL"),
                emergency_reportable=raw.get("EMERGENCY_REPORTABLE"),
                is_object1=True,
            ),
        )

        # ── OBJECT 2 ───────────────────────────────────────────────
        object2 = CDMObject(
            metadata=CDMObjectMetadata(
                OBJECT="OBJECT2",
                OBJECT_DESIGNATOR=_to_str(raw.get("SAT_2_ID")),
                OBJECT_NAME=_to_str(raw.get("SAT_2_NAME")),
                OBJECT_TYPE=_to_object_class(raw.get("SAT2_OBJECT_TYPE")),
                CATALOG_NAME="SATCAT",
            ),
            tracss_extensions=self._build_object_extensions(
                rcs=raw.get("SAT2_RCS"),
                excl_vol=raw.get("SAT_2_EXCL_VOL"),
                emergency_reportable=raw.get("EMERGENCY_REPORTABLE"),
                is_object1=False,
            ),
        )

        return CanonicalCDM(
            header=header,
            relative_metadata=relative_metadata,
            object1=object1,
            object2=object2,
        )

    def _build_object_extensions(
        self,
        rcs: Any,
        excl_vol: Any,
        emergency_reportable: Any,
        is_object1: bool,
    ) -> Optional[CDMTraCSSObjectExtensions]:
        """Preserve non-CCSDS Space-Track fields in TraCSS USER_DEFINED slots.

        We only emit object1's slot for shared fields (EMERGENCY_REPORTABLE)
        to avoid duplication. Object-specific fields (RCS, EXCL_VOL) always
        get emitted on both sides.
        """
        rcs_str = _to_str(rcs)
        excl_vol_str = _to_str(excl_vol)
        emrep_str = _to_str(emergency_reportable) if is_object1 else None

        # Hiçbir bilgi yoksa None döndür (clean output)
        if not any((rcs_str, excl_vol_str, emrep_str)):
            return None

        # USER_DEFINED_CORRELATION_ID -> bilgileri yapısal saklamak için kullanılıyor
        # Bunlar tek-string olarak saklanır çünkü Pydantic schema'da USER_DEFINED_*
        # field'ları str-typed (specifik anlamları operatör tanımlı).
        return CDMTraCSSObjectExtensions(
            USER_DEFINED_CORRELATION_ID=(
                f"SPACETRACK_PUBLIC|RCS={rcs_str or 'NA'}"
                f"|EXCL_VOL={excl_vol_str or 'NA'}"
                + (f"|EMERGENCY={emrep_str}" if emrep_str else "")
            ),
        )
