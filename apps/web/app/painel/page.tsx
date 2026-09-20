export default function PanelHome() {
  return (
    <main>
      <h1>Painel MuhBianco</h1>
      <p className="muted">
        Painel do tenant e ops. Login de staff, tenants, domínios e provisionamento chegam na fase 1.
        API: <code>/api/v1/auth/token</code>, <code>/api/v1/ops/tenants</code>.
      </p>
    </main>
  );
}
