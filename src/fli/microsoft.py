"""Managed-identity shared workflow bridge. No Blob triggers and no direct index writes.

The workflow freezes a complete result under the generation's normal text prefix.
Retries consume that result without repeating embeddings. Ambiguous/overlapping HTTP
submissions can still repeat provider work; attempt metrics must disclose this.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qsl, urlsplit

from fli.checkpoints import load
from fli.ids import text_prefix


class MicrosoftWorkflow:
    def __init__(self, endpoint, token_provider, http, blobs, *, wait_seconds=300, poll_seconds=2):
        url = urlsplit(endpoint)
        query = parse_qsl(url.query, keep_blank_values=True)
        allowed_query = not query or (len(query) == 1 and query[0][0] == "api-version" and bool(query[0][1]))
        if url.scheme != "https" or not url.netloc or not allowed_query or url.username or url.password or url.fragment:
            raise ValueError("Microsoft workflow requires an HTTPS Entra endpoint without SAS/query credentials")
        self.endpoint, self.tokens, self.http, self.blobs = endpoint, token_provider, http, blobs
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds

    async def prepare(self, work, source):
        path = f"{text_prefix(work['libraryId'], work['documentId'])}{work['generation']}/microsoft-result.json"
        result = await load(self.blobs, path)
        if result is not None:
            return {**result, "resultReused": True}
        token = await self.tokens()
        # Only trusted immutable source paths cross this boundary. The workflow's MI
        # reads bytes; no source URLs, credentials or model-selected scope are accepted.
        response = await self.http.post(
            self.endpoint,
            json={"job": work, "resultPath": path},
            headers={"Authorization": f"Bearer {token}"},
            timeout=55,
        )
        if response.status_code not in (200, 202):
            raise RuntimeError("microsoft_workflow_submission_failed")
        deadline = time.monotonic() + self.wait_seconds
        while True:
            result = await load(self.blobs, path)
            if result is not None:
                return {**result, "resultReused": False}
            if time.monotonic() >= deadline:
                raise TimeoutError("microsoft_workflow_result_unavailable")
            await asyncio.sleep(self.poll_seconds)
