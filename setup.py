#!/usr/bin/env python3
"""claudefree Setup — Cross-platform TUI

Run:  python setup.py
      uv run python setup.py      # if using uv

Replaces the legacy setup.sh (Linux/macOS) and setup.cmd (Windows).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from getpass import getpass
from pathlib import Path
from typing import NoReturn

# ── Constants ────────────────────────────────────────────────────────────

PROVIDERS_URL = "https://models.dev/api.json"
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
ENV_FILE = SCRIPT_DIR / ".env"

# ── ANSI styling (works on all modern terminals, including Windows 10+) ──

class S:
    RST = "\033[0m"
    BLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GRN = "\033[32m"
    YLW = "\033[33m"
    BLU = "\033[34m"
    MAG = "\033[35m"
    CYN = "\033[36m"

# ── Print helpers ────────────────────────────────────────────────────────

COLS = shutil.get_terminal_size().columns
COLS = min(COLS, 80)


def print_banner() -> None:
    """Render the box-drawn header banner."""
    print(f"{S.CYN}")
    print(f"╔{S.CYN}{'═' * (COLS - 2)}╗")
    print(f"║{S.CYN}{' ' * (COLS - 2)}║")
    line = "✨ claudefree Setup ✨"
    pad = (COLS - 2 - len(line)) // 2
    print(f"║{' ' * pad}{line}{' ' * (COLS - 2 - pad - len(line))}║")
    line = "Free AI for Claude Code — Multi-Provider"
    pad = (COLS - 2 - len(line)) // 2
    print(f"║{' ' * pad}{line}{' ' * (COLS - 2 - pad - len(line))}║")
    print(f"║{''.ljust(COLS - 2)}║")
    print(f"╚{S.CYN}{'═' * (COLS - 2)}╝")
    print(f"{S.RST}")


def print_step(n: int, total: int, desc: str) -> None:
    print(f"\n  {S.BLU}◉{S.RST} {S.BLD}Step {n} of {total}{S.RST}  {desc}")


def _print(sym: str, color: str, msg: str) -> None:
    print(f"  {msg} {color}{sym}{S.RST}")


def ok(msg: str) -> None:
    _print("✓", S.GRN, msg)


def info(msg: str) -> None:
    _print("ℹ", S.CYN, msg)


def warn(msg: str) -> None:
    _print("⚠", S.YLW, msg)


def error(msg: str) -> None:
    _print("✗", S.RED, msg)


def sub(msg: str) -> None:
    """Sub-step in-progress indicator — removed; spinner handles this."""
    pass  # spinner animation replaces the static [..] line


def sub_ok(msg: str) -> None:
    print(f"    {msg} {S.GRN}✓{S.RST}")


def sub_err(msg: str) -> None:
    print(f"    {msg} {S.RED}✗{S.RST}")


def sub_warn(msg: str) -> None:
    print(f"    {msg} {S.YLW}⚠{S.RST}")


def divider() -> None:
    print(f"  {S.DIM}{'─' * 36}{S.RST}")


# ── Spinner ──────────────────────────────────────────────────────────────

_SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠏⠎"


class Spinner:
    """Thread-based spinner for long operations."""

    def __init__(self, msg: str = ""):
        self.msg = msg
        self._running = False
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, success: bool = True) -> None:
        if not self._running and self._thread is None:
            return  # idempotent — already stopped
        self._running = False
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        sym = "✓" if success else "✗"
        color = S.GRN if success else S.RED
        sys.stdout.write(f"\r    {self.msg} {color}{sym}{S.RST}\n")
        sys.stdout.flush()

    def _spin(self) -> None:
        i = 0
        while self._running:
            ch = _SPINNER_CHARS[i % len(_SPINNER_CHARS)]
            sys.stdout.write(f"\r    {S.CYN}{ch}{S.RST} {self.msg}")
            sys.stdout.flush()
            i += 1
            self._spin_sleep(0.1)

    @staticmethod
    def _spin_sleep(secs: float) -> None:
        """Thread-safe sleep without blocking signals."""
        threading.Event().wait(secs)


# ── Terminal selection helpers ───────────────────────────────────────────

_HAS_FZF = bool(shutil.which("fzf"))
_HAS_FZY = bool(shutil.which("fzy"))

if _HAS_FZF:
    _FUZZY_CMD = "fzf"
elif _HAS_FZY:
    _FUZZY_CMD = "fzy"
else:
    _FUZZY_CMD = None


def _use_fuzzy() -> bool:
    return _FUZZY_CMD is not None and sys.stdin.isatty()


def fuzzy_select(options: list[str], prompt: str = "Search", **kwargs: str) -> str | None:
    """Use fzf/fzy to let the user filter-select from options."""
    if not _use_fuzzy():
        return None
    input_str = "\n".join(options)
    if _FUZZY_CMD == "fzy":
        args = ["fzy", "-p", f"{prompt}> "]
    else:
        args = ["fzf", "--prompt", f"{prompt}> "]
        for flag, val in kwargs.items():
            args.extend([f"--{flag.replace('_', '-')}", val])
    try:
        # stdin=PIPE sends the options list; stdout=PIPE captures the
        # selection; stderr is NOT redirected so fzf/fzy can draw their
        # interactive TUI on the terminal (capture_output=True would pipe
        # stderr away, making the TUI invisible = batch/instant-select mode).
        result = subprocess.run(
            args, input=input_str,
            stdout=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def numbered_menu(options: list[str], prompt: str = "") -> str | None:
    """Fallback numbered menu when fzf/fzy isn't available."""
    for i, opt in enumerate(options, 1):
        print(f"    {S.CYN}{i:3d}{S.RST}) {opt}")
    print()
    label = f"    {prompt} (1-{len(options)}): " if prompt else f"    Enter number (1-{len(options)}): "
    try:
        raw = input(label).strip()
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except (ValueError, EOFError, KeyboardInterrupt):
        pass
    return None


# ── Shell config detection ───────────────────────────────────────────────

def detect_shell_rc() -> Path | None:
    """Return path to .zshrc or .bashrc, or None."""
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    if "zsh" in shell:
        return home / ".zshrc"
    if "bash" in shell:
        return home / ".bashrc"
    # Windows: no shell rc to modify
    if sys.platform == "win32":
        return None
    return home / ".bashrc"


def is_already_configured(rc: Path | None) -> bool:
    """Check if ANTHROPIC environment vars are already set in shell rc."""
    if rc is None or not rc.exists():
        return False
    try:
        text = rc.read_text(encoding="utf-8", errors="replace")
        return "ANTHROPIC_AUTH_TOKEN" in text and "ANTHROPIC_BASE_URL" in text
    except OSError:
        return False


# ── Package manager helpers ──────────────────────────────────────────────

if sys.platform == "win32":
    _HOME_BIN = Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin"
else:
    _HOME_BIN = Path.home() / ".local" / "bin"


def _run_step(msg: str, fn, *args: object, **kwargs: object) -> object | None:
    """Run a function with a dotted spinner. On failure, print ✗ error + cause."""
    spinner = Spinner(msg)
    spinner.start()
    try:
        result = fn(*args, **kwargs)
        spinner.stop(success=True)
        return result
    except Exception as exc:
        spinner.stop(success=False)
        error(f"{msg}")
        warn(f"Cause: {type(exc).__name__} — {exc}")
        return None


def _run_cmd(msg: str, args: list[str], timeout: int = 60) -> bool:
    """Run a command with spinner and visible output. Returns True on success."""
    spinner = Spinner(msg)
    spinner.start()
    try:
        result = subprocess.run(
            args,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            spinner.stop(success=True)
            return True
        # Extract meaningful error from stderr
        cause = (result.stderr or "").strip() or "exit code %d" % result.returncode
        spinner.stop(success=False)
        warn(f"Cause: {cause}")
        return False
    except FileNotFoundError:
        spinner.stop(success=False)
        warn(f"Cause: command not found — {args[0]}")
        return False
    except subprocess.TimeoutExpired:
        spinner.stop(success=False)
        warn(f"Cause: {msg} timed out after {timeout}s")
        return False
    except Exception as exc:
        spinner.stop(success=False)
        warn(f"Cause: {type(exc).__name__} — {exc}")
        return False


def _check_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _kill_process_group(pid: int) -> None:
    """Kill an entire process group (children included)."""
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, PermissionError, AttributeError):
        try:
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError):
            pass


def _detect_os_release() -> str:
    """Read /etc/os-release or /usr/lib/os-release, return normalized text."""
    for p in ("/etc/os-release", "/usr/lib/os-release"):
        candidate = Path(p)
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace").lower()
    return ""


def _check_tools(*tools: str) -> bool:
    """Check that every tool in *tools is available on PATH."""
    return all(shutil.which(t) for t in tools)


def _install_fzy() -> bool:
    """Install fzy fuzzy finder."""
    if _check_cmd("fzy"):
        return True

    if sys.platform == "darwin":
        ok = _run_cmd("Installing fzy via Homebrew", ["brew", "install", "fzy"])
        if ok:
            return True
    else:
        # Detect distro and install
        text = _detect_os_release()
        if not text:
            sub_warn("Could not detect Linux distro — skipping fzy via package manager")
        else:
            ok = False
            if "fedora" in text:
                ok = _run_cmd("Installing fzy via dnf", ["sudo", "dnf", "install", "-y", "fzy"])
            elif "debian" in text or "ubuntu" in text:
                _run_cmd("Updating apt cache", ["sudo", "apt-get", "update", "-qq"])
                ok = _run_cmd("Installing fzy via apt", ["sudo", "apt-get", "install", "-y", "-qq", "fzy"])
            elif "arch" in text:
                ok = _run_cmd("Installing fzy via pacman", ["sudo", "pacman", "-S", "fzy", "--noconfirm"])
            elif "alpine" in text:
                ok = _run_cmd("Installing fzy via apk", ["sudo", "apk", "add", "fzy"])
            else:
                sub_warn("Unsupported distro — fzy not in package manager")
            if ok:
                return True

    # Try git build as fallback
    if not shutil.which("git"):
        sub_warn("git not found — can't build fzy from source")
        return False
    if not shutil.which("make") or not shutil.which("cc"):
        sub_warn("Build tools (make, cc) not found — can't build fzy from source")
        return False

    import tempfile
    tmp = tempfile.TemporaryDirectory()
    spinner = Spinner("Building fzy from source (git clone + make)...")
    spinner.start()
    try:
        tmp_path = Path(tmp.name)
        r1 = subprocess.run(
            ["git", "clone", "https://github.com/jhawthorn/fzy.git", str(tmp_path / "fzy")],
            capture_output=True, text=True, timeout=120,
        )
        if r1.returncode != 0:
            spinner.stop(success=False)
            cause = (r1.stderr or "").strip()[:80] or "git clone failed"
            warn(f"Cause: {cause}")
            return False
        fzy_dir = tmp_path / "fzy"
        r2 = subprocess.run(["make", "-s"], cwd=str(fzy_dir), capture_output=True, text=True, timeout=60)
        if r2.returncode != 0:
            spinner.stop(success=False)
            cause = (r2.stderr or "").strip()[:80] or "make failed"
            warn(f"Cause: {cause}")
            return False
        r3 = subprocess.run(["sudo", "make", "install"], cwd=str(fzy_dir), capture_output=True, text=True, timeout=30)
        if r3.returncode != 0:
            spinner.stop(success=False)
            cause = (r3.stderr or "").strip()[:80] or "make install failed"
            warn(f"Cause: {cause}")
            return False
        spinner.stop(success=True)
    except Exception as exc:
        spinner.stop(success=False)
        warn(f"Cause: {type(exc).__name__} — {exc}")
        return False
    finally:
        tmp.cleanup()
    return True


def _download_with_progress(url: str, dest: Path, chunk_size: int = 65536) -> bool:
    """Download a file with progress dots, returns True on success."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "claudefree-setup/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            dots = 0
            sys.stdout.write(f"      Downloading ...")
            sys.stdout.flush()
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Show a dot for every ~256 KB to give visual progress
                    if total:
                        ticks = int(downloaded / max(total / 40, 1))
                        while dots < ticks and dots < 40:
                            sys.stdout.write(".")
                            dots += 1
                        sys.stdout.flush()
            sys.stdout.write(f" ({downloaded / 1024:.0f} KB)\n")
            sys.stdout.flush()
        return True
    except Exception:
        return False


def _install_fzf() -> bool:
    """Install fzf fuzzy finder (Windows)."""
    if _check_cmd("fzf.exe"):
        return True

    # Prefer direct download from GitHub (fastest, no winget index download)
    import platform as _platform
    arch = _platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        arch_part = "windows_amd64"
    elif arch in ("arm64", "aarch64"):
        arch_part = "windows_arm64"
    else:
        arch_part = "windows_386"
    fzf_url = (
        "https://github.com/junegunn/fzf/releases/download/v0.60.3/"
        f"fzf-0.60.3-{arch_part}.zip"
    )
    win_bin = _HOME_BIN
    win_bin.mkdir(parents=True, exist_ok=True)
    zip_path = win_bin / "fzf.zip"
    sub("Downloading fzf from GitHub...")
    if _download_with_progress(fzf_url, zip_path):
        import zipfile
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extract("fzf.exe", win_bin)
            zip_path.unlink()
            if _check_cmd("fzf.exe"):
                sub_ok("fzf installed from GitHub release")
                return True
        except Exception:
            pass

    # Fallback: package managers with visible output
    for mgr in ("winget", "scoop", "choco"):
        if _check_cmd(mgr):
            sub(f"Installing fzf via {mgr}...")
            try:
                if mgr == "winget":
                    args = [mgr, "install", "fzf",
                            "--accept-package-agreements", "--accept-source-agreements"]
                elif mgr == "scoop":
                    args = [mgr, "install", "fzf", "-y"]
                else:  # choco
                    args = [mgr, "install", "fzf", "-y"]
                with subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True,
                ) as proc:
                    # Show first few lines so user sees progress
                    shown = 0
                    for line in proc.stdout:  # type: ignore[union-attr]
                        if shown < 3:
                            sys.stdout.write(f"        {line}")
                            shown += 1
                        sys.stdout.flush()
                    proc.wait(timeout=300)
                if _check_cmd("fzf.exe"):
                    sub_ok(f"fzf installed via {mgr}")
                    return True
                sub_warn(f"{mgr} failed — trying next...")
            except (subprocess.TimeoutExpired, OSError):
                sub_warn(f"{mgr} timed out — trying next...")
    return False


# ── Step implementations ─────────────────────────────────────────────────

def fetch_providers() -> dict | NoReturn:
    """Download and parse the providers JSON."""
    spinner = Spinner("Downloading provider list...")
    spinner.start()
    data: bytes | None = None
    try:
        import urllib.request
        req = urllib.request.Request(PROVIDERS_URL, headers={
            "User-Agent": "claudefree-setup/1.0",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        providers = json.loads(data)
        spinner.stop(success=True)
    except Exception as exc:
        spinner.stop(success=False)
        sys.stdout.write(f"\r{S.RST}\n")
        error(f"Failed to fetch providers: {exc}")
        sys.exit(1)
    sys.stdout.write(f"\r{'':{COLS}}\r")  # clear spinner line (works on all terminals)
    size_kb = len(data) / 1024  # type: ignore[arg-type]  # data is guaranteed set after successful try
    sub_ok(f"Provider list downloaded ({size_kb:.0f} KB)")
    return providers


def pick_provider(providers: dict) -> str | NoReturn:
    """Let user select a provider."""
    names = sorted(providers.keys())
    choice = fuzzy_select(names, prompt="Provider")
    if choice is None:
        choice = numbered_menu(names, prompt="Enter provider number")
    if not choice:
        error("No provider selected.")
        sys.exit(1)
    ok(f"Selected: {S.BLD}{choice}{S.RST}")
    return choice


def collect_api_key(provider: str) -> str | NoReturn:
    """Get API key from .env or prompt the user."""
    upper = provider.upper().replace("-", "_")
    env_var = f"{upper}_API_KEY"

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(env_var + "="):
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val:
                    ok("API key found in .env")
                    return val

    print()
    try:
        key = getpass(f"    {S.BLD}Enter API key for {S.CYN}{provider}{S.RST}:\n    {S.DIM}(input hidden){S.RST} ")
    except (EOFError, KeyboardInterrupt):
        error("Cancelled.")
        sys.exit(1)

    if not key.strip():
        error("API key cannot be empty.")
        sys.exit(1)
    ok("API key received")
    return key.strip()


def pick_models(providers: dict, provider: str) -> dict[str, str]:
    """Let user select models for each tier."""
    model_names = sorted(providers[provider]["models"].keys())
    info(f"{len(model_names)} models available")

    def pick_one(tier: str) -> str:
        print()
        print(f"    {S.BLU}── Model for {S.BLD}{tier}{S.RST}{S.BLU} ──{S.RST}")
        print(f"      {S.DIM} 0{S.RST}) [SAME_AS_DEFAULT]")
        print(f"      {S.DIM} 1{S.RST}) [CUSTOM_MODEL]")
        shown = 0
        for name in model_names:
            if shown >= 10:
                break
            print(f"      {S.DIM}{shown + 2:2d}{S.RST}) {name}")
            shown += 1
        if len(model_names) > 10:
            print(f"      {S.DIM}... and {len(model_names) - 10} more available{S.RST}")
        fzf_opts = ["[SAME_AS_DEFAULT]", "[CUSTOM_MODEL]"] + model_names
        choice = fuzzy_select(fzf_opts, prompt=f"Search {tier}")
        if choice == "[CUSTOM_MODEL]":
            return input(f"      {S.BLD}Custom name{S.RST}: ").strip()
        if choice:
            return choice

        # fzf not available — fallback to numbered menu
        try:
            raw = input(f"\n      {S.BLD}Selection{S.RST} (0-{len(model_names) + 1}): ").strip()
            if raw == "0":
                return "[SAME_AS_DEFAULT]"
            if raw == "1":
                return input(f"      {S.BLD}Custom name{S.RST}: ").strip()
            idx = int(raw) - 2
            if 0 <= idx < len(model_names):
                return model_names[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        warn("Invalid — using [SAME_AS_DEFAULT]")
        return "[SAME_AS_DEFAULT]"

    models = {
        "DEFAULT": pick_one("DEFAULT"),
        "OPUS": pick_one("OPUS"),
        "SONNET": pick_one("SONNET"),
        "HAIKU": pick_one("HAIKU"),
    }
    divider()
    print(f"    {S.GRN}DEFAULT{S.RST} → {models['DEFAULT']}")
    print(f"    {S.MAG}OPUS{S.RST}    → {models['OPUS']}")
    print(f"    {S.YLW}SONNET{S.RST}   → {models['SONNET']}")
    print(f"    {S.CYN}HAIKU{S.RST}    → {models['HAIKU']}")
    sub_ok("Models configured")
    return models


def save_config(provider: str, api_key: str, models: dict[str, str]) -> None:
    """Write config.json and merge/update .env."""
    msg = "Writing configuration files..."
    spinner = Spinner(msg)
    spinner.start()
    try:
        CONFIG_FILE.write_text(json.dumps({
            "provider": provider,
            "model_default": models["DEFAULT"],
            "model_opus": models["OPUS"],
            "model_sonnet": models["SONNET"],
            "model_haiku": models["HAIKU"],
        }, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        spinner.stop(success=False)
        error(f"Failed to write {CONFIG_FILE}")
        warn(f"Cause: {exc}")
        sys.exit(1)

    env_key = provider.upper().replace("-", "_") + "_API_KEY"
    updates = {env_key: api_key, "ANTHROPIC_AUTH_TOKEN": "God"}

    # Read existing .env (if any) and merge — preserving user's other keys
    new_lines = []
    seen_keys: set[str] = set()
    if ENV_FILE.exists():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    k = stripped.split("=", 1)[0].strip()
                    if k in updates:
                        new_lines.append(f'{k}="{updates[k]}"')
                        seen_keys.add(k)
                        continue
                new_lines.append(line)
        except OSError as exc:
            spinner.stop(success=False)
            error(f"Failed to read {ENV_FILE}")
            warn(f"Cause: {exc}")
            sys.exit(1)
    # Append any new keys not already in the file
    for k, v in updates.items():
        if k not in seen_keys:
            new_lines.append(f'{k}="{v}"')

    # Prepend header if creating new file
    if not ENV_FILE.exists():
        new_lines.insert(0, "# claudefree credentials")

    try:
        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        # Restrict permissions on Unix; best-effort on Windows (may be no-op)
        try:
            ENV_FILE.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
    except OSError as exc:
        spinner.stop(success=False)
        error(f"Failed to write {ENV_FILE}")
        warn(f"Cause: {exc}")
        sys.exit(1)

    spinner.stop(success=True)
    info(f"Config:  {S.DIM}{CONFIG_FILE}{S.RST}")
    info(f"Secrets: {S.DIM}{ENV_FILE}{S.RST}")


def setup_shell_env(rc: Path | None, already_configured: bool) -> None:
    """Add ANTHROPIC_* environment variables to shell rc (or Windows registry)."""
    if already_configured:
        info("Shell environment already configured — skipped")
        return

    if sys.platform == "win32":
        print()
        info("Setting ANTHROPIC environment variables (Windows)...")
        for var, val in [("ANTHROPIC_AUTH_TOKEN", "God"),
                         ("ANTHROPIC_BASE_URL", "http://localhost:16324")]:
            r = subprocess.run(["setx", var, val], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                err = (r.stderr or "").strip()[:100] or f"setx {var} failed"
                warn(f"Cause: {err}")
        sub_ok("Added to user environment variables")
        info("Restart your terminal for changes to take effect")
        return

    if rc is None:
        info("No shell config file found — skipping")
        return

    msg = f"Adding ANTHROPIC vars to {rc.name}..."
    spinner = Spinner(msg)
    spinner.start()
    try:
        if rc.exists():
            backup = rc.with_suffix(rc.suffix + ".backup")
            shutil.copy2(rc, backup)

        with rc.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n# claudefree Configuration\n")
            fh.write('export ANTHROPIC_AUTH_TOKEN="God"\n')
            fh.write('export ANTHROPIC_BASE_URL="http://localhost:16324"\n')
        spinner.stop(success=True)
    except (OSError, PermissionError) as exc:
        spinner.stop(success=False)
        error(f"Failed to write to {rc}")
        warn(f"Cause: {exc}")
        info(f"Manual fix: add these lines to {rc}:")
        print(f'    export ANTHROPIC_AUTH_TOKEN="God"')
        print(f'    export ANTHROPIC_BASE_URL="http://localhost:16324"')
        info(f"Then run: source {rc.name}")

    info(f"Run: {S.BLD}source {rc.name}{S.RST}  (or restart terminal)")


def install_start_server() -> None:
    """Install claude-start-server to PATH."""
    print()
    msg = "Installing claude-start-server to PATH..."
    spinner = Spinner(msg)
    spinner.start()
    try:
        _HOME_BIN.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        spinner.stop(success=False)
        error("Failed to create ~/.local/bin")
        warn(f"Cause: {exc}")
        return

    if sys.platform == "win32":
        dest_bat = _HOME_BIN / "claude-start-server.bat"
        try:
            dest_bat.write_text((
                '@echo off\n'
                'setlocal\n'
                f'set "DIR={SCRIPT_DIR}"\n'
                'cd /d "%DIR%"\n'
                'if exist "%DIR%\\.venv\\Scripts\\python.exe" (\n'
                '    "%DIR%\\.venv\\Scripts\\python.exe" -m cli.entrypoints %*\n'
                ') else (\n'
                '    python -m cli.entrypoints %*\n'
                ')'
            ), encoding="utf-8")
        except OSError as exc:
            spinner.stop(success=False)
            error("Failed to write claude-start-server.bat")
            warn(f"Cause: {exc}")
            return

        # Add ~\.local\bin to user PATH via PowerShell (safer than setx)
        ps_add_path = (
            f'$p = [Environment]::GetEnvironmentVariable("PATH","User");'
            f'if ($p -notlike "*{_HOME_BIN}*") {{'
            f'  [Environment]::SetEnvironmentVariable("PATH",$p+";{_HOME_BIN}","User")'
            f'}}'
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_add_path],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            err = (r.stderr or "").strip()[:100]
            warn(f"PATH auto-add failed: {err}")
            info(f"Add manually: setx PATH \"%PATH%;{_HOME_BIN}\"")
        spinner.stop(success=True)
        sub_ok("claude-start-server.bat installed")
        info("Restart terminal to use 'claude-start-server'")
        return

    src = SCRIPT_DIR / "claude-start-server"
    dest = _HOME_BIN / "claude-start-server"
    if not src.exists():
        spinner.stop(success=False)
        warn(f"Cause: claude-start-server not found at {src}")
        return

    try:
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.symlink_to(src)
        mode = "symlink"
    except (OSError, NotImplementedError) as exc1:
        try:
            shutil.copy2(src, dest)
            mode = "copy"
        except OSError as exc2:
            spinner.stop(success=False)
            error("Failed to install claude-start-server")
            warn(f"Cause: symlink failed ({exc1}); copy also failed ({exc2})")
            return
    spinner.stop(success=True)
    sub_ok(f"claude-start-server installed to ~/.local/bin ({mode})")


def _find_claude() -> str | None:
    """Locate claude CLI — checks PATH and common npm install locations."""
    # Check PATH first
    found = shutil.which("claude")
    if found:
        return found
    # On Windows, npm installs to %APPDATA%\npm — check there too
    if sys.platform == "win32":
        for base in (os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")):
            for name in ("claude", "claude.cmd", "claude.exe"):
                candidate = Path(base) / "npm" / name
                if candidate.exists():
                    return str(candidate)
    return None


def check_claude_cli() -> None:
    """Check for claude CLI, offer to install if missing."""
    print()
    msg = "Checking Claude Code CLI..."
    spinner = Spinner(msg)
    spinner.start()
    claude_path = _find_claude()
    if claude_path:
        try:
            ver = subprocess.run(
                [claude_path, "--version"], capture_output=True, text=True,
                timeout=10,
            ).stdout.strip() or "installed"
            spinner.stop(success=True)
            sub_ok(f"claude CLI found ({ver})")
            return
        except (FileNotFoundError, OSError):
            pass  # binary exists but can't run

    spinner.stop(success=False)
    warn("claude CLI not found \u2014 installing...")

    ok = False
    if sys.platform == "win32":
        # Windows: try PowerShell installer first
        ps_spinner = Spinner("Installing via PowerShell...")
        ps_spinner.start()
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "irm https://claude.ai/install.ps1 | iex"],
                timeout=300, capture_output=True, text=True,
            )
            if result.returncode == 0:
                ps_spinner.stop(success=True)
                ok = True
            else:
                err = (result.stderr or "").strip()[:100] or "non-zero exit"
                ps_spinner.stop(success=False)
                warn(f"Cause: PowerShell installer: {err}")
        except subprocess.TimeoutExpired:
            ps_spinner.stop(success=False)
            warn("Cause: PowerShell installer timed out after 300s")
        except OSError as exc:
            ps_spinner.stop(success=False)
            warn(f"Cause: PowerShell not available \u2014 {exc}")

        # Fallback to winget
        if not ok:
            w_spinner = Spinner("Trying winget...")
            w_spinner.start()
            try:
                result = subprocess.run(
                    ["winget", "install", "Anthropic.ClaudeCode",
                     "--accept-package-agreements", "--accept-source-agreements"],
                    timeout=300, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    w_spinner.stop(success=True)
                    ok = True
                else:
                    err = (result.stderr or "").strip()[:100] or "winget failed"
                    w_spinner.stop(success=False)
                    warn(f"Cause: {err}")
            except subprocess.TimeoutExpired:
                w_spinner.stop(success=False)
                warn("Cause: winget timed out after 300s")
            except OSError as exc:
                w_spinner.stop(success=False)
                warn(f"Cause: winget not available \u2014 {exc}")
    else:
        # macOS/Linux/WSL: curl installer
        curl_spinner = Spinner("Installing via curl | bash...")
        curl_spinner.start()
        try:
            proc = subprocess.Popen(
                ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            try:
                stdout_data, stderr_data = proc.communicate(timeout=300)
                if proc.returncode == 0:
                    curl_spinner.stop(success=True)
                    ok = True
                else:
                    err = (stderr_data or "").strip()[:100] or f"exit code {proc.returncode}"
                    curl_spinner.stop(success=False)
                    warn(f"Cause: {err}")
            except subprocess.TimeoutExpired:
                _kill_process_group(proc.pid)
                proc.wait()
                curl_spinner.stop(success=False)
                warn("Cause: curl installer timed out after 300s")
        except OSError as exc:
            curl_spinner.stop(success=False)
            warn(f"Cause: {exc}")

    if ok and _check_cmd("claude"):
        sub_ok("claude installed")
    else:
        sub_err("claude installation failed \u2014 install manually from https://claude.ai")


def show_summary(provider: str, models: dict[str, str]) -> None:
    """Render the final summary dashboard."""
    w = COLS - 4
    print(f"\n{S.GRN}")
    print(f"╔{'═' * (COLS - 2)}╗")
    label = "Setup Complete ✓"
    pad = (COLS - 2 - len(label)) // 2
    print(f"║{' ' * pad}{S.BLD}{S.GRN}{label}{S.RST}{S.GRN}{' ' * (COLS - 2 - pad - len(label))}║")
    print(f"╠{'═' * (COLS - 2)}╣")
    for key, val in [("Provider", provider),
                     ("Default Model", models["DEFAULT"]),
                     ("Opus Model", models["OPUS"]),
                     ("Sonnet Model", models["SONNET"]),
                     ("Haiku Model", models["HAIKU"])]:
        print(f"║  {S.BLD}{key:<19}{S.RST}{S.GRN} {val:{w}}{S.RST}{S.GRN}║")
    print(f"╠{'═' * (COLS - 2)}╣")
    print(f"║  {S.DIM}Config  {S.RST}{S.GRN} {CONFIG_FILE!s:{w - 8}}{S.RST}{S.GRN}║")
    print(f"║  {S.DIM}Secrets {S.RST}{S.GRN} {ENV_FILE!s:{w - 8}}{S.RST}{S.GRN}║")
    print(f"╠{'═' * (COLS - 2)}╣")
    print(f"║  {S.BLD}Next Steps:{S.RST}{S.GRN}{' ' * (COLS - 12)}║{S.RST}")
    print(f"║  {S.CYN}1.{S.RST} Start proxy → {S.BLD}claude-start-server{S.RST}{S.GRN}{' ' * 9}║{S.RST}")
    print(f"║  {S.CYN}2.{S.RST} Run Claude  → {S.BLD}claude{S.RST}{S.GRN}{' ' * 14}║{S.RST}")
    print(f"╚{'═' * (COLS - 2)}╝")
    print(f"{S.RST}")


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    global _HAS_FZF, _HAS_FZY, _FUZZY_CMD

    print_banner()
    # Determine if already configured
    rc = detect_shell_rc()
    already_configured = is_already_configured(rc)

    if already_configured:
        ok("Shell env already configured — skipping environment setup")
    else:
        info("Shell env not configured — will configure at the end")

    TOTAL = 5

    # ── Step 1: Prerequisites ────────────────────────────────────────────
    print_step(1, TOTAL, "Checking prerequisites")
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    sub_ok(f"Python {py_ver}")

    if sys.version_info < (3, 11):
        error(f"Python 3.11+ required (found {py_ver})")
        info("Upgrade Python: https://www.python.org/downloads/")
        sys.exit(1)

    curl_ok = _check_cmd("curl")
    if curl_ok:
        sub_ok("curl found")
    else:
        sub_warn("curl not found — will use urllib")

    uv_ok = _check_cmd("uv")
    if uv_ok:
        sub_ok("uv found")

    if sys.platform == "win32":
        _install_fzf()
    else:
        _install_fzy()

    # Refresh fuzzy detection after install attempt
    _HAS_FZF = bool(shutil.which("fzf"))
    _HAS_FZY = bool(shutil.which("fzy"))
    _FUZZY_CMD = "fzf" if _HAS_FZF else ("fzy" if _HAS_FZY else None)

    if _HAS_FZF or _HAS_FZY:
        sub_ok("fzf ready" if _HAS_FZF else "fzy ready")
    else:
        sub_warn("no fuzzy finder — using numbered menu")

    # ── Step 2: Fetch providers ──────────────────────────────────────────
    print_step(2, TOTAL, "Fetching providers from models.dev")
    providers = fetch_providers()

    # ── Step 3: Select provider + API key ────────────────────────────────
    print_step(3, TOTAL, "Select provider and enter API key")
    print()

    provider = pick_provider(providers)
    api_key = collect_api_key(provider)

    # ── Step 4: Select models ────────────────────────────────────────────
    print()
    print_step(4, TOTAL, "Select models per tier")
    models = pick_models(providers, provider)

    # ── Step 5: Save & finalize ──────────────────────────────────────────
    print()
    print_step(5, TOTAL, "Saving configuration")
    save_config(provider, api_key, models)

    setup_shell_env(rc, already_configured)
    install_start_server()
    check_claude_cli()

    # ── Summary ──────────────────────────────────────────────────────────
    show_summary(provider, models)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  Setup cancelled by user {S.YLW}⚠{S.RST}")
        sys.exit(1)
