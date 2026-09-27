import { useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { AREA_LABELS } from '../constants';
import { useContracts } from '../context/ContractsContext';
import type { VerificationArea, VerificationAreaProgress } from '../types';
import Icon from '../components/Icon';
import ProgressIndicator from '../components/ProgressIndicator';
import VerificationAreaCard from '../components/VerificationAreaCard';

// Progress animation steps — pauses at 90 to wait for the real backend result
const steps = [8, 18, 31, 47, 63, 78, 90];
const areas: VerificationArea[] = ['runtime_requirements', 'commands', 'config_env', 'api_docs'];

export default function VerificationRunning() {
  const navigate = useNavigate();
  const { project, verificationMode, verifyError, startVerification, finishVerification } = useContracts();
  const [progress, setProgress] = useState(6);
  const finished = useRef(false);

  useEffect(() => {
    if (!project.repository) return;
    if (verificationMode === 'idle') {
      startVerification();
      return;
    }
    if (verificationMode !== 'running') return;

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let index = 0;

    // Animate progress up to 90%, then hold and wait for finishVerification to
    // set verificationMode → 'complete' or 'error'.
    const timer = window.setInterval(() => {
      index += 1;
      const next = steps[Math.min(index, steps.length - 1)];
      setProgress(next);

      // Once we reach 90% kick off the real backend call (only once)
      if (next >= 90 && !finished.current) {
        finished.current = true;
        window.clearInterval(timer);
        finishVerification();
      }
    }, reduced ? 80 : 430);

    return () => window.clearInterval(timer);
  }, [finishVerification, project.repository, startVerification, verificationMode]);

  // Watch for backend completing or erroring, then navigate
  useEffect(() => {
    if (verificationMode === 'complete') {
      setProgress(100);
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.setTimeout(() => navigate('/dashboard', { replace: true }), reduced ? 120 : 650);
    }
    if (verificationMode === 'error') {
      setProgress(90);
    }
  }, [verificationMode, navigate]);

  const areaProgress = useMemo<VerificationAreaProgress[]>(() => areas.map((area, index) => {
    const thresholds = [22, 42, 62, 82];
    const completeAt = [42, 62, 82, 100];
    const status = progress >= completeAt[index] ? 'complete' : progress >= thresholds[index] ? 'running' : 'pending';
    return { area, label: AREA_LABELS[area], status };
  }), [progress]);

  if (!project.repository) return <Navigate to="/" replace />;

  if (verificationMode === 'error') {
    const isCloneError = verifyError.toLowerCase().includes('clone') || verifyError.toLowerCase().includes('not found') || verifyError.toLowerCase().includes('repository');
    return (
      <main className="verification-page">
        <header className="verification-header">
          <div className="setup-brand small"><span className="brand-mark"><Icon name="shield" size={16} /></span><span>DocProof</span></div>
          <span>{project.branch}</span>
        </header>
        <section className="verification-content">
          <section className="panel verification-error">
            <span className="error-mark"><Icon name="warning" size={22} /></span>
            <h1>{isCloneError ? 'Repository Not Found' : 'Verification Failed'}</h1>
            <p>
              {isCloneError
                ? `Could not clone "${project.repository}". Make sure the URL is correct and the repository is public.`
                : verifyError || 'An unexpected error occurred during verification. Please try again.'}
            </p>
            <button className="button button-primary" onClick={() => navigate('/', { replace: true })}>
              Try Another Repository
            </button>
          </section>
        </section>
      </main>
    );
  }

  const repoName = project.repository.replace(/\/$/, '').split('/').pop();
  return (
    <main className="verification-page">
      <header className="verification-header">
        <div className="setup-brand small"><span className="brand-mark"><Icon name="shield" size={16} /></span><span>DocProof</span></div>
        <span>{repoName} / {project.branch}</span>
      </header>
      <section className="verification-content">
        <div className="verification-title">
          <span className="live-dot" />
          <div>
            <h1>Verifying Documentation</h1>
            <p>DocProof is cloning and analysing repository evidence against your documentation claims.</p>
          </div>
        </div>
        <ProgressIndicator progress={progress} />

        <section className="workflow-stage-list panel">
          <div className={progress >= 8 ? 'complete' : 'active'}><span><Icon name="check" size={14} /></span><div><strong>Cloning Repository</strong><small>Fetching the latest commit from {repoName}.</small></div></div>
          <div className={progress >= 88 ? 'complete' : progress >= 18 ? 'active' : ''}><span><Icon name={progress >= 88 ? 'check' : 'refresh'} size={14} /></span><div><strong>Verifying Repository Claims</strong><small>Comparing claims against code, configuration, and routes.</small></div></div>
          <div className={progress >= 96 ? 'complete' : progress >= 88 ? 'active' : ''}><span><Icon name={progress >= 96 ? 'check' : 'code'} size={14} /></span><div><strong>Generating Evidence</strong><small>Building evidence-backed Documentation Contracts.</small></div></div>
          <div className={progress >= 100 ? 'complete' : progress >= 96 ? 'active' : ''}><span><Icon name={progress >= 100 ? 'check' : 'shield'} size={14} /></span><div><strong>Calculating Trust Score</strong><small>Summarizing verification results across all four areas.</small></div></div>
        </section>

        <div className="verification-area-grid">{areaProgress.map((item) => <VerificationAreaCard key={item.area} item={item} />)}</div>
        {progress >= 90 && verificationMode === 'running' && (
          <p className="verification-note">Waiting for backend analysis to complete — this may take up to 60 seconds for large repositories.</p>
        )}
      </section>
    </main>
  );
}
