"""Abstract base class for source mappers.

All CDM source mappers (Space-Track, TraCSS, EU SST, ESA Kelvins) implement
this interface. The contract is intentionally minimal:

    mapper = SomeMapper()
    canonical_cdm = mapper.from_source(raw_dict)

Mapper implementations are expected to:
1. Be source-specific and stateless (no instance state beyond config)
2. Never raise on missing/sparse fields (Optional everywhere)
3. Preserve original information — non-standard fields go into
   tracss_extensions.USER_DEFINED_* rather than being dropped
4. Log warnings (not errors) for unparseable values

Implementations should also expose:
- SOURCE_NAME: str  — identifier used in metadata / logs
- SOURCE_VERSION: str  — schema/API version supported
- EXPECTED_FIELDS: set[str]  — declarative documentation
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class MapperError(Exception):
    """Raised when a mapper encounters an unrecoverable structural problem.

    Sparse data is NOT an error — only structural issues (e.g. expected
    dict, got list) raise MapperError.
    """
    pass


class SourceMapper(ABC):
    """Abstract interface every source mapper must implement."""

    SOURCE_NAME: str = "abstract"
    SOURCE_VERSION: str = "n/a"
    EXPECTED_FIELDS: set = set()

    @abstractmethod
    def from_source(self, raw: Dict[str, Any]) -> Any:
        """Convert raw source dict to CanonicalCDM.

        Args:
            raw: dict-like CDM payload in source-specific format.

        Returns:
            CanonicalCDM instance. Even if input is empty/sparse, a
            valid (mostly-None) CanonicalCDM is returned.

        Raises:
            MapperError: if input is structurally invalid (e.g. not a dict).
        """
        raise NotImplementedError
