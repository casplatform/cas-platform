"""CDM enum types — CCSDS 508.0-B-1 + TraCSS-Spec-001 v2.1 normative values."""
from enum import Enum


class ObjectType(str, Enum):
    """OBJECT identifier — Object1 or Object2."""
    OBJECT1 = "OBJECT1"
    OBJECT2 = "OBJECT2"


class ObjectClass(str, Enum):
    """OBJECT_TYPE — physical category of the satellite/debris."""
    PAYLOAD = "PAYLOAD"
    ROCKET_BODY = "ROCKET BODY"
    DEBRIS = "DEBRIS"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


class ManeuverableStatus(str, Enum):
    """MANEUVERABLE — operator's maneuver capability."""
    YES = "YES"
    NO = "NO"
    NA = "N/A"
    UNKNOWN = "UNKNOWN"


class CovarianceMethod(str, Enum):
    """COVARIANCE_METHOD — how the covariance matrix was computed."""
    CALCULATED = "CALCULATED"
    DEFAULT = "DEFAULT"


class ReferenceFrame(str, Enum):
    """REF_FRAME — coordinate reference frame for position/velocity."""
    EME2000 = "EME2000"
    GCRF = "GCRF"
    ITRF2000 = "ITRF2000"
    ITRF93 = "ITRF93"
    ITRF97 = "ITRF97"
    TEME = "TEME"
    TOD = "TOD"


class OrbitCenter(str, Enum):
    """ORBIT_CENTER — central body about which both objects orbit."""
    EARTH = "EARTH"
    SUN = "SUN"
    MOON = "MOON"
    MARS = "MARS"
    VENUS = "VENUS"
    JUPITER = "JUPITER"


class YesNoFlag(str, Enum):
    """Binary YES/NO indicators."""
    YES = "YES"
    NO = "NO"


class ScreenVolumeShape(str, Enum):
    """SCREEN_VOLUME_SHAPE — geometry of the screening volume."""
    ELLIPSOID = "ELLIPSOID"
    BOX = "BOX"
    SPHERE = "SPHERE"


class OpsStatus(str, Enum):
    """OPS_STATUS — operational status (TraCSS extension)."""
    OPERATIONAL = "OPERATIONAL"
    OPERATIONAL_MANEUVERABLE = "OPERATIONAL_MANEUVERABLE"
    OPERATIONAL_NONMANEUVERABLE = "OPERATIONAL_NONMANEUVERABLE"
    NONOPERATIONAL = "NONOPERATIONAL"
    UNKNOWN = "UNKNOWN"
