from pydantic import BaseModel


class UpdateInfoOut(BaseModel):
    current_version: str
    latest_version: str
    download_url: str | None
    release_url: str


class ReleaseOptionOut(BaseModel):
    version: str
    download_url: str
    release_url: str
    is_current: bool
