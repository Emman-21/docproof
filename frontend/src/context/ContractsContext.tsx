import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  DEFAULT_BRANCH,
  DEFAULT_DOCUMENTATION,
  DEMO_REPOSITORY,
} from '../constants';
import {
  approveFix,
  getContracts,
  getFixes,
  getVerifyStatus,
  rejectFix,
  reverifyContract,
  triggerVerification,
} from '../api/client';
import type {
  DocumentationContract,
  FixSuggestion,
  ProjectSelection,
  VerificationRun,
  VerificationSummary,
} from '../types';
import {
  applyApprovedFix,
  applyRejectedFix,
  computeAreaScores,
  computeSummary,
  computeTrustScore,
  createCurrentHistoryRun,
  createFreshProjectSelection,
} from '../utils/verification';

type VerificationMode = 'idle' | 'running' | 'complete' | 'error';

interface ContractsContextValue {
  contracts: DocumentationContract[];
  fixes: FixSuggestion[];
  trustScoreAfter: number | null;
  verifyError: string;
  project: ProjectSelection;
  history: VerificationRun[];
  verificationMode: VerificationMode;
  lastVerifiedLabel: string;
  score: number;
  previousScore: number;
  summary: VerificationSummary;
  areaScores: ReturnType<typeof computeAreaScores>;
  setProject: (project: ProjectSelection) => void;
  useDemoProject: () => void;
  startVerification: (project?: ProjectSelection) => void;
  finishVerification: () => void;
  failVerification: () => void;
  approveContract: (contractId: string) => void;
  rejectContract: (contractId: string) => void;
  completeReverification: (contractId: string) => void;
  getContract: (contractId?: string) => DocumentationContract | undefined;
  resetDemo: () => void;
  startNewRepository: () => void;
}

const ContractsContext = createContext<ContractsContextValue | undefined>(undefined);

const defaultProject: ProjectSelection = createFreshProjectSelection(DEFAULT_BRANCH, DEFAULT_DOCUMENTATION);

export function ContractsProvider({ children }: { children: ReactNode }) {
  const [contracts, setContracts] = useState<DocumentationContract[]>([]);
  const [fixes, setFixes] = useState<FixSuggestion[]>([]);
  const [trustScoreAfter, setTrustScoreAfter] = useState<number | null>(null);
  const [verifyError, setVerifyError] = useState('');
  const [project, setProject] = useState<ProjectSelection>(defaultProject);
  const [history, setHistory] = useState<VerificationRun[]>([]);
  const [verificationMode, setVerificationMode] = useState<VerificationMode>('idle');
  const [previousScore, setPreviousScore] = useState(0);
  const [lastVerifiedLabel, setLastVerifiedLabel] = useState('Not verified yet');

  // On mount: restore project from sessionStorage, then fetch live contracts
  // and verification status from the backend.
  useEffect(() => {
    // Restore project so the topbar still shows repo/branch after a refresh
    const saved = sessionStorage.getItem('docproof_project');
    if (saved) {
      try { setProject(JSON.parse(saved)); } catch { /* ignore */ }
    }

    // Fetch current contracts from backend
    getContracts()
      .then((data) => {
        if (data.length > 0) setContracts(data);
      })
      .catch(() => {});

    // If backend is still running a pipeline, resume polling
    getVerifyStatus()
      .then(({ status }) => {
        if (status === 'running') setVerificationMode('running');
        if (status === 'complete') setVerificationMode('complete');
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Persist project to sessionStorage whenever it changes
  useEffect(() => {
    if (project.repository) {
      sessionStorage.setItem('docproof_project', JSON.stringify(project));
    }
  }, [project]);

  const summary = useMemo(() => computeSummary(contracts), [contracts]);
  const score = useMemo(() => computeTrustScore(contracts), [contracts]);
  const areaScores = useMemo(() => computeAreaScores(contracts), [contracts]);

  const useDemoProject = useCallback(() => {
    setProject({
      repository: DEMO_REPOSITORY,
      branch: DEFAULT_BRANCH,
      documentation: [...DEFAULT_DOCUMENTATION],
    });
  }, []);

  // Fire POST /verify immediately when the user confirms a repository so the
  // backend pipeline starts running in parallel with the progress animation.
  const startVerification = useCallback((selectedProject = project) => {
    setVerificationMode('running');
    triggerVerification(selectedProject).catch((error: unknown) => {
      // If the trigger itself fails (network down, etc.) surface the error.
      setVerifyError(
        error instanceof Error ? error.message : 'Could not start verification.'
      );
      setVerificationMode('error');
    });
  }, [project]);

  // Called by VerificationRunning once the progress animation reaches 90%.
  // By this point POST /verify has already been sent; we just start polling
  // GET /verify/status until the backend reports complete or error.
  const finishVerification = useCallback(() => {
    const poll = window.setInterval(() => {
      getVerifyStatus()
        .then(({ status, error, trust_score_after }) => {
          if (status === 'complete') {
            window.clearInterval(poll);
            setTrustScoreAfter(trust_score_after);
            return Promise.all([getContracts(), getFixes()]).then(([data, fixData]) => {
              setContracts(data);
              setFixes(fixData);
              setVerificationMode('complete');
              setLastVerifiedLabel('Just now');
              const run = createCurrentHistoryRun(data, project.branch || DEFAULT_BRANCH, 'api-verify', 'Just now');
              setHistory((current) => [run, ...current.map((item) => ({ ...item, current: false }))]);
            });
          }
          if (status === 'error') {
            window.clearInterval(poll);
            setVerifyError(error || 'Verification failed.');
            setVerificationMode('error');
          }
        })
        .catch(() => {
          window.clearInterval(poll);
          setVerificationMode('error');
        });
    }, 2000);
  }, [project]);

  const failVerification = useCallback(() => {
    setVerificationMode('error');
  }, []);

  const approveContract = useCallback((contractId: string) => {
    approveFix(contractId)
      .then((updated) => {
        setContracts((current) =>
          current.map((contract) => (contract.id === contractId ? updated : contract)),
        );
      })
      .catch(() => {
        // optimistic fallback — mark locally if API failed
        setContracts((current) =>
          current.map((contract) =>
            contract.id === contractId
              ? { ...contract, approvalStatus: 'approved', approved: true, reverified: false }
              : contract,
          ),
        );
      });
  }, []);

  const rejectContract = useCallback((contractId: string) => {
    rejectFix(contractId)
      .then((updated) => {
        setContracts((current) =>
          current.map((contract) => (contract.id === contractId ? updated : contract)),
        );
      })
      .catch(() => {
        setContracts((current) => applyRejectedFix(current, contractId));
      });
  }, []);

  const completeReverification = useCallback((contractId: string) => {
    const beforeScore = computeTrustScore(contracts);
    reverifyContract(contractId)
      .then((updatedContract) => {
        setContracts((current) => {
          const next = current.map((c) => (c.id === contractId ? updatedContract : c));
          const run = createCurrentHistoryRun(next, project.branch || DEFAULT_BRANCH, 'api-fix', 'Just now');
          setHistory((h) => [run, ...h.map((item) => ({ ...item, current: false }))]);
          return next;
        });
        setPreviousScore(beforeScore);
        setLastVerifiedLabel('Just now');
      })
      .catch(() => {
        // Fallback: apply locally if API unreachable
        const updated = applyApprovedFix(contracts, contractId);
        const run = createCurrentHistoryRun(updated, project.branch || DEFAULT_BRANCH, 'api-fix', 'Just now');
        setPreviousScore(beforeScore);
        setContracts(updated);
        setHistory((h) => [run, ...h.map((item) => ({ ...item, current: false }))]);
        setLastVerifiedLabel('Just now');
      });
  }, [contracts, project.branch]);

  const getContract = useCallback(
    (contractId?: string) => contracts.find((contract) => contract.id === contractId),
    [contracts],
  );

  const startNewRepository = useCallback(() => {
    setContracts([]);
    setHistory([]);
    setVerificationMode('idle');
    setPreviousScore(0);
    setLastVerifiedLabel('Not verified yet');
    setProject(createFreshProjectSelection(DEFAULT_BRANCH, DEFAULT_DOCUMENTATION));
    sessionStorage.removeItem('docproof_project');
  }, []);

  const resetDemo = useCallback(() => {
    // Re-fetch from backend so we get the current seed state
    getContracts()
      .then((data) => {
        setContracts(data);
        setPreviousScore(computeTrustScore(data));
      })
      .catch(() => {});
    setHistory([]);
    setVerificationMode('idle');
    setLastVerifiedLabel('Not verified yet');
    setProject({
      repository: DEMO_REPOSITORY,
      branch: DEFAULT_BRANCH,
      documentation: [...DEFAULT_DOCUMENTATION],
    });
  }, []);

  const value = useMemo<ContractsContextValue>(() => ({
    contracts,
    fixes,
    trustScoreAfter,
    verifyError,
    project,
    history,
    verificationMode,
    lastVerifiedLabel,
    score,
    previousScore,
    summary,
    areaScores,
    setProject,
    useDemoProject,
    startVerification,
    finishVerification,
    failVerification,
    approveContract,
    rejectContract,
    completeReverification,
    getContract,
    resetDemo,
    startNewRepository,
  }), [
    contracts,
    fixes,
    trustScoreAfter,
    verifyError,
    project,
    history,
    verificationMode,
    lastVerifiedLabel,
    score,
    previousScore,
    summary,
    areaScores,
    useDemoProject,
    startVerification,
    finishVerification,
    failVerification,
    approveContract,
    rejectContract,
    completeReverification,
    getContract,
    resetDemo,
    startNewRepository,
  ]);

  return <ContractsContext.Provider value={value}>{children}</ContractsContext.Provider>;
}

export function useContracts() {
  const context = useContext(ContractsContext);
  if (!context) throw new Error('useContracts must be used within ContractsProvider');
  return context;
}
