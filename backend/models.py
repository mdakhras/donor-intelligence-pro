from pydantic import BaseModel, Field
from typing import List, Optional

class ResearchData(BaseModel):
    """
    Structured research data collected about a donor.
    This model holds the raw findings from web searches and document analysis.
    """
    findings: List[str] = Field(description="A list of key facts, priorities, and recent activities about the donor, with source URLs.")

class DonorProfile(BaseModel):
    """
    A synthesized profile of a donor.
    This model consolidates research data and existing information into a coherent profile.
    """
    profile: str = Field(description="A structured donor profile with sections for key players, priorities, and funding history.")

class Strategy(BaseModel):
    """
    A strategic recommendation for donor engagement.
    """
    recommendation: str = Field(description="A strategic recommendation and justification, aligned with the donor's interests.")

class Guidance(BaseModel):
    """
    Guidance on how to engage with a donor.
    """
    guidance: str = Field(description="Engagement instructions, funding cycle information, and standard advisory notes.")

class ReportDraft(BaseModel):
    """
    A draft of the donor intelligence report before redaction.
    """
    draft: str = Field(description="The generated donor intelligence report, prior to any governance or redaction.")

class FinalReport(BaseModel):
    """
    The final, redacted donor intelligence report.
    """
    report: str = Field(description="The final, audience-appropriate donor report, with sensitive information redacted based on user role.")
