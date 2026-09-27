"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import styles from "../../../panel.module.css";

export interface NavItem {
  href: string;
  label: string;
}

/** Menu da loja em abas; a página atual fica marcada (aria-current, para leitor de tela também). */
export function PanelNav({ items, base }: { items: NavItem[]; base: string }) {
  const pathname = usePathname();
  // O middleware reescreve /x para /painel/x; a URL que a pessoa vê é sem o prefixo.
  const path = pathname.replace(/^\/painel(?=\/|$)/, "") || "/";
  const current = (href: string): boolean =>
    href === base ? path === base : path === href || path.startsWith(`${href}/`);
  return (
    <nav className={styles.subnav} aria-label="Menu da loja">
      {items.map((item) => (
        <Link key={item.href} href={item.href} aria-current={current(item.href) ? "page" : undefined}>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
