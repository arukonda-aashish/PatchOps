"""
PatchOps Agent Tasks — Celery orchestration for all 4 agents.

Agent 1 (Baseline): Fetch attachment, resolve dependencies, plan execution order
Agent 2 (Execution): Run PowerShell reboots in dependency-ordered buckets
Agent 3 (Validation): Collect post-state, compare with pre-state, flag deviations
Agent 4 (RCA): Deep analysis of failed servers, create ServiceNow incidents
"""
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Optional
import openpyxl
import hashlib
from io import BytesIO
import networkx as nx
from sqlalchemy import select

from app.worker.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from sqlalchemy.pool import NullPool
from app.models.change_request import ChangeRequest, CRStatus, ServerTask, TaskStatus
from app.models.agent_run import AgentRun, AgentLog, AgentType, AgentRunStatus
from app.models.knowledge import DependencyEdge, ScheduledRebootWindow, ServicePauseConfig
from app.models.server import Server
from app.models.incident import Incident, IncidentStatus
from app.core.config import settings
from app.services import gemini_service, winrm_service, email_service, servicenow_service

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run async coroutine in Celery sync context.
    Creates a fresh event loop per task — asyncpg connections must not be
    reused across loops, so AsyncSessionLocal is scoped inside each coro.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    except Exception as e:
        raise
    finally:
        try:
            # Cancel all pending tasks before closing
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        finally:
            loop.close()
            asyncio.set_event_loop(None)


async def _log(db, cr_id: int, run_id: Optional[int], agent_type: str,
               level: str, message: str, server: Optional[str] = None, meta: dict = None):
    """Write a log entry to agent_logs table"""
    log = AgentLog(
        cr_id=cr_id,
        run_id=run_id,
        agent_type=agent_type,
        level=level,
        message=message,
        server_hostname=server,
        metadata_=meta,
    )
    db.add(log)
    await db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# Change Window Monitor
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="monitor_change_window")
def monitor_change_window(cr_id: int):
    """Wait until change window opens, then trigger baseline agent"""
    run_async(_monitor_change_window(cr_id))


async def _monitor_change_window(cr_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = result.scalar_one_or_none()
        if not cr:
            return

        now = datetime.now(timezone.utc)

        if cr.change_window_start and cr.change_window_start > now:
            # Schedule delayed execution
            delay = (cr.change_window_start - now).total_seconds()
            celery_app.send_task("run_baseline_agent", args=[cr_id], countdown=max(0, delay - 10))
            logger.info(f"CR {cr.cr_number} baseline agent scheduled in {delay:.0f}s")
        else:
            # Window is already open (or no window set)
            celery_app.send_task("run_baseline_agent", args=[cr_id])


@celery_app.task(name="poll_change_windows")
def poll_change_windows():
    """Beat task: check for CRs whose change window has opened"""
    run_async(_poll_change_windows())


async def _poll_change_windows():
    from sqlalchemy import and_
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest).where(
                and_(
                    ChangeRequest.status == CRStatus.pending,
                    ChangeRequest.change_window_start <= now,
                    ChangeRequest.change_window_end >= now,
                )
            )
        )
        crs = result.scalars().all()
        for cr in crs:
            logger.info(f"Change window opened for {cr.cr_number}, triggering baseline agent")
            celery_app.send_task("run_baseline_agent", args=[cr.id])


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1: Baseline — dependency resolution + execution plan
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="run_baseline_agent")
def run_baseline_agent(cr_id: int):
    run_async(_run_baseline_agent(cr_id))


async def _run_baseline_agent(cr_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = result.scalar_one_or_none()
        if not cr:
            return

        run = AgentRun(cr_id=cr_id, agent_type=AgentType.baseline, status=AgentRunStatus.running)
        db.add(run)
        await db.flush()
        run_id = run.id

        cr.status = CRStatus.in_progress
        cr.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       f"🤖 Agent 1 (Baseline) started for {cr.cr_number}")
            await db.commit()

            # ── Step 1: Fetch server list from attachment ──────────────────
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       "📎 Fetching server list from ServiceNow attachment...")
            await db.commit()

            server_list, server_ip_map = await _fetch_server_list(cr)

            await _log(db, cr_id, run_id, "baseline", "INFO",
                       f"✅ Found {len(server_list)} servers: {', '.join(server_list)}")
            await db.commit()

            # ── Step 2: Load dependency graph ─────────────────────────────
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       "🔗 Loading dependency graph from knowledge base...")
            await db.commit()

            dep_result = await db.execute(
                select(DependencyEdge).where(DependencyEdge.is_active == True)
            )
            all_edges = dep_result.scalars().all()
            edges = [(e.dependent_server, e.dependency_server, e.reason)
                     for e in all_edges
                     if e.dependent_server in server_list or e.dependency_server in server_list]

            # ── Step 3: Topological sort ───────────────────────────────────
            G = nx.DiGraph()
            for srv in server_list:
                G.add_node(srv)
            for dep, prereq, _ in edges:
                if dep in server_list and prereq in server_list:
                    G.add_edge(prereq, dep)  # prereq must come before dep

            try:
                ordered = list(nx.topological_sort(G))
            except nx.NetworkXUnfeasible:
                await _log(db, cr_id, run_id, "baseline", "ERROR",
                           "❌ Circular dependency detected — cannot sort. Proceeding with original order.")
                await db.commit()
                ordered = server_list

            dep_notes = []
            for dep, prereq, reason in edges:
                if dep in server_list and prereq in server_list:
                    dep_notes.append(f"{dep} depends on {prereq}" + (f" ({reason})" if reason else ""))

            # ── Step 4: Check service pause requirements ───────────────────
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       "⏸️ Checking service pause requirements...")
            await db.commit()

            pause_result = await db.execute(
                select(ServicePauseConfig).where(
                    ServicePauseConfig.server_hostname.in_(server_list),
                    ServicePauseConfig.is_active == True
                )
            )
            pause_configs = {p.server_hostname: p for p in pause_result.scalars().all()}

            # ── Step 5: Check scheduled reboot windows ─────────────────────
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       "🕒 Checking timezone-specific reboot window constraints...")
            await db.commit()

            reboot_windows_result = await db.execute(
                select(ScheduledRebootWindow).where(ScheduledRebootWindow.is_active == True)
            )
            reboot_windows = reboot_windows_result.scalars().all()

            # Check server timezones (we detect at runtime from mock/winrm)
            server_tz_map = {}
            for srv in server_list:
                state = await winrm_service.get_server_state(
                    srv, ip_address=server_ip_map.get(srv)
                )
                tz = state.get("Timezone", "UTC")
                server_tz_map[srv] = tz

                # Check if any reboot window applies to this server's timezone
                for window in reboot_windows:
                    if window.timezone == tz:
                        await _log(db, cr_id, run_id, "baseline", "INFO",
                                   f"🕒 {srv} (TZ: {tz}) has scheduled window {window.preferred_start_time}–{window.preferred_end_time}",
                                   server=srv)
                        await db.commit()

            # ── Step 6: Build execution buckets ───────────────────────────
            buckets = _build_buckets(ordered, G)

            await _log(db, cr_id, run_id, "baseline", "INFO",
                       f"📦 Created {len(buckets)} execution buckets: " +
                       " | ".join([f"B{i+1}:[{','.join(b)}]" for i, b in enumerate(buckets)]))
            await db.commit()

            # ── Step 7: Generate summary via Gemini ───────────────────────
            await _log(db, cr_id, run_id, "baseline", "INFO",
                       "✨ Generating execution plan summary...")
            await db.commit()

            summary = await gemini_service.generate_server_order_summary(
                ordered_list=ordered,
                buckets=buckets,
                dependency_notes="\n".join(dep_notes) if dep_notes else "No dependencies between the target servers.",
            )

            # ── Step 8: Persist plan ───────────────────────────────────────
            cr.ordered_server_list = {
                "servers": ordered,
                "buckets": buckets,
                "dependency_notes": dep_notes,
                "pause_servers": list(pause_configs.keys()),
                "server_timezones": server_tz_map,
                "server_ip_map": server_ip_map,
                "reasoning": [
                    f"Topological sort resolved {len(edges)} dependency edges",
                    f"Servers in same bucket execute in parallel (max {settings.MAX_PARALLEL_REBOOTS})",
                    f"{len(pause_configs)} servers require service pause/resume",
                ],
            }
            cr.agent1_summary = summary
            cr.total_servers = len(server_list)
            cr.status = CRStatus.in_progress

            # Create ServerTask rows
            for bucket_idx, bucket in enumerate(buckets):
                for exec_idx, hostname in enumerate(bucket):
                    pause_cfg = pause_configs.get(hostname)
                    task = ServerTask(
                        cr_id=cr_id,
                        server_hostname=hostname,
                        bucket_number=bucket_idx,
                        execution_order=exec_idx,
                        requires_service_pause=bool(pause_cfg),
                        service_name=pause_cfg.service_name if pause_cfg else None,
                    )
                    db.add(task)

            run.status = AgentRunStatus.waiting_approval
            run.completed_at = datetime.now(timezone.utc)
            run.result = {"servers": len(server_list), "buckets": len(buckets)}
            await db.commit()

            await _log(db, cr_id, run_id, "baseline", "SUCCESS",
                       f"✅ Baseline complete — {len(server_list)} servers in {len(buckets)} buckets. Awaiting user approval.")
            await db.commit()

        except Exception as e:
            logger.error(f"Baseline agent error for CR {cr_id}: {e}", exc_info=True)
            await _log(db, cr_id, run_id, "baseline", "ERROR", f"❌ Agent error: {str(e)}")
            run.status = AgentRunStatus.failed
            run.error = str(e)
            cr.status = CRStatus.failed
            await db.commit()

            
async def _get_kb_pre_reboot_script(hostname: str, server_state: dict = None) -> Optional[str]:
    """Fetch KB document for server and generate pre-reboot PS1 via Gemini.
    Returns None if no KB document exists for this server.
    """
    import requests as _requests
    from app.core.config import settings as _settings
    try:
        async with AsyncSessionLocal() as db:
            from app.models.knowledge import ServerKBDocument
            result = await db.execute(
                select(ServerKBDocument).where(
                    ServerKBDocument.server_hostname == hostname,
                    ServerKBDocument.is_active == True,
                )
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None

            scripts = await gemini_service.generate_reboot_scripts(
                server_hostname=hostname,
                kb_document=doc.document_content,
                server_state=server_state,
            )
            # Cache scripts back to DB
            doc.last_pre_reboot_script = scripts["pre_reboot_script"]
            doc.last_post_reboot_script = scripts["post_reboot_script"]
            doc.last_script_generated_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(f"KB pre-reboot script generated for {hostname}: {scripts.get('apps_identified', [])}")
            return scripts["pre_reboot_script"]
    except Exception as e:
        logger.error(f"Failed to get KB pre-reboot script for {hostname}: {e}")
        return None


async def _get_kb_post_reboot_script(hostname: str) -> Optional[str]:
    """Fetch cached post-reboot script for server (generated during pre-reboot phase).
    Returns None if no KB document exists.
    """
    try:
        async with AsyncSessionLocal() as db:
            from app.models.knowledge import ServerKBDocument
            result = await db.execute(
                select(ServerKBDocument).where(
                    ServerKBDocument.server_hostname == hostname,
                    ServerKBDocument.is_active == True,
                )
            )
            doc = result.scalar_one_or_none()
            if not doc or not doc.last_post_reboot_script:
                return None
            return doc.last_post_reboot_script
    except Exception as e:
        logger.error(f"Failed to get KB post-reboot script for {hostname}: {e}")
        return None




def _build_buckets(ordered: list, G: nx.DiGraph) -> list[list[str]]:
    """
    Group servers into parallel execution buckets based on dependency levels.
    Servers with no pending predecessors in the same CR can run in parallel.
    """
    if not ordered:
        return []

    # Assign topological levels
    levels = {}
    for node in ordered:
        preds = list(G.predecessors(node))
        if not preds:
            levels[node] = 0
        else:
            levels[node] = max(levels.get(p, 0) for p in preds) + 1

    # Group by level, respecting MAX_PARALLEL_REBOOTS
    from collections import defaultdict
    level_groups = defaultdict(list)
    for node, level in levels.items():
        level_groups[level].append(node)

    buckets = []
    for level in sorted(level_groups.keys()):
        group = level_groups[level]
        # Split large groups into sub-buckets of MAX_PARALLEL_REBOOTS
        for i in range(0, len(group), settings.MAX_PARALLEL_REBOOTS):
            buckets.append(group[i: i + settings.MAX_PARALLEL_REBOOTS])

    return buckets


async def _fetch_server_list(cr: ChangeRequest) -> tuple[list[str], dict[str, str]]:
    """
    Fetch server list from ServiceNow attachment.
    Returns (hostnames, hostname→ip map).
    Falls back to mock list if SN is unavailable or in mock mode.
    """
    # Try real ServiceNow attachment
    if cr.sn_sys_id and settings.SERVICENOW_INSTANCE:
        logger.info(f"Fetching attachments for sys_id: {cr.sn_sys_id}")
        # Use requests (sync) to avoid event loop conflicts in Celery worker context
        import requests
        try:
            auth = (settings.SERVICENOW_USER, settings.SERVICENOW_PASSWORD)
            headers = {"Accept": "application/json"}
            base = settings.SERVICENOW_INSTANCE.rstrip("/")

            resp = requests.get(
                f"{base}/api/now/attachment",
                params={"sysparm_query": f"table_sys_id={cr.sn_sys_id}"},
                auth=auth,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            attachments = resp.json().get("result", [])
            logger.info(f"Found {len(attachments)} attachments: {[a.get('file_name') for a in attachments]}")

            for att in attachments:
                fname = att.get("file_name", "")
                dl_url = att.get("download_link", "")
                if not dl_url:
                    continue
                dl_resp = requests.get(dl_url, auth=auth, timeout=60)
                dl_resp.raise_for_status()
                content = dl_resp.content
                if not content:
                    continue
                logger.info(f"Downloaded attachment: {fname} ({len(content)} bytes)")
                if fname.endswith(".xlsx"):
                    return _parse_server_list_xlsx(content)
                elif fname.endswith((".txt", ".csv", ".json")):
                    return _parse_server_list(content.decode("utf-8")), {}
        except Exception as e:
            logger.error(f"Failed to fetch SN attachment: {e}")
    # Mock server list for demo
    base_servers = [
        "srv-db-01", "srv-db-02", "srv-app-01", "srv-app-02", "srv-app-03",
        "srv-web-01", "srv-web-02", "srv-cache-01", "srv-mq-01",
    ]
    seed = int(hashlib.md5(cr.cr_number.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    count = rng.randint(4, len(base_servers))
    return rng.sample(base_servers, count), {}

def _parse_server_list_xlsx(content: bytes) -> tuple[list[str], dict[str, str]]:
    """Parse server list from Excel — reads hostname (col1) and optional ip_address (col2).
    Returns (hostnames, hostname→ip map).
    """
    try:
        wb = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        servers = []
        ip_map = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            val = row[0] if row else None
            if not val or not isinstance(val, str):
                continue
            srv = val.strip().lower()
            if not srv or srv.startswith("#"):
                continue
            servers.append(srv)
            # Second column is ip_address (optional)
            if len(row) > 1 and row[1]:
                ip_map[srv] = str(row[1]).strip()
        return list(dict.fromkeys(servers)), ip_map
    except Exception as e:
        logger.error(f"Failed to parse xlsx attachment: {e}")
        return [], {}
def _parse_server_list(content: str) -> list[str]:
    """Parse newline/comma-separated server list from attachment"""
    servers = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for srv in line.split(","):
            srv = srv.strip().lower()
            if srv:
                servers.append(srv)
    return list(dict.fromkeys(servers))  # deduplicate preserving order


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2: Execution — run PowerShell reboots bucket by bucket
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="run_execution_agent")
def run_execution_agent(cr_id: int):
    run_async(_run_execution_agent(cr_id))


async def _run_execution_agent(cr_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChangeRequest).where(ChangeRequest.id == cr_id)
        )
        cr = result.scalar_one_or_none()
        if not cr or not cr.ordered_server_list:
            return

        run = AgentRun(cr_id=cr_id, agent_type=AgentType.execution, status=AgentRunStatus.running)
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        completed_tasks = []
        failed_tasks = []

        try:
            await _log(db, cr_id, run_id, "execution", "INFO",
                       "🚀 Agent 2 (Execution) started — beginning server reboots")
            await db.commit()

            buckets = cr.ordered_server_list.get("buckets", [])
            pause_servers = set(cr.ordered_server_list.get("pause_servers", []))

            for bucket_idx, bucket in enumerate(buckets):
                await _log(db, cr_id, run_id, "execution", "INFO",
                           f" Processing Bucket {bucket_idx + 1}/{len(buckets)}: {', '.join(bucket)}")
                await db.commit()

                # Run all servers in bucket in parallel
                bucket_tasks = [
                    _reboot_server(db, cr, run_id, hostname, bucket_idx, hostname in pause_servers)
                    for hostname in bucket
                ]
                results = await asyncio.gather(*bucket_tasks, return_exceptions=True)

                for hostname, result in zip(bucket, results):
                    if isinstance(result, Exception) or not result.get("success"):
                        failed_tasks.append({"hostname": hostname, "error": str(result)})
                        await _log(db, cr_id, run_id, "execution", "ERROR",
                                   f" {hostname} failed", server=hostname)
                    else:
                        completed_tasks.append({"hostname": hostname})
                        await _log(db, cr_id, run_id, "execution", "SUCCESS",
                                   f" {hostname} rebooted successfully", server=hostname)

                # Update progress
                total = cr.total_servers or len([s for b in buckets for s in b])
                done = len(completed_tasks) + len(failed_tasks)
                cr.progress_percent = round(done / total * 100, 1) if total else 0
                cr.completed_servers = len(completed_tasks)
                cr.failed_servers = len(failed_tasks)
                await db.commit()

                await _log(db, cr_id, run_id, "execution", "INFO",
                           f" Bucket {bucket_idx + 1} complete — Progress: {cr.progress_percent:.1f}%")
                await db.commit()

            # ── Generate execution summary ─────────────────────────────────
            await _log(db, cr_id, run_id, "execution", "INFO",
                       " Generating execution summary...")
            await db.commit()

            summary = await gemini_service.generate_execution_summary(completed_tasks, failed_tasks)
            cr.execution_summary = summary

            run.status = AgentRunStatus.waiting_approval if not failed_tasks else AgentRunStatus.completed
            run.completed_at = datetime.now(timezone.utc)
            run.result = {
                "completed": len(completed_tasks),
                "failed": len(failed_tasks),
            }

            await db.commit()

            await _log(db, cr_id, run_id, "execution", "SUCCESS",
                       f" Execution complete — {len(completed_tasks)} succeeded, {len(failed_tasks)} failed. Review summary to proceed.")
            await db.commit()

            # Auto-trigger validation after execution
            celery_app.send_task("run_validation_agent", args=[cr_id])

        except Exception as e:
            logger.error(f"Execution agent error: {e}", exc_info=True)
            await _log(db, cr_id, run_id, "execution", "ERROR", f" Execution agent error: {e}")
            run.status = AgentRunStatus.failed
            run.error = str(e)
            cr.status = CRStatus.failed
            await db.commit()


async def _reboot_server(db, cr: ChangeRequest, run_id: int, hostname: str,
                          bucket_idx: int, needs_pause: bool) -> dict:
    """Execute full reboot sequence for one server"""
    # Resolve IP address from plan — Azure VMs need IP not hostname
    ip_address = (cr.ordered_server_list or {}).get("server_ip_map", {}).get(hostname)

    await _log(db, cr.id, run_id, "execution", "INFO",
               f"Collecting pre-reboot state for {hostname}" +
               (f" ({ip_address})" if ip_address else ""), server=hostname)
    await db.commit()

    pre_state = await winrm_service.get_server_state(hostname, ip_address=ip_address)

    # Store pre-state in ServerTask
    task_result = await db.execute(
        select(ServerTask).where(
            ServerTask.cr_id == cr.id,
            ServerTask.server_hostname == hostname
        )
    )
    task = task_result.scalar_one_or_none()
    if task:
        task.pre_state = pre_state
        task.status = TaskStatus.running
        task.started_at = datetime.now(timezone.utc)
        await db.flush()

    # Also store in CR pre_state
    if not cr.pre_state:
        cr.pre_state = {}
    cr.pre_state[hostname] = pre_state
    await db.flush()
    await db.commit()

    # Pause service if required
    # Run KB-generated pre-reboot script if available, else fall back to service pause
    kb_script = await _get_kb_pre_reboot_script(hostname, pre_state)
    if kb_script:
        await _log(db, cr.id, run_id, "execution", "INFO",
                   f"📋 Running KB-generated pre-reboot script on {hostname}", server=hostname)
        await db.commit()
        kb_result = await winrm_service.execute_script(hostname, kb_script, ip_address=ip_address)
        # Stream each line of output to live logs
        for line in kb_result.stdout.splitlines():
            if line.strip():
                await _log(db, cr.id, run_id, "execution", "INFO", line.strip(), server=hostname)
        await db.commit()
        if not kb_result.success:
            await _log(db, cr.id, run_id, "execution", "WARNING",
                       f"⚠️ Pre-reboot script had errors on {hostname}: {kb_result.stderr[:200]}",
                       server=hostname)
            await db.commit()
        else:
            if task:
                task.service_paused_at = datetime.now(timezone.utc)
                await db.flush()
                await db.commit()
    elif needs_pause:
        await _log(db, cr.id, run_id, "execution", "INFO",
                   f"⏸️ Pausing service on {hostname}", server=hostname)
        await db.commit()
        pause_result = await winrm_service.execute_script(
            hostname,
            f"& C:\\PatchOps\\Pause-Service.ps1",
            ip_address=ip_address,
        )
        if not pause_result.success:
            await _log(db, cr.id, run_id, "execution", "WARNING",
                       f"⚠️ Service pause failed on {hostname}: {pause_result.stderr}",
                       server=hostname)
            await db.commit()
        else:
            if task:
                task.service_paused_at = datetime.now(timezone.utc)
                await db.flush()
                await db.commit()

    # Initiate reboot
    await _log(db, cr.id, run_id, "execution", "INFO",
               f" Initiating reboot on {hostname}", server=hostname)
    await db.commit()

    reboot_result = await winrm_service.initiate_reboot(hostname, ip_address=ip_address)
    if not reboot_result.success:
        if task:
            task.status = TaskStatus.failed
            task.error_message = reboot_result.stderr
            task.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.commit()
        return {"success": False, "hostname": hostname, "error": reboot_result.stderr}

    # Wait for server to come back
    await _log(db, cr.id, run_id, "execution", "INFO",
               f" Waiting for {hostname} to come back online...", server=hostname)
    await db.commit()

    came_back = await winrm_service.wait_for_reboot(hostname, timeout=settings.REBOOT_TIMEOUT_SECONDS, ip_address=ip_address)
    if not came_back:
        if task:
            task.status = TaskStatus.failed
            task.error_message = "Server did not come back within timeout"
            task.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.commit()
        return {"success": False, "hostname": hostname, "error": "Reboot timeout"}

 
    # Run KB-generated post-reboot script if available, else fall back to service resume
    kb_post_script = await _get_kb_post_reboot_script(hostname)
    if kb_post_script:
        await _log(db, cr.id, run_id, "execution", "INFO",
                   f"📋 Running KB-generated post-reboot script on {hostname}", server=hostname)
        await db.commit()
        kb_post_result = await winrm_service.execute_script(hostname, kb_post_script, ip_address=ip_address)
        for line in kb_post_result.stdout.splitlines():
            if line.strip():
                await _log(db, cr.id, run_id, "execution", "INFO", line.strip(), server=hostname)
        await db.commit()
        if not kb_post_result.success:
            await _log(db, cr.id, run_id, "execution", "WARNING",
                       f"⚠️ Post-reboot script had errors on {hostname}: {kb_post_result.stderr[:200]}",
                       server=hostname)
        else:
            if task:
                task.service_resumed_at = datetime.now(timezone.utc)
                await db.flush()
    elif needs_pause:
        await _log(db, cr.id, run_id, "execution", "INFO",
                   f"▶️ Resuming service on {hostname}", server=hostname)
        await db.commit()
        resume_result = await winrm_service.execute_script(
            hostname,
            f"& C:\\PatchOps\\Resume-Service.ps1",
            ip_address=ip_address,
        )
        if not resume_result.success:
            await _log(db, cr.id, run_id, "execution", "WARNING",
                       f"⚠️ Service resume failed on {hostname}", server=hostname)
        else:
            if task:
                task.service_resumed_at = datetime.now(timezone.utc)
                await db.flush()

    if task:
        task.status = TaskStatus.completed
        task.completed_at = datetime.now(timezone.utc)
        await db.flush()
        await db.commit()

    return {"success": True, "hostname": hostname}


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3: Validation — post-reboot health check and deviation analysis
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="run_validation_agent")
def run_validation_agent(cr_id: int):
    run_async(_run_validation_agent(cr_id))


async def _run_validation_agent(cr_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = result.scalar_one_or_none()
        if not cr:
            return

        run = AgentRun(cr_id=cr_id, agent_type=AgentType.validation, status=AgentRunStatus.running)
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        failed_servers = []
        validation_results = []

        try:
            await _log(db, cr_id, run_id, "validation", "INFO",
                       "🔍 Agent 3 (Validation) started — checking server health...")
            await db.commit()

            # Get all server tasks
            tasks_result = await db.execute(
                select(ServerTask).where(ServerTask.cr_id == cr_id)
            )
            tasks = tasks_result.scalars().all()

            for task in tasks:
                hostname = task.server_hostname
                await _log(db, cr_id, run_id, "validation", "INFO",
                           f"🔍 Checking {hostname}...", server=hostname)
                await db.commit()

                ip_address = (cr.ordered_server_list or {}).get("server_ip_map", {}).get(hostname)
                post_state = await winrm_service.get_server_state(hostname, ip_address=ip_address)
                pre_state = task.pre_state or {}

                # Compare pre/post states
                deviation = _calculate_deviation(pre_state, post_state)
                health_ok = deviation < settings.DEVIATION_THRESHOLD_PERCENT

                task.post_state = post_state
                task.health_ok = health_ok
                task.deviation_percent = deviation

                if not cr.post_state:
                    cr.post_state = {}
                cr.post_state[hostname] = post_state

                validation_results.append({
                    "hostname": hostname,
                    "health_ok": health_ok,
                    "deviation_percent": deviation,
                    "pre_services": pre_state.get("Services", 0),
                    "post_services": post_state.get("Services", 0),
                })

                if health_ok:
                    await _log(db, cr_id, run_id, "validation", "SUCCESS",
                               f" {hostname} healthy — deviation: {deviation:.1f}%", server=hostname)
                else:
                    await _log(db, cr_id, run_id, "validation", "ERROR",
                               f" {hostname} unhealthy — deviation: {deviation:.1f}% (threshold: {settings.DEVIATION_THRESHOLD_PERCENT}%)",
                               server=hostname)
                    failed_servers.append({
                        "hostname": hostname,
                        "error": f"Health deviation {deviation:.1f}%",
                        "pre_state": pre_state,
                        "post_state": post_state,
                    })
                    await email_service.send_deviation_alert(cr.cr_number, hostname, deviation)

                await db.flush()
                await db.commit()

            cr.validation_report = {
                "results": validation_results,
                "total": len(tasks),
                "healthy": sum(1 for r in validation_results if r["health_ok"]),
                "unhealthy": sum(1 for r in validation_results if not r["health_ok"]),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            all_healthy = len(failed_servers) == 0
            cr.status = CRStatus.completed if all_healthy else CRStatus.failed
            cr.completed_at = datetime.now(timezone.utc)
            cr.progress_percent = 100.0

            run.status = AgentRunStatus.completed
            run.completed_at = datetime.now(timezone.utc)
            await db.commit()

            await _log(db, cr_id, run_id, "validation", "SUCCESS" if all_healthy else "WARNING",
                       f"{' All servers healthy' if all_healthy else f' {len(failed_servers)} server(s) failed health check'}")
            await db.commit()

            # Trigger RCA for failed servers
            if failed_servers:
                for srv in failed_servers:
                    celery_app.send_task("run_rca_agent", args=[cr_id, srv["hostname"], srv["error"]])

        except Exception as e:
            logger.error(f"Validation agent error: {e}", exc_info=True)
            await _log(db, cr_id, run_id, "validation", "ERROR", f"❌ Validation error: {e}")
            run.status = AgentRunStatus.failed
            run.error = str(e)
            await db.commit()


def _calculate_deviation(pre: dict, post: dict) -> float:
    """Calculate percentage deviation between pre and post server states"""
    if not pre:
        return 0.0

    metrics = []

    # Service count deviation
    pre_svc = pre.get("Services", 0)
    post_svc = post.get("Services", 0)
    if pre_svc:
        metrics.append(abs(post_svc - pre_svc) / pre_svc * 100)

    # Memory deviation
    pre_mem = pre.get("FreeMemoryGB", 0)
    post_mem = post.get("FreeMemoryGB", 0)
    if pre_mem:
        metrics.append(abs(post_mem - pre_mem) / pre_mem * 100)

    return sum(metrics) / len(metrics) if metrics else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4: RCA — root cause analysis for failed servers
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="run_rca_agent")
def run_rca_agent(cr_id: int, server_hostname: str, error_message: str):
    run_async(_run_rca_agent(cr_id, server_hostname, error_message))


async def _run_rca_agent(cr_id: int, server_hostname: str, error_message: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = result.scalar_one_or_none()
        if not cr:
            return

        run = AgentRun(cr_id=cr_id, agent_type=AgentType.rca, status=AgentRunStatus.running)
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()

        try:
            await _log(db, cr_id, run_id, "rca", "INFO",
                       f"🔬 Agent 4 (RCA) analyzing {server_hostname}...", server=server_hostname)
            await db.commit()

            # Get server task logs
            task_result = await db.execute(
                select(ServerTask).where(
                    ServerTask.cr_id == cr_id,
                    ServerTask.server_hostname == server_hostname
                )
            )
            task = task_result.scalar_one_or_none()
            winrm_logs = task.winrm_logs if task else ""
            pre_state = task.pre_state if task else {}

            # Create ServiceNow incident
            await _log(db, cr_id, run_id, "rca", "INFO",
                       f" Creating ServiceNow incident for {server_hostname}...", server=server_hostname)
            await db.commit()

            incident_data = await servicenow_service.sn_client.create_incident(
                short_description=f"Server {server_hostname} failed during patch deployment {cr.cr_number}",
                description=f"CR: {cr.cr_number}\nServer: {server_hostname}\nError: {error_message}",
            )
            sn_number = incident_data.get("number") if incident_data else None

            incident = Incident(
                cr_id=cr_id,
                server_hostname=server_hostname,
                sn_incident_number=sn_number,
                sn_sys_id=incident_data.get("sys_id") if incident_data else None,
                status=IncidentStatus.open,
                title=f"Server {server_hostname} failed during {cr.cr_number}",
                description=error_message,
            )
            db.add(incident)
            await db.flush()
            await db.commit()

            await _log(db, cr_id, run_id, "rca", "INFO",
                       f" Incident created: {sn_number or 'N/A'}", server=server_hostname)

            # Send failure email
            await email_service.send_failure_alert(server_hostname, cr.cr_number, error_message)

            # Run RCA analysis
            await _log(db, cr_id, run_id, "rca", "INFO",
                       f" Running Gemini Pro RCA analysis...", server=server_hostname)
            await db.commit()

            rca_result = await gemini_service.run_rca_analysis(
                server_hostname=server_hostname,
                error_message=error_message,
                winrm_logs=winrm_logs or "",
                server_config=pre_state,
                cr_context=f"CR {cr.cr_number}: {cr.title}",
            )

            incident.rca_analysis = rca_result.get("analysis", "")
            incident.rca_root_cause = rca_result.get("root_cause", "")
            incident.rca_steps = "\n".join(rca_result.get("immediate_steps", []))
            incident.rca_completed_at = datetime.now(timezone.utc)
            incident.email_sent = True

            # Add RCA as comment in ServiceNow
            if sn_number and incident.sn_sys_id:
                await servicenow_service.sn_client.add_comment(
                    incident.sn_sys_id,
                    rca_result.get("servicenow_comment", ""),
                )

            run.status = AgentRunStatus.completed
            run.completed_at = datetime.now(timezone.utc)
            run.result = rca_result
            await db.commit()

            await _log(db, cr_id, run_id, "rca", "SUCCESS",
                       f" RCA complete for {server_hostname} — Root cause: {rca_result.get('root_cause', 'Unknown')}",
                       server=server_hostname)
            await db.commit()

        except Exception as e:
            logger.error(f"RCA agent error: {e}", exc_info=True)
            await _log(db, cr_id, run_id, "rca", "ERROR", f" RCA error: {e}", server=server_hostname)
            run.status = AgentRunStatus.failed
            run.error = str(e)
            await db.commit()
