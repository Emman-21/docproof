# DocProof Frontend

Frontend-only hackathon implementation for **DocProof**.

> Your code has tests. Your documentation should too.

DocProof verifies whether documentation claims still match repository evidence. This frontend demonstrates the complete user experience with deterministic mock data while remaining ready for a later FastAPI + IBM Bob 2.0 integration.

## Stack

- React 18
- TypeScript
- Vite
- React Router
- Plain CSS with design tokens

## Run

The frontend uses the FastAPI backend for contracts and verification. Start both
services in separate terminals from the repository root.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.
The API should respond at `http://127.0.0.1:8000/health`.

### Frontend

In a second terminal from the repository root:

```bash
cd frontend
npm install
npm run dev
```

Vite normally opens at `http://localhost:5173` and proxies API requests to the
backend on port 8000.

## Checks

```bash
npm run typecheck
npm run test:logic
npm run build
```

## Demo workflow

1. Open `/`.
2. Click **Try Demo Repository**.
3. Click **Run Verification**.
4. Watch the deterministic verification workflow.
5. Review the Dashboard and Trust Score.
6. Open a failed Documentation Contract such as `DP-001`.
7. Inspect repository evidence.
8. Click **Review Fix**.
9. Click **Approve & Re-verify**.
10. Watch the contract change from failed to passed and the Trust Score increase.

## Routes

- `/` Project Setup
- `/verify` Verification Running
- `/dashboard` Overview Dashboard
- `/contracts` Documentation Contracts
- `/contracts/:id` Contract Detail
- `/contracts/:id/fix` Human Approval / Diff
- `/contracts/:id/reverified` Re-verification Result
- `/issues` Issues & Fixes
- `/trust-score` Documentation Trust Score
- `/history` Verification History

## Deployment configuration

The Vercel frontend requires a separately deployed FastAPI backend. In the
Vercel project's environment variables, set:

```bash
VITE_API_BASE_URL=https://<your-backend-domain>
```

Set `CORS_ORIGINS` on the backend to a comma-separated list containing the
frontend's exact Vercel domain, for example:

```bash
CORS_ORIGINS=http://localhost:5173,https://docproof-me5w.vercel.app
```

Redeploy the frontend after changing `VITE_API_BASE_URL`, since Vite embeds it
at build time. The backend can verify public HTTPS GitHub repositories and
branches; private repositories require authentication and are not supported.

`src/api/client.ts` uses these backend endpoints:

- `GET /contracts`
- `GET /contracts/{id}`
- `POST /verify`
- `POST /approve/{id}`
- `POST /reject/{id}`
- `GET /trust-score`

The shared `DocumentationContract` type lives in `src/types.ts` and mirrors the project architecture contract.
