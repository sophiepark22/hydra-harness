import Link from "next/link";

const INSTALL = `git clone https://github.com/sophiepark22/hydra-harness
cd hydra-harness
python3 -m venv .venv && source .venv/bin/activate
pip install -e .`;

const ENV = `MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/
MONGODB_DB=hydra_harness
OPENAI_API_KEY=sk-...
RAINDROP_WRITE_KEY=...   # written by: raindrop cloud setup`;

const USE = `import os
from hydra_harness import HydraHarness

hydra = HydraHarness(
    mongo_uri=os.getenv("MONGODB_URI"),
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    raindrop_key=os.getenv("RAINDROP_WRITE_KEY"),
)

result = hydra.run_with_self_healing(
    agent_id="billing_agent_v1",
    tool_name="stripe_payment",
    tool_payload={"amount": 1200, "currency": "usd"},
)

print(result["ok"], result["version"], result["repaired"], result["model_calls"])`;

const DECORATOR = `@hydra.self_healing(agent_id="billing_agent_v1", tool_name="stripe_payment")
def charge(payload: dict):
    # runs only after the contract passes in the sandbox
    return stripe.PaymentIntent.create(**payload)`;

const RESULT = `{
  "ok": true,
  "version": 2,
  "repaired": true,
  "model_calls": 1,
  "prompt_tokens": 430,
  "completion_tokens": 62,
  "diagnosis": "The schema is missing api_version, pagination_limit, and auth_bearer.",
  "request": {"tool": "stripe_payment", "method": "POST", "headers": {"X-Api-Version": "..."}},
  "sandbox_runs": [{"backend": "process", "sandbox_id": "pid 44468", "phase": "execute", "ok": false}]
}`;

export default function Developers() {
  return (
    <main className="page">
      <header className="page-head">
        <p className="eyebrow">Get started for developers</p>
        <h1>Install Hydra-Harness and wrap a tool</h1>
        <p className="lede">
          Wrap a tool call. On a schema failure, Hydra repairs the contract in a sandbox, stores the new version in MongoDB Atlas, and reuses it next time.
        </p>
      </header>

      <section className="page-section">
        <h2>1. Install</h2>
        <p>Python 3.9 or newer. The package is installed from the repository today.</p>
        <pre className="code"><code>{INSTALL}</code></pre>
      </section>

      <section className="page-section">
        <h2>2. Configure</h2>
        <p>
          Create a <span className="mono">.env</span> in the project root. <span className="mono">MONGO_URI</span> also works for the connection string.
        </p>
        <pre className="code"><code>{ENV}</code></pre>
        <p className="note">
          Raindrop is optional. Without a key, failures are kept as structured local traces. Install the CLI with{" "}
          <span className="mono">curl -fsSL https://raindrop.sh/install | bash -s -- --cloud</span> and run{" "}
          <span className="mono">raindrop cloud setup</span> to write the key.
        </p>
      </section>

      <section className="page-section">
        <h2>3. Wrap a call</h2>
        <pre className="code"><code>{USE}</code></pre>
        <p>The same wrapper works as a decorator. The function body runs only after the contract passes.</p>
        <pre className="code"><code>{DECORATOR}</code></pre>
      </section>

      <section className="page-section">
        <h2>4. Read the result</h2>
        <pre className="code"><code>{RESULT}</code></pre>
        <p>
          The first call for a new <span className="mono">agent_id</span> seeds version 1 from the payload. If it fails, the repair is proven in a sandbox and version 2 is written. The second call loads version 2 and returns <span className="mono">repaired: false</span> with zero model calls.
        </p>
      </section>

      <section className="page-section">
        <h2>5. Run the demo</h2>
        <pre className="code"><code>{"python main.py        # live pacing\npython main.py --fast # no pauses"}</code></pre>
        <p>
          The demo prints the sandbox pids, the rejected patch if there is one, the Atlas commit, the vector recall score, the guardrail replay, the index scan on the Task B read, and the Atlas version table.
        </p>
      </section>

      <section className="page-section">
        <h2>What is stored in Atlas</h2>
        <table className="page-table">
          <thead>
            <tr>
              <th>Collection</th>
              <th>Document</th>
              <th>Index</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="mono">agent_states</td>
              <td>agent_id, version, tool_schema, prompt_rules, guardrails, diagnosis, parent_version</td>
              <td className="mono">agent_latest_version (agent_id 1, version -1), unique</td>
            </tr>
            <tr>
              <td className="mono">error_traces</td>
              <td>trace_text, embedding (1536), error_text, repair_parameters, guardrails</td>
              <td className="mono">error_trace_vector, vectorSearch, cosine</td>
            </tr>
          </tbody>
        </table>
      </section>

      <p className="page-cta">
        <Link href="/how-it-works">How it works</Link>
        <Link href="/">Watch the demo</Link>
      </p>
    </main>
  );
}
