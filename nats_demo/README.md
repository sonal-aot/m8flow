# M8Flow NATS Event Trigger Demo

This demo demonstrates how an external NATS event triggers a BPMN process instance in M8Flow using the SpiffWorkflow backend engine.

## 🏗️ Architecture & Working Structure

The event-driven execution flow enables external applications to trigger M8Flow BPMN processes asynchronously using NATS JetStream as a message broker.

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
   - It sets the multi-tenancy context using the `tenant_id` string extracted from the message metadata (`set_context_tenant_id`) ensuring the workflow runs against the correct tenant's database schema and files.
4. **Initiator Resolution:** The consumer queries the SpiffWorkflow DB for a specific user (e.g., `tenant-admin@spiffworkflow`) to attribute the process instance creation to.
5. **Execution:**
   - The parsed event payload (e.g., `{"order_id": "TEST-123"}`) is passed to SpiffWorkflow's `ProcessInstanceService.create_and_run_process_instance`.
   - `persistence_level="persistent"` is used so that the process instance gets saved to the database and appears in the M8Flow UI dashboard.
   - The workflow variables (like `order_id`) are injected directly into the initial scope of the BPMN execution.

---

## 📂 What's inside

| File                 | Purpose                                                      |
| -------------------- | ------------------------------------------------------------ |
| `docker-compose.yml` | Starts a NATS JetStream server locally                       |
| `setup_stream.py`    | Creates the `M8FLOW_EVENTS` JetStream stream                 |
| `consumer.py`        | Subscribes to NATS, loads the app context, and triggers BPMN |
| `publisher.py`       | CLI tool to mock an external system sending events           |

**BPMN used:** `process_models/{tenant_id}/new-workflow/nats-event-trigger-test/nats-event-trigger-test.bpmn`
(A simple template that receives a string and logs it)

---

## 🚀 Step-by-step: Running the demo

### Step 1 — Start the local NATS server

```bash
cd nats_demo
docker compose up -d
```

### Step 2 — Initialize the NATS Stream

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
  --process_identifier "new-workflow/nats-event-trigger-test" \
  --order_id           "ORD-123" \
  --customer           "ACME Corp"
```

### Step 5 — Verify Execution

Look at the **consumer** terminal window. You should see Logs confirming:

1. Message received via NATS.
2. The payload injected and metadata parsed.
3. The Process Instance generated.

You can also log into the M8Flow frontend as `tenant-admin@spiffworkflow` to view the successfully generated Persistent Process Instance!

---

## 🔍 Code Explanation: `consumer.py`

The core logic that integrates NATS events into the SpiffWorkflow backend engine is located in `consumer.py`. Below is a breakdown of what happens under the hood when an event is processed.

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

- We connect directly to the JetStream message broker.
- A **durable subscription** is used. This means that if the consumer goes offline and events are published, JetStream remembers where it left off and delivers the missed messages when the consumer reconnects.

### 2. Setting the SpiffWorkflow DB & Tenancy Context

```python
def handle_event(payload: dict, tenant_id: str, username: str, process_identifier: str, flask_app) -> None:
    ...
    with flask_app.app_context():
        # Set the Tenant Context to avoid process_model_not_found errors in multi-tenancy
        target_tenant_id = tenant_id or "317474b3-60bc-4a42-9539-34a1d0627dc4"
        set_context_tenant_id(target_tenant_id)
```

- M8Flow relies on a Flask application context for all Database (PostgreSQL) transactions.
- Because M8Flow supports **Multi-Tenancy**, the `ProcessModelService` will fail to find `process_models` unless a valid tenant is set. Here, we extract it from the event payload and configure the context via `set_context_tenant_id`.

### 3. Finding the Initiator (UserModel)

```python
        # We query for the dynamically provided user to ensure they are marked as the initiator.
        target_user = username or "tenant-admin@spiffworkflow_dd763f0f"
        user = UserModel.query.filter_by(username=target_user).first()
```

- SpiffWorkflow requires a `UserModel` when triggering any **persistent** background execution.
- We deliberately query for a dynamically provided user (defaulting to the tenant-admin if omitted), linking any process instances started by the consumer back to this user in the M8Flow UI.

### 4. Executing the Workflow & Injecting Data

```python
        processor = ProcessInstanceService.create_and_run_process_instance(
            process_model=process_model,
            persistence_level="persistent",
            data_to_inject=payload_data,
            user=user
        )
```

- The `ProcessInstanceService` fetches the BPMN, creates a new internal record for it, and then steps the execution engine.
- `persistence_level="persistent"` saves the execution state to the DB so you can inspect its live status.
- `data_to_inject` takes the JSON payload sent via NATS (like `{"order_id": "TEST-123"}`) and maps it perfectly into the starting `data` dictionary of the workflow context.
