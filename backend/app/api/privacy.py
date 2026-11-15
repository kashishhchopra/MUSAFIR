"""Privacy management API (DPDP-oriented).

  Public     GET  /privacy/notice
             GET  /privacy/deletion-status/{request_id}   (no PII; the id is an unguessable secret)
  Tourist    (own record only; admins are NOT allowed through these)
             GET  /tourists/{id}/privacy/overview
             GET|POST /tourists/{id}/consents, GET .../consents/history,
             POST /tourists/{id}/consents/{purpose}/withdraw
             GET  /tourists/{id}/location-history,  POST .../location-history/delete
             POST|GET /tourists/{id}/data-export, GET .../{export_id}[/download]
             GET  /tourists/{id}/account-deletion[/preview], POST ... , .../{rid}/confirm|cancel
             POST|GET /tourists/{id}/grievances, GET|POST .../{gid}[/reply]
  Admin      /admin/privacy/...  retention policies, exceptions, jobs, deletion
             requests, grievances.
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.medical_profile import require_self
from app.core.config import settings
from app.core.ratelimit import check_key
from app.db.session import get_db
from app.models.privacy import AccountDeletionRequest
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.privacy import (
    ConsentGrant,
    DeletionConfirmBody,
    DeletionStartBody,
    ExceptionCreate,
    ExportRequestBody,
    GrievanceCreate,
    GrievanceReply,
    GrievanceRespond,
    LocationDeleteRequest,
    PolicyUpdate,
    RetentionRunBody,
)
from app.services import audit, privacy, privacy_consent, privacy_deletion, privacy_export
from app.services import privacy_grievance as grievances
from app.services import privacy_registry, privacy_retention
from app.services.privacy_registry import subject_ref

router = APIRouter(tags=["privacy"])


def _tourist(tourist_id: int, db: Session) -> Tourist:
    t = db.get(Tourist, tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tourist not found")
    return t


def _bad(exc: Exception, code: int = 400):
    return HTTPException(status_code=code, detail=str(exc))


def _reauth_limit(user: User) -> None:
    """Password re-entry endpoints are brute-force targets: cap attempts per account."""
    check_key(f"privacy-reauth:{user.id}", 8, 300)


# -------------------------------------------------------------------- public
@router.get("/privacy/notice")
def privacy_notice():
    return privacy_consent.notice()


@router.get("/privacy/deletion-status/{request_id}")
def public_deletion_status(request_id: str, db: Session = Depends(get_db)):
    req = privacy_deletion.by_request_id(db, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Deletion request not found")
    return privacy_deletion.status_out(req)


# ------------------------------------------------------------------ overview
@router.get("/tourists/{tourist_id}/privacy/overview")
def overview(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    notice = privacy_consent.notice()
    latest = privacy_deletion.latest_for_tourist(db, t.id)
    return {
        "notice": {k: notice[k] for k in ("version", "updated", "legal_note", "contact_email", "rights")},
        "consents": privacy_consent.status_overview(db, t),
        "data_inventory": privacy_registry.inventory(db),
        "retention": [privacy_retention.policy_public(p) for p in privacy_retention.list_policies(db)],
        "holds_on_my_data": [
            {"category": e.category, "reason": e.reason, "review_by": e.review_by}
            for e in privacy_retention.exceptions_for_tourist(db, t.id)],
        "location": {"tracking_enabled": t.tracking_enabled,
                     "data_retention_days": t.data_retention_days},
        "grievance_response_days": settings.PRIVACY_GRIEVANCE_RESPONSE_DAYS,
        "deletion": privacy_deletion.status_out(latest) if latest else None,
    }


# ------------------------------------------------------------------ consents
@router.get("/tourists/{tourist_id}/consents")
def get_consents(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    return privacy_consent.status_overview(db, _tourist(tourist_id, db))


@router.get("/tourists/{tourist_id}/consents/history")
def consent_history(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    return privacy_consent.history(db, _tourist(tourist_id, db))


@router.post("/tourists/{tourist_id}/consents", status_code=201)
def grant_consent(tourist_id: int, body: ConsentGrant, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    try:
        rec = privacy_consent.grant(db, t, body.purpose)
    except privacy_consent.ConsentError as exc:
        raise _bad(exc)
    audit.record(db, "consent.granted", actor=user.email, target=subject_ref(t.id),
                 detail=body.purpose, request=request)
    return privacy_consent._out(rec)


@router.post("/tourists/{tourist_id}/consents/{purpose}/withdraw")
def withdraw_consent(tourist_id: int, purpose: str, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    try:
        rec, effects = privacy_consent.withdraw(db, t, purpose)
    except privacy_consent.ConsentError as exc:
        raise _bad(exc)
    audit.record(db, "consent.withdrawn", actor=user.email, target=subject_ref(t.id),
                 detail=purpose, request=request)
    return {**privacy_consent._out(rec), "effects": effects,
            "message": (privacy_consent.purpose_map().get(purpose) or {}).get("withdraw_effect", "")}


# ----------------------------------------------------------------- locations
@router.get("/tourists/{tourist_id}/location-history")
def location_history(tourist_id: int, date_from: datetime | None = None,
                     date_to: datetime | None = None,
                     limit: int = Query(100, ge=1), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    limit = min(limit, settings.PRIVACY_LOCATION_PAGE_MAX)
    return privacy.list_location_history(db, t, date_from=date_from, date_to=date_to,
                                         limit=limit, offset=offset)


@router.post("/tourists/{tourist_id}/location-history/delete")
def delete_locations(tourist_id: int, body: LocationDeleteRequest, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    try:
        res = privacy.delete_location_history(
            db, t, ids=body.ids, date_from=body.date_from, date_to=body.date_to,
            delete_all=body.delete_all)
    except ValueError as exc:
        raise _bad(exc)
    mode = "all" if body.delete_all else ("ids" if body.ids else "range")
    privacy_retention.record_job(db, "location_history", job_type="manual",
                                 processed=res["matched"], deleted=res["deleted"],
                                 retained=res["retained"], subject_ref=subject_ref(t.id))
    audit.record(db, "location_history.deleted", actor=user.email, target=subject_ref(t.id),
                 detail=f"mode={mode} deleted={res['deleted']} retained={res['retained']}",
                 request=request)
    return res


# ------------------------------------------------------------------- exports
@router.post("/tourists/{tourist_id}/data-export", status_code=201)
def create_export(tourist_id: int, body: ExportRequestBody, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    _reauth_limit(user)
    from app.core.security import verify_password

    if not verify_password(body.password, user.hashed_password):
        audit.record(db, "data_export.requested", actor=user.email, outcome="failure",
                     target=subject_ref(t.id), detail="bad password", request=request)
        raise HTTPException(status_code=403, detail="Password is incorrect.")
    try:
        req = privacy_export.request_export(db, t, user, body.format)
    except privacy_export.ExportError as exc:
        raise _bad(exc, 429 if "limit" in str(exc) else 400)
    audit.record(db, "data_export.requested", actor=user.email, target=subject_ref(t.id),
                 detail=f"{req.export_id} {req.export_format} {req.status}", request=request)
    return privacy_export.out(req)


@router.get("/tourists/{tourist_id}/data-export")
def list_exports(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    _tourist(tourist_id, db)
    return [privacy_export.out(r) for r in privacy_export.list_for(db, tourist_id)]


@router.get("/tourists/{tourist_id}/data-export/{export_id}")
def export_status(tourist_id: int, export_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_self)):
    r = privacy_export.get_for(db, tourist_id, export_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Export not found")
    return privacy_export.out(r)


@router.get("/tourists/{tourist_id}/data-export/{export_id}/download")
def download_export(tourist_id: int, export_id: str, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_self)):
    r = privacy_export.get_for(db, tourist_id, export_id)   # owner-scoped: others' ids are 404
    if r is None:
        raise HTTPException(status_code=404, detail="Export not found")
    try:
        data, media, name = privacy_export.read_file(db, r)
    except privacy_export.ExportError as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    audit.record(db, "data_export.downloaded", actor=user.email, target=subject_ref(tourist_id),
                 detail=export_id, request=request)
    return Response(content=data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ---------------------------------------------------------- account deletion
def _run_in_background(bind, request_row_id: int) -> None:
    """Runs after the response, on its own session bound to the same engine, so
    the person is not kept waiting and the request's session is not reused."""
    with Session(bind=bind) as bg:
        privacy_deletion.process_request(bg, request_row_id)


@router.get("/tourists/{tourist_id}/account-deletion/preview")
def deletion_preview(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    return privacy_deletion.preview(db, _tourist(tourist_id, db), user)


@router.get("/tourists/{tourist_id}/account-deletion")
def deletion_status(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    req = privacy_deletion.latest_for_tourist(db, tourist_id)
    return privacy_deletion.status_out(req) if req else None


@router.post("/tourists/{tourist_id}/account-deletion", status_code=201)
def deletion_start(tourist_id: int, body: DeletionStartBody, request: Request,
                   db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    _reauth_limit(user)
    try:
        req = privacy_deletion.start_request(db, t, user, body.password)
    except privacy_deletion.DeletionError as exc:
        raise _bad(exc, 403)
    return privacy_deletion.status_out(req)


@router.post("/tourists/{tourist_id}/account-deletion/{request_id}/confirm")
def deletion_confirm(tourist_id: int, request_id: str, body: DeletionConfirmBody,
                     background: BackgroundTasks, db: Session = Depends(get_db),
                     user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    _reauth_limit(user)
    try:
        req = privacy_deletion.confirm(db, t, user, request_id, body.password, body.confirmation)
    except privacy_deletion.DeletionError as exc:
        raise _bad(exc, 403 if "assword" in str(exc) else 400)
    except LookupError as exc:
        raise _bad(exc, 404)
    if settings.PRIVACY_DELETION_GRACE_HOURS <= 0 and req.status == "requested":
        background.add_task(_run_in_background, db.get_bind(), req.id)
    return privacy_deletion.status_out(req)


@router.post("/tourists/{tourist_id}/account-deletion/{request_id}/cancel")
def deletion_cancel(tourist_id: int, request_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    try:
        return privacy_deletion.status_out(privacy_deletion.cancel(db, t, request_id))
    except privacy_deletion.DeletionError as exc:
        raise _bad(exc, 409)
    except LookupError as exc:
        raise _bad(exc, 404)


# ---------------------------------------------------------------- grievances
@router.post("/tourists/{tourist_id}/grievances", status_code=201)
def create_grievance(tourist_id: int, body: GrievanceCreate, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_self)):
    t = _tourist(tourist_id, db)
    try:
        g = grievances.submit(db, t, body.category, body.description)
    except grievances.GrievanceError as exc:
        raise _bad(exc)
    audit.record(db, "grievance.submitted", actor=user.email, target=g.grievance_id, request=request)
    return grievances.out(db, g)


@router.get("/tourists/{tourist_id}/grievances")
def my_grievances(tourist_id: int, db: Session = Depends(get_db), user: User = Depends(require_self)):
    return [grievances.out(db, g, with_responses=False) for g in grievances.list_for(db, tourist_id)]


@router.get("/tourists/{tourist_id}/grievances/{grievance_id}")
def my_grievance(tourist_id: int, grievance_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_self)):
    g = grievances.get_for(db, tourist_id, grievance_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return grievances.out(db, g)


@router.post("/tourists/{tourist_id}/grievances/{grievance_id}/reply")
def reply_grievance(tourist_id: int, grievance_id: str, body: GrievanceReply,
                    db: Session = Depends(get_db), user: User = Depends(require_self)):
    g = grievances.get_for(db, tourist_id, grievance_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Grievance not found")
    try:
        grievances.tourist_reply(db, g, body.message)
    except grievances.GrievanceError as exc:
        raise _bad(exc, 409)
    return grievances.out(db, g)


# --------------------------------------------------------------------- admin
@router.get("/admin/privacy/inventory")
def admin_inventory(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return privacy_registry.inventory(db)


@router.get("/admin/privacy/retention/policies")
def admin_policies(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [{**privacy_retention.policy_public(p), "updated_by": p.updated_by}
            for p in privacy_retention.list_policies(db)]


@router.patch("/admin/privacy/retention/policies/{category}")
def admin_update_policy(category: str, body: PolicyUpdate, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        p = privacy_retention.update_policy(db, category, body.model_dump(exclude_unset=True), admin.email)
    except LookupError as exc:
        raise _bad(exc, 404)
    except ValueError as exc:
        raise _bad(exc)
    audit.record(db, "retention_policy.updated", actor=admin.email, target=category,
                 detail=",".join(sorted(body.model_dump(exclude_unset=True))), request=request)
    return privacy_retention.policy_public(p)


@router.get("/admin/privacy/retention/exceptions")
def admin_exceptions(active_only: bool = False, db: Session = Depends(get_db),
                     _: User = Depends(require_admin)):
    return [privacy_retention.exception_out(e) for e in privacy_retention.list_exceptions(db, active_only)]


@router.post("/admin/privacy/retention/exceptions", status_code=201)
def admin_add_exception(body: ExceptionCreate, request: Request, db: Session = Depends(get_db),
                        admin: User = Depends(require_admin)):
    try:
        e = privacy_retention.add_exception(
            db, category=body.category, reason=body.reason, basis=body.basis,
            review_days=body.review_days, actor=admin.email, tourist_id=body.tourist_id)
    except LookupError as exc:
        raise _bad(exc, 404)
    except ValueError as exc:
        raise _bad(exc)
    audit.record(db, "retention_exception.created", actor=admin.email, target=str(e.id),
                 detail=e.category, request=request)
    return privacy_retention.exception_out(e)


@router.post("/admin/privacy/retention/exceptions/{exception_id}/release")
def admin_release_exception(exception_id: int, request: Request, db: Session = Depends(get_db),
                            admin: User = Depends(require_admin)):
    try:
        e = privacy_retention.release_exception(db, exception_id, admin.email)
    except LookupError as exc:
        raise _bad(exc, 404)
    audit.record(db, "retention_exception.released", actor=admin.email, target=str(e.id), request=request)
    return privacy_retention.exception_out(e)


@router.get("/admin/privacy/retention/jobs")
def admin_jobs(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
               _: User = Depends(require_admin)):
    return privacy_retention.summary(db, limit)


@router.post("/admin/privacy/retention/run")
def admin_run(body: RetentionRunBody, request: Request, db: Session = Depends(get_db),
              admin: User = Depends(require_admin)):
    results = privacy_retention.run(db, job_type="manual", category=body.category)
    audit.record(db, "retention.run", actor=admin.email, target=body.category or "all",
                 detail=f"{len(results)} job(s)", request=request)
    return {"jobs": results}


@router.get("/admin/privacy/deletion-requests")
def admin_deletion_requests(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.query(AccountDeletionRequest).order_by(AccountDeletionRequest.id.desc()).limit(200).all()
    return [privacy_deletion.status_out(r) for r in rows]


@router.post("/admin/privacy/deletion-requests/process")
def admin_process_deletions(request: Request, db: Session = Depends(get_db),
                            admin: User = Depends(require_admin)):
    done = privacy_deletion.process_due(db)
    audit.record(db, "account_deletion.processed", actor=admin.email, detail=f"{len(done)}", request=request)
    return {"processed": done}


@router.get("/admin/privacy/grievances")
def admin_grievances(status: str | None = None, db: Session = Depends(get_db),
                     _: User = Depends(require_admin)):
    return [grievances.out(db, g, with_responses=False) | {"subject_ref": g.subject_ref}
            for g in grievances.list_all(db, status)]


@router.get("/admin/privacy/grievances/{grievance_id}")
def admin_grievance(grievance_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    g = grievances.get_any(db, grievance_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return grievances.out(db, g) | {"subject_ref": g.subject_ref}


@router.post("/admin/privacy/grievances/{grievance_id}/respond")
def admin_respond(grievance_id: str, body: GrievanceRespond, request: Request,
                  db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    g = grievances.get_any(db, grievance_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Grievance not found")
    try:
        grievances.respond(db, g, body.message, body.status)
    except grievances.GrievanceError as exc:
        raise _bad(exc)
    audit.record(db, "grievance.responded", actor=admin.email, target=g.grievance_id,
                 detail=g.status, request=request)
    return grievances.out(db, g)
