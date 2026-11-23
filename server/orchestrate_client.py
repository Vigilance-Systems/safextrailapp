"""
Server-side client for watsonx Orchestrate. The API key and agent id live ONLY here (read from
environment variables / .env). The browser never sees them: it talks to our /api/v1/* routes.

Flow:  API key --(IAM token endpoint)--> short-lived bearer token --> POST chat/completions

Config (environment variables):
  WXO_API_KEY        API key from Orchestrate > Settings > API details > Generate API key
  WXO_INSTANCE_URL   "Service instance URL" from the same page (ends with /instances/<id>)
  WXO_RISK_AGENT_ID  id of the risk_agent
  WXO_TOKEN_URL      optional override of the token endpoint

NOTE: the token endpoints and the token response field names ("access_token" for IBM Cloud IAM,
"token" for AWS/MCSP) follow IBM's docs but were not run against a live instance here. If you get
a token error, print the response status and compare with the docs for your offering.
"""
import os
import threading
import time

import requests


class OrchestrateError(RuntimeError):
    pass


class OrchestrateClient:
    def __init__(self, api_key=None, instance_url=None, agent_id=None, token_url=None, timeout=120):
        env = os.environ.get
        self.api_key = (api_key if api_key is not None else env("WXO_API_KEY", "")).strip()
        self.instance_url = (instance_url if instance_url is not None else env("WXO_INSTANCE_URL", "")).strip().rstrip("/")
        self.agent_id = (agent_id if agent_id is not None else env("WXO_RISK_AGENT_ID", "")).strip()
        self.token_url = (token_url or env("WXO_TOKEN_URL") or self._default_token_url()).strip()
        self.timeout = timeout          # an agent that calls several tools can take 20-60 s
        self._token = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.instance_url and self.agent_id)

    def _default_token_url(self) -> str:
        if "cloud.ibm.com" in self.instance_url:                       # IBM Cloud offering
            return "https://iam.cloud.ibm.com/identity/token"
        return "https://iam.platform.saas.ibm.com/siusermgr/api/1.0/apikeys/token"   # AWS offering (MCSP v1)

    def _fetch_token(self):
        if "iam.cloud.ibm.com" in self.token_url:
            r = requests.post(self.token_url, timeout=30, headers={"Accept": "application/json"},
                              data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": self.api_key})
        else:
            r = requests.post(self.token_url, timeout=30, headers={"Accept": "application/json"},
                              json={"apikey": self.api_key})
        if r.status_code >= 400:
            raise OrchestrateError("token request failed (HTTP %d): %s" % (r.status_code, r.text[:200]))
        data = r.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise OrchestrateError("token response had neither 'access_token' nor 'token'")
        return token, time.time() + int(data.get("expires_in") or 3000)

    def _bearer(self, force=False) -> str:
        with self._lock:
            if force or not self._token or time.time() > self._expires_at - 60:
                self._token, self._expires_at = self._fetch_token()
            return self._token

    @staticmethod
    def extract_text(data: dict) -> str:
        """Pull the assistant text out of an OpenAI-style response (string or list of content parts)."""
        try:
            choice = data["choices"][0]
            content = (choice.get("message") or choice.get("delta") or {}).get("content", choice.get("text", ""))
        except (KeyError, IndexError, TypeError, AttributeError):
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
        return ""

    def chat(self, prompt: str, thread_id=None, context=None) -> dict:
        """Send one user message to the risk agent; returns {text, thread_id, raw}."""
        if not self.configured:
            raise OrchestrateError("Orchestrate is not configured (set WXO_API_KEY, WXO_INSTANCE_URL, WXO_RISK_AGENT_ID)")
        url = "%s/v1/orchestrate/%s/chat/completions" % (self.instance_url, self.agent_id)
        body = {"messages": [{"role": "user", "content": prompt}], "stream": False}   # docs: stream defaults to TRUE
        if context:
            body["context"] = context
        for attempt in (1, 2):
            headers = {"Authorization": "Bearer " + self._bearer(force=(attempt == 2)),
                       "Content-Type": "application/json"}
            if thread_id:
                headers["X-IBM-THREAD-ID"] = thread_id
            try:
                r = requests.post(url, json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                raise OrchestrateError("could not reach Orchestrate: %s" % exc)
            if r.status_code == 401 and attempt == 1:       # token expired or revoked: refresh once and retry
                continue
            break
        if r.status_code >= 400:
            raise OrchestrateError("Orchestrate returned HTTP %d: %s" % (r.status_code, r.text[:300]))
        data = r.json()
        return {"text": self.extract_text(data), "thread_id": data.get("thread_id"), "raw": data}
