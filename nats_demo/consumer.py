#!/usr/bin/env python3
"""
consumer.py — M8Flow NATS Demo Consumer

Subscribes to the JetStream subject `m8flow.events.>` and, for each
incoming message, starts the target BPMN process instance from the DB user.

Usage (from nats_demo/ directory):
    uv run python consumer.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import nats
from nats.js.api import ConsumerConfig
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging & Setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [CONSUMER] %(levelname)s %(message)s")
log = logging.getLogger("nats_demo.consumer")

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Load environment variables from the repo root
env_path = _REPO_ROOT / ".env"
load_dotenv(dotenv_path=env_path)

# Enforce absolute path for process models (overriding any generic relative .env paths)
bpmn_dir = str(_REPO_ROOT / "process_models")
os.environ["SPIFFWORKFLOW_BACKEND_BPMN_SPEC_ABSOLUTE_DIR"] = bpmn_dir
os.environ["M8FLOW_BACKEND_BPMN_SPEC_ABSOLUTE_DIR"] = bpmn_dir

# Add the repo root and the src directories to sys.path
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "extensions" / "m8flow-backend" / "src"))
sys.path.insert(0, str(_REPO_ROOT / "spiffworkflow-backend" / "src"))

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
STREAM_NAME = os.environ.get("STREAM_NAME", "M8FLOW_EVENTS")
SUBJECT = "m8flow.events.>"

# The Process Model to trigger will be extracted from the event payload
# or defaulted to a fallback value within the handler.


# ---------------------------------------------------------------------------
# Core Handler
# ---------------------------------------------------------------------------
def handle_event(payload: dict, tenant_id: str, username: str, process_identifier: str, flask_app) -> None:
    """Uses the SpiffWorkflow backend app context to trigger a process model."""
    from spiffworkflow_backend.services.process_instance_service import ProcessInstanceService
    from spiffworkflow_backend.services.process_model_service import ProcessModelService
    from spiffworkflow_backend.models.user import UserModel
    from spiffworkflow_backend.models.db import db
    from m8flow_backend.tenancy import set_context_tenant_id
    
    with flask_app.app_context():
        # Set the Tenant Context to avoid process_model_not_found errors in multi-tenancy
        target_tenant_id = tenant_id or "317474b3-60bc-4a42-9539-34a1d0627dc4"
        set_context_tenant_id(target_tenant_id)
        
        # Because we need a user to create a *persistent* process instance:
        # We query for the specific tenant admin to ensure they are marked as the initiator.
        target_user = username or "tenant-admin@spiffworkflow_dd763f0f"
        user = UserModel.query.filter_by(username=target_user).first()
        
        if not user:
            log.error(f"❌ User '{target_user}' not found in the database. Cannot start process instance.")
            return

        target_process_identifier = process_identifier or "new-workflow/nats-event-trigger-test"
        log.info(f"Triggering {target_process_identifier} for user {user.id}")

        try:
            process_model = ProcessModelService.get_process_model(target_process_identifier)
            
            processor = ProcessInstanceService.create_and_run_process_instance(
                process_model=process_model,
                persistence_level="persistent",  # Save to the DB so it appears in the UI
                data_to_inject=payload,        # Injects the NATS payload into the workflow's data
                user=user
            )
            
            # Commit the transaction so the instance is fully saved
            db.session.commit()
            
            log.info(f"✅ Process Instance {processor.process_instance_model.id} successfully created and started.")
            
        except Exception as e:
            log.exception(f"❌ Failed to run the process instance: {e}")
            db.session.rollback()


# ---------------------------------------------------------------------------
# NATS Subscription loop
# ---------------------------------------------------------------------------
async def run(flask_app):
    log.info(f"Connecting to NATS at {NATS_URL}...")
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    log.info(f"Subscribing to JetStream stream '{STREAM_NAME}' on subject '{SUBJECT}'...")

    async def on_message(msg):
        log.info(f"━━━ Received NATS message on subject: {msg.subject}")
        try:
            body = json.loads(msg.data.decode())
            # We assume a payload under the "payload" key, but default to the whole body if missing a structured wrapper
            payload = body.get("payload", body)
            tenant_id = body.get("tenant_id")
            username = body.get("username")
            process_identifier = body.get("process_identifier")

            log.info(f"Injecting Payload: {json.dumps(payload)}")
            
            handle_event(payload, tenant_id, username, process_identifier, flask_app)
            
        except json.JSONDecodeError as exc:
            log.error(f"Failed to parse JSON msg: {exc}")
        except Exception as exc:
            log.exception(f"Unexpected handler error: {exc}")
        finally:
            await msg.ack()

    # Durable push consumer so it remembers processed messages between restarts
    await js.subscribe(
        SUBJECT,
        stream=STREAM_NAME,
        durable="m8flow-fresh-demo-consumer-v4", 
        cb=on_message,
        config=ConsumerConfig(ack_wait=30)
    )

    log.info("🟢 Consumer is ready and listening for events! (Press Ctrl+C to stop)")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await nc.drain()
        log.info("Consumer stopped gracefully.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    # Because we've already modified sys.path, we can now import from `extensions`
    from extensions.app import flask_app

    try:
        asyncio.run(run(flask_app))
    except KeyboardInterrupt:
        log.info("Interrupted by user.")

if __name__ == "__main__":
    main()
