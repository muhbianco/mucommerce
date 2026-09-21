"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { completeUpload, mediaStatus, requestUpload } from "./actions";

const ACCEPT = ["image/jpeg", "image/png", "image/webp"];
const MAX_BYTES = 10 * 1024 * 1024;
const POLL_MS = 1500;
const POLL_LIMIT = 40; // ~60 s

type Line = { name: string; state: string };

const ERRORS: Record<string, string> = {
  storage_unavailable: "armazenamento indisponível",
  conflict: "limite de imagens atingido",
  feature_disabled: "catálogo desligado",
  not_found: "item não encontrado",
};

/**
 * Direct upload: the API hands out a presigned POST for the private bucket, the browser sends
 * the file straight to storage (the API never proxies the bytes), then we confirm and poll
 * until the worker has produced the WebP renditions.
 */
export function ImageUploader({
  tenantId,
  ownerType,
  ownerId = null,
  label = "Enviar imagens",
}: {
  tenantId: string;
  ownerType: "product" | "tenant_brand" | "landing";
  ownerId?: string | null;
  label?: string;
}) {
  const router = useRouter();
  const [lines, setLines] = useState<Line[]>([]);
  const [busy, setBusy] = useState(false);

  const update = (index: number, state: string) =>
    setLines((current) => current.map((line, i) => (i === index ? { ...line, state } : line)));

  async function uploadOne(file: File, index: number): Promise<void> {
    if (!ACCEPT.includes(file.type)) return update(index, "formato não aceito (JPEG, PNG, WebP)");
    if (file.size > MAX_BYTES) return update(index, "maior que 10 MB");
    update(index, "preparando…");
    const ticket = await requestUpload({
      tenantId,
      ownerType,
      ownerId,
      mime: file.type,
      bytes: file.size,
      filename: file.name,
    });
    if (!ticket.ok) return update(index, `erro: ${ERRORS[ticket.code] ?? ticket.code}`);

    update(index, "enviando…");
    const body = new FormData();
    for (const [key, value] of Object.entries(ticket.fields)) body.append(key, value);
    body.append("file", file); // must be the last field of a POST policy upload
    const sent = await fetch(ticket.url, { method: "POST", body }).catch(() => null);
    if (!sent || !sent.ok) return update(index, "falha no envio ao armazenamento");

    const confirmed = await completeUpload(tenantId, ticket.mediaId);
    if (confirmed === "error") return update(index, "falha ao confirmar");
    update(index, "processando…");
    for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      const { status, reason } = await mediaStatus(tenantId, ticket.mediaId);
      if (status === "ready") return update(index, "pronta");
      if (status === "failed") return update(index, `recusada: ${reason ?? "imagem inválida"}`);
      if (status === "error") return update(index, "falha ao consultar");
    }
    update(index, "ainda processando; atualize a página em instantes");
  }

  async function onChange(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setBusy(true);
    setLines(files.map((file) => ({ name: file.name, state: "na fila" })));
    // One at a time: keeps position order and the media worker processes serially anyway.
    for (const [index, file] of files.entries()) await uploadOne(file, index);
    setBusy(false);
    router.refresh();
  }

  return (
    <div>
      <label>
        {label}{" "}
        <input type="file" accept={ACCEPT.join(",")} multiple onChange={onChange} disabled={busy} />
      </label>
      {lines.length > 0 ? (
        <ul>
          {lines.map((line, i) => (
            <li key={`${line.name}-${i}`}>
              {line.name}: {line.state}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
