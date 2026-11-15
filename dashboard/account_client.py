"""Server-to-server control client; the portal token never reaches the browser."""
from pathlib import Path

import httpx


class PortalUnavailable(RuntimeError):
    pass


class AccountPortalClient:
    def __init__(self, url, token_file):
        self.url = url.rstrip("/")
        self.token_file = token_file

    def request(self, path, *, payload=None, session=None):
        try:
            token = Path(self.token_file).read_text(encoding="utf-8").strip()
            headers = {"Authorization": "Bearer " + token}
            if session:
                headers["X-Account-Session"] = session
            with httpx.Client(trust_env=False, timeout=240 if path == "/login" else 210) as client:
                if payload is None:
                    response = client.get(self.url + path, headers=headers)
                else:
                    response = client.post(self.url + path, headers=headers, json=payload)
            data = response.json()
            if response.status_code == 401:
                return {"ok": False, "expired": True, "error": "نشست پایان یافته است؛ دوباره وارد شوید."}
            if not isinstance(data, dict):
                raise ValueError("Invalid response")
            return data
        except (OSError, httpx.HTTPError, ValueError):
            raise PortalUnavailable("رابط اتصال ویندوز در دسترس نیست. برنامه را با Start.cmd باز کنید.") from None
