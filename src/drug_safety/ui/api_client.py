"""The interface's only link to the back end: Person A's API (spec p17), at API_URL.

    client = ApiClient(settings.api_url, settings.api_timeout_s)
    response = client.assess(patient, use_llm=True)   # AssessResponse, or raises ApiError
    ok, detail = client.health()

Every response is checked against the shared contract (AssessResponse), so a field the API
renamed or forgot fails here with a clear message instead of as a broken screen.
"""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from drug_safety.contracts import CONTRACTS_VERSION, AssessRequest, AssessResponse, Patient


class ApiError(Exception):
    """A problem the screen shows to the user as is."""


class ApiClient:
    def __init__(
        self, base_url: str, timeout_s: float = 420.0, transport: httpx.BaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s, transport=transport)

    def assess(self, patient: Patient, use_llm: bool = True) -> AssessResponse:
        body = AssessRequest(patient=patient, use_llm=use_llm).model_dump(mode="json")
        r = self._send("POST", "/v1/assess", json=body)
        try:
            return AssessResponse.model_validate(r.json())
        except (ValueError, ValidationError) as e:
            raise ApiError(
                f"The API's answer does not match the shared contract (version "
                f"{CONTRACTS_VERSION}): {_short(e)}"
            ) from e

    def health(self) -> tuple[bool, dict | str]:
        """(reachable and healthy, the API's own health body or the error)."""
        try:
            r = self._http.get("/health", timeout=10)
        except httpx.HTTPError as e:
            return False, f"Cannot reach the API at {self.base_url} ({type(e).__name__})"
        try:
            body = r.json()
        except ValueError:
            body = r.text[:300]
        return r.status_code == 200, body

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.TimeoutException as e:
            raise ApiError(f"The API at {self.base_url} did not answer in time.") from e
        except httpx.HTTPError as e:
            raise ApiError(
                f"Cannot reach the API at {self.base_url}. Is it running? ({type(e).__name__})"
            ) from e
        if r.status_code >= 400:
            raise ApiError(f"The API refused the request ({r.status_code}): {_detail(r)}")
        return r


def _detail(r: httpx.Response) -> str:
    """FastAPI puts the reason in `detail` (a string, or a list of field errors)."""
    try:
        d = r.json().get("detail", r.json())
    except (ValueError, AttributeError):
        return r.text[:300] or r.reason_phrase
    if isinstance(d, list):
        return "; ".join(
            f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg', e)}"
            if isinstance(e, dict)
            else str(e)
            for e in d
        )
    return str(d)


def _short(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
    return str(e)[:300]
