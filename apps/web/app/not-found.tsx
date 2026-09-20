export default function NotFound() {
  // Neutral on purpose: never reveal which tenants exist.
  return (
    <main>
      <h1>Página não encontrada</h1>
      <p className="muted">Este endereço não está configurado.</p>
    </main>
  );
}
