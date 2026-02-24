#!/usr/bin/env python3
"""
publisher.py — M8Flow NATS Demo Publisher

Publishes a test event to NATS JetStream, simulating an external system
sending a business event that triggers a BPMN process in M8Flow.

Usage:
    python nats_demo/publisher.py
    python nats_demo/publisher.py --message_name OrderReceived --order_id ORD-999

Options:
    --message_name   Message name (must match BPMN Message Start Event name)
                     Default: OrderReceived
    --tenant_id      Logical tenant/group identifier
                     Default: default
    --order_id       Demo payload field: order ID
                     Default: ORD-001
    --customer       Demo payload field: customer name
                     Default: Alice
    --nats_url       NATS server URL
                     Default: nats://localhost:4222

Example:
    python nats_demo/publisher.py --message_name OrderReceived --order_id ORD-042 --customer Bob
"""

import argparse
import asyncio
import json
import logging

import nats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PUBLISHER] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nats_demo.publisher")


async def publish(
    nats_url: str,
    message_name: str,
    tenant_id: str,
    username: str,
    process_identifier: str,
    payload: dict,
) -> None:
    log.info("Connecting to NATS at %s …", nats_url)
    nc = await nats.connect(nats_url)
    js = nc.jetstream()

    subject = f"m8flow.events.{tenant_id}.{message_name}"
    body = {
        "message_name": message_name,
        "tenant_id": tenant_id,
        "username": username,
        "process_identifier": process_identifier,
        "payload": payload,
    }
    data = json.dumps(body).encode()

    log.info("Publishing to subject:  %s", subject)
    log.info("Payload:                %s", json.dumps(body, indent=2))

    ack = await js.publish(subject, data)
    log.info("✅ Published!  stream=%s  seq=%d", ack.stream, ack.seq)

    await nc.drain()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a test event to M8Flow via NATS")
    parser.add_argument("--message_name", default="OrderReceived",     help="BPMN Message Start Event name")
    parser.add_argument("--tenant_id",    default="317474b3-60bc-4a42-9539-34a1d0627dc4",           help="Tenant/group identifier")
    parser.add_argument("--username",     default="tenant-admin@spiffworkflow_dd763f0f", help="Initiating User")
    parser.add_argument("--process_identifier", default="new-workflow/nats-event-trigger-test", help="Process Identifier")
    parser.add_argument("--order_id",     default="ORD-001",           help="Demo: order ID")
    parser.add_argument("--customer",     default="Alice",             help="Demo: customer name")
    parser.add_argument("--nats_url",     default="nats://localhost:4222", help="NATS server URL")
    args = parser.parse_args()

    payload = {
        "order_id": args.order_id,
        "customer": args.customer,
    }

    asyncio.run(publish(
        nats_url=args.nats_url,
        message_name=args.message_name,
        tenant_id=args.tenant_id,
        username=args.username,
        process_identifier=args.process_identifier,
        payload=payload,
    ))


if __name__ == "__main__":
    main()
