

import asyncio
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="StudySync API",
    description="FastAPI backend with Circuit Breaker fault-tolerance pattern",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STUDENT_ID = os.getenv("STUDENT_ID", "BSCS23190")   
LLM_API_URL = os.getenv(
    "LLM_API_URL", "https://api.openai.com/v1/chat/completions"
)
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "5"))

llm_circuit = CircuitBreaker(
    failure_threshold=3,       
    recovery_timeout=20.0,    
    expected_exception=Exception,
    name="LLM-API",
)

@app.middleware("http")
async def add_student_id_header(request: Request, call_next):
    """Assignment requirement: every response must include X-Student-ID."""
    response = await call_next(request)
    response.headers["X-Student-ID"] = STUDENT_ID
    return response


class AskRequest(BaseModel):
    question: str
    simulate_failure: bool = False   


class AskResponse(BaseModel):
    answer: str
    source: str          
    circuit_state: str


class CircuitStatusResponse(BaseModel):
    state: str
    failure_count: int
    failure_threshold: int
    recovery_timeout: float
    seconds_until_retry: Optional[float]


async def _call_llm(question: str, force_fail: bool) -> str:
    """
    Async wrapper around the LLM HTTP call.
    If  force_fail=True  we simulate a hanging / erroring LLM (for demos).
    """
    if force_fail:
        logger.warning("Simulating LLM failure (force_fail=True)")
        raise httpx.TimeoutException("Simulated LLM timeout after 60 s")

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:

        resp = await client.post(
            "https://httpbin.org/post",
            json={"question": question},
        )
        resp.raise_for_status()
        return f"[Mock LLM] Echo: {question}"


FALLBACK_ANSWER = (
    "Our AI assistant is temporarily unavailable. "
    "Please try again in a few moments, or contact support@studysync.io."
)


@app.get("/", summary="Health check")
async def root():
    return {"status": "ok", "service": "StudySync API"}


@app.post("/api/ask", response_model=AskResponse, summary="Ask the LLM a question")
async def ask_llm(body: AskRequest):

    source = "llm"

    try:
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(
            None,
            lambda: llm_circuit.call(
                lambda: asyncio.run(_call_llm(body.question, body.simulate_failure))
            ),
        )

    except CircuitBreakerOpenError as exc:
        logger.warning("Circuit OPEN – serving fallback. Detail: %s", exc)
        answer = FALLBACK_ANSWER
        source = "fallback"

    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"LLM service error: {exc}. Failures recorded by circuit breaker.",
        )

    return AskResponse(
        answer=answer,
        source=source,
        circuit_state=llm_circuit.state.value,
    )


@app.get(
    "/api/circuit-status",
    response_model=CircuitStatusResponse,
    summary="Inspect the LLM circuit breaker",
)
async def circuit_status():
    """Returns real-time state of the LLM circuit breaker."""
    cb = llm_circuit
    state = cb.state  
    return CircuitStatusResponse(
        state=state.value,
        failure_count=cb._failure_count,
        failure_threshold=cb.failure_threshold,
        recovery_timeout=cb.recovery_timeout,
        seconds_until_retry=cb._seconds_until_retry() if state == CircuitState.OPEN else None,
    )


@app.post("/api/reset-circuit", summary="Reset the circuit breaker (test helper)")
async def reset_circuit():
    """Force the circuit breaker back to CLOSED state (useful for demos / tests)."""
    llm_circuit._state = CircuitState.CLOSED
    llm_circuit._failure_count = 0
    llm_circuit._last_failure_time = None
    logger.info("Circuit breaker manually reset to CLOSED.")
    return {"message": "Circuit breaker reset to CLOSED.", "state": "CLOSED"}