import styles from "../../../panel.module.css";

import { loadTenantContext } from "@/lib/panel/tenant-context";

const MODULES: { key: string; label: string }[] = [
  { key: "storefront", label: "Vitrine" },
  { key: "catalog", label: "Catálogo" },
  { key: "inventory", label: "Estoque" },
  { key: "events", label: "Eventos" },
];

export default async function TenantOverview({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  const context = await loadTenantContext(tenantId);
  const accessMode = String(context.settings.storefront?.access_mode ?? "whitelist");
  return (
    <>
      <section className={styles.card}>
        <h2>Loja</h2>
        <table className={styles.table}>
          <tbody>
            <tr>
              <th>Status</th>
              <td>
                <span className={styles.badge}>{context.status}</span>
              </td>
            </tr>
            <tr>
              <th>Endereço</th>
              <td>
                {context.primary_host ? (
                  <a href={`https://${context.primary_host}`} target="_blank" rel="noreferrer">
                    {context.primary_host}
                  </a>
                ) : (
                  "—"
                )}
              </td>
            </tr>
            <tr>
              <th>Acesso à vitrine</th>
              <td>{accessMode}</td>
            </tr>
            <tr>
              <th>Fuso / moeda</th>
              <td>
                {context.timezone} · {context.currency}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section className={styles.card}>
        <h2>Módulos</h2>
        <ul>
          {MODULES.map((module) => (
            <li key={module.key}>
              {module.label}: {context.features[module.key] ? "ligado" : "desligado"}
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
