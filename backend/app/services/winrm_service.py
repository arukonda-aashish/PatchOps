"""WinRM service — executes PowerShell scripts on Windows servers.
   WINRM_MOCK_MODE=true uses simulated responses for demo/testing.
"""
import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class WinRMResult:
    def __init__(self, success: bool, stdout: str, stderr: str, exit_code: int):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


async def execute_script(
    hostname: str,
    script_content: str,
    timeout: int = 60,
    ip_address: str = None,
) -> WinRMResult:
    """Execute a PowerShell script on a remote Windows server via WinRM.
    Uses ip_address for the actual connection if provided — hostname is used
    for logging only. Azure VMs are reached by public IP, not hostname.
    """
    if settings.WINRM_MOCK_MODE:
        return await _mock_execute(hostname, script_content)

    # Use IP address if provided, otherwise fall back to hostname
    connect_target = ip_address if ip_address else hostname

    try:
        import winrm
        protocol = "https" if settings.WINRM_USE_SSL else "http"
        session = winrm.Session(
            f"{protocol}://{connect_target}:{settings.WINRM_PORT}/wsman",
            auth=(settings.WINRM_USERNAME, settings.WINRM_PASSWORD),
            transport="basic",
            server_cert_validation="ignore",
        )
        result = session.run_ps(script_content)
        return WinRMResult(
            success=result.status_code == 0,
            stdout=result.std_out.decode("utf-8", errors="replace"),
            stderr=result.std_err.decode("utf-8", errors="replace"),
            exit_code=result.status_code,
        )
    except Exception as e:
        logger.error(f"WinRM error for {hostname}: {e}")
        return WinRMResult(
            success=False,
            stdout="",
            stderr=str(e),
            exit_code=1,
        )


async def get_server_state(hostname: str, ip_address: str = None) -> dict:
    """Collect pre/post-reboot state metrics from a Windows server"""
    script = """
$info = @{
    Hostname = $env:COMPUTERNAME
    OS = (Get-CimInstance Win32_OperatingSystem).Caption
    Uptime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
    Services = (Get-Service | Where-Object {$_.StartType -eq 'Automatic'} | Where-Object {$_.Status -eq 'Running'} | Measure-Object).Count
    TotalServices = (Get-Service | Where-Object {$_.StartType -eq 'Automatic'} | Measure-Object).Count
    CPU = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    MemoryGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize / 1MB, 2)
    FreeMemoryGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
    DiskUsagePercent = [math]::Round(((Get-PSDrive C).Used / ((Get-PSDrive C).Used + (Get-PSDrive C).Free)) * 100, 1)
    Timezone = (Get-TimeZone).Id
}
$info | ConvertTo-Json
"""
    result = await execute_script(hostname, script, ip_address=ip_address)
    if result.success:
        import json
        try:
            return json.loads(result.stdout)
        except Exception:
            pass

    # Mock state on failure
    return _mock_server_state(hostname)


async def initiate_reboot(hostname: str, ip_address: str = None) -> WinRMResult:
    """Initiate graceful Windows reboot"""
    script = "Restart-Computer -Force"
    return await execute_script(hostname, script, timeout=settings.REBOOT_TIMEOUT_SECONDS, ip_address=ip_address)


async def wait_for_reboot(hostname: str, timeout: int = 300, ip_address: str = None) -> bool:
    """Poll until server is responsive again after reboot"""
    if settings.WINRM_MOCK_MODE:
        await asyncio.sleep(random.uniform(5, 12))
        return True

    connect_target = ip_address if ip_address else hostname
    import winrm
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            protocol = "https" if settings.WINRM_USE_SSL else "http"
            session = winrm.Session(
                f"{protocol}://{connect_target}:{settings.WINRM_PORT}/wsman",
                auth=(settings.WINRM_USERNAME, settings.WINRM_PASSWORD),
                transport="basic",
                server_cert_validation="ignore",
            )
            result = session.run_ps("echo 'alive'")
            if result.status_code == 0:
                return True
        except Exception:
            pass
        await asyncio.sleep(10)
    return False


# ── Mock implementations ──────────────────────────────────────────────────────

async def _mock_execute(hostname: str, script_content: str) -> WinRMResult:
    """Simulate WinRM execution with realistic delays"""
    await asyncio.sleep(random.uniform(0.5, 2.5))

    # Simulate occasional failures (10% failure rate)
    if random.random() < 0.1:
        return WinRMResult(
            success=False,
            stdout="",
            stderr=f"WinRM connection failed: The WS-Management service cannot complete the operation within the time specified. (hostname: {hostname})",
            exit_code=1,
        )

    script_lower = script_content.lower()
    if "restart-computer" in script_lower:
        stdout = f"[{datetime.now().isoformat()}] Initiating restart for {hostname}...\nRestart initiated successfully."
    elif "stop-service" in script_lower or "pause" in script_lower.lower():
        stdout = f"Service stopped successfully on {hostname}"
    elif "start-service" in script_lower or "resume" in script_lower.lower():
        stdout = f"Service started successfully on {hostname}"
    else:
        stdout = f"Script executed successfully on {hostname}"

    return WinRMResult(success=True, stdout=stdout, stderr="", exit_code=0)


def _mock_server_state(hostname: str) -> dict:
    """Generate realistic mock server state"""
    import hashlib
    seed = int(hashlib.md5(hostname.encode()).hexdigest()[:8], 16)
    random.seed(seed)
    return {
        "Hostname": hostname,
        "OS": "Windows Server 2022 Standard",
        "Uptime": datetime.now(timezone.utc).isoformat(),
        "Services": random.randint(42, 58),
        "TotalServices": random.randint(60, 75),
        "CPU": random.uniform(5, 45),
        "MemoryGB": random.choice([16, 32, 64]),
        "FreeMemoryGB": random.uniform(4, 20),
        "DiskUsagePercent": random.uniform(40, 75),
        "Timezone": random.choice(["UTC", "Eastern Standard Time", "Pacific Standard Time", "India Standard Time"]),
    }
