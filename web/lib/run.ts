export type NodeId =
  | "atlas"
  | "sandbox"
  | "evaluator"
  | "raindrop"
  | "model"
  | "vector"
  | "commit";

export type Tone = "fail" | "pass" | "info";

export type MoveMode = "forward" | "reverse" | "direct";

export type Move = { to: NodeId; mode: MoveMode };

export type Beat = {
  id: string;
  kicker: string;
  title: string;
  body: string;
  focus: NodeId;
  route: Move[];
  live: NodeId[];
  log: { tone: Tone; text: string };
  showV2: boolean;
  showTrace: boolean;
  showRecall: boolean;
  showGuard: boolean;
  showTaskB: boolean;
  callsA: number;
  callsRecall: number;
  callsB: number;
};

export const PHASES: { id: string; n: string; title: string; line: string; beats: string[] }[] = [
  {
    id: "execute",
    n: "01",
    title: "Baseline Execution (v1)",
    line: "Production task encounters runtime schema drift or API contract failure.",
    beats: ["seed", "fail", "trace"],
  },
  {
    id: "repair",
    n: "02",
    title: "Sandboxed Verification",
    line: "Isolated subprocess validates the patch against runtime assertions prior to commit.",
    beats: ["model", "prove"],
  },
  {
    id: "commit",
    n: "03",
    title: "MongoDB Atlas Persistence",
    line: "Patch committed as a versioned contract with compound indexing for the next read.",
    beats: ["commit", "embed", "recall"],
  },
  {
    id: "inherit",
    n: "04",
    title: "Fleet-Wide Inheritance",
    line: "Downstream agents inherit the verified contract directly from Atlas with zero token overhead.",
    beats: ["guard", "transfer"],
  },
];

export function phaseForBeat(beatId: string) {
  return PHASES.find((phase) => phase.beats.includes(beatId)) ?? PHASES[0];
}

export const NODES: { id: NodeId; label: string; detail: string }[] = [
  { id: "atlas", label: "Database", detail: "saved versions" },
  { id: "sandbox", label: "Test run", detail: "safe copy" },
  { id: "evaluator", label: "Check", detail: "pass or fail" },
  { id: "raindrop", label: "Log", detail: "what went wrong" },
  { id: "model", label: "Model", detail: "suggests a fix" },
  { id: "commit", label: "Save", detail: "after the test" },
  { id: "vector", label: "Memory", detail: "past mistakes" },
];

export const BEATS: Beat[] = [
  {
    id: "seed",
    kicker: "01  Start",
    title: "A blank tool is saved",
    body: "A new agent is written to MongoDB Atlas. The tool can name a resource. The version, page size, login token, and guardrail are absent.",
    focus: "atlas",
    route: [{ to: "atlas", mode: "direct" }],
    live: ["atlas"],
    log: { tone: "info", text: "Atlas  insert  hydra-b8b39c  v1  resource only" },
    showV2: false,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 0,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "fail",
    kicker: "02  Fails",
    title: "The first call fails",
    body: "List customers runs in a separate Python process. That process cannot see the database password or the API keys. The evaluator stops on the first missing field.",
    focus: "sandbox",
    route: [{ to: "sandbox", mode: "forward" }],
    live: ["atlas", "sandbox", "evaluator"],
    log: { tone: "fail", text: "Sandbox  pid 44468  execute  fail  Missing api_version" },
    showV2: false,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 0,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "trace",
    kicker: "03  Logged",
    title: "The mistake is written down",
    body: "The error becomes one event, hydra_tool_failure, on this agent. The model has not been called yet.",
    focus: "raindrop",
    route: [{ to: "raindrop", mode: "forward" }],
    live: ["evaluator", "raindrop"],
    log: { tone: "info", text: "Raindrop  hydra_tool_failure  convo hydra-b8b39c" },
    showV2: false,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 0,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "model",
    kicker: "04  Ask",
    title: "The model suggests a fix",
    body: "gpt-4o-mini sees the failure and a short API note. It chooses the values itself: version v1, page size 10, and a secret reference. It also adds an idempotency rule.",
    focus: "model",
    route: [{ to: "model", mode: "forward" }],
    live: ["raindrop", "model"],
    log: { tone: "info", text: "Model  gpt-4o-mini  1 call  ~484 tokens" },
    showV2: false,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "prove",
    kicker: "05  Test",
    title: "The fix is tested safely",
    body: "A customers call with no idempotency key must fail. A call that carries a key must pass. Atlas is still unchanged.",
    focus: "sandbox",
    route: [{ to: "sandbox", mode: "direct" }],
    live: ["model", "sandbox", "evaluator"],
    log: { tone: "pass", text: "Sandbox  pid 44471 fail guardrail  ·  pid 44472 pass verify" },
    showV2: false,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "commit",
    kicker: "06  Save",
    title: "Version 2 is saved",
    body: "The parent process writes version 2 only after the passing sandbox. The document keeps the schema, the diagnosis, and the new guardrail.",
    focus: "commit",
    route: [{ to: "evaluator", mode: "forward" }, { to: "commit", mode: "direct" }],
    live: ["sandbox", "commit", "atlas"],
    log: { tone: "pass", text: "Commit  v2  6acc05a67a8f758e946bb5cf  guardrail stored" },
    showV2: true,
    showTrace: false,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "embed",
    kicker: "07  Remember",
    title: "The mistake is remembered",
    body: "The exact failure and a plain-language wording are stored as vectors in error_traces. The repair that fixed them sits on the same documents.",
    focus: "vector",
    route: [{ to: "vector", mode: "forward" }],
    live: ["commit", "vector", "atlas"],
    log: { tone: "info", text: "Atlas Vector Search  index error_trace_vector  2 wordings stored" },
    showV2: true,
    showTrace: true,
    showRecall: false,
    showGuard: false,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "recall",
    kicker: "08  Match",
    title: "A similar error finds that fix",
    body: "List invoices fails with other words: no version header, no page size, no bearer credential. Vector search matches the customers repair at 0.94. The model is not called. The sandbox still has to pass before the write.",
    focus: "vector",
    route: [{ to: "atlas", mode: "forward" }, { to: "sandbox", mode: "forward" }, { to: "vector", mode: "direct" }],
    live: ["vector", "sandbox", "commit"],
    log: { tone: "pass", text: "Vector  score 0.94  prior agent hydra-b8b39c  model calls 0" },
    showV2: true,
    showTrace: true,
    showRecall: true,
    showGuard: false,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "guard",
    kicker: "09  Rule",
    title: "The new rule is enforced",
    body: "Customers without an idempotency key fail. The same call with the key passes. Both stay at zero model calls, because version 2 already has the rule.",
    focus: "evaluator",
    route: [{ to: "atlas", mode: "forward" }, { to: "sandbox", mode: "forward" }, { to: "evaluator", mode: "forward" }],
    live: ["atlas", "sandbox", "evaluator"],
    log: { tone: "fail", text: "Sandbox  pid 44512 fail without key  ·  pid 44513 pass with key" },
    showV2: true,
    showTrace: true,
    showRecall: true,
    showGuard: true,
    showTaskB: false,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
  {
    id: "transfer",
    kicker: "10  Next",
    title: "The next call works",
    body: "List orders reads version 2 from Atlas, carries idempotency key ord-1042, and passes in the sandbox. Repair stays off. Model calls stay at zero.",
    focus: "evaluator",
    route: [{ to: "atlas", mode: "direct" }, { to: "sandbox", mode: "forward" }, { to: "evaluator", mode: "forward" }],
    live: ["atlas", "sandbox", "evaluator"],
    log: { tone: "pass", text: "Sandbox  pid 44514  execute  pass  GET /v1/orders  attempt 1" },
    showV2: true,
    showTrace: true,
    showRecall: true,
    showGuard: true,
    showTaskB: true,
    callsA: 1,
    callsRecall: 0,
    callsB: 0,
  },
];

export const SCHEMA_ROWS = [
  { name: "resource", v1: "customers", v2: "customers", change: "kept" },
  { name: "api_version", v1: "—", v2: "v1", change: "added" },
  { name: "pagination_limit", v1: "—", v2: "10", change: "added" },
  { name: "auth_bearer", v1: "—", v2: "secret_reference", change: "added" },
  { name: "idempotency_key", v1: "—", v2: "unique_idempotency_key", change: "added" },
] as const;
