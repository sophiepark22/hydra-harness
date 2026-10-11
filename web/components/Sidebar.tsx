"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import logo from "../logo.png";

const PRODUCT = [
  { href: "/", label: "Live demo", detail: "Watch a real run", icon: PlayIcon },
  { href: "/how-it-works", label: "How it works", detail: "The story in order", icon: LoopIcon },
];

const DEVELOPERS = [
  { href: "/developers", label: "Get started", detail: "For developers", icon: CodeIcon },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <Link href="/" className="brand" aria-label="Hydra-Harness home">
        <img src={logo.src} alt="" className="brand-logo" />
      </Link>

      <nav className="side-nav" aria-label="Sections">
        <p className="side-kicker">Product</p>
        {PRODUCT.map((link) => (
          <SideLink key={link.href} {...link} active={pathname === link.href} />
        ))}
        <p className="side-kicker">Developers</p>
        {DEVELOPERS.map((link) => (
          <SideLink key={link.href} {...link} active={pathname === link.href} />
        ))}
      </nav>

      <a
        className="side-link side-bottom"
        href="https://github.com/sophiepark22/hydra-harness"
        target="_blank"
        rel="noreferrer"
      >
        <GitIcon />
        <span className="side-copy">
          <strong>GitHub</strong>
          <small>sophiepark22/hydra-harness</small>
        </span>
      </a>
    </aside>
  );
}

function SideLink({
  href,
  label,
  detail,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  detail: string;
  icon: () => ReactNode;
  active: boolean;
}) {
  return (
    <Link href={href} className={active ? "side-link on" : "side-link"}>
      <Icon />
      <span className="side-copy">
        <strong>{label}</strong>
        <small>{detail}</small>
      </span>
    </Link>
  );
}

function PlayIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path d="M5 3.5v11l9-5.5-9-5.5z" fill="currentColor" />
    </svg>
  );
}

function LoopIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M3.5 9a5.5 5.5 0 0 1 9.6-3.7" />
      <path d="M14.5 9a5.5 5.5 0 0 1-9.6 3.7" />
      <path d="M12.5 2.5v3h-3" />
      <path d="M5.5 15.5v-3h3" />
    </svg>
  );
}

function CodeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M6 5 2.5 9 6 13" />
      <path d="m12 5 3.5 4-3.5 4" />
      <path d="m10.5 3-3 12" />
    </svg>
  );
}

function GitIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="5" cy="4" r="1.6" />
      <circle cx="5" cy="14" r="1.6" />
      <circle cx="13" cy="7" r="1.6" />
      <path d="M5 5.6v6.8" />
      <path d="M13 8.6c0 2.4-2.4 3-5.6 3.4" />
    </svg>
  );
}
