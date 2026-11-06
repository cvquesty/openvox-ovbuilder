/** Thin fetch wrapper around the FastAPI backend. */

const BASE = '/api';

/** Soft 401: AuthProvider listens and navigates without a full page reload. */
export const AUTH_UNAUTHORIZED_EVENT = 'ovbuilder:unauthorized';

export function emitUnauthorized() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('ovbuilder_token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    const hadToken = !!token;
    localStorage.removeItem('ovbuilder_token');
    // Only soft-expire when a session existed; bare /auth/me without a token is normal.
    if (hadToken) emitUnauthorized();
    throw new Error('Session expired');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type AppRole = 'admin' | 'builder' | 'viewer';

export interface AuthUser {
  username: string;
  role: AppRole;
  display_name?: string;
  email?: string;
}

export interface AuthUserAdmin {
  username: string;
  email?: string | null;
  display_name?: string | null;
  ldap_role: AppRole;
  role_override: AppRole | null;
  role: AppRole;
  ldap_checked_at?: string | null;
}

export const auth = {
  login: (username: string, password: string) => {
    const body = new URLSearchParams();
    body.append('username', username);
    body.append('password', password);
    return request<{ access_token: string; token_type: string; role: string; username: string }>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    }).then((data) => {
      localStorage.setItem('ovbuilder_token', data.access_token);
      return data;
    });
  },
  me: () => request<AuthUser>('/auth/me'),
  logout: () => {
    localStorage.removeItem('ovbuilder_token');
    return Promise.resolve();
  },
  users: () => request<AuthUserAdmin[]>('/auth/users'),
  setRoleOverride: (username: string, role_override: AppRole | null) =>
    request<AuthUserAdmin>(`/auth/users/${encodeURIComponent(username)}`, {
      method: 'PUT',
      body: JSON.stringify({ role_override }),
    }),
  notificationPrefs: () => request<NotificationPrefs>('/auth/me/notifications'),
  updateNotificationPrefs: (body: Partial<NotificationPrefs>) =>
    request<NotificationPrefs>('/auth/me/notifications', { method: 'PUT', body: JSON.stringify(body) }),
};

export interface NotificationPrefs {
  email_enabled: boolean;
  on_success: boolean;
  on_failure: boolean;
  on_cancelled: boolean;
}

export const builds = {
  submit: (body: BuildRequest) => request<BuildJob>('/builds', { method: 'POST', body: JSON.stringify(body) }),
  list: () => request<BuildJob[]>('/builds'),
  get: (id: string) => request<BuildJob>(`/builds/${id}`),
  cancel: (id: string) => request<BuildJob>(`/builds/${id}/cancel`, { method: 'POST' }),
};

export const inventory = {
  osImages: () => request<OsImage[]>('/inventory/os-images'),
  environments: () => request<Environment[]>('/inventory/environments'),
  clusters: () => request<string[]>('/inventory/clusters'),
  hosts: () => request<string[]>('/inventory/hosts'),
  networks: () => request<string[]>('/inventory/networks'),
  datacenters: () => request<string[]>('/inventory/datacenters'),
  datastoreClusters: () => request<string[]>('/inventory/datastore-clusters'),
  datastores: () => request<string[]>('/inventory/datastores'),
};

const vmPath = (name: string) => `/vms/${encodeURIComponent(name)}`;
type ActionResult = Record<string, unknown>;

export const vms = {
  list: () => request<InventoryVm[]>('/vms'),
  powerOn: (name: string) => request<ActionResult>(`${vmPath(name)}/power-on`, { method: 'POST' }),
  powerOff: (name: string) => request<ActionResult>(`${vmPath(name)}/power-off`, { method: 'POST' }),
  reboot: (name: string) => request<ActionResult>(`${vmPath(name)}/reboot`, { method: 'POST' }),
  reset: (name: string) => request<ActionResult>(`${vmPath(name)}/reset`, { method: 'POST' }),
  snapshots: (name: string) => request<VmSnapshot[]>(`${vmPath(name)}/snapshots`),
  snapshot: (name: string, snapName: string, description = '', memory = false) =>
    request<ActionResult>(`${vmPath(name)}/snapshots`, {
      method: 'POST',
      body: JSON.stringify({ name: snapName, description, memory }),
    }),
  revertSnapshot: (name: string, snapshotId: string) =>
    request<ActionResult>(`${vmPath(name)}/snapshots/${encodeURIComponent(snapshotId)}/revert`, { method: 'POST' }),
  deleteSnapshot: (name: string, snapshotId: string, removeChildren = false) =>
    request<ActionResult>(
      `${vmPath(name)}/snapshots/${encodeURIComponent(snapshotId)}?remove_children=${removeChildren}`,
      { method: 'DELETE' },
    ),
  resize: (name: string, body: VmResize) =>
    request<ActionResult>(`${vmPath(name)}/resize`, { method: 'POST', body: JSON.stringify(body) }),
  lease: (name: string) => request<VmLease>(`${vmPath(name)}/lease`),
  setLease: (name: string, expiresAt: string | null) =>
    request<VmLease>(`${vmPath(name)}/lease`, {
      method: 'PUT',
      body: JSON.stringify({ expires_at: expiresAt }),
    }),
  destroy: (name: string) =>
    request<ActionResult>(`${vmPath(name)}?confirm=${encodeURIComponent(name)}`, { method: 'DELETE' }),
  reclaimPreview: () => request<ReclaimReport>('/vms/reclaim/preview'),
  reclaim: () => request<ReclaimReport>('/vms/reclaim', { method: 'POST' }),
};

export interface BuildRequest {
  hostname: string;
  ip: string;
  os_image: string;
  prefix?: string;
  cpus?: number;
  memory_gb?: number;
  disk_gb?: number;
  gateway?: string;
  dns?: string[];
  environment: string;
  cluster?: string;
  network?: string;
  location?: string;
  vsphere_server?: string;
  vsphere_user?: string;
  /** Accepted on submit only; omitted from every job response. */
  vsphere_password?: string;
  skip_dnf_groups?: boolean;
}

export interface BuildJob {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelling' | 'cancelled';
  request: BuildRequest;
  requested_by: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  celery_task_id?: string;
  log_tail: string;
  error?: string;
  vm_ip?: string;
  vm_name?: string;
}

export interface OsImage {
  key: string;
  label: string;
  default_user: string;
  /** Inventory name (ovbuilder-*); never includes datacenter. */
  name?: string;
}

export interface Environment {
  key: string;
  label: string;
  datastore_cluster: string;
  cluster?: string;
}

export interface InventoryVm {
  name: string;
  power_state: string;
  guest_os?: string;
  ip?: string | null;
  cpus?: number | null;
  memory_mb?: number | null;
  disk_gb?: number | null;
  uuid?: string | null;
  tools?: string;
  folder?: string | null;
  overall_status?: string;
  owner?: string | null;
  /** Created by a succeeded ovbuilder build. */
  managed?: boolean;
  /** Left behind by a failed/cancelled build (verified via Terraform state). */
  orphan?: boolean;
  job_id?: string | null;
  expires_at?: string | null;
}

export interface VmSnapshot {
  id: string;
  name: string;
  description: string;
  created?: string | null;
  power_state: string;
  parent_id?: string | null;
  current: boolean;
}

export interface VmResize {
  cpus?: number;
  memory_gb?: number;
  disk_gb?: number;
  disk_index?: number;
}

export interface VmLease {
  vm_name: string;
  owner?: string | null;
  managed: boolean;
  expires_at?: string | null;
  explicit: boolean;
  powered_off_since?: string | null;
  last_action?: string | null;
  last_action_at?: string | null;
  max_days: number;
}

export interface ReclaimCandidate {
  name: string;
  owner: string;
  reason: 'expired' | 'idle';
  action: 'power_off' | 'destroy' | 'none';
  power_state: string;
  expires_at?: string | null;
  powered_off_since?: string | null;
  error?: string | null;
  done: boolean;
}

export interface ReclaimReport {
  dry_run: boolean;
  enabled: boolean;
  policy: { lease_days: number; idle_days: number; action: string };
  scanned: number;
  candidates: ReclaimCandidate[];
}

export interface RuntimeSettings {
  vsphere_server: string;
  vsphere_user: string;
  vsphere_password_set: boolean;
  vsphere_datacenter: string;
  vsphere_ignore_ssl: boolean;
  npm_registry: string;
  notify_webhook_set: boolean;
  notify_on_success: boolean;
  notify_on_failure: boolean;
  notify_on_cancelled: boolean;
  notify_webhook_format: WebhookFormat;
  public_base_url: string;
  notify_text_template: string;
  notify_email_subject_template: string;
  notify_email_body_template: string;
  notify_email_enabled: boolean;
  notify_email_owner: boolean;
  notify_email_to: string;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password_set: boolean;
  smtp_starttls: boolean;
  smtp_ssl: boolean;
  smtp_from: string;
}

export type WebhookFormat = 'slack' | 'json';

export interface RuntimeSettingsUpdate {
  vsphere_server?: string;
  vsphere_user?: string;
  /** Omit or undefined to leave unchanged; empty string clears. */
  vsphere_password?: string;
  vsphere_datacenter?: string;
  vsphere_ignore_ssl?: boolean;
  npm_registry?: string;
  /** Omit or undefined to leave unchanged; empty string clears. */
  notify_webhook_url?: string;
  notify_on_success?: boolean;
  notify_on_failure?: boolean;
  notify_on_cancelled?: boolean;
  notify_webhook_format?: WebhookFormat;
  public_base_url?: string;
  notify_text_template?: string;
  notify_email_subject_template?: string;
  notify_email_body_template?: string;
  notify_email_enabled?: boolean;
  notify_email_owner?: boolean;
  notify_email_to?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_user?: string;
  /** Omit or undefined to leave unchanged; empty string clears. */
  smtp_password?: string;
  smtp_starttls?: boolean;
  smtp_ssl?: boolean;
  smtp_from?: string;
}

export const settings = {
  get: () => request<RuntimeSettings>('/settings'),
  update: (body: RuntimeSettingsUpdate) =>
    request<RuntimeSettings>('/settings', { method: 'PUT', body: JSON.stringify(body) }),
};
