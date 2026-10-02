import type {
  DocumentationContract,
  FixSuggestion,
  ProjectSelection,
} from '../types';

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL as string | undefined
)?.trim() ?? '';

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let errorMessage = `DocProof API request failed with status ${response.status}`;

    try {
      const errorData = await response.json();

      if (errorData?.detail) {
        errorMessage = errorData.detail;
      }
    } catch {
      // Ignore JSON parsing errors and use the default message
    }

    throw new Error(errorMessage);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function getContracts(): Promise<DocumentationContract[]> {
  return request<DocumentationContract[]>('/contracts');
}

export async function getContract(
  id: string
): Promise<DocumentationContract | undefined> {
  return request<DocumentationContract>(
    `/contracts/${encodeURIComponent(id)}`
  );
}

export async function triggerVerification(
  project: ProjectSelection
): Promise<void> {
  await request<void>('/verify', {
    method: 'POST',
    body: JSON.stringify(project),
  });
}

export async function approveFix(
  id: string
): Promise<DocumentationContract> {
  return request<DocumentationContract>(
    `/approve/${encodeURIComponent(id)}`,
    {
      method: 'POST',
    }
  );
}

export async function rejectFix(
  id: string
): Promise<DocumentationContract> {
  return request<DocumentationContract>(
    `/reject/${encodeURIComponent(id)}`,
    {
      method: 'POST',
    }
  );
}

export async function reverifyContract(
  id: string
): Promise<DocumentationContract> {
  return request<DocumentationContract>(
    `/reverify/${encodeURIComponent(id)}`,
    {
      method: 'POST',
    }
  );
}

export async function getVerifyStatus(): Promise<{
  status: string;
  error: string;
  trust_score_after: number | null;
}> {
  return request<{
    status: string;
    error: string;
    trust_score_after: number | null;
  }>('/verify/status');
}

export async function getFixes(): Promise<FixSuggestion[]> {
  return request<FixSuggestion[]>('/fixes');
}

export async function getTrustScore(): Promise<{ score: number }> {
  return request<{ score: number }>('/trust-score');
}