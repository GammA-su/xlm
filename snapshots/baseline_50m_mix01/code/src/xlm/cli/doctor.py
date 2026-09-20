"""Diagnostic doctor for inspecting XLM environment, hardware, and runtime capabilities."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from xlm import __version__
from xlm.core.paths import get_artifact_root


@dataclass
class DoctorReport:
    """Structured report produced by the XLM doctor."""

    package_version: str
    python_version: str
    python_executable: str
    uv_version: str | None
    os_name: str
    os_release: str
    os_arch: str
    cpu_count_logical: int | None
    cpu_count_physical: int | None
    ram_total_gb: float
    ram_available_gb: float
    artifact_root: str
    artifact_root_exists: bool
    disk_free_gb: float | None
    disk_total_gb: float | None
    torch_installed: bool
    torch_version: str | None
    torch_cuda_version: str | None
    torch_cuda_available: bool
    torch_bf16_supported: bool
    sdpa_kernels: dict[str, bool]
    cuda_devices: list[dict[str, Any]]
    nvidia_driver_version: str | None
    nvidia_driver_cuda_version: str | None
    unsupported_explanations: list[str]
    capabilities: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return asdict(self)


def get_uv_version() -> str | None:
    """Retrieve uv CLI version if installed."""
    uv_path = shutil.which("uv")
    if not uv_path:
        return None
    try:
        res = subprocess.run(
            [uv_path, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def get_nvidia_smi_info() -> tuple[str | None, str | None]:
    """Query nvidia-smi for driver version and max supported CUDA version.

    Returns:
        tuple of (driver_version, max_supported_cuda_version)
    """
    smi_path = shutil.which("nvidia-smi")
    if not smi_path:
        return None, None
    try:
        res = subprocess.run(
            [
                smi_path,
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        driver_ver = (
            res.stdout.strip().splitlines()[0]
            if res.returncode == 0 and res.stdout.strip()
            else None
        )

        # Also get CUDA version via nvidia-smi banner header
        banner_res = subprocess.run(
            [smi_path],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        cuda_ver = None
        if banner_res.returncode == 0:
            for line in banner_res.stdout.splitlines():
                if "CUDA Version:" in line:
                    parts = line.split("CUDA Version:")
                    if len(parts) > 1:
                        cuda_ver = parts[1].split()[0].replace("|", "").strip()
                    break

        return driver_ver, cuda_ver
    except (subprocess.SubprocessError, OSError):
        return None, None


def inspect_torch() -> tuple[
    bool, str | None, str | None, bool, bool, dict[str, bool], list[dict[str, Any]]
]:
    """Inspect PyTorch installation without failing if absent or broken."""
    try:
        import warnings  # noqa: PLC0415

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import torch  # noqa: PLC0415
    except ImportError:
        return False, None, None, False, False, {}, []
    except Exception as exc:  # noqa: BLE001
        # Broken torch installation or missing DLLs
        return False, f"error: {exc}", None, False, False, {}, []

    torch_ver = getattr(torch, "__version__", None)
    torch_cuda_build = getattr(torch.version, "cuda", None) if hasattr(torch, "version") else None
    cuda_available = False
    bf16_supported = False
    sdpa_kernels: dict[str, bool] = {}
    cuda_devices: list[dict[str, Any]] = []

    try:
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                cuda_devices.append(
                    {
                        "index": i,
                        "name": props.name,
                        "total_memory_gb": round(props.total_memory / (1024**3), 2),
                        "major": props.major,
                        "minor": props.minor,
                    }
                )
            try:
                bf16_supported = bool(torch.cuda.is_bf16_supported())
            except Exception:  # noqa: BLE001
                bf16_supported = False
            from xlm.models.backends import probe_sdpa_kernels  # noqa: PLC0415

            sdpa_kernels = probe_sdpa_kernels("cuda")
    except Exception:  # noqa: BLE001
        cuda_available = False

    return (
        True,
        torch_ver,
        torch_cuda_build,
        cuda_available,
        bf16_supported,
        sdpa_kernels,
        cuda_devices,
    )


def explain_unsupported_configs(
    torch_installed: bool,
    torch_cuda_build: str | None,
    torch_cuda_available: bool,
    torch_bf16_supported: bool,
    driver_cuda: str | None,
    cuda_devices: list[dict[str, Any]],
) -> list[str]:
    """Detect and explain unsupported configurations without installing anything."""
    explanations: list[str] = []
    if torch_installed and not torch_cuda_build and cuda_devices:
        explanations.append(
            "A CPU-only torch wheel is installed while CUDA devices are present; "
            "CUDA execution needs the CUDA wheel (uv sync --extra cuda)."
        )
    if torch_installed and torch_cuda_build and not torch_cuda_available and not cuda_devices:
        explanations.append(
            f"A CUDA torch wheel ({torch_cuda_build}) is installed but no GPU is visible; "
            "CUDA runs will fail until a device is present."
        )
    if torch_cuda_available and not torch_bf16_supported:
        explanations.append(
            "The CUDA device reports no BF16 support; 'bf16_fp32_master' runs will be "
            "refused -- use 'fp32' or 'fp16' explicitly."
        )
    if driver_cuda and torch_cuda_build:
        try:
            driver_parts = [int(x) for x in driver_cuda.split(".")[:2]]
            wheel_parts = [int(x) for x in str(torch_cuda_build).split(".")[:2]]
            if driver_parts < wheel_parts:
                explanations.append(
                    f"Driver CUDA {driver_cuda} predates the torch wheel build "
                    f"{torch_cuda_build}; upgrade the driver, never the code path."
                )
        except ValueError:
            pass
    return explanations


def inspect_disk_space(target_path: Path) -> tuple[float | None, float | None]:
    """Inspect disk space for a given path or its nearest existing ancestor."""
    probe = target_path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        return None, None
    try:
        usage = psutil.disk_usage(str(probe))
        return round(usage.free / (1024**3), 2), round(usage.total / (1024**3), 2)
    except OSError:
        return None, None


def collect_doctor_report() -> DoctorReport:
    """Collect all diagnostic information into a structured report."""
    root = get_artifact_root()
    disk_free, disk_total = inspect_disk_space(root)
    ram = psutil.virtual_memory()

    driver_ver, driver_cuda = get_nvidia_smi_info()
    (
        torch_installed,
        torch_ver,
        torch_cuda_build,
        torch_cuda_available,
        torch_bf16_supported,
        sdpa_kernels,
        cuda_devices,
    ) = inspect_torch()

    # Honest capabilities declaration
    capabilities = {
        "offline_cpu_execution": "ready",
        "cli_and_path_resolver": "ready",
        "cuda_acceleration": (
            "ready"
            if torch_cuda_available
            else (
                "disabled (no cuda torch)" if torch_installed else "disabled (torch not installed)"
            )
        ),
        "bf16_execution": (
            "ready"
            if torch_bf16_supported
            else "unsupported on this device/build (refused, never downgraded)"
        ),
        "training_engine": "ready (prompts 05, 12, 14)",
        "execution_profiler": "ready (prompt 14)",
        "evaluation_harness": "unimplemented (prompt 15 pending)",
    }

    unsupported = explain_unsupported_configs(
        torch_installed,
        torch_cuda_build,
        torch_cuda_available,
        torch_bf16_supported,
        driver_cuda,
        cuda_devices,
    )

    return DoctorReport(
        package_version=__version__,
        python_version=platform.python_version(),
        python_executable=sys.executable,
        uv_version=get_uv_version(),
        os_name=platform.system(),
        os_release=platform.release(),
        os_arch=platform.machine(),
        cpu_count_logical=psutil.cpu_count(logical=True),
        cpu_count_physical=psutil.cpu_count(logical=False),
        ram_total_gb=round(ram.total / (1024**3), 2),
        ram_available_gb=round(ram.available / (1024**3), 2),
        artifact_root=str(root),
        artifact_root_exists=root.exists(),
        disk_free_gb=disk_free,
        disk_total_gb=disk_total,
        torch_installed=torch_installed,
        torch_version=torch_ver,
        torch_cuda_version=torch_cuda_build,
        torch_cuda_available=torch_cuda_available,
        torch_bf16_supported=torch_bf16_supported,
        sdpa_kernels=sdpa_kernels,
        cuda_devices=cuda_devices,
        nvidia_driver_version=driver_ver,
        nvidia_driver_cuda_version=driver_cuda,
        unsupported_explanations=unsupported,
        capabilities=capabilities,
    )


def render_doctor_text(report: DoctorReport) -> str:
    """Format report as human-readable text."""
    lines = [
        "=== XLM Environment & Diagnostic Report ===",
        f"XLM Package Version:       {report.package_version}",
        f"Python Version:            {report.python_version} ({report.python_executable})",
        f"uv Version:                {report.uv_version or 'Not found in PATH'}",
        f"Operating System:          {report.os_name} {report.os_release} ({report.os_arch})",
        (
            f"CPU Cores:                 {report.cpu_count_logical} logical, "
            f"{report.cpu_count_physical} physical"
        ),
        (
            f"System RAM:                {report.ram_available_gb} GB available / "
            f"{report.ram_total_gb} GB total"
        ),
        (
            f"Artifact Root (XLM_HOME):  {report.artifact_root} "
            f"(exists: {report.artifact_root_exists})"
        ),
        (
            f"Artifact Disk Space:       {report.disk_free_gb} GB free / "
            f"{report.disk_total_gb} GB total"
        ),
        "",
        "--- PyTorch & Accelerator Status ---",
    ]

    if not report.torch_installed:
        lines.append("PyTorch:                   Not installed (Base installation mode)")
    else:
        lines.append(f"PyTorch Version:           {report.torch_version}")
        lines.append(
            f"PyTorch CUDA Build:        {report.torch_cuda_version or 'None (CPU build)'}"
        )
        lines.append(f"PyTorch CUDA Available:    {report.torch_cuda_available}")

    lines.append(
        f"NVIDIA Driver Version:     {report.nvidia_driver_version or 'Not detected / unavailable'}"
    )
    lines.append(
        f"Driver Supported CUDA:     {report.nvidia_driver_cuda_version or 'Not detected'}"
    )

    if report.cuda_devices:
        lines.append("Detected CUDA Devices:")
        for dev in report.cuda_devices:
            dev_desc = (
                f"  [{dev['index']}] {dev['name']} "
                f"({dev['total_memory_gb']} GB VRAM, sm_{dev['major']}{dev['minor']})"
            )
            lines.append(dev_desc)
    else:
        lines.append("Detected CUDA Devices:     None (running in CPU-only mode)")

    lines.append(f"BF16 Device Support:       {report.torch_bf16_supported}")
    if report.sdpa_kernels:
        probed = ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in report.sdpa_kernels.items())
        lines.append(f"Probed SDPA Kernels:       {probed}")
    if report.unsupported_explanations:
        lines.append("Unsupported Configurations:")
        for explanation in report.unsupported_explanations:
            lines.append(f"  ! {explanation}")

    lines.append("")
    lines.append("--- Active Capabilities ---")
    for cap, status in report.capabilities.items():
        lines.append(f"  {cap:<28}: {status}")

    return "\n".join(lines)


def run_doctor(as_json: bool = False) -> None:
    """Execute the doctor diagnosis and write output to stdout."""
    report = collect_doctor_report()
    if as_json:
        # STRICT JSON on stdout - no extra text or banners
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(render_doctor_text(report) + "\n")
