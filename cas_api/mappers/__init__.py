"""CAS source mappers — convert various CDM source formats to CanonicalCDM.

Mappers convert raw CDM data from each provider into the internal
canonical Pydantic model (schemas.CanonicalCDM).

Available mappers:
- SpaceTrackPublicMapper: Space-Track public 16-field tier (current production)
- SpaceTrackLegacyMapper: Space-Track Owner/Operator tier (future, SAT1_/SAT2_ prefix)
- TraCSSMapper: TraCSS JSON-TraCSS unique format (future)
- TraCSSJsonStMapper: TraCSS JSON-st format with SAT1_/SAT2_ prefix (future)
- EuSstMapper: EU SST CDM (future)
- EsaKelvinsMapper: ESA Kelvins training dataset (for model retraining)

All mappers implement the SourceMapper abstract interface:
    mapper.from_source(raw_dict: dict) -> CanonicalCDM
"""
from mappers.base import SourceMapper, MapperError
from mappers.spacetrack_public import SpaceTrackPublicMapper
from mappers.esa_kelvins import EsaKelvinsMapper
from mappers.tracss import TraCSSMapper

__all__ = [
    "SourceMapper",
    "MapperError",
    "SpaceTrackPublicMapper",
    "EsaKelvinsMapper",
    "TraCSSMapper",
]
from mappers.ccsds_cdm import CcsdsCdmMapper
