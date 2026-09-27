import type { VerificationArea } from './types';

export const APP_NAME = 'DocProof';
export const APP_TAGLINE = 'Your code has tests. Your documentation should too.';
// The backend resolves this sentinel to the bundled sample_repo directory.
export const DEMO_REPOSITORY = 'sample_repo';
export const DEFAULT_BRANCH = 'main';
export const DEFAULT_DOCUMENTATION = ['README.md', 'docs/setup.md', 'docs/api.md'];
export const AVAILABLE_DOCUMENTATION = ['README.md', 'docs/setup.md', 'docs/api.md', '.env.example'];

export const AREA_LABELS: Record<VerificationArea, string> = {
  runtime_requirements: 'Runtime Requirements',
  commands: 'Project Commands',
  config_env: 'Environment & Configuration',
  api_docs: 'API Documentation',
};

export const AREA_SHORT_LABELS: Record<VerificationArea, string> = {
  runtime_requirements: 'Runtime',
  commands: 'Commands',
  config_env: 'Configuration',
  api_docs: 'API Docs',
};
