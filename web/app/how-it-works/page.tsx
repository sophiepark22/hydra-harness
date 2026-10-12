import Link from "next/link";
import { PHASES } from "@/lib/run";

const PROOF = [
  {
    panel: "Raindrop telemetry",
    text: "The right side shows the live failure: the missing field, the customers payload, and the process id. The model has not been called yet.",
  },
  {
    panel: "Isolated subprocess",
    text: "The right side shows the separate process, the model call and token count, the fields that were added, and whether the test passed. A failed test is not saved.",
  },
  {
    panel: "MongoDB Atlas",
    text: "The right side shows the v1 to v2 parameter diff and the document written to agent_states. The latest version is read through the index on (agent_id, version).",
  },
  {
    panel: "Downstream task",
    text: "The right side shows the orders call loading version 2, the index lookup, and the model-call count. That count stays at 0 because the saved contract is reused.",
  },
];

export default function HowItWorks() {
  return (
    <main className="page">
      <header className="page-head">
        <p className="eyebrow">How it works</p>
        <h1>Four steps, one live run</h1>
        <p className="lede">
          Press Run. The live run walks the four steps. When it finishes, Back and Next revisit any step, and the panel on the right shows that step’s proof.
        </p>
      </header>

      <section className="page-section">
        <h2>The steps</h2>
        <div className="stage-grid">
          {PHASES.map((phase, index) => (
            <article key={phase.id} className="stage-card">
              <div className="stage-top">
                <h3>
                  {phase.n} {phase.title}
                </h3>
                <span className="mono">{PROOF[index].panel}</span>
              </div>
              <p>{phase.line}</p>
              <p>{PROOF[index].text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="page-section">
        <h2>What a run records</h2>
        <table className="page-table">
          <thead>
            <tr>
              <th>Call</th>
              <th>Result</th>
              <th>Model calls</th>
              <th>Tokens</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>First call, list customers</td>
              <td>Failed, then learned a fix</td>
              <td className="mono">1</td>
              <td className="mono">~492</td>
            </tr>
            <tr>
              <td>A similar error, different words</td>
              <td>Found the old fix</td>
              <td className="mono">0</td>
              <td className="mono">0</td>
            </tr>
            <tr>
              <td>Same call, missing the safety key</td>
              <td>Blocked by the new rule</td>
              <td className="mono">0</td>
              <td className="mono">0</td>
            </tr>
            <tr>
              <td>Next call, list orders</td>
              <td>Worked the first time</td>
              <td className="mono">0</td>
              <td className="mono">0</td>
            </tr>
          </tbody>
        </table>
        <p className="note">From a real run on 11 Oct 2026. The index lookup examined one key in about 1 ms.</p>
      </section>

      <p className="page-cta">
        <Link href="/">Watch a real run</Link>
        <Link href="/developers">Get started</Link>
      </p>
    </main>
  );
}
