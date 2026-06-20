"""Knowledge base CRUD routes — admin-only write access"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone

from app.db.session import get_db
from app.models.knowledge import DependencyEdge, ScheduledRebootWindow, ServicePauseConfig
from app.core.security import get_current_user, require_admin
from app.services.gemini_service import verify_dependency_graph

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Dependency Graph ──────────────────────────────────────────────────────────

class DependencyEdgeCreate(BaseModel):
    dependent_server: str
    dependency_server: str
    reason: Optional[str] = None


class DependencyEdgeOut(BaseModel):
    id: int
    dependent_server: str
    dependency_server: str
    reason: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/dependencies")
async def list_dependencies(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(DependencyEdge).where(DependencyEdge.is_active == True).order_by(DependencyEdge.id)
    )
    edges = result.scalars().all()
    return [
        {
            "id": e.id,
            "dependent_server": e.dependent_server,
            "dependency_server": e.dependency_server,
            "reason": e.reason,
            "is_active": e.is_active,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in edges
    ]


@router.post("/dependencies", dependencies=[Depends(require_admin)])
async def create_dependency(
    body: DependencyEdgeCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    # Check for self-dependency
    if body.dependent_server == body.dependency_server:
        raise HTTPException(status_code=400, detail="A server cannot depend on itself")

    # Load all existing edges + new one to validate graph
    result = await db.execute(select(DependencyEdge).where(DependencyEdge.is_active == True))
    existing = result.scalars().all()
    edges_for_check = [(e.dependent_server, e.dependency_server) for e in existing]
    edges_for_check.append((body.dependent_server, body.dependency_server))

    # AI verification
    verification = await verify_dependency_graph(edges_for_check)
    if not verification["valid"]:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Graph validation failed",
                "issues": verification["issues"],
                "ai_reasoning": verification["reasoning"],
            },
        )

    edge = DependencyEdge(
        dependent_server=body.dependent_server,
        dependency_server=body.dependency_server,
        reason=body.reason,
        created_by=current_user.id,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return {"id": edge.id, "message": "Dependency created", "ai_verification": verification}


@router.delete("/dependencies/{edge_id}", dependencies=[Depends(require_admin)])
async def delete_dependency(edge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DependencyEdge).where(DependencyEdge.id == edge_id))
    edge = result.scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    edge.is_active = False
    await db.commit()
    return {"status": "deleted"}


@router.post("/dependencies/verify", dependencies=[Depends(require_admin)])
async def verify_graph(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """AI-powered graph verification — checks cycles, orphans, logical issues"""
    edges = body.get("edges", [])
    edge_tuples = [(e["dependent_server"], e["dependency_server"]) for e in edges]
    result = await verify_dependency_graph(edge_tuples)
    return result


# ── Scheduled Reboot Windows ──────────────────────────────────────────────────

class RebootWindowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    timezone: str
    preferred_start_time: str   # HH:MM
    preferred_end_time: str     # HH:MM
    allowed_days: str = "0,1,2,3,4"
    reason: Optional[str] = None


@router.get("/reboot-windows")
async def list_reboot_windows(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(select(ScheduledRebootWindow).order_by(ScheduledRebootWindow.timezone))
    windows = result.scalars().all()
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "timezone": w.timezone,
            "preferred_start_time": w.preferred_start_time,
            "preferred_end_time": w.preferred_end_time,
            "allowed_days": w.allowed_days,
            "reason": w.reason,
            "is_active": w.is_active,
        }
        for w in windows
    ]


@router.post("/reboot-windows", dependencies=[Depends(require_admin)])
async def create_reboot_window(
    body: RebootWindowCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    w = ScheduledRebootWindow(**body.model_dump(), created_by=current_user.id)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return {"id": w.id, "message": "Reboot window created"}


@router.put("/reboot-windows/{wid}", dependencies=[Depends(require_admin)])
async def update_reboot_window(
    wid: int,
    body: RebootWindowCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ScheduledRebootWindow).where(ScheduledRebootWindow.id == wid))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in body.model_dump().items():
        setattr(w, k, v)
    await db.commit()
    return {"status": "updated"}


@router.delete("/reboot-windows/{wid}", dependencies=[Depends(require_admin)])
async def delete_reboot_window(wid: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ScheduledRebootWindow).where(ScheduledRebootWindow.id == wid))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="Not found")
    w.is_active = False
    await db.commit()
    return {"status": "deleted"}


# ── Service Pause Configs ──────────────────────────────────────────────────────

class ServicePauseCreate(BaseModel):
    server_hostname: str
    service_name: str
    pause_script: str = "Pause-Service.ps1"
    resume_script: str = "Resume-Service.ps1"
    reason: Optional[str] = None
    pre_pause_wait_seconds: int = 5
    post_resume_wait_seconds: int = 10


@router.get("/service-pauses")
async def list_service_pauses(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(ServicePauseConfig).where(ServicePauseConfig.is_active == True).order_by(ServicePauseConfig.server_hostname)
    )
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "server_hostname": c.server_hostname,
            "service_name": c.service_name,
            "pause_script": c.pause_script,
            "resume_script": c.resume_script,
            "reason": c.reason,
            "pre_pause_wait_seconds": c.pre_pause_wait_seconds,
            "post_resume_wait_seconds": c.post_resume_wait_seconds,
            "is_active": c.is_active,
        }
        for c in configs
    ]


@router.post("/service-pauses", dependencies=[Depends(require_admin)])
async def create_service_pause(
    body: ServicePauseCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    c = ServicePauseConfig(**body.model_dump(), created_by=current_user.id)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "message": "Service pause config created"}


@router.put("/service-pauses/{cid}", dependencies=[Depends(require_admin)])
async def update_service_pause(
    cid: int,
    body: ServicePauseCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ServicePauseConfig).where(ServicePauseConfig.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    await db.commit()
    return {"status": "updated"}


@router.delete("/service-pauses/{cid}", dependencies=[Depends(require_admin)])
async def delete_service_pause(cid: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ServicePauseConfig).where(ServicePauseConfig.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")
    c.is_active = False
    await db.commit()
    return {"status": "deleted"}


class ServerKBDocumentCreate(BaseModel):
    server_hostname: str
    document_content: str


class ServerKBDocumentUpdate(BaseModel):
    document_content: str


@router.get("/server-kb")
async def list_server_kb(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.knowledge import ServerKBDocument
    result = await db.execute(
        select(ServerKBDocument)
        .where(ServerKBDocument.is_active == True)
        .order_by(ServerKBDocument.server_hostname)
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "server_hostname": d.server_hostname,
            "document_content": d.document_content,
            "last_pre_reboot_script": d.last_pre_reboot_script,
            "last_post_reboot_script": d.last_post_reboot_script,
            "last_script_generated_at": d.last_script_generated_at.isoformat() if d.last_script_generated_at else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        }
        for d in docs
    ]


@router.get("/server-kb/{hostname}")
async def get_server_kb(
    hostname: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.models.knowledge import ServerKBDocument
    result = await db.execute(
        select(ServerKBDocument).where(
            ServerKBDocument.server_hostname == hostname,
            ServerKBDocument.is_active == True,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="No KB document for this server")
    return {
        "id": doc.id,
        "server_hostname": doc.server_hostname,
        "document_content": doc.document_content,
        "last_pre_reboot_script": doc.last_pre_reboot_script,
        "last_post_reboot_script": doc.last_post_reboot_script,
        "last_script_generated_at": doc.last_script_generated_at.isoformat() if doc.last_script_generated_at else None,
    }


@router.post("/server-kb", dependencies=[Depends(require_admin)])
async def create_server_kb(
    body: ServerKBDocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_admin),
):
    from app.models.knowledge import ServerKBDocument
    # Check for existing
    result = await db.execute(
        select(ServerKBDocument).where(ServerKBDocument.server_hostname == body.server_hostname)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.document_content = body.document_content
        existing.is_active = True
        existing.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return {"id": existing.id, "message": "KB document updated"}

    doc = ServerKBDocument(
        server_hostname=body.server_hostname,
        document_content=body.document_content,
        created_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {"id": doc.id, "message": "KB document created"}


@router.put("/server-kb/{doc_id}", dependencies=[Depends(require_admin)])
async def update_server_kb(
    doc_id: int,
    body: ServerKBDocumentUpdate,
    db: AsyncSession = Depends(get_db),
):
    from app.models.knowledge import ServerKBDocument
    result = await db.execute(select(ServerKBDocument).where(ServerKBDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    doc.document_content = body.document_content
    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated"}


@router.delete("/server-kb/{doc_id}", dependencies=[Depends(require_admin)])
async def delete_server_kb(doc_id: int, db: AsyncSession = Depends(get_db)):
    from app.models.knowledge import ServerKBDocument
    result = await db.execute(select(ServerKBDocument).where(ServerKBDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    doc.is_active = False
    await db.commit()
    return {"status": "deleted"}


@router.post("/server-kb/{doc_id}/preview-scripts", dependencies=[Depends(require_admin)])
async def preview_generated_scripts(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Preview the AI-generated pre/post reboot scripts without executing them"""
    from app.models.knowledge import ServerKBDocument
    from app.services.gemini_service import generate_reboot_scripts
    result = await db.execute(select(ServerKBDocument).where(ServerKBDocument.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    scripts = await generate_reboot_scripts(
        server_hostname=doc.server_hostname,
        kb_document=doc.document_content,
    )
    # Cache the generated scripts
    doc.last_pre_reboot_script = scripts["pre_reboot_script"]
    doc.last_post_reboot_script = scripts["post_reboot_script"]
    doc.last_script_generated_at = datetime.now(timezone.utc)
    await db.commit()
    return scripts