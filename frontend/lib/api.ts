const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

function getErrorMessage(data: unknown, fallback: string): string {
  const d = data as { detail?: unknown; message?: string };
  if (typeof d?.detail === "string") return d.detail;
  if (typeof d?.message === "string") return d.message;
  if (Array.isArray(d?.detail)) {
    return d.detail
      .map((item: { msg?: string; message?: string }) => item?.msg ?? item?.message ?? String(item))
      .join(". ");
  }
  return fallback;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

export function saveToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem("token", token);
  document.cookie = `token=${token}; path=/; max-age=86400; samesite=lax`;
}

export function removeToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("token");
  document.cookie = "token=; path=/; max-age=0";
}

async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers: HeadersInit = {
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers });
}

export const api = {
  auth: {
    async register(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Registration failed"));
      return data as { token: string; expires_in: number; user: { id: number; email: string } };
    },
    async login(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Login failed"));
      return data as { token: string; expires_in: number; user: { id: number; email: string } };
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/auth/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get user"));
      return data as { id: string; email: string; created_at: string };
    },
  },
  clients: {
    async create(name: string) {
      const res = await authFetch(`${BASE_URL}/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create client"));
      return data as { id: string; name: string; api_key: string; created_at: string };
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/clients/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get client"));
      return data as { id: string; name: string; api_key: string; created_at: string };
    },
  },
  documents: {
    async list() {
      const res = await authFetch(`${BASE_URL}/documents`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to list documents"));
      const list = data as { documents: Array<{ id: string; filename: string; file_type: string; status: string; created_at: string }> };
      return list.documents;
    },
    async upload(file: File) {
      const formData = new FormData();
      formData.append("file", file);
      const token = getToken();
      const res = await fetch(`${BASE_URL}/documents`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to upload document"));
      return data as {
        id: string;
        filename: string;
        file_type: string;
        status: string;
        created_at: string;
      };
    },
    async delete(id: string) {
      const res = await authFetch(`${BASE_URL}/documents/${id}`, { method: "DELETE" });
      if (res.status !== 204 && !res.ok) {
        const data = await res.json();
        throw new Error(getErrorMessage(data, "Failed to delete document"));
      }
    },
  },
  embeddings: {
    async create(documentId: string) {
      const res = await authFetch(`${BASE_URL}/embeddings/documents/${documentId}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create embeddings"));
      return data as { document_id: string; chunks_created: number; status: string };
    },
  },
  chat: {
    async send(question: string, apiKey: string, sessionId?: string) {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      };
      const body: { question: string; session_id?: string } = { question };
      if (sessionId) body.session_id = sessionId;
      const res = await fetch(`${BASE_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Chat failed"));
      return data as {
        answer: string;
        session_id: string;
        source_documents?: string[];
        tokens_used?: number;
      };
    },
    async getHistory(sessionId: string) {
      const res = await authFetch(`${BASE_URL}/chat/history/${sessionId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get history"));
      return data as {
        session_id: string;
        messages: Array<{
          id: number;
          role: string;
          content: string;
          created_at: string;
        }>;
      };
    },
  },
};
