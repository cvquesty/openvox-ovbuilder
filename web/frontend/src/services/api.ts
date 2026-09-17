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
  me: () => request<{ username: string; role: string; display_name?: string; email?: string }>('/auth/me'),
  logout: () => {
    localStorage.removeItem('ovbuilder_token');
    return Promise.resolve();
  },
};

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
  networks: () => request<string[]>('/inventory/networks'),
};

export const vms = {
  list: () => request<InventoryVm[]>('/vms'),
  powerOn: (name: string) =>
    request<Record<string, unknown>>(`/vms/${encodeURIComponent(name)}/power-on`, { method: 'POST' }),
  powerOff: (name: string) =>
    request<Record<string, unknown>>(`/vms/${encodeURIComponent(name)}/power-off`, { method: 'POST' }),
  reboot: (name: string) =>
    request<Record<string, unknown>>(`/vms/${encodeURIComponent(name)}/reboot`, { method: 'POST' }),
  snapshot: (name: string, snapName: string, description = '') =>
    request<Record<string, unknown>>(`/vms/${encodeURIComponent(name)}/snapshot`, {
      method: 'POST',
      body: JSON.stringify({ name: snapName, description }),
    }),
  destroy: (name: string) =>
    request<Record<string, unknown>>(`/vms/${encodeURIComponent(name)}`, { method: 'DELETE' }),
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
  location?: string;
  vsphere_server?: string;
  vsphere_user?: string;
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
}

export interface Environment {
  key: string;
  label: string;
  datastore_cluster: string;
}

export interface InventoryVm {
  name: string;
  power_state: string;
  guest_os?: string;
  ip?: string | null;
  cpus?: number | null;
  memory_mb?: number | null;
  uuid?: string | null;
  tools?: string;
  folder?: string | null;
  overall_status?: string;
}
