import os
from pathlib import Path

# Platform root: .../AutoTrading-Ideal-Platform (parent of orchestrator/)
PLATFORM_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PLATFORM_ROOT / "agents"
REGISTRY_PATH = AGENTS_DIR / "registry.json"
MUSASHABAN_PORT_DIR = PLATFORM_ROOT / "musashaban-port"
SKILLS_DIR = PLATFORM_ROOT / "skills"
STATE_DIR = Path(__file__).resolve().parent / "state"
RUNS_DIR = STATE_DIR / "runs"
REPORTS_DIR = STATE_DIR / "reports"
ALERTS_PATH = STATE_DIR / "alerts.json"
GEO_DB_PATH = STATE_DIR / "geo.db"
SKILLS_STATE_PATH = STATE_DIR / "skills_state.json"

_ENV_VARS = (
    "JAVA_HOME",
    "RUSTUP_HOME",
    "CARGO_HOME",
    "BUN_INSTALL",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
)


def _registry_values(root, subkey):
    import winreg

    values = {}
    try:
        with winreg.OpenKey(root, subkey) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(value, str):
                    values[name] = value
                index += 1
    except OSError:
        pass
    return values


def hydrate_environment():
    """Merge Windows registry env into this process.

    A terminal that was already open when a toolchain was installed keeps its
    old PATH, so the orchestrator started from it would not find cargo/java/
    cmake and would report components as blocked. Re-reading the persisted
    environment at startup fixes that without requiring a new terminal.
    """
    if os.name != "nt":
        return
    import winreg

    user = _registry_values(winreg.HKEY_CURRENT_USER, "Environment")
    machine = _registry_values(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    combined = dict(machine)
    combined.update(user)

    for name in _ENV_VARS:
        value = user.get(name) or machine.get(name)
        if value and not os.environ.get(name):
            os.environ[name] = os.path.expandvars(value)

    # The user PATH holds the toolchains installed for this platform (Node 24,
    # cargo, cmake, bun, ...). Put it FIRST so they win over any older system
    # runtime (e.g. a pre-existing node.exe on the machine PATH). The shared
    # Python venv is prepended too so component scripts use the single shared
    # dependency tree.
    preferred = [
        str(PLATFORM_ROOT / ".venv" / "Scripts"),
        str(PLATFORM_ROOT / ".venv" / "bin"),
    ]
    # The platform's pinned Node runtime wins over any older system node.exe.
    node_bin = os.environ.get("PLATFORM_NODE_BIN") or ""
    if node_bin and Path(node_bin).exists():
        preferred.insert(0, node_bin)
    for entry in user.get("Path", "").split(os.pathsep):
        entry = os.path.expandvars(entry.strip())
        if entry:
            preferred.append(entry)

    current = list(preferred) + [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    seen = {p.rstrip("\\").casefold() for p in current}
    for source in (machine.get("Path", ""),):
        for entry in source.split(os.pathsep):
            entry = os.path.expandvars(entry.strip())
            if not entry:
                continue
            key = entry.rstrip("\\").casefold()
            if key not in seen:
                seen.add(key)
                current.append(entry)
    os.environ["PATH"] = os.pathsep.join(current)


hydrate_environment()

ENV_FILE = PLATFORM_ROOT / ".env"


def load_env_file():
    """Load generated free/local defaults into child processes."""
    if not ENV_FILE.exists():
        return
    try:
        lines = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

HOST = os.environ.get("ORCHESTRATOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8765"))

# Safety: allow tool execution only within the platform root by default.
ALLOWED_ROOT = PLATFORM_ROOT


def ensure_dirs():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
