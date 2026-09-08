from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    project_name: str
    version: str
