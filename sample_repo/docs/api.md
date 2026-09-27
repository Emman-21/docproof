# API Reference

This document describes every HTTP endpoint exposed by the DocProof backend.

## Contracts

### List all contracts

```
GET /contracts
```

Returns a JSON array of all `DocumentationContract` objects currently stored.

**Response:** `200 OK` — array of `DocumentationContract`

---

### Get a single contract

```
GET /contracts/{id}
```

Returns the `DocumentationContract` with the given `id`.

**Response:** `200 OK` — `DocumentationContract` | `404 Not Found`

---

### Delete a contract

```
DELETE /contracts/{id}
```

Permanently removes the contract with the given `id`.

**Response:** `204 No Content` | `404 Not Found`

---

## Verification

### Trigger verification

```
POST /verify
```

Accepts a `ProjectSelection` body and queues a new verification run.

**Response:** `202 Accepted`

---

## Approval

### Approve a contract

```
POST /approve/{id}
```

Marks the contract as approved.

**Response:** `200 OK` — updated `DocumentationContract`

---

### Reject a contract

```
POST /reject/{id}
```

Marks the contract as rejected.

**Response:** `200 OK` — updated `DocumentationContract`

---

## Status

### Application status

```
GET /status
```

Returns the application status.

**Response:** `200 OK` — `{"status": "ok"}`

---

## Trust Score

### Get trust score

```
GET /trust-score
```

Returns the current documentation trust score.

**Response:** `200 OK` — `TrustScoreResponse`
