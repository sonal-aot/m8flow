#!/usr/bin/env python3
"""
setup_stream.py — M8Flow NATS Demo: JetStream Stream Setup

Creates the M8FLOW_EVENTS JetStream stream that the consumer subscribes to.
Run this ONCE after starting NATS (before starting consumer.py).

Usage:
    python nats_demo/setup_stream.py
    NATS_URL=nats://localhost:4222 python nats_demo/setup_stream.py

Requires:  pip install nats-py
"""

import asyncio
import os

import nats
from nats.js.api import StreamConfig, RetentionPolicy, StorageType

NATS_URL    = os.environ.get("NATS_URL",    "nats://localhost:4222")
STREAM_NAME = os.environ.get("STREAM_NAME", "M8FLOW_EVENTS")
SUBJECT     = "m8flow.events.>"   # wildcard — catches all tenants + message types


async def setup() -> None:
    print(f"Connecting to NATS at {NATS_URL} …")
    nc = await nats.connect(NATS_URL)
    jsm = nc.jetstream()

    # Check if the stream already exists
    try:
        existing = await jsm.find_stream(SUBJECT)
        print(f"Stream '{existing}' already exists — skipping creation.")
    except Exception:
        # Stream doesn't exist — create it
        await jsm.add_stream(
            StreamConfig(
                name=STREAM_NAME,
                subjects=[SUBJECT],
                retention=RetentionPolicy.LIMITS,
                max_msgs=10_000,
                max_age=86_400,          # 24 hours in seconds
                storage=StorageType.MEMORY,   # fast, no disk persistence for demo
            )
        )
        print(f"✅ Stream '{STREAM_NAME}' created.")
        print(f"   Listening on subjects: {SUBJECT}")

    await nc.drain()


if __name__ == "__main__":
    asyncio.run(setup())
