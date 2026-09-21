/** Shapes returned by api-commerce (v1) that the panel renders. */

export interface Membership {
  tenant_id: string;
  tenant_slug: string;
  role: string;
  scopes: string[];
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  platform_role: string | null;
  memberships: Membership[];
}

export interface TenantPanelContext {
  tenant_id: string;
  slug: string;
  name: string;
  status: string;
  timezone: string;
  currency: string;
  primary_host: string | null;
  features: Record<string, boolean>;
  settings: Record<string, Record<string, unknown>>;
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  status: string;
  plan: string;
  timezone: string;
  created_at: string;
  activated_at: string | null;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Domain {
  id: string;
  hostname: string;
  kind: string;
  purpose: string;
  role: string;
  status: string;
  tls_status: string;
  last_error: string | null;
}

/** Mirrors TenantService.set_status: which transitions the API accepts from each status. */
export const TENANT_TRANSITIONS: Record<string, string[]> = {
  draft: ["provisioning", "active", "archived"],
  provisioning: ["active", "draft"],
  active: ["suspended"],
  suspended: ["active", "archived"],
  archived: [],
};

export const ACCESS_MODES = ["public", "login_required", "whitelist"] as const;
