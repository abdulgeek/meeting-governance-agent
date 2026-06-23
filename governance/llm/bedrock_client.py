"""Bedrock runtime client for Claude. Uses standard AWS credential chain via boto3."""

from __future__ import annotations

import boto3
from botocore.config import Config

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
DEFAULT_REGION = "us-east-1"


class BedrockClient:
    def __init__(self, model_id: str = DEFAULT_MODEL, region: str = DEFAULT_REGION,
                 max_tokens: int = 1024):
        self.model_id = model_id
        self.max_tokens = max_tokens
        # retry once on throttling; cap the read so one slow call can't stall the run
        cfg = Config(retries={"max_attempts": 2, "mode": "standard"}, read_timeout=30)
        self._client = boto3.client("bedrock-runtime", region_name=region, config=cfg)

    def complete(self, system: str, user: str) -> str:
        # temperature 0 so the same line gives the same verdict run to run
        resp = self._client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0.0},
        )
        return resp["output"]["message"]["content"][0]["text"]
