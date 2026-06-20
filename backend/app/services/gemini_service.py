"""Google Gemini AI service — CR classification, dependency graph verification, RCA"""
import json
import logging
import os
from typing import Optional
import google.generativeai as genai
from app.core.config import settings

logger = logging.getLogger(__name__)

# Configure Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)
elif settings.GOOGLE_APPLICATION_CREDENTIALS:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS


def _get_model(model_name: str):
    return genai.GenerativeModel(model_name)


async def classify_cr(title: str, description: str) -> dict:
    """
    Determine if a CR is patching-related using Gemini Flash.
    Returns: {is_patching: bool, confidence: float, reasoning: str, category: str}
    """
    prompt = f"""You are an IT change management classifier. Your task is to determine if a Change Request (CR) is related to patching/patch management.

Patching includes: OS patches, Windows Updates, security patches, cumulative updates, hotfixes, kernel updates, firmware updates, vulnerability remediation.

NOT patching: application deployments, configuration changes, hardware upgrades, network changes, database schema changes, user access changes.

CR Title: {title}
CR Description: {description or 'No description provided'}

Respond ONLY with a JSON object:
{{
  "is_patching": true/false,
  "confidence": 0.0-1.0,
  "category": "one of: os_patch, security_patch, firmware_update, hotfix, not_patching",
  "reasoning": "brief explanation"
}}"""

    try:
        model = _get_model(settings.GEMINI_MODEL_CLASSIFICATION)
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return result
    except Exception as e:
        logger.error(f"Gemini classification error: {e}")
        # Fallback: keyword-based classification
        keywords = ["patch", "update", "hotfix", "security", "vulnerability", "cumulative", "firmware", "kernel"]
        combined = f"{title} {description or ''}".lower()
        is_patching = any(kw in combined for kw in keywords)
        return {
            "is_patching": is_patching,
            "confidence": 0.6 if is_patching else 0.4,
            "category": "os_patch" if is_patching else "not_patching",
            "reasoning": "Keyword-based fallback classification (Gemini unavailable)",
        }


async def verify_dependency_graph(edges: list[tuple]) -> dict:
    """
    Use Gemini to verify a dependency graph for cycles, logical issues, etc.
    Returns: {valid: bool, issues: list, reasoning: str}
    """
    edges_text = "\n".join([f"  {a} depends_on {b}" for a, b in edges])
    prompt = f"""You are a systems architect reviewing a server dependency graph for IT change management.

The dependency graph means: if server A depends_on server B, then server B must be rebooted BEFORE server A.

Current dependency edges:
{edges_text if edges else "  (empty graph)"}

Analyze this graph for:
1. Circular dependencies (cycles) — e.g., A→B→C→A (this would cause a deadlock)
2. Logical issues — dependencies that don't make sense
3. Missing critical dependencies that are obvious

Respond ONLY with JSON:
{{
  "valid": true/false,
  "has_cycles": true/false,
  "cycle_details": ["list of cycles found, e.g., A→B→A"],
  "issues": ["list of other issues"],
  "warnings": ["non-blocking warnings"],
  "reasoning": "detailed explanation"
}}"""

    try:
        model = _get_model(settings.GEMINI_MODEL_AGENT)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return result
    except Exception as e:
        logger.error(f"Gemini graph verification error: {e}")
        # Fallback: use NetworkX cycle detection
        import networkx as nx
        G = nx.DiGraph()
        for a, b in edges:
            G.add_edge(a, b)
        try:
            cycle = nx.find_cycle(G)
            return {
                "valid": False,
                "has_cycles": True,
                "cycle_details": [" → ".join(f"{u}" for u, v in cycle)],
                "issues": ["Circular dependency detected"],
                "warnings": [],
                "reasoning": "Cycle detected via graph analysis (AI verification unavailable)",
            }
        except nx.NetworkXNoCycle:
            return {
                "valid": True,
                "has_cycles": False,
                "cycle_details": [],
                "issues": [],
                "warnings": [],
                "reasoning": "No cycles detected (AI verification unavailable, used graph analysis)",
            }


async def generate_server_order_summary(
    ordered_list: list,
    buckets: list,
    dependency_notes: str,
) -> str:
    """Agent 1: Generate human-readable summary of the planned execution order"""
    prompt = f"""You are PatchOps, an AI assistant for Windows server patch management.

Summarize the following server reboot plan clearly for an operations engineer who needs to approve it.

Ordered server list (with dependencies resolved):
{json.dumps(ordered_list, indent=2)}

Execution buckets (servers in same bucket restart in parallel):
{json.dumps(buckets, indent=2)}

Dependency reasoning:
{dependency_notes}

Write a concise, professional summary (3-5 sentences) explaining:
- How many servers, how many parallel buckets
- The key dependency decisions made
- Any special handling (service pauses, scheduled windows)
- Risk level

Be direct and professional. No markdown headers."""

    try:
        model = _get_model(settings.GEMINI_MODEL_AGENT)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Summary generation error: {e}")
        return f"Execution plan ready: {len(ordered_list)} servers across {len(buckets)} parallel buckets."


async def generate_execution_summary(completed_tasks: list, failed_tasks: list) -> str:
    """Agent 2: Generate post-execution summary"""
    prompt = f"""Summarize the Windows server reboot execution results for an operations engineer.

Completed successfully: {len(completed_tasks)} servers
{json.dumps([t['hostname'] for t in completed_tasks], indent=2)}

Failed: {len(failed_tasks)} servers
{json.dumps([{'hostname': t['hostname'], 'error': t.get('error')} for t in failed_tasks], indent=2)}

Write a brief professional summary (2-4 sentences). If there are failures, mention them prominently."""

    try:
        model = _get_model(settings.GEMINI_MODEL_AGENT)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Execution summary error: {e}")
        return f"Execution complete: {len(completed_tasks)} succeeded, {len(failed_tasks)} failed."
async def generate_reboot_scripts(
    server_hostname: str,
    kb_document: str,
    server_state: dict = None,
) -> dict:
    """
    Agent 2: Read the server's KB document (plain English) and generate
    custom pre-reboot and post-reboot PowerShell scripts.
    Returns: {pre_reboot_script, post_reboot_script, reasoning, apps_identified}
    """
    state_context = ""
    if server_state:
        state_context = f"""
Current server state:
- Running services: {server_state.get('Services', 'unknown')}
- OS: {server_state.get('OS', 'unknown')}
- Uptime hours: {server_state.get('Uptime', 'unknown')}
"""

    prompt = f"""You are a Windows PowerShell expert and IT operations engineer.
You need to generate safe, production-ready PowerShell scripts for a Windows server reboot operation.

Server: {server_hostname}
{state_context}

Server Knowledge Base Document (written by the operations team):
---
{kb_document}
---

Based on the knowledge base document above, generate TWO PowerShell scripts:

1. PRE_REBOOT_SCRIPT: Run BEFORE the server reboots
   - Gracefully stop/drain applications that need it
   - Respect any "never stop" rules
   - Follow any ordering rules (stop A before B)
   - Log each action with Write-Output
   - Exit 0 on success, Exit 1 on failure

2. POST_REBOOT_SCRIPT: Run AFTER the server comes back online
   - Start applications in the correct order
   - Wait for each to be healthy before starting the next
   - Perform health checks as described
   - Log each action with Write-Output
   - Exit 0 on success, Exit 1 on failure

IMPORTANT RULES:
- Never stop Windows security software (Defender, CrowdStrike, etc.)
- Never stop the WinRM service itself
- Always use try/catch for error handling
- Always use Write-Output (not Write-Host) so PatchOps can capture logs
- Keep scripts focused and safe — if something is unclear, skip it with a comment
- Use Start-Sleep between steps where the KB mentions wait times

Respond ONLY with a JSON object (no markdown):
{{
  "pre_reboot_script": "# Full PowerShell script here\\n...",
  "post_reboot_script": "# Full PowerShell script here\\n...",
  "apps_identified": ["list of applications found in the KB document"],
  "reasoning": "brief explanation of what the scripts do and why",
  "warnings": ["any concerns or things the scripts cannot handle"]
}}"""

    try:
        model = _get_model(settings.GEMINI_MODEL_AGENT)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return result
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        # Fallback: generic safe scripts
        return {
            "pre_reboot_script": f"# Auto-generated pre-reboot script for {server_hostname}\n# KB document parsing failed — using safe defaults\nWrite-Output 'Pre-reboot check: No custom actions configured'\nWrite-Output 'Server is ready for reboot'\nexit 0",
            "post_reboot_script": f"# Auto-generated post-reboot script for {server_hostname}\n# KB document parsing failed — using safe defaults\nWrite-Output 'Post-reboot check: Verifying Windows services...'\n$autoServices = Get-Service | Where-Object {{$_.StartType -eq 'Automatic' -and $_.Status -ne 'Running'}}\nforeach ($svc in $autoServices) {{\n    Write-Output \"Starting: $($svc.Name)\"\n    Start-Service $svc.Name -ErrorAction SilentlyContinue\n}}\nWrite-Output 'Post-reboot startup complete'\nexit 0",
            "apps_identified": [],
            "reasoning": "Fallback scripts used — Gemini unavailable",
            "warnings": ["Using generic fallback scripts. Add a valid Gemini API key for custom script generation."],
        }
async def generate_reboot_scripts(
    server_hostname: str,
    kb_document: str,
    server_state: dict = None,
) -> dict:
    """
    Read the server's KB document (plain English) and generate
    custom pre-reboot and post-reboot PowerShell scripts via Gemini.
    """
    state_context = ""
    if server_state:
        state_context = f"""
Current server state:
- Running services: {server_state.get('Services', 'unknown')}
- OS: {server_state.get('OS', 'unknown')}
- Uptime hours: {server_state.get('Uptime', 'unknown')}
"""

    prompt = f"""You are a Windows PowerShell expert and IT operations engineer.
Generate safe, production-ready PowerShell scripts for a Windows server reboot operation.

Server: {server_hostname}
{state_context}

Server Knowledge Base Document:
---
{kb_document}
---

Generate TWO PowerShell scripts:

1. PRE_REBOOT_SCRIPT: Run BEFORE the server reboots
   - Gracefully stop applications that need it per the KB document
   - Respect any never-stop rules
   - Log each action with Write-Output
   - Exit 0 on success, Exit 1 on failure

2. POST_REBOOT_SCRIPT: Run AFTER the server comes back online
   - Start applications in the correct order per the KB document
   - Wait for each to be healthy before proceeding
   - Log each action with Write-Output
   - Exit 0 on success, Exit 1 on failure

IMPORTANT RULES:
- Never stop Windows Defender, CrowdStrike, or any security software
- Never stop the WinRM service itself
- Never stop WindowsAzureGuestAgent or Azure monitoring agents
- Always use Write-Output (not Write-Host) so PatchOps can capture logs
- Always wrap in try/catch blocks

Respond ONLY with a JSON object (no markdown fences):
{{
  "pre_reboot_script": "# Full PowerShell script\\n...",
  "post_reboot_script": "# Full PowerShell script\\n...",
  "apps_identified": ["list of apps found in KB"],
  "reasoning": "brief explanation of what scripts do",
  "warnings": ["any concerns"]
}}"""

    try:
        model = _get_model(settings.GEMINI_MODEL_AGENT)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        return result
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        return {
            "pre_reboot_script": f"# Pre-reboot script for {server_hostname}\nWrite-Output 'No custom pre-reboot actions configured'\nexit 0",
            "post_reboot_script": f"# Post-reboot script for {server_hostname}\nWrite-Output 'No custom post-reboot actions configured'\nexit 0",
            "apps_identified": [],
            "reasoning": f"Fallback scripts used — error: {str(e)}",
            "warnings": ["Using fallback scripts. Check Gemini API key."],
        }


async def run_rca_analysis(
    server_hostname: str,
    error_message: str,
    winrm_logs: str,
    server_config: dict,
    cr_context: str,
) -> dict:
    """Agent 4 (RCA): Deep analysis of server failure using Gemini Pro"""
    prompt = f"""You are a senior Windows infrastructure engineer performing Root Cause Analysis (RCA) for a server that failed during patch deployment.

Server: {server_hostname}
Error: {error_message}

Change Request Context:
{cr_context}

Server Configuration:
{json.dumps(server_config, indent=2)}

WinRM Execution Logs:
{winrm_logs[-8000:] if winrm_logs else 'No logs available'}

Perform a thorough RCA and respond with JSON:
{{
  "root_cause": "Concise root cause statement",
  "analysis": "Detailed technical analysis of what went wrong",
  "contributing_factors": ["list of contributing factors"],
  "immediate_steps": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "preventive_measures": ["future prevention recommendations"],
  "severity": "critical|high|medium|low",
  "estimated_resolution_time": "e.g., 2-4 hours",
  "servicenow_comment": "Ready-to-paste comment for the ServiceNow incident (include root cause, steps taken, and next actions)"
}}"""

    try:
        model = _get_model(settings.GEMINI_MODEL_RCA)
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        logger.error(f"RCA analysis error: {e}")
        return {
            "root_cause": f"Analysis failed: {str(e)}",
            "analysis": error_message,
            "contributing_factors": [],
            "immediate_steps": ["Manual investigation required"],
            "preventive_measures": [],
            "severity": "high",
            "estimated_resolution_time": "Unknown",
            "servicenow_comment": f"Automated RCA failed. Manual review required.\nError: {error_message}",
        }
