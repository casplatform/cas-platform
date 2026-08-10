"""CDM Relative Metadata + Data — CCSDS §3.3 + TraCSS extensions."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.cdm_enums import ScreenVolumeShape


class CDMRelativeMetadata(BaseModel):
    """CDM Relative Metadata + Data (CCSDS Table 3-2, 3-3 + TraCSS ext)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    APPROACH_ANGLE: Optional[float] = Field(default=None, description='The angle between the inertial velocity vector of Object1 and the inertial velocity vector of Object', json_schema_extra={'source': 'TraCSS-Spec-001'})
    CLASSIFICATION: Optional[str] = Field(default=None, description='Description of dissemination controls', json_schema_extra={'source': 'TraCSS-Spec-001'})
    COLLISION_MAX_PC_METHOD: Optional[str] = Field(default=None, description='Method used to calculate COLLISION_MAX_PROBABILI TY', json_schema_extra={'source': 'TraCSS-Spec-001'})
    COLLISION_MAX_PROBABILITY: Optional[float] = Field(default=None, description='The maximum collision probability that Object1 and Object2 will collide', json_schema_extra={'source': 'TraCSS-Spec-001'})
    COLLISION_PROBABILITY: Optional[float] = Field(default=None, description='The probability (denoted ‘p’ where 0.0<=p<=1.0), that Object1 and Object2 will c', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    COLLISION_PROBABILITY_METHOD: Optional[str] = Field(default=None, description='The method that was used to calculate the collision probability. (See annex E fo', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CONJUNCTION_ID: Optional[str] = Field(default=None, description='ID that uniquely identifies all the CDMs representing the same conjunction. Based on object1 id, obj', json_schema_extra={'source': 'TraCSS-Spec-001'})
    MAHALANOBIS_DISTANCE: Optional[float] = Field(default=None, description='The length of the relative position vector, normalized to one-sigma dispersions of the combined erro', json_schema_extra={'source': 'TraCSS-Spec-001'})
    MISS_DISTANCE: Optional[float] = Field(default=None, description='The norm of the relative position vector. It indicates how close the two objects', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_POSITION_N: Optional[float] = Field(default=None, description='The N component of Object2’s position relative to Object1’s position in the RTN', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_POSITION_R: Optional[float] = Field(default=None, description='The R component of Object2’s position relative to Object1’s position in the Radi', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_POSITION_T: Optional[float] = Field(default=None, description='The T component of Object2’s position relative to Object1’s position in the RTN', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_SPEED: Optional[float] = Field(default=None, description='The norm of the relative velocity vector. It indicates how fast the two objects', json_schema_extra={'units': 'm/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_VELOCITY_N: Optional[float] = Field(default=None, description='The N component of Object2’s velocity relative to Object1’s velocity in the RTN', json_schema_extra={'units': 'm/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_VELOCITY_R: Optional[float] = Field(default=None, description='The R component of Object2’s velocity relative to Object1’s velocity in the RTN', json_schema_extra={'units': 'm/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    RELATIVE_VELOCITY_T: Optional[float] = Field(default=None, description='The T component of Object2’s velocity relative to Object1’s velocity in the RTN', json_schema_extra={'units': 'm/s', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SCREEN_ENTRY_TIME: Optional[datetime] = Field(default=None, description='The time in UTC when Object2 enters the screening volume. (See 6.3.2.6 for forma', json_schema_extra={'source': 'CCSDS-508.0-B-1'})
    SCREEN_EXIT_TIME: Optional[datetime] = Field(default=None, description='The time in UTC when Object2 exits the screening volume. (See 6.3.2.6 for format', json_schema_extra={'source': 'CCSDS-508.0-B-1'})
    SCREEN_PC_THRESHOLD: Optional[float] = Field(default=None, description='The collision probability screening threshold used to identify this conjunction.', json_schema_extra={'source': 'TraCSS-Spec-001'})
    SCREEN_VOLUME_FRAME: Optional[str] = Field(default=None, description='Name of the Object1 centered reference frame in which the screening volume data', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SCREEN_VOLUME_SHAPE: Optional[ScreenVolumeShape] = Field(default=None, description='Shape of the screening volume: ELLIPSOID or BOX.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SCREEN_VOLUME_X: Optional[float] = Field(default=None, description='The R or T (depending on if RTN or TVN is selected) component size of the screen', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SCREEN_VOLUME_Y: Optional[float] = Field(default=None, description='The T or V (depending on if RTN or TVN is selected) component size of the screen', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    SCREEN_VOLUME_Z: Optional[float] = Field(default=None, description='The N component size of the screening volume in the SCREEN_VOLUME_FRAME. Data ty', json_schema_extra={'units': 'm', 'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    START_SCREEN_PERIOD: Optional[datetime] = Field(default=None, description='The start time in UTC of the screening period for the conjunction assessment. (S', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    STOP_SCREEN_PERIOD: Optional[datetime] = Field(default=None, description='The stop time in UTC of the screening period for the conjunction assessment. (Se', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    TCA: Optional[datetime] = Field(default=None, description='The date and time in UTC of the closest approach. (See 6.3.2.6 for formatting ru', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
