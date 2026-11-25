from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel


class AlertmanagerAlert(BaseModel):
    status: str = Field(pattern="^(firing|resolved)$")
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str | None = None
    fingerprint: str | None = None

    @field_validator("endsAt")
    @classmethod
    def _zero_time_is_none(cls, v: datetime | None):
        # Alertmanager sends 0001-01-01T00:00:00Z for alerts that have not ended.
        return None if v is not None and v.year < 1970 else v

    @model_validator(mode="after")
    def _require_alertname(self):
        if not self.labels.get("alertname"):
            raise ValueError("labels.alertname is required")
        return self


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    status: str = Field(pattern="^(firing|resolved)$")
    receiver: str = ""
    groupKey: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = None
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=100)


class WebhookResult(BaseModel):
    received: int
    created_incidents: list[str]
    attached_alerts: int
    updated_alerts: int
    ignored: int


class AlertOut(ORMModel):
    id: str
    incident_id: str | None
    fingerprint: str
    alert_name: str
    status: str
    severity: str
    service: str
    labels: dict
    annotations: dict
    starts_at: datetime
    ends_at: datetime | None
    generator_url: str | None
    created_at: datetime
