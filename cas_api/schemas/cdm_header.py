"""CDM Header — CCSDS 508.0-B-1 §3.2."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CDMHeader(BaseModel):
    """CDM Header section (CCSDS Table 3-1)"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )

    CCSDS_CDM_VERS: Optional[str] = Field(default=None, description='Format version in the form of ‘x.y’, where ‘y’ is incremented for corrections an', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    CREATION_DATE: Optional[datetime] = Field(default=None, description='Message creation date/time in Coordinated Universal Time (UTC). (See 6.3.2.6 for', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    MESSAGE_FOR: Optional[str] = Field(default=None, description='Spacecraft name(s) for which the CDM is provided.', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    MESSAGE_ID: Optional[str] = Field(default=None, description='ID that uniquely identifies a message from a given originator. The format and co', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
    ORIGINATOR: Optional[str] = Field(default=None, description='Creating agency or owner/operator. Value should be the ‘Abbreviation’ value from', json_schema_extra={'source': 'CCSDS-508.0-B-1 + TraCSS-Spec-001'})
