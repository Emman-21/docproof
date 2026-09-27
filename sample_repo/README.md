# Sample Project

A demo application used by DocProof to exercise its real verification engine.

## Prerequisites

Before running this project, make sure you have the following installed:

- **Node.js 18+** — the minimum version required to run the frontend.
- **npm 8+** — required to manage frontend dependencies.
- **Python 3.9+** — required for the backend verification service.

## Getting Started

### Install dependencies

```bash
npm install
pip install -r requirements.txt
```

### Run the development server

```bash
npm start
```

### Run the backend

```bash
python -m uvicorn main:app --reload
```

## Runtime Requirements

| Runtime  | Minimum Version |
|----------|-----------------|
| Node.js  | 18              |
| npm      | 8               |
| Python   | 3.9             |

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

- `DATABASE_URL` — required. Connection string for the database.
- `API_KEY` — required. API key for external service.

## Notes

This project is intentionally seeded with version mismatches between this README
and the actual configuration files (`package.json`, `requirements.txt`) so that
DocProof can detect and report those contradictions.

- README claims **Node.js 18+** but `package.json` enforces `>=20.0.0`.
- README claims **npm 8+** but `package.json` enforces `>=10.0.0`.
- README claims **Python 3.9+** but `requirements.txt` pins `python_requires>=3.11`.
