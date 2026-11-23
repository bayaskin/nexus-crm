from datetime import datetime
from typing import Literal
from uuid import UUID
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GitHubChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    status: Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    previous_filename: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def validate_rename(self) -> "GitHubChangedFile":
        if self.status == "renamed" and not self.previous_filename:
            raise ValueError("Renamed files must include the previous filename")
        return self


class GitHubPRContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: int = Field(gt=0)
    repository_full_name: str = Field(min_length=3, max_length=255)
    repository_owner: str = Field(min_length=1, max_length=100)
    repository_name: str = Field(min_length=1, max_length=100)
    default_branch: str | None = Field(default=None, max_length=255)
    pull_request_number: int = Field(gt=0)
    title: str = Field(max_length=1024)
    url: str = Field(min_length=1, max_length=2048)
    author: str = Field(min_length=1, max_length=100)
    state: Literal["open", "closed"]
    draft: bool
    source_branch: str = Field(min_length=1, max_length=255)
    target_branch: str = Field(min_length=1, max_length=255)
    base_sha: str
    event_base_sha: str | None = None
    head_sha: str
    merge_sha: str | None = None
    changed_files: list[GitHubChangedFile] = Field(max_length=3000)
    reported_changed_files: int = Field(ge=0, le=3000)
    revision_id: str = Field(default_factory=lambda: str(uuid4()), min_length=36, max_length=36)
    event_type: Literal["opened", "reopened", "synchronize"]
    delivery_id: str = Field(min_length=1, max_length=128)
    event_updated_at: datetime

    @field_validator("base_sha", "event_base_sha", "head_sha", "merge_sha")
    @classmethod
    def validate_sha(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 40 or any(char not in "0123456789abcdefABCDEF" for char in value)):
            raise ValueError("GitHub revision must be a 40-character SHA")
        return value.lower() if value else value

    @model_validator(mode="after")
    def validate_repository_and_url(self) -> "GitHubPRContext":
        if self.event_base_sha is None:
            self.event_base_sha = self.base_sha
        if self.repository_full_name.casefold() != f"{self.repository_owner}/{self.repository_name}".casefold():
            raise ValueError("Repository identity fields do not match")
        expected_url = f"https://github.com/{self.repository_full_name}/pull/{self.pull_request_number}"
        if self.url != expected_url:
            raise ValueError("Pull request URL does not match the configured GitHub repository")
        return self


class GitHubWebhookResponse(BaseModel):
    status: Literal["accepted", "ignored"]
    duplicate: bool = False
    run_id: UUID | None = None
