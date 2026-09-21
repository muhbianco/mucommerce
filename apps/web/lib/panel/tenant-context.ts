import "server-only";

import { notFound } from "next/navigation";
import { cache } from "react";

import { api, ApiError } from "./api";
import type { TenantPanelContext } from "./types";

/** Tenant context for panel pages, fetched once per request (layout and page share it). */
export const loadTenantContext = cache(async (tenantId: string): Promise<TenantPanelContext> => {
  try {
    return await api<TenantPanelContext>(`/admin/tenants/${encodeURIComponent(tenantId)}/context`);
  } catch (error) {
    // The API answers 404 both for unknown tenants and for tenants the user is not a member of.
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
});
