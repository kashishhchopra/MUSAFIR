"""AI/ML Evaluation Dashboard API (admin-only). See services/evaluation.py."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import audit, evaluation

router = APIRouter(prefix="/ml/evaluation", tags=["ml-evaluation"])


class RunRequest(BaseModel):
    n_scenarios: int = Field(100, ge=20, le=1000)
    seed: int | None = Field(None, ge=0, le=10**9)
    suspicious_fraction: float = Field(0.4, ge=0.1, le=0.9)


@router.get("/latest")
def latest(_: User = Depends(require_admin)):
    """The most recent evaluation report (or null) plus a history of earlier runs."""
    runs = evaluation.load_runs()
    return {"latest": runs[-1] if runs else None, "history": [evaluation.summarise(r) for r in runs]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, _: User = Depends(require_admin)):
    for r in evaluation.load_runs():
        if r["run_id"] == run_id:
            return r
    raise HTTPException(status_code=404, detail="Evaluation run not found (history keeps the last 12)")


@router.post("/run")
async def run(payload: RunRequest, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Generate labelled scenarios, run the production detectors, store + return the report.
    Read-only on tourist/alert/incident data."""
    report = await asyncio.to_thread(
        evaluation.run_evaluation, db, payload.n_scenarios, payload.seed, payload.suspicious_fraction)
    audit.record(db, "ml_evaluation_run", actor=user.email,
                 target=f"{report['totals']['n_scenarios']} scenarios (seed {report['config']['seed']})")
    db.commit()
    return report
