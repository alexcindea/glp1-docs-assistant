"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/documents", label: "Documents" },
  { href: "/health", label: "Health" },
];

export function SiteHeader() {
  const path = usePathname();
  return (
    <header className="flex h-12 shrink-0 items-center gap-6 border-b px-4">
      <Link href="/" className="text-sm font-semibold">
        Docs Assistant
      </Link>
      <nav className="flex gap-4 text-sm" aria-label="Main">
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            aria-current={path === link.href ? "page" : undefined}
            className={cn(
              "hover:text-foreground transition-colors",
              path === link.href ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
