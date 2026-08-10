"""CanonicalCDM — top-level Conjunction Data Message model."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.cdm_header import CDMHeader
from schemas.cdm_relative_metadata import CDMRelativeMetadata
from schemas.cdm_object import CDMObject
from schemas.cdm_cas_extensions import CDMCASExtensions


class CanonicalCDM(BaseModel):
    """Canonical CDM — internal schema for all CAS conjunction data.

    Sources (Space-Track public, TraCSS, EU SST, ESA Kelvins) flow through
    source-specific mappers into this canonical form. Sparse data is normal.
    """
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
    )

    header: Optional[CDMHeader] = Field(default=None)
    relative_metadata: Optional[CDMRelativeMetadata] = Field(default=None)
    object1: Optional[CDMObject] = Field(default=None)
    object2: Optional[CDMObject] = Field(default=None)
    cas_extensions: Optional[CDMCASExtensions] = Field(default=None)

    def coverage(self) -> dict:
        """Count populated fields for ML confidence assessment."""
        def count_populated(obj):
            if obj is None:
                return 0, 0
            total = 0
            pop = 0
            if hasattr(obj, "model_fields"):
                for fname in obj.model_fields:
                    val = getattr(obj, fname, None)
                    if hasattr(val, "model_fields"):
                        sub_t, sub_p = count_populated(val)
                        total += sub_t
                        pop += sub_p
                    else:
                        total += 1
                        if val is not None:
                            pop += 1
            return total, pop

        sections = {}
        total = 0
        populated = 0
        for sec_name in ("header", "relative_metadata", "object1", "object2", "cas_extensions"):
            sec = getattr(self, sec_name, None)
            t, p = count_populated(sec)
            sections[sec_name] = {"total": t, "populated": p}
            total += t
            populated += p

        return {
            "total": total,
            "populated": populated,
            "coverage_pct": round(100 * populated / total, 1) if total else 0,
            "by_section": sections,
        }
