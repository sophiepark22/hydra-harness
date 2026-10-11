"use client";

import { useEffect, useRef, useState } from "react";
import { NODES, phaseForBeat, type Beat, type NodeId } from "@/lib/run";

const W = 1000;
const H = 340;
const TOP = 92;
const BOTTOM = 248;
const LABEL_GAP = 54;
const LEFT = 150;
const RIGHT = 850;
const R = (BOTTOM - TOP) / 2;
const LINE = RIGHT - LEFT;
const ARC = Math.PI * R;
const TOTAL = 2 * LINE + 2 * ARC;

const RING_PATH = `M${LEFT},${TOP} H${RIGHT} A${R},${R} 0 0 1 ${RIGHT},${BOTTOM} H${LEFT} A${R},${R} 0 0 1 ${LEFT},${TOP} Z`;

type Point = { x: number; y: number; at: number };

const POINTS: Record<NodeId, Point> = {
  atlas: { x: LEFT, y: TOP, at: 0 },
  sandbox: { x: LEFT + LINE / 3, y: TOP, at: LINE / 3 },
  evaluator: { x: LEFT + (2 * LINE) / 3, y: TOP, at: (2 * LINE) / 3 },
  raindrop: { x: RIGHT, y: TOP, at: LINE },
  model: { x: RIGHT, y: BOTTOM, at: LINE + ARC },
  commit: { x: (LEFT + RIGHT) / 2, y: BOTTOM, at: LINE + ARC + LINE / 2 },
  vector: { x: LEFT, y: BOTTOM, at: 2 * LINE + ARC },
};

const STEP = LINE / 3;

function wrap(value: number): number {
  return ((value % TOTAL) + TOTAL) % TOTAL;
}

function ease(t: number): number {
  return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
}

export function LoopStage({ beat }: { beat: Beat }) {
  const pathRef = useRef<SVGPathElement>(null);
  const atRef = useRef<NodeId>("atlas");
  const frameRef = useRef(0);
  const [pos, setPos] = useState({ x: POINTS.atlas.x, y: POINTS.atlas.y });

  const shortcut = beat.route.some((move) => move.mode === "direct" && move.to === "vector");

  useEffect(() => {
    const path = pathRef.current;
    if (!path) return;
    cancelAnimationFrame(frameRef.current);
    const actual = path.getTotalLength();
    const moves = beat.route;

    const pointAt = (at: number) => {
      const p = path.getPointAtLength((wrap(at) / TOTAL) * actual);
      return { x: p.x, y: p.y };
    };

    const run = (step: number) => {
      if (step >= moves.length) return;
      const move = moves[step];
      const from = POINTS[atRef.current];
      const to = POINTS[move.to];
      let duration = 500;
      let distance = 0;
      let direction = 1;
      if (move.mode === "forward") {
        distance = wrap(to.at - from.at);
      } else if (move.mode === "reverse") {
        distance = wrap(from.at - to.at);
        direction = -1;
      }
      if (move.mode !== "direct") {
        duration = Math.max(260, (distance / STEP) * 240);
      }
      if (move.mode !== "direct" && distance === 0) {
        atRef.current = move.to;
        run(step + 1);
        return;
      }
      const started = performance.now();
      const tick = (now: number) => {
        const t = Math.min(1, (now - started) / duration);
        const k = ease(t);
        if (move.mode === "direct") {
          setPos({ x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k });
        } else {
          setPos(pointAt(from.at + direction * distance * k));
        }
        if (t < 1) {
          frameRef.current = requestAnimationFrame(tick);
          return;
        }
        atRef.current = move.to;
        run(step + 1);
      };
      frameRef.current = requestAnimationFrame(tick);
    };

    run(0);
    return () => cancelAnimationFrame(frameRef.current);
  }, [beat]);

  return (
    <div className="loop" aria-label="Harness loop">
      <svg className="loop-svg" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        <path ref={pathRef} d={RING_PATH} fill="none" stroke="#eeeae6" strokeWidth="3" />
        <path
          d={`M${RIGHT - 40},${TOP} l-10,-7 v14 z`}
          fill="#d6d3d1"
        />
        <path
          d={`M${LEFT + 60},${BOTTOM} l10,-7 v14 z`}
          fill="#d6d3d1"
        />
        {shortcut ? (
          <line
            x1={POINTS.sandbox.x}
            y1={POINTS.sandbox.y}
            x2={POINTS.vector.x}
            y2={POINTS.vector.y}
            stroke="#ff4f1a"
            strokeWidth="2"
            strokeDasharray="8 8"
          />
        ) : null}
        {NODES.map((node) => {
          const point = POINTS[node.id];
          return <circle key={node.id} cx={point.x} cy={point.y} r="5" fill="#ffffff" stroke="#d6d3d1" strokeWidth="2" />;
        })}
        <text x={W / 2} y={H / 2 + 6} textAnchor="middle" fill="#111111" fontSize="18" fontWeight="600">
          {shortcut ? "Reused a past fix" : phaseForBeat(beat.id).title}
        </text>
      </svg>
      {NODES.map((node) => {
        const point = POINTS[node.id];
        const on = node.id === beat.focus;
        const labelY = point.y === TOP ? point.y - LABEL_GAP : point.y + LABEL_GAP;
        return (
          <article
            key={node.id}
            className={on ? "node on" : "node"}
            style={{ left: `${(point.x / W) * 100}%`, top: `${(labelY / H) * 100}%` }}
          >
            {node.label}
            <small>{node.detail}</small>
          </article>
        );
      })}
      <div className="orb" style={{ left: `${(pos.x / W) * 100}%`, top: `${(pos.y / H) * 100}%` }} />
    </div>
  );
}
