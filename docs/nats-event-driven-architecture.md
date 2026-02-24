# NATS Event-Driven Architecture in M8Flow

This document details the configuration, architecture, and implementation of the event-driven workflow execution system within M8Flow, using NATS JetStream as the message broker to asynchronously trigger SpiffWorkflow BPMN process instances.

## 🏛️ Architecture & Working Structure

The event-driven execution flow enables external applications to trigger M8Flow BPMN processes asynchronously.

```text
                             [ External System / Publisher ]
                                            |
                                            | Publishes NATS Event
                                            v
.---------------------------------------------------------------------------------.
|                             M8Flow Event Consumer                               |
|                                                                                 |
|                               [ NATS JetStream Broker ]                         |
|                                            |                                    |
|                                            | Subscribes to Event                |
|                                            v                                    |
|                                  [ NATS Consumer ]                              |
|                                            |                                    |
|                                            | Extracts Payload & Tenant          |
|                                            v                                    |
|                                < Environment Context >                          |
|                                            |                                    |
|                                            | Sets up DB & Tenant Context        |
|                                            v                                    |
|                           [ SpiffWorkflow App Context ]                         |
'---------------------------------------------------------------------------------'
                                            |
                                            | Queries target UserModel
                                            v
.---------------------------------------------------------------------------------.
|                                M8Flow Backend                                   |
|                                                                                 |
|                                   [( Database )]                                |
|                                            |                                    |
|                                            | Identifies Initiator               |
|                                            v                                    |
|                           [ Process Instance Service ]                          |
|                                  /                    \                         |
|                 Loads BPMN      /                      \    Injects NATS        |
|                 from Disk      v                        v   Payload             |
|    [ Process Model Service ]          [ create_and_run_process_instance ]       |
|                                                                 |               |
|                                                                 | Starts        |
|                                                                 v Workflow      |
|                                                   ( Persistent BPMN Instance )  |
'---------------------------------------------------------------------------------'
```

### ⚙️ How It Works (Step-by-Step)

1. **Event Publication:** An external system publishes a JSON payload to the NATS JetStream stream `M8FLOW_EVENTS` under a specific subject (e.g., `m8flow.events.317474b3...OrderReceived`).
2. **Consumer Subscription:** The durable NATS consumer (`consumer.py`) is constantly listening to the `M8FLOW_EVENTS` stream. When it receives the message, it parses the JSON data.
3. **Application Context Preparation:**
   - The consumer loads the Flask application context using `extensions.app.create_app()`.
   - It extracts the `tenant_id` from the publisher payload and sets the M8Flow tenant context (`set_context_tenant_id`) so the process model can be found (Multi-Tenancy support).
4. **Initiator Identification:** It extracts the `username` from the payload and queries the SpiffWorkflow backend Database for that user (defaulting to a predefined tenant admin if omitted). This ensures the background process instance is attributed to a real user in the system.
5. **Process Initiation:** Using the SpiffWorkflow `ProcessInstanceService`, it calls `create_and_run_process_instance` with the target `process_model`, configuring it as a `persistent` level execution, and injects the JSON payload as `data_to_inject`.
6. **Execution Complete:** The process engine creates the instance, saves it to the DB, and processes the current ready tasks based on the injected data.

---

## 🚀 Pre-requisites & Setup

This implementation relies heavily on Python's async features, NATS JetStream, and the SpiffWorkflow backend. The setup requires a local instance of the M8Flow Backend environment and `uv` or `poetry` for dependency isolation.

All execution takes place in the `nats_demo` directory.

### Step 1 — Start the local NATS server

The NATS server must have JetStream enabled. This is already provided in the `docker-compose.yml`.

```bash
cd nats_demo
docker compose up -d
```

### Step 2 — Initialize the NATS Stream

JetStream requires streams to be pre-defined to capture subjects.

```bash
cd nats_demo
uv run python setup_stream.py
```

Expected output: `✅ Stream "M8FLOW_EVENTS" created.`

### Step 3 — Start the NATS Consumer

Open a new terminal window to keep the consumer running:

```bash
cd nats_demo
uv run python consumer.py
```

Expected output: `🟢 Consumer is ready and listening for events! (Press Ctrl+C to stop)`

### Step 4 — Publish a test event

Open another terminal window to simulate external system traffic:

```bash
cd nats_demo
uv run python publisher.py \
  --message_name       "OrderReceived" \
  --tenant_id          "317474b3-60bc-4a42-9539-34a1d0627dc4" \
  --username           "tenant-admin@spiffworkflow_dd763f0f" \
  --process_identifier "new-workflow/nats-event-trigger-test"
```

Expected output: `✅ Published OrderReceived to m8flow.events.317474b3...OrderReceived`

### Step 5 — Verify Execution

Look at the **consumer** terminal window. You should see Logs confirming:

1. Message received via NATS.
2. The payload injected and metadata parsed.
3. The Process Instance generated.

You can also log into the M8Flow frontend as `tenant-admin@spiffworkflow` to view the successfully generated Persistent Process Instance visible on the dashboard!

---

## 🔍 Code Implementation Details: `consumer.py`

The core logic that integrates NATS events into the SpiffWorkflow backend engine is located in `consumer.py`. Below is a technical breakdown of the engine integration.

### 1. Connecting & Subscribing to NATS

```python
async def run(flask_app):
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    # Durable push consumer so it remembers processed messages between restarts
    await js.subscribe(
        SUBJECT,
        stream=STREAM_NAME,
        durable="m8flow-demo-consumer-v1",
        cb=on_message,
        config=ConsumerConfig(ack_wait=30)
    )
```

- A direct connection to the JetStream message broker is created.
- A **durable subscription** is used. This means that if the consumer goes offline and events are published, JetStream remembers the exact index where it left off and delivers the missed messages when the consumer reconnects.

### 2. Setting the SpiffWorkflow DB & Tenancy Context

```python
def handle_event(payload: dict, tenant_id: str, username: str, process_identifier: str, flask_app) -> None:
    ...
    with flask_app.app_context():
        # Set the Tenant Context to avoid process_model_not_found errors in multi-tenancy
        target_tenant_id = tenant_id or "317474b3-60bc-4a42-9539-34a1d0627dc4"
        set_context_tenant_id(target_tenant_id)
```

- M8Flow relies on a strict Flask application context for all Database (PostgreSQL) transactions using SQLAlchemy.
- Because M8Flow supports **Multi-Tenancy**, the `ProcessModelService` will fail with a `ProcessEntityNotFoundError` unless a valid tenant is set. Here, it is extracted from the event payload and configured via `m8flow_backend.tenancy.set_context_tenant_id`.

### 3. Finding the Initiator (UserModel)

```python
        # We query for the provided user to ensure they are marked as the initiator.
        target_user = username or "tenant-admin@spiffworkflow_dd763f0f"
        user = UserModel.query.filter_by(username=target_user).first()
```

- SpiffWorkflow structurally requires a `UserModel` when triggering any **persistent** background execution. If a `UserModel` is not provided to `persistence_level="persistent"`, the engine throws an exception.
- The consumer queries for the dynamic `target_user` (or master tenant-admin), linking any process instances started by the background consumer seamlessly into the UI.

### 4. Executing the Workflow & Injecting Data

```python
        processor = ProcessInstanceService.create_and_run_process_instance(
            process_model=process_model,
            persistence_level="persistent",
            data_to_inject=payload_data,
            user=user
        )
```

- The `ProcessInstanceService` parses the JSON, compiles the BPMN, creates a new internal database record for the instance, and executes the engine steps.
- `persistence_level="persistent"` physically saves the workflow state and the task execution paths to the DB so you can inspect its live status inside the process viewer.
- `data_to_inject` takes the dynamic JSON payload published via NATS (e.g., `{"order_id": "TEST-123", "customer": "Alice"}`) and maps it perfectly into the starting `data` dictionary of the workflow Execution Context, ensuring process models execute dynamically based on the NATS event.

---

## 🛠️ Adding New Triggers in the Future

The current consumer routes an incoming `"OrderReceived"` string to a specific BPMN model identifier (`new-workflow:nats-event-trigger-test`).

To trigger **different** workflows based on different NATS events:

1. Create your new BPMN model in M8Flow and get its identifier.
2. Edit `EVENT_TO_PROCESS_MODEL_MAP` in `nats_demo/consumer.py`:
   ```python
   EVENT_TO_PROCESS_MODEL_MAP = {
       "OrderReceived": "new-workflow:nats-event-trigger-test",
       "PaymentProcessed": "finance:payment-processing-flow",
       "UserSignup": "onboarding:new-user-setup",
   }
   ```
3. Have your external system publish a payload with `"message_name": "PaymentProcessed"`. The consumer will automatically launch the mapped `finance:payment-processing-flow` instance.
