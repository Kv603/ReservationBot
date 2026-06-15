from __future__ import annotations

import hashlib
import hmac
import os
import time

import boto3


class SlackRequestVerifier:
    def __init__(self, signing_secret: str, tolerance_seconds: int = 300) -> None:
        self.signing_secret = signing_secret
        self.tolerance_seconds = tolerance_seconds

    @classmethod
    def from_environment(cls) -> "SlackRequestVerifier":
        arn = os.environ.get("SLACK_SIGNING_SECRET_ARN")
        if not arn:
            return cls("")
        client = boto3.client("secretsmanager")
        secret = client.get_secret_value(SecretId=arn)["SecretString"]
        return cls(secret)

    def verify(self, *, timestamp: str, signature: str, raw_body: str) -> bool:
        if not self.signing_secret:
            return False
        try:
            request_ts = int(timestamp)
        except ValueError:
            return False
        if abs(time.time() - request_ts) > self.tolerance_seconds:
            return False
        base = f"v0:{timestamp}:{raw_body}".encode("utf-8")
        digest = hmac.new(self.signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        return hmac.compare_digest(expected, signature)

