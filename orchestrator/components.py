"""Subplatform readiness checks.

The orchestrator can dispatch work to a component, but many components need a
toolchain or installed dependencies before they can actually run. This module
reports, per component, what is present, what is missing, and the exact next
step — without installing anything or executing component code.

Components often contain several subprojects (Pix has Java, Python and Rust;
FinceptTerminal has a CMake/Qt app). Those are discovered up to two levels deep
so a missing JDK or Rust toolchain is reported instead of a false "ready".
"""

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import PLATFORM_ROOT
from .registry import subplatforms

_NODE_MANIFESTS = ("package.json",)
_PYTHON_MANIFESTS = ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg", "pixi.toml")
_RUST_MANIFESTS = ("Cargo.toml",)
_JAVA_MANIFESTS = ("pom.xml", "build.gradle", "build.gradle.kts")
_GO_MANIFESTS = ("go.mod",)
_CMAKE_MANIFESTS = ("CMakeLists.txt",)

# One shared dependency tree for the whole platform (see root package.json and
# requirements.txt). A component is considered installed when it resolves here,
# even if it has no local node_modules/.venv of its own.
SHARED_NODE_MODULES = PLATFORM_ROOT / "node_modules"
SHARED_VENV = PLATFORM_ROOT / ".venv"

_SKIP_DIRS = {
    "node_modules", ".git", "target", "dist", "build", ".venv", "venv", "__pycache__",
    ".next", ".nuxt", ".turbo", ".cache", "vendor", "third_party", "site-packages",
}

_TOOLCHAIN_HINTS = {
    "node": ("node", "npm"),
    "python": ("python",),
    "rust": ("cargo",),
    "java": ("java",),
    "go": ("go",),
    "cmake": ("cmake",),
}


def _has_any(path: Path, names) -> Optional[str]:
    for name in names:
        if (path / name).exists():
            return name
    return None


def _which_all(executables) -> Dict[str, bool]:
    return {exe: bool(shutil.which(exe)) for exe in executables}


def _safe_iterdir(path: Path):
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except Exception:
        return []


def _nested_duplicates(comp_dir: Path) -> List[str]:
    """Direct children that look like a second copy of this same repo."""
    duplicates = []
    comp_name = comp_dir.name
    for child in _safe_iterdir(comp_dir):
        name = child.name.lower()
        if name not in (comp_name.lower(), (comp_name + "-main").lower()):
            continue
        # A real package directory (e.g. vibetrading/vibetrading) has no README
        # and no top-level project manifest of its own.
        looks_like_repo = (child / "README.md").exists() or (
            (child / "package.json").exists() or (child / "pyproject.toml").exists()
            or (child / "Cargo.toml").exists() or (child / "pom.xml").exists()
        )
        if looks_like_repo:
            duplicates.append(child.name)
    return duplicates


def _empty_submodules(comp_dir: Path) -> List[str]:
    """Submodule paths declared in .gitmodules but not checked out."""
    gitmodules = comp_dir / ".gitmodules"
    if not gitmodules.exists():
        return []
    empty = []
    try:
        text = gitmodules.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return empty
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("path") or "=" not in stripped:
            continue
        rel = stripped.split("=", 1)[1].strip()
        target = comp_dir / rel
        if target.exists() and target.is_dir():
            try:
                if not any(target.iterdir()):
                    empty.append(rel)
            except Exception:
                pass
        else:
            empty.append(rel)
    return empty


def _python_package_dirs(path: Path) -> List[str]:
    found = []
    for child in _safe_iterdir(path):
        if child.name in _SKIP_DIRS:
            continue
        if (child / "__init__.py").exists():
            found.append(child.name)
    return found


def _parse_ver(text: str) -> Optional[Tuple[int, ...]]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) if nums else None


@lru_cache(maxsize=None)
def _site_packages_dirs() -> List[str]:
    """Names inside the shared venv's site-packages (empty when absent)."""
    import sys
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    for rel in ("Lib/site-packages", f"lib/python{py_ver}/site-packages"):
        path = SHARED_VENV / rel
        if path.is_dir():
            try:
                return [p.name for p in path.iterdir()]
            except Exception:
                return []
    return []


def _venv_python() -> Optional[Path]:
    for rel in ("Scripts/python.exe", "bin/python"):
        candidate = SHARED_VENV / rel
        if candidate.exists():
            return candidate
    return None


def _declared_python_modules(path: Path) -> List[str]:
    """Importable module names this project declares."""
    modules: List[str] = []
    pyproject = path / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^\s*name\s*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
            if match:
                modules.append(match.group(1).replace("-", "_").lower())
        except Exception:
            pass
    for base in (path, path / "src"):
        for child in _safe_iterdir(base):
            if child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            if (child / "__init__.py").exists():
                modules.append(child.name.replace("-", "_").lower())
    return list(dict.fromkeys(modules))


def _python_deps_installed(path: Path) -> Optional[bool]:
    """True only when a real environment exists and the declared module is importable."""
    if (path / ".venv").exists() or (path / "venv").exists():
        return True
    if _venv_python() is None:
        return False
    modules = _declared_python_modules(path)
    if not modules:
        return True  # dependencies-only requirements file, nothing importable to verify
    installed = _site_packages_dirs()
    lowered = [name.lower() for name in installed]
    for module in modules:
        if any(
            marker in name
            for name in lowered
            for marker in (f"__editable__.{module}", f"{module}-", f"{module}.dist-info", f"{module}._")
        ):
            return True
        if module in lowered:
            return True
    return False


def _shared_node_installed() -> bool:
    """A partial npm install still leaves a node_modules directory.

    npm writes `.package-lock.json` inside node_modules only after a successful
    install; its presence distinguishes a real install from a truncated one.
    """
    marker = SHARED_NODE_MODULES / ".package-lock.json"
    if not marker.exists():
        return False
    try:
        return any(p.is_dir() for p in SHARED_NODE_MODULES.iterdir() if p.name != ".bin")
    except Exception:
        return False


def _node_deps_installed(path: Path) -> bool:
    """A completed install leaves node_modules/.package-lock.json behind.

    npm creates nested node_modules directories while an install is still in
    progress (workspace hoisting), so the directory alone proves nothing.
    """
    local = path / "node_modules"
    if (local / ".package-lock.json").exists():
        try:
            if any(p.is_dir() for p in local.iterdir() if p.name != ".bin"):
                return True
        except Exception:
            pass
    return _shared_node_installed()


@lru_cache(maxsize=None)
def _rustup_toolchains() -> List[str]:
    rustup = shutil.which("rustup")
    if not rustup:
        return []
    try:
        out = subprocess.run([rustup, "toolchain", "list"], capture_output=True, text=True, timeout=15).stdout
        return [line.split()[0] for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def _rust_toolchain_problem(path: Path) -> Optional[str]:
    toolchain_file = path / "rust-toolchain.toml"
    if not toolchain_file.exists():
        return None
    try:
        text = toolchain_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    match = re.search(r"channel\s*=\s*\"([^\"]+)\"", text)
    if not match:
        return None
    required = match.group(1)
    installed = _rustup_toolchains()
    if not installed:
        return f"rust-toolchain.toml pins {required} but rustup is not installed"
    if not any(name == required or name.startswith(required) for name in installed):
        return f"rust-toolchain.toml pins {required}, but installed toolchains are: {', '.join(installed)}"
    return None


def _env_value(comp_dir: Path, key: str) -> Optional[str]:
    """A configured value from the process env, then the component's .env."""
    value = os.environ.get(key)
    if value:
        return value.strip()
    for name in (".env", ".env.local"):
        path = comp_dir / name
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(rf"\s*{re.escape(key)}\s*=\s*(.*?)\s*$", line)
                if m:
                    return m[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return None


def _configured_mongo_uri(comp_dir: Path) -> Optional[str]:
    """A real MONGO_URI (not the .env.example placeholder), if one is set."""
    uri = _env_value(comp_dir, "MONGO_URI") or _env_value(comp_dir, "MONGODB_URI")
    if not uri:
        return None
    lowered = uri.lower()
    if "username:password" in lowered or "<user" in lowered or "example.com" in lowered:
        return None
    return uri


def _wrangler_available(comp_dir: Path) -> bool:
    """Wrangler ships as the component's devDependency; a global install is optional."""
    if shutil.which("wrangler"):
        return True
    for base in (comp_dir / "node_modules", SHARED_NODE_MODULES):
        if (base / "wrangler").is_dir():
            return True
        for exe in ("wrangler", "wrangler.cmd", "wrangler.ps1"):
            if (base / ".bin" / exe).exists():
                return True
    return False


def _required_services(comp_dir: Path) -> List[Dict[str, Any]]:
    """Services the component needs to actually run.

    Detection uses configuration (env template variables, wrangler config),
    never prose in the README: mentioning MongoDB in documentation does not
    make it a runtime requirement.

    A configured remote MONGO_URI satisfies MongoDB even when no local `mongod`
    binary is on PATH. Wrangler is deployment tooling that ships as a
    devDependency, so a missing global install is a note, not a blocker.
    """
    services: List[Dict[str, Any]] = []
    env_names: List[str] = []
    env_example = comp_dir / ".env.example"
    if env_example.exists():
        try:
            for line in env_example.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", line)
                if m:
                    env_names.append(m[1])
        except Exception:
            pass
    if any(name.startswith("MONGO") for name in env_names):
        configured = _configured_mongo_uri(comp_dir)
        services.append({
            "name": "mongodb",
            "present": bool(configured or shutil.which("mongod")),
            "blocking": True,
            "why": (
                "MONGO_URI is configured remotely" if configured
                else "MONGO_URI is required by .env.example (no remote URI configured and mongod is not on PATH)"
            ),
        })
    if (comp_dir / "wrangler.jsonc").exists() or (comp_dir / "wrangler.toml").exists():
        services.append({
            "name": "wrangler",
            "present": _wrangler_available(comp_dir),
            "blocking": False,
            "why": "Cloudflare Worker deployment target (devDependency; needed only to deploy)",
        })
    return services


@lru_cache(maxsize=None)
def _node_version() -> Optional[Tuple[int, ...]]:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        return _parse_ver(out)
    except Exception:
        return None


@lru_cache(maxsize=None)
def _bun_version() -> Optional[Tuple[int, ...]]:
    bun = shutil.which("bun")
    if not bun:
        return None
    try:
        out = subprocess.run([bun, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        return _parse_ver(out)
    except Exception:
        return None


def _version_satisfies(range_text: str, version: Optional[Tuple[int, ...]]) -> Optional[bool]:
    """Best-effort check of common semver range forms against an installed version.

    Returns None when the range cannot be interpreted (no false alarms).
    """
    if not range_text or not version:
        return None
    constraints = re.findall(r"(>=|<=|>|<|\^|~)?\s*(\d+(?:\.\d+){0,2})", str(range_text))
    if not constraints:
        return None
    ok = True
    for op, raw in constraints:
        want = _parse_ver(raw)
        if not want:
            continue
        if op in (">=", ""):
            if version < want:
                ok = False
        elif op == ">":
            if version <= want:
                ok = False
        elif op == "<=":
            if version > want:
                ok = False
        elif op == "<":
            if version >= want:
                ok = False
        elif op == "^":
            if version < want or version[0] != want[0]:
                ok = False
        elif op == "~":
            if version < want or version[:2] != want[:2]:
                ok = False
    return ok


@lru_cache(maxsize=None)
def _qt_available() -> bool:
    """Whether a usable Qt 6 development install is present."""
    for exe in ("qmake6", "qmake", "qmake-qt6"):
        if shutil.which(exe):
            return True
    for env_name in ("QT_DIR", "QT6_DIR", "Qt6_DIR", "CMAKE_PREFIX_PATH"):
        value = os.environ.get(env_name, "")
        for entry in value.split(os.pathsep):
            if entry and ("Qt6" in entry or "qt6" in entry) and Path(entry).exists():
                return True
    for base in (os.environ.get("ProgramFiles", ""), r"C:\Qt", r"D:\Qt"):
        if not base or not Path(base).exists():
            continue
        try:
            for child in Path(base).glob("**/Qt6Config.cmake"):
                return True
        except Exception:
            continue
    return False


def _needs_qt(comp_dir: Path) -> bool:
    """Whether any CMake project in this component actually requires Qt."""
    for cmake_file in comp_dir.rglob("CMakeLists.txt"):
        if any(part in _SKIP_DIRS for part in cmake_file.parts):
            continue
        try:
            text = cmake_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "Qt6" in text or "find_package(Qt" in text or "Qt5" in text:
            return True
    return False


def _unit_for(path: Path) -> Optional[Dict[str, Any]]:
    """Build a project-unit description for one directory, if it has a project."""
    manifests = {
        "node": _has_any(path, _NODE_MANIFESTS),
        "python": _has_any(path, _PYTHON_MANIFESTS),
        "rust": _has_any(path, _RUST_MANIFESTS),
        "java": _has_any(path, _JAVA_MANIFESTS),
        "go": _has_any(path, _GO_MANIFESTS),
        "cmake": _has_any(path, _CMAKE_MANIFESTS),
    }
    languages = [lang for lang, manifest in manifests.items() if manifest]

    # Python package without a manifest (dependency-free toolkits, vendored pkg).
    if not languages:
        packages = _python_package_dirs(path)
        py_files = list(path.glob("*.py"))
        if packages or py_files:
            languages = ["python"]
            manifests["python"] = "(python package)"

    if not languages:
        return None

    dependencies: Dict[str, Optional[bool]] = {}
    if "node" in languages:
        dependencies["node"] = _node_deps_installed(path)
    if "python" in languages:
        if manifests["python"] == "(python package)":
            # A bare package/script directory has no declared environment.
            dependencies["python"] = None
        else:
            dependencies["python"] = _python_deps_installed(path)
    if "rust" in languages:
        dependencies["rust"] = (path / "target").exists()
    if "java" in languages:
        dependencies["java"] = (path / "target").exists()
    if "go" in languages:
        dependencies["go"] = None
    if "cmake" in languages:
        dependencies["cmake"] = (path / "build").exists() or (path / "build-qt").exists()

    return {
        "path": path,
        "languages": languages,
        "manifests": {lang: manifest for lang, manifest in manifests.items() if manifest},
        "dependencies": dependencies,
    }


def _filter_unit(unit: Dict[str, Any], covered: set) -> Optional[Dict[str, Any]]:
    """Drop languages already covered by the root project (workspaces/subpackages)."""
    keep = [lang for lang in unit["languages"] if lang not in covered]
    if not keep:
        return None
    filtered = dict(unit)
    filtered["languages"] = keep
    filtered["manifests"] = {k: v for k, v in unit["manifests"].items() if k in keep}
    filtered["dependencies"] = {k: v for k, v in unit["dependencies"].items() if k in keep}
    return filtered


def _discover_units(comp_dir: Path, duplicates: List[str]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    dup_names = {d.lower() for d in duplicates}
    root_unit = _unit_for(comp_dir)
    covered: set = set()
    if root_unit:
        units.append(root_unit)
        covered = set(root_unit["languages"])

    # Always scan two levels: a root manifest does not make nested subprojects
    # (LibreChat api/client, freellmapi server/client, Pix rust/python) go away.
    depth = 2
    for child in _safe_iterdir(comp_dir):
        if child.name in _SKIP_DIRS or child.name.startswith(".") or child.name.lower() in dup_names:
            continue
        child_unit = _unit_for(child)
        if child_unit:
            units.append(child_unit)
            covered.update(child_unit["languages"])
            continue
        if depth < 2:
            continue
        for grandchild in _safe_iterdir(child):
            if grandchild.name in _SKIP_DIRS or grandchild.name.startswith("."):
                continue
            gc_unit = _unit_for(grandchild)
            if gc_unit:
                units.append(gc_unit)
                covered.update(gc_unit["languages"])
    return units


def _rel(comp_dir: Path, path: Path) -> str:
    try:
        rel = path.relative_to(comp_dir)
        return str(rel) if str(rel) != "." else "."
    except Exception:
        return str(path)


def check_component(sp: Dict[str, Any]) -> Dict[str, Any]:
    name = sp.get("name", "")
    comp_name = sp.get("component", "")
    comp_dir = PLATFORM_ROOT / comp_name
    errors: List[str] = []
    warnings: List[str] = []
    next_steps: List[str] = []
    toolchain: Dict[str, Any] = {}

    if not comp_dir.exists() or not comp_dir.is_dir():
        return {
            "name": name,
            "component": comp_name,
            "path": str(comp_dir),
            "status": "blocked",
            "languages": [],
            "manifests": [],
            "toolchain": {},
            "missing_tools": [],
            "deps_installed": None,
            "errors": [f"component directory not found: {comp_dir}"],
            "warnings": [],
            "next_steps": ["Restore the component directory or fix agents/registry.json."],
            "services": [],
            "nested_duplicates": [],
            "empty_submodules": [],
        }

    duplicates = _nested_duplicates(comp_dir)
    empty_submodules = _empty_submodules(comp_dir)
    root_unit = _unit_for(comp_dir)
    units = _discover_units(comp_dir, duplicates)

    languages: List[str] = []
    manifests: List[str] = []
    for unit in units:
        for lang in unit["languages"]:
            if lang not in languages:
                languages.append(lang)
        for lang, manifest in unit["manifests"].items():
            if manifest == "(python package)":
                entry = f"{_rel(comp_dir, unit['path'])} (python package)"
            else:
                entry = f"{_rel(comp_dir, unit['path'])}/{manifest}"
            if entry not in manifests:
                manifests.append(entry)

    # Source present but its build manifest is missing.
    for parent in [comp_dir] + [u["path"] for u in units]:
        src = parent / "src"
        if src.exists() and any(src.rglob("*.rs")) and not (parent / "Cargo.toml").exists():
            errors.append(f"Rust sources found in {_rel(comp_dir, parent)} but Cargo.toml is missing; the crate cannot build.")
            next_steps.append(f"Restore Cargo.toml (and Cargo.lock) in {_rel(comp_dir, parent)}/.")
            break

    # Node dependencies are installed per component (one workspace tree covers
    # every listed unit), so one check applies to all Node units here.
    component_node_ok = _node_deps_installed(comp_dir)
    for unit in units:
        if "node" in unit["languages"]:
            unit["dependencies"]["node"] = component_node_ok

    # --- dependency installation state (per unit, then aggregate) ---
    known = [v for unit in units for v in unit["dependencies"].values() if v is not None]
    deps_installed: Optional[bool] = all(known) if known else None
    for unit in units:
        where = _rel(comp_dir, unit["path"])
        deps = unit["dependencies"]
        if deps.get("node") is False:
            errors.append(
                f"Node dependencies are not installed for {where}/ "
                "(a truncated node_modules is ignored; npm writes .package-lock.json only on success)."
            )
            next_steps.append("Run `npm run init` from the platform root to install the shared node_modules.")
            cmd = "npm ci" if (unit["path"] / "package-lock.json").exists() else "npm install"
            next_steps.append(f"Per-component install: node scripts/init-platform.js --component {comp_name}")
            next_steps.append(f"Local fallback: cd {comp_name}/{where} && {cmd}")
        if deps.get("python") is False:
            errors.append(
                f"Python dependencies are not installed for {where}/ "
                "(declared module is absent from the shared venv, or no venv exists)."
            )
            next_steps.append("Run `npm run init` from the platform root to install the shared .venv.")
            next_steps.append(f"Per-component install: node scripts/init-platform.js --component {comp_name}")
        if deps.get("rust") is False:
            warnings.append(f"No Rust build output yet in {where}/ (target/ missing).")
            next_steps.append(f"cd {comp_name}/{where} && cargo build")
        if deps.get("java") is False:
            warnings.append(f"Java project has not been built in {where}/ (target/ missing).")
            wrapper = "mvnw.cmd" if (unit["path"] / "mvnw.cmd").exists() else "mvn"
            next_steps.append(f"cd {comp_name}/{where} && {wrapper} package")
        if deps.get("go") is None and "go" in unit["languages"]:
            next_steps.append(f"cd {comp_name}/{where} && go build ./...")

    # --- toolchains ---
    unit_requirements: List[set] = []
    bun_needed: List[str] = []
    for unit in units:
        req = set()
        for lang in unit["languages"]:
            req.update(_TOOLCHAIN_HINTS.get(lang, ()))
        if "node" in unit["languages"]:
            pkg = {}
            where = _rel(comp_dir, unit["path"])
            try:
                pkg = json.loads((unit["path"] / "package.json").read_text(encoding="utf-8"))
            except Exception:
                errors.append(f"package.json is not valid JSON in {where}/.")
            manager = str(pkg.get("packageManager") or "").lower()
            if manager.startswith("bun") or (unit["path"] / "bun.lock").exists():
                req.add("bun")
                bun_needed.append(where)

            engines = pkg.get("engines") or {}
            node_range = engines.get("node") if isinstance(engines, dict) else None
            if node_range:
                installed = _node_version()
                if installed is None:
                    errors.append(f"Node.js is required by {where}/ (engines.node {node_range}) but node was not found on PATH.")
                elif _version_satisfies(node_range, installed) is False:
                    errors.append(
                        f"{where}/ requires Node {node_range}, but Node "
                        f"{'.'.join(str(n) for n in installed)} is on PATH."
                    )
                    next_steps.append(
                        f"Use a Node version satisfying {node_range} for {where}/ "
                        "(the platform standardizes on Node 24)."
                    )
            # engines.bun pins a Bun version even when no packageManager is set.
            bun_engine = engines.get("bun") if isinstance(engines, dict) else None
            if bun_engine:
                have_bun = _bun_version()
                required = _parse_ver(str(bun_engine))
                if have_bun is None:
                    errors.append(f"{where}/ requires Bun {bun_engine} but bun is not on PATH.")
                elif required and _version_satisfies(str(bun_engine), have_bun) is False:
                    warnings.append(
                        f"{where}/ requires Bun {bun_engine}, installed Bun "
                        f"{'.'.join(str(n) for n in have_bun)}."
                    )
                    next_steps.append(f"Use Bun {bun_engine} for {where}/ (bun upgrade / version manager).")

            # An explicit bun packageManager pins a Bun major/minor.
            bun_match = re.match(r"bun@(\d+(?:\.\d+){0,2})", manager)
            if bun_match:
                required_bun = _parse_ver(bun_match.group(1))
                have_bun = _bun_version()
                if have_bun is None:
                    errors.append(f"{where}/ declares {pkg['packageManager']} but Bun is not on PATH.")
                elif required_bun and have_bun[0] != required_bun[0]:
                    warnings.append(
                        f"{where}/ declares {pkg['packageManager']} but Bun "
                        f"{'.'.join(str(n) for n in have_bun)} is installed."
                    )
        if "java" in unit["languages"]:
            if (unit["path"] / "mvnw.cmd").exists() or (unit["path"] / "mvnw").exists():
                req.discard("mvn")
                req.discard("gradle")
        unit_requirements.append(req)

    if any((comp_dir / marker).exists() for marker in ("bun.lock", ".bun-version")):
        bun_needed.append(".")
        if unit_requirements:
            unit_requirements[0].add("bun")
    if bun_needed and "bun" not in languages:
        languages.append("bun")

    required = sorted(set().union(*unit_requirements)) if unit_requirements else []
    root_requirements = unit_requirements[0] if root_unit and unit_requirements else set()
    toolchain = _which_all(required)
    missing_tools = [exe for exe, present in toolchain.items() if not present]
    # Missing tools for the root project always block it. For secondary
    # subprojects they are warnings when another subproject can run
    # (e.g. Pix can run its Python package without a JDK).
    any_unit_runnable = any(all(shutil.which(t) for t in req) for req in unit_requirements)
    for exe in missing_tools:
        if exe in root_requirements or not any_unit_runnable:
            errors.append(f"required toolchain not found on PATH: {exe}")
        else:
            warnings.append(f"optional toolchain not found on PATH: {exe} (needed by some subprojects only)")
    if bun_needed and not shutil.which("bun"):
        next_steps.append("Install Bun (https://bun.sh) — required by " + ", ".join(sorted(set(bun_needed))) + ".")
    if "cmake" in languages and not any(shutil.which(c) for c in ("cl", "g++", "clang", "clang++")):
        warnings.append("CMake project found but no C/C++ compiler (cl/g++/clang) was found on PATH.")
    if _needs_qt(comp_dir) and not _qt_available():
        warnings.append("Qt-based CMake project found but no Qt 6 development install was detected (qmake6/Qt6Config.cmake).")
        next_steps.append("Install Qt 6 and put qmake6 on PATH to build the Qt frontend (e.g. `winget install Qt.QT.6.8`).")

    # --- rust toolchain pins ---
    for unit in units:
        if "rust" not in unit["languages"]:
            continue
        problem = _rust_toolchain_problem(unit["path"])
        if problem:
            where = _rel(comp_dir, unit["path"])
            errors.append(f"Rust toolchain unavailable in {where}/: {problem}.")
            next_steps.append("Install the pinned toolchain: `rustup toolchain install <channel>`.")

    # --- external services the component actually needs ---
    services = _required_services(comp_dir)
    for svc in services:
        if svc["present"]:
            continue
        if svc.get("blocking", True):
            errors.append(
                f"required service '{svc['name']}' is not available ({svc['why']}); "
                "the component cannot run without it."
            )
            if svc["name"] == "mongodb":
                next_steps.append("Start MongoDB locally or set MONGO_URI to a reachable instance.")
        else:
            warnings.append(f"optional service '{svc['name']}' is not available ({svc['why']}).")
            if svc["name"] == "wrangler":
                next_steps.append("Install Wrangler for deployment (`npm i -w agentic-inbox wrangler`) and authenticate to Cloudflare.")

    # --- repository hygiene ---
    if duplicates:
        warnings.append(
            "nested duplicate copy detected (" + ", ".join(duplicates) + "); "
            "it doubles the tree and can confuse builds/imports."
        )
        next_steps.append(f"Review and remove the nested duplicate(s) in {comp_name}/ after confirming they are redundant.")

    for rel in empty_submodules:
        errors.append(f"git submodule '{rel}' is not checked out (directory is empty/missing).")
        next_steps.append(f"cd {comp_name} && git submodule update --init --recursive")

    if not units:
        warnings.append("No recognizable project manifest or entrypoint found.")

    status = "blocked" if errors else ("needs_setup" if warnings else "ready")
    return {
        "name": name,
        "component": comp_name,
        "path": str(comp_dir),
        "status": status,
        "languages": languages,
        "manifests": manifests,
        "toolchain": toolchain,
        "missing_tools": missing_tools,
        "deps_installed": deps_installed,
        "errors": errors,
        "warnings": warnings,
        "next_steps": next_steps,
        "services": services,
        "nested_duplicates": duplicates,
        "empty_submodules": empty_submodules,
        "units": [
            {
                "path": _rel(comp_dir, unit["path"]),
                "languages": unit["languages"],
                "manifests": unit["manifests"],
                "dependencies": unit["dependencies"],
            }
            for unit in units
        ],
    }


def readiness() -> Dict[str, Any]:
    """Readiness for every registered subplatform."""
    items = [check_component(sp) for sp in subplatforms()]
    summary = {"total": len(items), "ready": 0, "needs_setup": 0, "blocked": 0}
    for item in items:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {"summary": summary, "components": items}


def component_readiness(name: str) -> Optional[Dict[str, Any]]:
    name_l = (name or "").lower()
    for sp in subplatforms():
        if sp.get("name", "").lower() == name_l or sp.get("component", "").lower() == name_l:
            return check_component(sp)
    return None
