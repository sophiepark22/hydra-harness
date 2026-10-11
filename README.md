# Hydra-Harness

Hydra-Harness wraps an agent tool call. If the tool schema is missing a required field, it records the failure, asks a model for a repair, tests that repair in an isolated process, and writes the next version to MongoDB. The next call from the same agent loads that version.

## Install

From this repository, while the package is still local:

```bash
pip install -e .
```

After it is published:

```bash
pip install hydra-harness
```

## Use it

```python
import os
from hydra_harness import HydraHarness

hydra = HydraHarness(
    mongo_uri=os.getenv("MONGO_URI"),
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    raindrop_key=os.getenv("RAINDROP_WRITE_KEY"),
)

def call_payment_api(payload: dict):
    response = hydra.run_with_self_healing(
        agent_id="billing_agent_v1",
        tool_name="stripe_payment",
        tool_payload=payload,
    )
    return response
```

`MONGO_URI` can also be set as `MONGODB_URI`. If you omit the constructor arguments, Hydra reads those variables from the environment.

The same wrapper is available as a decorator. The wrapped function runs only after the contract passes:

```python
@hydra.self_healing(agent_id="billing_agent_v1", tool_name="stripe_payment")
def call_payment_api(payload: dict):
    return payload
```

## What a call does

1. Load the latest schema for `agent_id` from MongoDB. The first call stores a version 1 contract taken from the tool name and payload.
2. Run that contract in a separate Python process. The process does not receive database or API credentials.
3. On a missing required field (`api_version`, `pagination_limit`, or `auth_bearer`), send the failure to Raindrop and ask the model for a schema patch.
4. Test the patch in a new sandbox process.
5. Write version 2 to MongoDB only after that process passes.
6. Later calls for the same `agent_id` load version 2 and skip the repair.

The return value includes `ok`, `version`, `repaired`, `request`, and `diagnosis`.

## Demo

The repository demo is separate from the SDK:

```bash
python main.py
```
