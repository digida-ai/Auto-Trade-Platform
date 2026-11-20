import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config import ALLOWED_ROOT

# Force PowerShell (5.1 on Windows) and any child Python process to speak UTF-8.
# Without this the console/OEM codepage is used and non-ASCII output raises
# UnicodeDecodeError, which previously failed otherwise-successful steps.
_PS_PREAMBLE = (
    "$ProgressPreference = 'SilentlyContinue'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$PSDefaultParameterValues['Get-Content:Encoding'] = 'utf8'; "
    "$env:PYTHONIOENCODING = 'utf-8'; "
    "$env:PYTHONUTF8 = '1'; "
)


def _resolve_within_root(path: Optional[str], default: Path) -> Path:
    if not path:
        p = default
    else:
        p = Path(path)
        if not p.is_absolute():
            p = (default / p).resolve()
        else:
            p = p.resolve()
    root = ALLOWED_ROOT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise ValueError(f"Path outside allowed platform root: {p}")
    return p


def run_shell(command: str, cwd: Optional[str] = None, timeout: int = 120) -> Dict[str, Any]:
    """Execute a shell command (PowerShell on Windows) within the platform root."""
    return run_shell_streaming(command, cwd=cwd, timeout=timeout, on_update=None)


def run_shell_streaming(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
    on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Execute a shell command with live output updates."""
    workdir = _resolve_within_root(cwd, ALLOWED_ROOT)
    start = time.time()
    output_chunks = []
    last_push = 0.0

    def snapshot(ok=None, exit_code=None, done=False, err=None):
        text = "".join(output_chunks)
        if len(text) > 20000:
            text = text[-20000:]
        res = {
            "ok": ok,
            "exit_code": exit_code,
            "stdout": text,
            "stderr": err or "",
            "cwd": str(workdir),
            "done": done,
            "elapsed_s": round(time.time() - start, 2),
        }
        return res

    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PREAMBLE + command],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            universal_newlines=True,
        )
    except Exception as e:
        res = snapshot(ok=False, exit_code=None, done=True, err=str(e))
        if on_update:
            on_update(res)
        return res

    try:
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if line:
                output_chunks.append(line)
            now = time.time()
            if on_update and (now - last_push >= 0.4 or (line and len(output_chunks) <= 5)):
                last_push = now
                on_update(snapshot(ok=None, exit_code=None, done=False))
            if not line and proc.poll() is not None:
                break
            if timeout and (now - start) > timeout:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                res = snapshot(ok=False, exit_code=None, done=True, err=f"Timed out after {timeout}s")
                if on_update:
                    on_update(res)
                return res
        # drain remaining
        try:
            rest = proc.stdout.read()
            if rest:
                output_chunks.append(rest)
        except Exception:
            pass
        code = proc.returncode if proc.returncode is not None else proc.wait()
        res = snapshot(ok=(code == 0), exit_code=code, done=True)
        if on_update:
            on_update(res)
        return res
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        res = snapshot(ok=False, exit_code=None, done=True, err=str(e))
        if on_update:
            on_update(res)
        return res


def read_text(path: str, base: Optional[str] = None) -> Dict[str, Any]:
    p = _resolve_within_root(path, Path(base) if base else ALLOWED_ROOT)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > 200000:
            text = text[:200000] + "\n...[truncated]"
        return {"ok": True, "path": str(p), "content": text}
    except Exception as e:
        return {"ok": False, "path": str(p), "error": str(e)}


def write_text(path: str, content: str, base: Optional[str] = None) -> Dict[str, Any]:
    p = _resolve_within_root(path, Path(base) if base else ALLOWED_ROOT)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"ok": False, "path": str(p), "error": str(e)}


def list_dir(path: Optional[str] = None, base: Optional[str] = None) -> Dict[str, Any]:
    p = _resolve_within_root(path or ".", Path(base) if base else ALLOWED_ROOT)
    try:
        entries = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
            })
        return {"ok": True, "path": str(p), "entries": entries}
    except Exception as e:
        return {"ok": False, "path": str(p), "error": str(e)}
