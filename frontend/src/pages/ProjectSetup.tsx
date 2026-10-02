import { useMemo, useState, type ChangeEvent, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  APP_TAGLINE,
  AVAILABLE_DOCUMENTATION,
  DEFAULT_DOCUMENTATION,
  DEMO_REPOSITORY,
} from '../constants';
import { useContracts } from '../context/ContractsContext';
import Icon from '../components/Icon';

interface FormErrors {
  repository?: string;
  branch?: string;
  documentation?: string;
}

export default function ProjectSetup() {
  const navigate = useNavigate();
  const { project, setProject, useDemoProject, startVerification } = useContracts();
  const [repository, setRepository] = useState(project.repository);
  const [branch, setBranch] = useState(project.branch || 'main');
  const [documentation, setDocumentation] = useState<string[]>(project.documentation.length ? project.documentation : DEFAULT_DOCUMENTATION);
  const [errors, setErrors] = useState<FormErrors>({});

  const selectedText = useMemo(() => `${documentation.length} source${documentation.length === 1 ? '' : 's'} selected`, [documentation.length]);

  function toggleDocument(file: string) {
    setDocumentation((current) => current.includes(file) ? current.filter((item) => item !== file) : [...current, file]);
    setErrors((current) => ({ ...current, documentation: undefined }));
  }

  function fillDemo() {
    useDemoProject();
    setRepository(DEMO_REPOSITORY);
    setBranch('main');
    setDocumentation([...DEFAULT_DOCUMENTATION]);
    setErrors({});
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const nextErrors: FormErrors = {};
    if (!repository.trim()) nextErrors.repository = 'Enter a GitHub repository URL.';
    if (!branch.trim()) nextErrors.branch = 'Enter a branch to verify.';
    if (documentation.length === 0) nextErrors.documentation = 'Select at least one documentation source.';
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;

    const selectedProject = {
      repository: repository.trim(),
      branch: branch.trim(),
      documentation,
    };
    setProject(selectedProject);
    startVerification(selectedProject);
    navigate('/verify');
  }

  return (
    <main className="setup-page">
      <section className="setup-intro">
        <div className="setup-brand"><span className="brand-mark large"><Icon name="shield" size={21} /></span><span>DocProof</span></div>
        <h1>Your code has tests.<br />Your documentation should too.</h1>
        <p>Verify that your technical documentation still matches your actual codebase. DocProof finds documentation drift, shows repository evidence, and keeps fixes under human control.</p>
        <div className="setup-workflow" aria-label="DocProof workflow">
          <span>Understand</span><Icon name="arrow-right" size={13} /><span>Verify</span><Icon name="arrow-right" size={13} /><span>Prove</span><Icon name="arrow-right" size={13} /><span>Fix</span><Icon name="arrow-right" size={13} /><span>Verify Again</span>
        </div>
        <p className="setup-tagline">{APP_TAGLINE}</p>
      </section>

      <form className="setup-card" onSubmit={submit} noValidate>
        <div className="setup-card-heading"><div><span>Start verification</span><h2>Choose a repository</h2></div><span className="demo-chip">IBM Bob 2.0 Hackathon</span></div>

        <label className="field-group">
          <span>GitHub Repository</span>
          <div className={`input-shell${errors.repository ? ' error' : ''}`}><Icon name="github" size={17} /><input value={repository} onChange={(event: ChangeEvent<HTMLInputElement>) => { setRepository(event.target.value); setErrors((current) => ({ ...current, repository: undefined })); }} placeholder="https://github.com/your-team/project" /></div>
          {errors.repository && <small className="field-error">{errors.repository}</small>}
        </label>

        <label className="field-group">
          <span>Branch</span>
          <div className={`input-shell${errors.branch ? ' error' : ''}`}><Icon name="branch" size={16} /><input value={branch} onChange={(event: ChangeEvent<HTMLInputElement>) => { setBranch(event.target.value); setErrors((current) => ({ ...current, branch: undefined })); }} placeholder="main" /></div>
          {errors.branch && <small className="field-error">{errors.branch}</small>}
        </label>

        <fieldset className="doc-selector">
          <legend>Documentation <span>{selectedText}</span></legend>
          <div className="doc-options">
            {AVAILABLE_DOCUMENTATION.map((file) => (
              <label key={file} className={`doc-option${documentation.includes(file) ? ' selected' : ''}`}>
                <input type="checkbox" checked={documentation.includes(file)} onChange={() => toggleDocument(file)} />
                <span className="doc-check"><Icon name="check" size={13} /></span>
                <Icon name="file" size={16} />
                <span>{file}</span>
              </label>
            ))}
          </div>
          {errors.documentation && <small className="field-error">{errors.documentation}</small>}
        </fieldset>

        <div className="setup-actions">
          <button type="button" className="button button-secondary" onClick={fillDemo}><Icon name="spark" size={15} /> Try Demo Repository</button>
          <button type="submit" className="button button-primary">Run Verification <Icon name="play" size={15} /></button>
        </div>
      </form>
    </main>
  );
}
