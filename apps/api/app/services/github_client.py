from __future__ import annotations

import json
import re
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx


GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class GitHubAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str = "GitHub API request failed") -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubRateLimited(GitHubAPIError):
    pass


class GitHubPullRequestClient:
    """Bounded read-only client for GitHub PR metadata."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 8.0, deadline_seconds: float = 60.0,
                 max_retries: int = 2, max_response_bytes: int = 2_000_000,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token, self.transport = token, transport
        self.timeout, self.deadline_seconds = timeout, deadline_seconds
        self.max_retries, self.max_response_bytes = max_retries, max_response_bytes
        self.monotonic, self.sleep = monotonic, sleep

    @staticmethod
    def validate_repository_name(full_name: str) -> tuple[str, str]:
        parts = full_name.split("/")
        if len(parts) != 2 or any(part in {".", ".."} or not REPOSITORY_PART.fullmatch(part) for part in parts):
            raise ValueError("Invalid GitHub repository name")
        return parts[0], parts[1]

    def fetch_pull_request(self, full_name: str, number: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        owner, repository = self.validate_repository_name(full_name)
        base = f"{GITHUB_API_BASE}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                   "X-GitHub-Api-Version": GITHUB_API_VERSION, "User-Agent": "Nexus-Agentic-QA"}
        deadline = self.monotonic() + self.deadline_seconds
        with httpx.Client(timeout=self.timeout, headers=headers, transport=self.transport) as client:
            for snapshot_attempt in range(2):
                pr = self._get_json(client, f"{base}/pulls/{number}", deadline)
                reported_count = pr.get("changed_files")
                if not isinstance(reported_count, int) or reported_count < 0 or reported_count > 3000:
                    raise GitHubAPIError(502, "GitHub returned an invalid changed-file count")
                files: list[dict[str, Any]] = []
                for page in range(1, 32):
                    page_files = self._get_json(client, f"{base}/pulls/{number}/files?per_page=100&page={page}", deadline)
                    if not isinstance(page_files, list) or len(page_files) > 100:
                        raise GitHubAPIError(502, "GitHub returned an invalid changed-files response")
                    files.extend(page_files)
                    if len(files) > 3000:
                        raise GitHubAPIError(502, "GitHub changed-file count exceeded its documented limit")
                    if len(page_files) < 100:
                        break
                else:
                    raise GitHubAPIError(502, "GitHub changed-file pagination exceeded its documented limit")
                latest = self._get_json(client, f"{base}/pulls/{number}", deadline)
                initial_head = pr.get("head", {}).get("sha")
                latest_head = latest.get("head", {}).get("sha")
                if latest_head != initial_head:
                    raise GitHubAPIError(409, "Pull request head changed while its changed files were being retrieved")
                if latest.get("base", {}).get("sha") != pr.get("base", {}).get("sha"):
                    if snapshot_attempt == 0:
                        continue
                    raise GitHubAPIError(503, "Pull request base kept changing while its file snapshot was being retrieved")
                if len(files) != reported_count:
                    raise GitHubAPIError(502, "GitHub changed-file snapshot is incomplete")
                self._check_deadline(deadline)
                return pr, files
        raise GitHubAPIError(503, "GitHub pull request snapshot could not be stabilized")

    def _get_json(self, client: httpx.Client, url: str, deadline: float) -> Any:
        for attempt in range(self.max_retries + 1):
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise GitHubAPIError(504, "GitHub API ingestion deadline exceeded")
            try:
                with client.stream("GET", url, timeout=min(self.timeout, remaining)) as response:
                    status, headers = response.status_code, response.headers
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        self._check_deadline(deadline)
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise GitHubAPIError(502, "GitHub API response exceeded the permitted size")
                    self._check_deadline(deadline)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries and self._retry_delay(deadline, attempt, None):
                    continue
                raise GitHubAPIError(504, "GitHub API request timed out") from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries and self._retry_delay(deadline, attempt, None):
                    continue
                raise GitHubAPIError(502, "GitHub API is unavailable") from exc
            if status in {429, 500, 502, 503, 504} or (status == 403 and (headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After"))):
                if status in {429, 403} and (status == 429 or headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After")):
                    if attempt == self.max_retries or not self._retry_delay(deadline, attempt, headers.get("Retry-After")):
                        raise GitHubRateLimited(429, "GitHub API rate limit exceeded")
                    continue
                if attempt < self.max_retries and self._retry_delay(deadline, attempt, None):
                    continue
                raise GitHubAPIError(503, "GitHub API is temporarily unavailable")
            if status in {401, 403}:
                raise GitHubAPIError(502, "GitHub credentials cannot access the configured repository")
            if status == 404:
                raise GitHubAPIError(404, "GitHub repository or pull request was not found")
            if status >= 400:
                raise GitHubAPIError(502, "GitHub rejected the pull request request")
            self._check_deadline(deadline)
            try:
                parsed = json.loads(body)
            except (ValueError, UnicodeDecodeError) as exc:
                raise GitHubAPIError(502, "GitHub returned malformed JSON") from exc
            self._check_deadline(deadline)
            return parsed
        raise GitHubAPIError(503, "GitHub API is temporarily unavailable")

    def _check_deadline(self, deadline: float) -> None:
        if self.monotonic() >= deadline:
            raise GitHubAPIError(504, "GitHub API ingestion deadline exceeded")

    def _retry_delay(self, deadline: float, attempt: int, retry_after: str | None) -> bool:
        try:
            delay = float(retry_after) if retry_after is not None else min(0.25 * (2 ** attempt), 2.0)
        except ValueError:
            delay = min(0.25 * (2 ** attempt), 2.0)
        if delay < 0 or self.monotonic() + delay >= deadline:
            return False
        self.sleep(delay)
        return True
