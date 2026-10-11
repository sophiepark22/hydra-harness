"use client";

import { useEffect, useRef, useState } from "react";
import { PHASES } from "@/lib/run";

type SchemaRow = { name: string; v1: unknown; v2: unknown; change: string };
type AtlasRow = { version: string; where: string; detail: string; id?: string; guardrail?: string };

type Live = {
  agentId: string;
  host: string;
  status: string;
  schemaRows: SchemaRow[];
  atlasRows: AtlasRow[];
  score?: number;
  tokens?: number;
  modelCalls?: number;
  elapsedMs?: number;
  indexName?: string;
  indexMs?: number;
  keysExamined?: number;
  diagnosis?: string;
  failure?: string;
  failPid?: string;
  provePid?: string;
  proveOk?: boolean;
  documentId?: string;
  versionSaved?: number;
  taskACalls?: number;
  taskATokens?: number;
  taskBCalls?: number;
  phaseNotes: Record<string, string>;
  finished: boolean;
};

const EMPTY: Live = {
  agentId: "",
  host: "",
  status: "Press Play. The run walks the four steps, then you can go back through them.",
  schemaRows: [],
  atlasRows: [],
  phaseNotes: {},
  finished: false,
};

const PHASE_HOLD_MS = 2000;

export function DemoBoard() {
  const [shown, setShown] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(false);
  const [live, setLive] = useState<Live>(EMPTY);
  const sourceRef = useRef<EventSource | null>(null);
  const shownRef = useRef(-1);
  const reachedRef = useRef(-1);
  const holdUntilRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const reviewPendingRef = useRef(false);

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const reveal = (next: number) => {
    shownRef.current = next;
    setShown(next);
  };

  const pump = () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    const wait = holdUntilRef.current - Date.now();
    if (wait > 0) {
      timerRef.current = window.setTimeout(pump, wait);
      return;
    }
    timerRef.current = null;
    if (shownRef.current < reachedRef.current) {
      reveal(shownRef.current + 1);
      holdUntilRef.current = Date.now() + PHASE_HOLD_MS;
      timerRef.current = window.setTimeout(pump, PHASE_HOLD_MS);
      return;
    }
    if (reviewPendingRef.current && shownRef.current >= PHASES.length - 1) {
      setBusy(false);
      setReview(true);
    }
  };

  const markReached = (beatId: string) => {
    const index = PHASES.findIndex((phase) => phase.beats.includes(beatId));
    if (index > reachedRef.current) {
      reachedRef.current = index;
      pump();
    }
  };

  const applyPayload = (payload: Record<string, unknown>) => {
    if (payload.type === "start") {
      setLive((current) => ({
        ...current,
        agentId: String(payload.agent_id || ""),
        host: String(payload.host || ""),
          status: "Connected. Saving a blank version of the tool.",
      }));
      return;
    }
    if (payload.type === "error") {
      setPlaying(false);
      setBusy(false);
      reviewPendingRef.current = false;
      setLive((current) => ({ ...current, status: String(payload.message || "Demo failed") }));
      sourceRef.current?.close();
      return;
    }
    if (payload.type === "done") {
      setPlaying(false);
      reviewPendingRef.current = true;
      setLive((current) => ({
        ...current,
        finished: true,
        status: "The next call used what the first one learned.",
      }));
      sourceRef.current?.close();
      pump();
      return;
    }
    if (payload.type === "beat") {
      if (typeof payload.id === "string") markReached(payload.id);
      setLive((current) => applyBeat(current, payload));
    }
  };

  const startRun = () => {
    sourceRef.current?.close();
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    reviewPendingRef.current = false;
    reachedRef.current = 0;
    holdUntilRef.current = Date.now() + PHASE_HOLD_MS;
    setReview(false);
    setBusy(true);
    reveal(0);
    setLive({ ...EMPTY, status: "Connecting to Atlas…" });
    setPlaying(true);
    const source = new EventSource("/api/demo");
    sourceRef.current = source;
    source.onmessage = (event) => {
      try {
        applyPayload(JSON.parse(event.data));
      } catch {
        return;
      }
    };
    source.onerror = () => {
      if (reviewPendingRef.current) {
        source.close();
        return;
      }
      setPlaying(false);
      setBusy(false);
      setLive((current) => ({
        ...current,
        status: current.agentId ? current.status : "Could not start the Python harness.",
      }));
      source.close();
    };
  };

  return (
    <main className="board stage">
      <header className="top">
        <div>
          <p className="eyebrow">Live demo</p>
          <h1>Watch your AI learn</h1>
          <p className="lede">Press Play for the live run. When it finishes, use Back and Next to review.</p>
        </div>
        <div className="controls">
          {review ? (
            <>
              <span className="live-badge ok">
                v2 saved{live.indexMs != null ? ` · ${live.indexMs} ms lookup` : ""}
              </span>
              <button type="button" className="ghost" onClick={() => reveal(Math.max(0, shown - 1))} disabled={shown <= 0}>
                Back
              </button>
              <button type="button" className="step" onClick={() => reveal(Math.min(PHASES.length - 1, shown + 1))} disabled={shown >= PHASES.length - 1}>
                Next
              </button>
              <button type="button" className="ghost" onClick={startRun}>
                Restart
              </button>
            </>
          ) : busy ? (
            <>
              <span className="live-badge">{live.agentId ? "Running" : "Connecting to Atlas…"}</span>
              <button type="button" disabled>
                Running
              </button>
            </>
          ) : (
            <button type="button" onClick={startRun}>
              Play
            </button>
          )}
        </div>
      </header>

      <nav className="rail story" aria-label="Demo phases">
        {PHASES.map((phase, phaseIndex) => {
          const state = phaseIndex === shown ? "on" : phaseIndex < shown ? "done" : "";
          return (
            <div key={phase.id} className={state}>
              <em>{phase.n}</em>
              <strong>{phase.title}</strong>
              <span>{phase.line}</span>
            </div>
          );
        })}
      </nav>

      <section className="proof">
        <Inspector shown={shown} live={live} />
      </section>
    </main>
  );
}

function applyBeat(current: Live, payload: Record<string, unknown>): Live {
  const next = { ...current };
  if (typeof payload.log === "string") next.status = payload.log;
  if (payload.id === "fail" && typeof payload.message === "string" && !payload.message.startsWith("Running")) {
    next.failure = payload.message;
  }
  if (payload.id === "fail" && payload.sandbox_id) next.failPid = String(payload.sandbox_id);
  if (payload.id === "prove" && payload.sandbox_id) next.provePid = String(payload.sandbox_id);
  if (payload.id === "prove" && typeof payload.ok === "boolean") next.proveOk = payload.ok;
  if (payload.id === "model" && payload.model_calls != null) next.taskACalls = Number(payload.model_calls);
  if (payload.id === "model" && payload.tokens != null) next.taskATokens = Number(payload.tokens);
  if (payload.id === "commit" && payload.document_id) next.documentId = String(payload.document_id);
  if (payload.id === "commit" && payload.version != null) next.versionSaved = Number(payload.version);
  if (payload.id === "transfer" && payload.model_calls != null) next.taskBCalls = Number(payload.model_calls);
  if (payload.index_ms != null) next.indexMs = Number(payload.index_ms);
  if (payload.keys_examined != null) next.keysExamined = Number(payload.keys_examined);
  if (payload.schema_rows) next.schemaRows = payload.schema_rows as SchemaRow[];
  if (payload.atlas) next.atlasRows = payload.atlas as AtlasRow[];
  if (payload.score != null) next.score = Number(payload.score);
  if (payload.tokens != null) next.tokens = Number(payload.tokens);
  if (payload.model_calls != null) next.modelCalls = Number(payload.model_calls);
  if (payload.elapsed_ms != null) next.elapsedMs = Number(payload.elapsed_ms);
  if (typeof payload.index === "string") next.indexName = payload.index;
  if (typeof payload.diagnosis === "string") next.diagnosis = payload.diagnosis;
  return next;
}

function Inspector({ shown, live }: { shown: number; live: Live }) {
  if (shown <= 0) return <FailureTrace live={live} />;
  if (shown === 1) return <SandboxTrace live={live} />;
  if (shown === 2) return <AtlasTrace live={live} />;
  return <InheritTrace live={live} />;
}

function FailureTrace({ live }: { live: Live }) {
  return (
    <article className="card inspector">
      <h2>Raindrop telemetry</h2>
      <p className="card-kicker">Live failure</p>
      {live.failure ? (
        <dl className="trace">
          <div>
            <dt>Event</dt>
            <dd>hydra_tool_failure</dd>
          </div>
          <div>
            <dt>Error</dt>
            <dd>{live.failure}</dd>
          </div>
          <div>
            <dt>Payload</dt>
            <dd>{"{ \"resource\": \"customers\", \"endpoint\": \"/v1/customers\" }"}</dd>
          </div>
          <div>
            <dt>Process</dt>
            <dd>{live.failPid || "running"}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>Not called yet</dd>
          </div>
        </dl>
      ) : (
        <p className="waiting">Waiting for the live failure from this run.</p>
      )}
    </article>
  );
}

function SandboxTrace({ live }: { live: Live }) {
  const added = live.schemaRows.filter((row) => row.change === "added");
  return (
    <article className="card inspector">
      <h2>Isolated subprocess</h2>
      <p className="card-kicker">Sandbox</p>
      {live.provePid || live.taskACalls != null ? (
        <dl className="trace">
          <div>
            <dt>Process</dt>
            <dd>{live.provePid || live.failPid || "running"}</dd>
          </div>
          <div>
            <dt>Model calls</dt>
            <dd>{live.taskACalls != null ? String(live.taskACalls) : "running"}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>{live.taskATokens != null ? String(live.taskATokens) : "running"}</dd>
          </div>
          <div>
            <dt>Patch</dt>
            <dd>
              {added.length
                ? added.map((row) => `${row.name}: ${fmt(row.v2)}`).join(" · ")
                : "Waiting for the model patch"}
            </dd>
          </div>
          <div>
            <dt>Result</dt>
            <dd>{live.proveOk ? "Passed. Safe to save." : "Still testing."}</dd>
          </div>
        </dl>
      ) : (
        <p className="waiting">Waiting for the isolated test from this run.</p>
      )}
    </article>
  );
}

function AtlasTrace({ live }: { live: Live }) {
  return (
    <article className="card inspector">
      <h2>MongoDB Atlas</h2>
      <p className="card-kicker">agent_states · index agent_latest_version (agent_id, version)</p>
      {live.schemaRows.length ? (
        <>
          <table>
            <thead>
              <tr>
                <th>Parameter</th>
                <th>v1</th>
                <th>v2</th>
              </tr>
            </thead>
            <tbody>
              {live.schemaRows.map((row) => (
                <tr key={row.name}>
                  <td className="mono">{row.name}</td>
                  <td className="mono">{fmt(row.v1)}</td>
                  <td className="mono">{fmt(row.v2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Committed{live.versionSaved != null ? ` v${live.versionSaved}` : ""}
            {live.documentId ? ` · ${live.documentId}` : ""}
          </p>
        </>
      ) : (
        <p className="waiting">Waiting for version 2 to be written.</p>
      )}
    </article>
  );
}

function InheritTrace({ live }: { live: Live }) {
  return (
    <article className="card inspector">
      <h2>Downstream task</h2>
      <p className="card-kicker">Reads the saved contract</p>
      {live.taskBCalls != null ? (
        <dl className="trace">
          <div>
            <dt>Task</dt>
            <dd>List orders, first try</dd>
          </div>
          <div>
            <dt>Loaded</dt>
            <dd>version {live.versionSaved != null ? live.versionSaved : 2} from agent_states</dd>
          </div>
          <div>
            <dt>Index</dt>
            <dd>
              {live.indexName || "agent_latest_version"}
              {live.keysExamined != null ? ` · ${live.keysExamined} key` : ""}
              {live.indexMs != null ? ` · ${live.indexMs} ms` : ""}
            </dd>
          </div>
          <div>
            <dt>Model calls</dt>
            <dd>{live.taskBCalls}</dd>
          </div>
          <div>
            <dt>Result</dt>
            <dd>{live.taskBCalls === 0 ? "Passed. The model was not called." : "Passed."}</dd>
          </div>
        </dl>
      ) : (
        <p className="waiting">Waiting for the next task to read version 2.</p>
      )}
    </article>
  );
}

function fmt(value: unknown): string {
  if (value == null || value === "" || value === "—") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
