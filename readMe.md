# Your Name | YOUR-STUDENT-ID

# PDC-Sp24-[Your-ID]-[Your-LastName]
### Course: Parallel and Distributed Computing | StudySync Assignment

---

## ▶️ How to Run the Code (Start Here)

### Step 1 — Clone the repo

```bash
git clone https://github.com/<your-username>/PDC-Sp24-[Your-ID]-[Your-LastName].git
cd PDC-Sp24-[Your-ID]-[Your-LastName]
```

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 3 — Run the before/after demo (proves the fix works)

```bash
python tests/test_circuit_breaker.py
```

You will see two sections printed in your terminal:
- **BEFORE** — simulates the server hanging for all users (no circuit breaker)
- **AFTER** — shows instant fallbacks once the breaker trips, full recovery cycle

### Step 4 — Run all unit tests

```bash
pytest tests/test_circuit_breaker.py -v
```

Expected output: **7 passed**

### Step 5 — Start the FastAPI server

```bash
export STUDENT_ID="BSCS23190"

uvicorn app.main:app --reload --port 8000
```

Then open: **http://localhost:8000/docs** (Swagger UI with all endpoints)

### Step 6 — Manually test the circuit breaker via curl

```bash
# Healthy call (should succeed)
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is recursion?", "simulate_failure": false}'

# Force a failure (run this 3 times to trip the circuit)
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is recursion?", "simulate_failure": true}'

# After 3 failures — circuit is OPEN, this returns instant fallback
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "test", "simulate_failure": false}'

# Check circuit state
curl http://localhost:8000/api/circuit-status

# Reset the circuit back to CLOSED
curl -X POST http://localhost:8000/api/reset-circuit

# Verify the X-Student-ID header is on EVERY response
curl -I http://localhost:8000/
```

---

## 📁 Project Structure

```
PDC-Sp24-[Your-ID]-[Your-LastName]/
│
├── app/
│   ├── __init__.py
│   ├── circuit_breaker.py   ← Core pattern: CLOSED / OPEN / HALF-OPEN state machine
│   └── main.py              ← FastAPI app, X-Student-ID middleware, all endpoints
│
├── tests/
│   └── test_circuit_breaker.py  ← Before/after demo + 7 unit tests
│
├── requirements.txt
├── README.md                ← You are here
└── REPORT.md                ← Full written report (Parts 1 & 2)
```

---

## 🌐 API Endpoints

| Method | Path                    | Description                                   |
|--------|-------------------------|-----------------------------------------------|
| GET    | `/`                     | Health check                                  |
| POST   | `/api/ask`              | Ask the LLM — protected by circuit breaker    |
| GET    | `/api/circuit-status`   | Live state, failure count, retry timer        |
| POST   | `/api/reset-circuit`    | Reset breaker to CLOSED (for testing/demos)   |

### POST `/api/ask` — request body

```json
{
  "question": "Explain recursion",
  "simulate_failure": false
}
```

Set `simulate_failure: true` to force the LLM to throw a timeout error — use this to trip the breaker during your demo.

---

## ⚙️ Circuit Breaker Settings (in `main.py`)

| Parameter           | Value  | Meaning                                        |
|---------------------|--------|------------------------------------------------|
| `failure_threshold` | 3      | Trips to OPEN after 3 consecutive failures     |
| `recovery_timeout`  | 20 s   | Waits 20 s in OPEN before allowing a probe     |

---

## ✅ Assignment Checklist

- [x] `X-Student-ID` header on every API response (middleware in `main.py`)
- [x] Circuit Breaker pattern fully implemented with all 3 states
- [x] Test script triggers the failure state and proves the fix
- [x] README first line = Name + Student ID
- [x] Repo named `PDC-Sp24-[Your-ID]-[Your-LastName]`
- [x] `REPORT.md` contains Parts 1 and 2