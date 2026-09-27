export type VerificationArea =
  | 'runtime_requirements'
  | 'commands'
  | 'config_env'
  | 'api_docs';

export type VerificationStatus = 'pass' | 'fail' | 'warning' | 'pending' | 'verifying';
export type ContractResultStatus = Exclude<VerificationStatus, 'pending' | 'verifying'>;
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';
export type IssueBucket = 'open' | 'pending' | 'resolved' | 'rejected';

export interface DocumentationContract {
  id: string;
  area: VerificationArea;
  source: string;
  claim: string;
  expected: string;
  actual: string;
  status: ContractResultStatus;
  evidence: string;
  evidenceFile?: string;
  evidenceLines?: string;
  evidenceSnippet?: string;
  suggested_fix: string;
  approvalStatus: ApprovalStatus;
  approved: boolean;
  reverified: boolean;
  severity?: 'high' | 'medium' | 'low';
}

export interface VerificationSummary {
  total: number;
  passed: number;
  failed: number;
  warnings: number;
}

export interface VerificationRun {
  id: string;
  date: string;
  branch: string;
  commit: string;
  trustScore: number;
  summary: VerificationSummary;
  current?: boolean;
}

export interface ProjectSelection {
  repository: string;
  branch: string;
  documentation: string[];
}

export interface VerificationAreaProgress {
  area: VerificationArea;
  label: string;
  status: 'pending' | 'running' | 'complete' | 'error';
}

export interface ContractFilters {
  status: 'all' | ContractResultStatus;
  query: string;
  area: 'all' | VerificationArea;
  source: 'all' | string;
}

export type PatchType = 'doc_edit' | 'config_edit' | 'code_edit';

export interface FixSuggestion {
  contract_id: string;
  area: VerificationArea;
  patch_type: PatchType;
  target_file: string;
  description: string;
  diff: string;
  raw_fix: string;
}
