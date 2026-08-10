"""CDM Object data — CCSDS §3.4-3.5 + TraCSS extensions."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.cdm_enums import (
    ObjectType, ObjectClass, ManeuverableStatus, CovarianceMethod,
    ReferenceFrame, OrbitCenter, YesNoFlag, OpsStatus,
)
from schemas.cdm_cas_extensions import CDMCASObjectExtensions


class CDMObjectMetadata(BaseModel):
    """Object identification & propagation models (CCSDS §3.4)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    ATMOSPHERIC_MODEL: Optional[str] = Field(default=None, description='The atmospheric density model used for the OD of the object. If ‘NONE’ is specif', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CATALOG_NAME: Optional[str] = Field(default=None, description='The satellite catalog used for the object. Value should be taken from the SANA ‘', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    COVARIANCE_METHOD: Optional[CovarianceMethod] = Field(default=None, description='Method used to calculate the covariance during the OD that produced the state ve', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    EARTH_TIDES: Optional[YesNoFlag] = Field(default=None, description='Indication of whether solid Earth and ocean tides were used for the OD of the ob', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    EPHEMERIS_NAME: Optional[str] = Field(default=None, description='Unique name of the external ephemeris file used for the object or NONE. This is', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    GRAVITY_MODEL: Optional[str] = Field(default=None, description='The gravity model used for the OD of the object. (See annex E under GRAVITY_MODE', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    INTERNATIONAL_DESIGNATOR: Optional[str] = Field(default=None, description='The full international designator for the object. Values shall have the format Y', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    INTRACK_THRUST: Optional[YesNoFlag] = Field(default=None, description='Indication of whether in-track thrust modeling was used for the OD of the object', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    MANEUVERABLE: Optional[ManeuverableStatus] = Field(default=None, description='The maneuver capacity of the object. (See 1.4.3.1 for definition of ‘N/A’.)', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    N_BODY_PERTURBATIONS: Optional[str] = Field(default=None, description='The N-body gravitational perturbations used for the OD of the object. If ‘NONE’', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBJECT: Optional[ObjectType] = Field(default=None, description='The object to which the metadata and data apply (Object1 or Object2).', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBJECT_DESIGNATOR: Optional[str] = Field(default=None, description='The satellite catalog designator for the object. (See 5.2.9 for formatting rules', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBJECT_NAME: Optional[str] = Field(default=None, description='Spacecraft name for the object.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBJECT_TYPE: Optional[ObjectClass] = Field(default=None, description='The object type.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OPERATOR_CONTACT_POSITION: Optional[str] = Field(default=None, description='Contact position of the owner/operator of the object.', json_schema_extra={'source': 'CCSDS-508.0-B-1'})
    OPERATOR_EMAIL: Optional[str] = Field(default=None, description='Email address of the contact position or organization of the object.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OPERATOR_ORGANIZATION: Optional[str] = Field(default=None, description='Contact organization of the object.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OPERATOR_PHONE: Optional[str] = Field(default=None, description='Phone number of the contact position or organization for the object.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OPS_STATUS: Optional[OpsStatus] = Field(default=None, description='Specification of the operational status of the space object. Information will be pulled from corresp', json_schema_extra={'source': 'TraCSS-Spec-001'})
    ORBIT_CENTER: Optional[OrbitCenter] = Field(default=None, description='The central body about which Object1 and Object2 orbit. If not specified, the ce', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    REF_FRAME: Optional[ReferenceFrame] = Field(default=None, description='Name of the reference frame in which the state vector data are given. Value must', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SOLAR_RAD_PRESSURE: Optional[YesNoFlag] = Field(default=None, description='Indication of whether solar radiation pressure perturbations were used for the O', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})


class CDMODParameters(BaseModel):
    """Orbit Determination quality parameters (CCSDS §3.5)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    ACTUAL_OD_SPAN: Optional[float] = Field(default=None, description='Based on the observations available and the RECOMMENDED_OD_SPAN, the actual time', json_schema_extra={'units': 'd', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBS_AVAILABLE: Optional[int] = Field(default=None, description='The number of observations available for the OD of the object. (See annex E for', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    OBS_USED: Optional[int] = Field(default=None, description='The number of observations accepted for the OD of the object. (See annex E for d', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RECOMMENDED_OD_SPAN: Optional[float] = Field(default=None, description='The recommended OD time span calculated for the object. (See annex E for definit', json_schema_extra={'units': 'd', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RESIDUALS_ACCEPTED: Optional[float] = Field(default=None, description='The percentage of residuals accepted in the OD of the object. Data type = double', json_schema_extra={'units': '%', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    TIME_LASTOB_END: Optional[datetime] = Field(default=None, description='The end of a time interval (UTC) that contains the time of the last accepted obs', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    TIME_LASTOB_START: Optional[datetime] = Field(default=None, description='The start of a time interval (UTC) that contains the time of the last accepted o', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    TRACKS_AVAILABLE: Optional[int] = Field(default=None, description='The number of sensor tracks available for the OD of the object. (See annex E for', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    TRACKS_USED: Optional[int] = Field(default=None, description='The number of sensor tracks accepted for the OD of the object. (See annex E for', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    WEIGHTED_RMS: Optional[float] = Field(default=None, description='The weighted Root Mean Square (RMS) of the residuals from a batch least squares', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})


class CDMAdditionalParameters(BaseModel):
    """Physical params + state vectors + TraCSS orbital extras"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    APOAPSIS_ALTITUDE: Optional[float] = Field(default=None, description='The apogee of the object in km above oblate earth surface', json_schema_extra={'source': 'TraCSS-Spec-001'})
    AREA_DRG: Optional[float] = Field(default=None, description='The effective area of the object exposed to atmospheric drag. (See annex E for d', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1'})
    AREA_PC: Optional[float] = Field(default=None, description='The actual area of the object. (See annex E for definition.) Data type = double.', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    AREA_SRP: Optional[float] = Field(default=None, description='The effective area of the object exposed to solar radiation pressure. (See annex', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1'})
    CD_AREA_OVER_MASS: Optional[float] = Field(default=None, description='The object’s C •A/m used to propagate the D state vector and covariance to TCA.', json_schema_extra={'units': 'm**2/kg', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CR_AREA_OVER_MASS: Optional[float] = Field(default=None, description='The object’s C •A/m used to propagate the r state vector and covariance to TCA.', json_schema_extra={'units': 'm**2/kg', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    DENSITY_FORECAST_UNCERTAINTY: Optional[float] = Field(default=None, description='The dynamic considers parameter (DCP) 1-sigma uncertainty of the relative atmospheric density for th', json_schema_extra={'source': 'TraCSS-Spec-001'})
    HBR: Optional[float] = Field(default=None, description='The Hard Body Radius in m used by the TraCSS system to calculate probability of collision', json_schema_extra={'source': 'TraCSS-Spec-001'})
    INCLINATION: Optional[float] = Field(default=None, description='The inclination of the object in deg', json_schema_extra={'source': 'TraCSS-Spec-001'})
    MASS: Optional[float] = Field(default=None, description='The mass of the object. Data type = double.', json_schema_extra={'units': 'kg', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    PERIAPSIS_ALTITUDE: Optional[float] = Field(default=None, description='The perigee of the object in km above oblate earth surface', json_schema_extra={'source': 'TraCSS-Spec-001'})
    SCREENING_DATA_SOURCE: Optional[str] = Field(default=None, description='The data used to generate the CDM', json_schema_extra={'source': 'TraCSS-Spec-001'})
    SEDR: Optional[float] = Field(default=None, description='The amount of energy being removed from the object’s orbit by atmospheric drag.', json_schema_extra={'units': 'W/kg', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    THRUST_ACCELERATION: Optional[float] = Field(default=None, description='The object’s acceleration due to in-track thrust used to propagate the state vec', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    X: Optional[float] = Field(default=None)
    X_DOT: Optional[float] = Field(default=None, description='Object Velocity Vector X component.', json_schema_extra={'units': 'km/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    Y: Optional[float] = Field(default=None)
    Y_DOT: Optional[float] = Field(default=None, description='Object Velocity Vector Y component.', json_schema_extra={'units': 'km/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    Z: Optional[float] = Field(default=None)
    Z_DOT: Optional[float] = Field(default=None, description='Object Velocity Vector Z component.', json_schema_extra={'units': 'km/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})


class CDMCovarianceMatrix(BaseModel):
    """9x9 lower-triangular covariance (CCSDS Table 3-8)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    CDRG_DRG: Optional[float] = Field(default=None, description='Object covariance matrix [7,7].', json_schema_extra={'units': 'm**4/kg**2', 'source': 'CCSDS-508.0-B-1'})
    CDRG_N: Optional[float] = Field(default=None, description='Object covariance matrix [7,3].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CDRG_NDOT: Optional[float] = Field(default=None, description='Object covariance matrix [7,6].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CDRG_R: Optional[float] = Field(default=None, description='Object covariance matrix [7,1].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CDRG_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [7,4].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CDRG_T: Optional[float] = Field(default=None, description='Object covariance matrix [7,2].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CDRG_TDOT: Optional[float] = Field(default=None, description='Object covariance matrix [7,5].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CNDOT_N: Optional[float] = Field(default=None, description='Object covariance matrix [6,3].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CNDOT_NDOT: Optional[float] = Field(default=None, description='Object covariance matrix [6,6].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CNDOT_R: Optional[float] = Field(default=None, description='Object covariance matrix [6,1].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CNDOT_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [6,4].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CNDOT_T: Optional[float] = Field(default=None, description='Object covariance matrix [6,2].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CNDOT_TDOT: Optional[float] = Field(default=None, description='Object covariance matrix [6,5].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CN_N: Optional[float] = Field(default=None, description='Object covariance matrix [3,3].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CN_R: Optional[float] = Field(default=None, description='Object covariance matrix [3,1].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CN_T: Optional[float] = Field(default=None, description='Object covariance matrix [3,2].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CRDOT_N: Optional[float] = Field(default=None, description='Object covariance matrix [4,3].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CRDOT_R: Optional[float] = Field(default=None, description='Object covariance matrix [4,1].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CRDOT_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [4,4].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CRDOT_T: Optional[float] = Field(default=None, description='Object covariance matrix [4,2].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CR_R: Optional[float] = Field(default=None, description='Object covariance matrix [1,1].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CSRP_DRG: Optional[float] = Field(default=None, description='Object covariance matrix [8,7].', json_schema_extra={'units': 'm**4/kg**2', 'source': 'CCSDS-508.0-B-1'})
    CSRP_N: Optional[float] = Field(default=None, description='Object covariance matrix [8,3].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CSRP_NDOT: Optional[float] = Field(default=None, description='Object covariance matrix [8,6].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CSRP_R: Optional[float] = Field(default=None, description='Object covariance matrix [8,1].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CSRP_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [8,4].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CSRP_SRP: Optional[float] = Field(default=None, description='Object covariance matrix [8,8].', json_schema_extra={'units': 'm**4/kg**2', 'source': 'CCSDS-508.0-B-1'})
    CSRP_T: Optional[float] = Field(default=None, description='Object covariance matrix [8,2].', json_schema_extra={'units': 'm**3/kg', 'source': 'CCSDS-508.0-B-1'})
    CSRP_TDOT: Optional[float] = Field(default=None, description='Object covariance matrix [8,5].', json_schema_extra={'units': 'm**3/(kg*s)', 'source': 'CCSDS-508.0-B-1'})
    CTDOT_N: Optional[float] = Field(default=None, description='Object covariance matrix [5,3].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CTDOT_R: Optional[float] = Field(default=None, description='Object covariance matrix [5,1].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CTDOT_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [5,4].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CTDOT_T: Optional[float] = Field(default=None, description='Object covariance matrix [5,2].', json_schema_extra={'units': 'm**2/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CTDOT_TDOT: Optional[float] = Field(default=None, description='Object covariance matrix [5,5].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CTHR_DRG: Optional[float] = Field(default=None, description='Object covariance matrix [9,7].', json_schema_extra={'units': 'm**3/(kg*s**2)', 'source': 'CCSDS-508.0-B-1'})
    CTHR_N: Optional[float] = Field(default=None, description='Object covariance matrix [9,3].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1'})
    CTHR_NDOT: Optional[float] = Field(default=None, description='Object covariance matrix [9,6].', json_schema_extra={'units': 'm**2/s**3', 'source': 'CCSDS-508.0-B-1'})
    CTHR_R: Optional[float] = Field(default=None, description='Object covariance matrix [9,1].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1'})
    CTHR_RDOT: Optional[float] = Field(default=None, description='Object covariance matrix [9,4].', json_schema_extra={'units': 'm**2/s**3', 'source': 'CCSDS-508.0-B-1'})
    CTHR_SRP: Optional[float] = Field(default=None, description='Object covariance matrix [9,8].', json_schema_extra={'units': 'm**3/(kg*s**2)', 'source': 'CCSDS-508.0-B-1'})
    CTHR_T: Optional[float] = Field(default=None, description='Object covariance matrix [9,2].', json_schema_extra={'units': 'm**2/s**2', 'source': 'CCSDS-508.0-B-1'})
    CTHR_TDOT: Optional[float] = Field(default=None, description='Object covariance matrix [9,5].', json_schema_extra={'units': 'm**2/s**3', 'source': 'CCSDS-508.0-B-1'})
    CTHR_THR: Optional[float] = Field(default=None, description='Object covariance matrix [9,9].', json_schema_extra={'units': 'm**2/s**4', 'source': 'CCSDS-508.0-B-1'})
    CT_R: Optional[float] = Field(default=None, description='Object covariance matrix [2,1].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CT_T: Optional[float] = Field(default=None, description='Object covariance matrix [2,2].', json_schema_extra={'units': 'm**2', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})


class CDMTraCSSObjectExtensions(BaseModel):
    """TraCSS object-level extensions (USER_DEFINED_*, DCP_*)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    DCP_SENSITIVITY_VECTOR_POSITION: Optional[str] = Field(default=None, description='The DCP position sensitivity vector expressed in the object’s radial-transverse-normal (RTN) referen', json_schema_extra={'source': 'TraCSS-Spec-001'})
    DCP_SENSITIVITY_VECTOR_VELOCITY: Optional[str] = Field(default=None, description='The DCP velocity vector that relates changes in the object’s TCA inertial velocity vector to variati', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_CORRELATION_ID: Optional[str] = Field(default=None, description='The unique ID correlates the CDM to the package of SP Vectors used by TraCSS. May be used by operato', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_DILUTION_SIGNIFICANCE: Optional[str] = Field(default=None, description='Value indicating difference between Pc and Max Pc. This relates how severely diluted a conjunction i', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_DILUTION_STATUS: Optional[str] = Field(default=None, description='Flag indicating whether the conjunction is in the dilution region or robust region.', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_ENVIRONMENTAL_IMPACT_FRAGMENTATION: Optional[str] = Field(default=None, description='Value indicating number of predicted fragments if this collision were to occur', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_FRAGMENTATION_MODEL: Optional[str] = Field(default=None, description='Free text field containing the name of the space environment fragmentation model used', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_MEETS_ALERTABLE_CRITERIA: Optional[str] = Field(default=None, description='A comment placed here to indicate if this CDM meets TraCSS alertable criteria defined in the TraCSS', json_schema_extra={'source': 'TraCSS-Spec-001'})
    USER_DEFINED_RUN_ID: Optional[str] = Field(default=None, description='The unique ID of the conjunction analysis run that produced this CDM, for traceability.', json_schema_extra={'source': 'TraCSS-Spec-001'})


class CDMObject(BaseModel):
    """A single object's full CDM data (used twice: object1 + object2)."""
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    metadata: Optional[CDMObjectMetadata] = Field(default=None)
    od_parameters: Optional[CDMODParameters] = Field(default=None)
    additional: Optional[CDMAdditionalParameters] = Field(default=None)
    covariance: Optional[CDMCovarianceMatrix] = Field(default=None)
    tracss_extensions: Optional[CDMTraCSSObjectExtensions] = Field(default=None)
    cas_extensions: Optional[CDMCASObjectExtensions] = Field(default=None)
