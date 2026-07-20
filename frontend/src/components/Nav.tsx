"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/calculator", label: "Calculator" },
  { href: "/schedule", label: "Schedule" },
  { href: "/regions", label: "Regions" },
  { href: "/history", label: "History" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-surface/90 backdrop-blur">
      <nav className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:gap-6 sm:px-6">
        <Link
          href="/"
          className="flex shrink-0 items-center gap-2 font-semibold"
        >
          <span
            className="inline-block h-3 w-3 rounded-full"
            style={{ background: "var(--color-brand)" }}
            aria-hidden
          />
          <span>EcoCompute</span>
        </Link>

        <ul className="flex items-center gap-1 overflow-x-auto text-sm [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {LINKS.slice(1).map((link) => {
            const active =
              pathname === link.href ||
              (link.href !== "/" && pathname.startsWith(link.href));
            return (
              <li key={link.href} className="shrink-0">
                <Link
                  href={link.href}
                  className={
                    "rounded-md px-3 py-1.5 transition-colors " +
                    (active
                      ? "bg-brand-soft text-brand-dark font-medium"
                      : "text-muted hover:bg-surface-muted hover:text-fg")
                  }
                >
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
