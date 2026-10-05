"""Local-only Ollama transport. No proxies, redirects, downloads or cloud models."""
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Model endpoint redirects are disabled")


class GraniteClient:
    runtime = "Ollama"

    def __init__(self, base_url=None, model=None, timeout=600):
        self.base_url = (base_url or os.getenv("GRANITE_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Granite endpoint must be a numeric loopback HTTP address")
        self.model_name = model or os.getenv("GRANITE_MODEL", "granite4.2:latest")
        self.model_version = None
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.base_url + path, data=body, headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=self.timeout) as response:
            return json.load(response)

    def check_model(self):
        models = self._request("/api/tags")["models"]
        model = next((m for m in models if m["name"] == self.model_name), None)
        if (model is None or model.get("remote_host") or model.get("remote_model")
                or "cloud" in self.model_name.casefold()
                or model.get("details", {}).get("family") != "granite"):
            raise RuntimeError("An installed local Granite model is required")
        self.model_version = model["digest"]

    def generate_json(self, prompt: str) -> str:
        if self.model_version is None:
            self.check_model()
        response = self._request("/api/chat", {
            "model": self.model_name, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False, "format": "json",
            "options": {"temperature": 0, "seed": 0, "num_ctx": 16384, "num_predict": 8192},
        })
        if not response.get("done") or response.get("done_reason") == "length":
            raise RuntimeError("Incomplete model response")
        content = response["message"]["content"]
        if not isinstance(content, str):
            raise RuntimeError("Model response is not text")
        return content


def generate_json(prompt: str) -> str:
    return GraniteClient().generate_json(prompt)
