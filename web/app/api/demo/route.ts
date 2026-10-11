import { spawn } from "child_process";
import path from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

const ROOT = path.resolve(process.cwd(), "..");
const PYTHON = path.join(ROOT, ".venv", "bin", "python");

export async function GET() {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      const child = spawn(PYTHON, ["-m", "hydra_harness.stream_demo"], {
        cwd: ROOT,
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      });

      const send = (payload: unknown) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`));
      };

      let leftover = "";
      child.stdout.on("data", (chunk: Buffer) => {
        leftover += chunk.toString("utf8");
        const lines = leftover.split("\n");
        leftover = lines.pop() || "";
        for (const line of lines) {
          const text = line.trim();
          if (!text) continue;
          try {
            send(JSON.parse(text));
          } catch {
            send({ type: "log", message: text });
          }
        }
      });

      child.stderr.on("data", (chunk: Buffer) => {
        const text = chunk.toString("utf8").trim();
        if (text) send({ type: "log", message: text.slice(0, 240) });
      });

      const finish = (error?: string) => {
        if (error) send({ type: "error", message: error });
        try {
          controller.close();
        } catch {
          return;
        }
      };

      child.on("error", (err) => finish(err.message));
      child.on("close", (code) => {
        if (leftover.trim()) {
          try {
            send(JSON.parse(leftover));
          } catch {
            send({ type: "log", message: leftover.trim() });
          }
        }
        if (code && code !== 0) {
          send({ type: "error", message: "Demo process exited {}".replace("{}", String(code)) });
        }
        finish();
      });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
