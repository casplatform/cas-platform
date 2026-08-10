"""CAS-specific CDM extensions — non-CCSDS quantities CAS preserves for ML.

These fields are NOT part of CCSDS 508.0-B-1 or TraCSS-Spec-001. They hold
source-provided quantities (space weather, conjunction geometry, osculating
orbital elements, RCS, risk-volume size) that the canonical CCSDS+TraCSS core
does not define, but which CAS's ML layer consumes as features. Every field is
tagged source='CAS-extension'. Sparse data is normal (Optional everywhere).

Provenance: field set established from the ESA Kelvins Collision Avoidance
Challenge dataset (Uriot et al. 2020) — the first source requiring these.
Other sources populate whatever subset they provide.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CDMCASExtensions(BaseModel):
    """Conjunction-level CAS extensions (space weather + encounter geometry)."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore", str_strip_whitespace=True)

    F10: Optional[float] = Field(default=None, description="10.7 cm solar radio flux index.", json_schema_extra={'units': '1e-22 W/(m^2 Hz)', 'source': 'CAS-extension'})
    F3M: Optional[float] = Field(default=None, description="81-day running mean of F10.7 (3 solar rotations).", json_schema_extra={'units': '1e-22 W/(m^2 Hz)', 'source': 'CAS-extension'})
    SSN: Optional[float] = Field(default=None, description="Wolf sunspot number.", json_schema_extra={'source': 'CAS-extension'})
    AP: Optional[float] = Field(default=None, description="Daily planetary geomagnetic amplitude (Ap) index.", json_schema_extra={'source': 'CAS-extension'})
    GEOCENTRIC_LATITUDE: Optional[float] = Field(default=None, description="Latitude of the conjunction point.", json_schema_extra={'units': 'deg', 'source': 'CAS-extension'})
    AZIMUTH: Optional[float] = Field(default=None, description="Relative velocity vector azimuth angle.", json_schema_extra={'units': 'deg', 'source': 'CAS-extension'})
    ELEVATION: Optional[float] = Field(default=None, description="Relative velocity vector elevation angle.", json_schema_extra={'units': 'deg', 'source': 'CAS-extension'})


class CDMCASObjectExtensions(BaseModel):
    """Object-level CAS extensions (osculating elements + physical size)."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore", str_strip_whitespace=True)

    SEMI_MAJOR_AXIS: Optional[float] = Field(default=None, description="Osculating semi-major axis (J2000).", json_schema_extra={'units': 'km', 'source': 'CAS-extension'})
    ECCENTRICITY: Optional[float] = Field(default=None, description="Osculating eccentricity.", json_schema_extra={'source': 'CAS-extension'})
    RCS_ESTIMATE: Optional[float] = Field(default=None, description="Radar cross-sectional area estimate.", json_schema_extra={'units': 'm^2', 'source': 'CAS-extension'})
    RISK_COMPUTATION_SIZE: Optional[float] = Field(default=None, description="Object size used by the collision-risk (Pc) computation; min 2 m diameter assumed for chaser.", json_schema_extra={'units': 'm', 'source': 'CAS-extension'})
