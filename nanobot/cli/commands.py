"""CLI commands for nanobot."""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nanobot import __version__, __logo__

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    from nanobot.logging_config import setup_logging
    setup_logging()


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_repository, get_db_path, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    db_path = get_db_path()
    repo = get_config_repository()

    if repo.has_config():
        console.print(f"[yellow]Config already exists in {db_path}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit()

    # Create default config
    config = Config()
    save_config(config)
    console.print(f"[green]✓[/green] Created config in SQLite: {db_path}")

    # Create workspace
    workspace = get_workspace_path()
    console.print(f"[green]✓[/green] Created workspace at {workspace}")

    # Create default bootstrap files
    _create_workspace_templates(workspace)

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key via the [cyan]web UI Config page[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")




def _create_workspace_templates(workspace: Path):
    """Create default workspace template files."""
    templates = {
        "AGENTS.md": """# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in your memory files
""",
        "SOUL.md": """# Soul

I am nanobot, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }
    
    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")
    
    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    from nanobot.config.loader import load_config, get_db_path
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop
    from nanobot.channels.manager import ChannelManager
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.services.memory_maintenance import MemoryMaintenanceService

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    
    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")
    
    config = load_config()
    
    # Create components
    bus = MessageBus()
    
    model = config.agents.defaults.model
    # Create provider (supports OpenRouter, Anthropic, OpenAI, Bedrock, Zhipu, etc.)
    api_key = config.get_api_key(model)
    api_base = config.get_api_base(model)
    is_bedrock = model.startswith("bedrock/")

    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one via the web UI Config page, or use: nanobot web-ui")
        raise typer.Exit(1)
    
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )

    subagent_model = (getattr(config.agents.defaults, "subagent_model", "") or "").strip()
    if subagent_model:
        sa_key = config.get_api_key(subagent_model)
        if sa_key and hasattr(provider, "ensure_api_key_for_model"):
            provider.ensure_api_key_for_model(
                subagent_model, sa_key, config.get_api_base(subagent_model)
            )
    
    # Create cron service first (callback set after agent creation)
    cron = CronService(get_db_path())

    # Create agent with cron service（子 agent 模板由 AgentLoop 内部从 SQLite 加载，所有渠道一致）
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        subagent_model=subagent_model or None,
        max_iterations=config.agents.defaults.max_tool_iterations,
        max_execution_time=getattr(config.agents.defaults, "max_execution_time", 600) or 0,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        filesystem_config=config.tools.filesystem,
        claude_code_config=config.tools.claude_code,
        cron_service=cron,
        max_parallel_tool_calls=getattr(config.agents.defaults, "max_parallel_tool_calls", 5),
        enable_parallel_tools=getattr(config.agents.defaults, "enable_parallel_tools", True),
        thread_pool_size=getattr(config.agents.defaults, "thread_pool_size", 4),
    )
    
    # Set cron callback (needs agent)
    # job 是 CronRepository._row_to_job 返回的 dict 结构
    async def on_cron_job(job: dict) -> str | None:
        """Execute a cron job through the agent."""
        payload = job.get("payload", {})
        message = payload.get("message", "")
        deliver = payload.get("deliver", False)
        channel = payload.get("channel") or "cli"
        to = payload.get("to") or "direct"
        job_id = job.get("id", "unknown")
        job_name = job.get("name", "unknown")

        response = await agent.process_direct(
            message,
            session_key=f"cron:{job_id}",
            channel=channel,
            chat_id=to,
        )

        # 推送回复到对应渠道
        if deliver and payload.get("to"):
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=channel,
                chat_id=to,
                content=response or ""
            ))
            logger.info(f"Cron job '{job_name}' response delivered to {channel}:{to}")

        return response
    cron.on_job = on_cron_job
    
    # Create heartbeat service
    async def on_heartbeat(prompt: str) -> str:
        """Execute heartbeat through the agent."""
        return await agent.process_direct(prompt, session_key="heartbeat")
    
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True
    )

    memory_maintenance = MemoryMaintenanceService(
        workspace=config.workspace_path,
        provider=provider,
        model=model,
    )

    # Create channel manager（传入 agent 供 /stop 等命令使用）
    channels = ChannelManager(config, bus, agent=agent)
    
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    
    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")
    
    console.print(f"[green]✓[/green] Heartbeat: every 30m")
    console.print(f"[green]✓[/green] Memory maintenance: every 60m summarize, daily 00:05 merge")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await memory_maintenance.start()

            async def _cron_db_sync_loop():
                """每 60s 将 DB 中的任务同步到调度器（感知 web-ui 新增/修改的任务）。"""
                while True:
                    await asyncio.sleep(60)
                    await cron.sync_from_db()

            await asyncio.gather(
                agent.run(),
                channels.start_all(),
                _cron_db_sync_loop(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
            memory_maintenance.stop()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()
    
    asyncio.run(run())




# ============================================================================
# Mirror (镜室) Commands
# ============================================================================


mirror_app = typer.Typer(help="镜室相关命令")
app.add_typer(mirror_app, name="mirror")


@mirror_app.command("seal-stale")
def mirror_seal_stale(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="仅列出将被封存的会话，不实际执行"),
):
    """封存非当日、未封存的悟/辩会话。建议通过 crontab 每日 0 点执行。"""
    from nanobot.config.loader import load_config
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.session.manager import SessionManager
    from nanobot.services.mirror_seal_stale import seal_stale_sessions

    config = load_config()
    model = config.agents.defaults.model
    api_key = config.get_api_key(model)
    api_base = config.get_api_base(model)

    if not api_key:
        console.print("[red]Error: No API key configured. Set providers.*.apiKey in config.[/red]")
        raise typer.Exit(1)

    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model,
    )
    sessions = SessionManager(config.workspace_path)

    async def llm_chat(messages, model=None, max_tokens=800, temperature=0.3):
        return await provider.chat(
            messages,
            model=model or config.agents.defaults.model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    sealed = seal_stale_sessions(
        workspace=config.workspace_path,
        sessions=sessions,
        llm_chat=llm_chat,
        model=model,
        dry_run=dry_run,
    )
    if dry_run:
        console.print(f"[dim]Would seal {sealed} stale mirror session(s) (dry-run)[/dim]")
    else:
        console.print(f"[green]Sealed {sealed} stale mirror session(s)[/green]")


# ============================================================================
# Web UI Commands
# ============================================================================


@app.command("web-ui")
def web_ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Web API host"),
    port: int = typer.Option(6788, "--port", "-p", help="Web API port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose (DEBUG) logging"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug (TRACE) logging, most verbose"),
):
    """Start nanobot Web UI API server."""
    from nanobot.web.api import run_server
    from nanobot.logging_config import reconfigure_logging

    if debug:
        reconfigure_logging("TRACE")
        console.print("[dim]Debug mode enabled (TRACE level)[/dim]")
    elif verbose:
        reconfigure_logging("DEBUG")
        console.print("[dim]Verbose mode enabled (DEBUG level)[/dim]")

    console.print(f"{__logo__} Starting Web UI API on http://{host}:{port}")
    run_server(host=host, port=port)


@app.command("launcher")
def launcher(
    host: str = typer.Option("127.0.0.1", "--host", help="Web API host"),
    port: int = typer.Option(6788, "--port", "-p", help="Web API port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose (DEBUG) logging"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug (TRACE) logging, most verbose"),
):
    """Start nanobot with auto-restart guardian (supports self-update)."""
    # 防止 --debug 被误解析为 --host 的值（如 --host --debug）
    if host.startswith("-"):
        console.print("[yellow]Warning: --host 的值不能是选项，已恢复默认 127.0.0.1，并启用 debug[/yellow]")
        host = "127.0.0.1"
        debug = True

    import subprocess
    import sys
    import time

    from nanobot.agent.tools.self_update import RESTART_EXIT_CODE

    MAX_RAPID_RESTARTS = 5
    RAPID_RESTART_WINDOW = 60
    restart_timestamps: list[float] = []

    console.print(f"\n  [cyan]{'=' * 36}[/cyan]")
    console.print(f"   [cyan]nanobot launcher (guardian mode)[/cyan]")
    console.print(f"  [cyan]{'=' * 36}[/cyan]\n")

    while True:
        cmd = [sys.executable, "-m", "nanobot", "web-ui", "--host", host, "--port", str(port)]
        if verbose:
            cmd.append("--verbose")
        if debug:
            cmd.append("--debug")

        console.print(f"[green][launcher] Starting:[/green] {' '.join(cmd)}")
        console.print(f"[dim][launcher] Restart exit code: {RESTART_EXIT_CODE} | Ctrl+C to stop[/dim]\n")

        try:
            result = subprocess.run(cmd)
            exit_code = result.returncode
        except KeyboardInterrupt:
            console.print("\n[green][launcher] Stopped by user. Goodbye.[/green]")
            raise typer.Exit(0)

        console.print(f"\n[yellow][launcher] nanobot exited with code: {exit_code}[/yellow]")

        if exit_code == RESTART_EXIT_CODE:
            now = time.time()
            restart_timestamps[:] = [t for t in restart_timestamps if now - t < RAPID_RESTART_WINDOW]
            restart_timestamps.append(now)

            if len(restart_timestamps) >= MAX_RAPID_RESTARTS:
                console.print(
                    f"[red][launcher] Too many rapid restarts "
                    f"({MAX_RAPID_RESTARTS} in {RAPID_RESTART_WINDOW}s). Exiting.[/red]"
                )
                raise typer.Exit(1)

            console.print("[cyan][launcher] Self-update restart requested. Reinstalling...[/cyan]")

            repo_dir = Path(__file__).resolve().parent.parent.parent
            if (repo_dir / "pyproject.toml").exists():
                console.print(f"[dim][launcher] Running: pip install -e . (in {repo_dir})[/dim]")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
                    cwd=str(repo_dir),
                )

            console.print("[cyan][launcher] Restarting in 2 seconds...[/cyan]")
            time.sleep(2)
            continue
        else:
            console.print("[green][launcher] Normal exit. Goodbye.[/green]")
            raise typer.Exit(exit_code or 0)


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Interact with the agent directly."""
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop
    
    config = load_config()
    
    model = config.agents.defaults.model
    api_key = config.get_api_key(model)
    api_base = config.get_api_base(model)
    is_bedrock = model.startswith("bedrock/")

    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        raise typer.Exit(1)

    bus = MessageBus()
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )
    
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        max_iterations=config.agents.defaults.max_tool_iterations,
        max_execution_time=getattr(config.agents.defaults, "max_execution_time", 600) or 0,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        filesystem_config=config.tools.filesystem,
        claude_code_config=config.tools.claude_code,
        max_parallel_tool_calls=getattr(config.agents.defaults, "max_parallel_tool_calls", 5),
        enable_parallel_tools=getattr(config.agents.defaults, "enable_parallel_tools", True),
        thread_pool_size=getattr(config.agents.defaults, "thread_pool_size", 4),
    )
    
    if message:
        # Single message mode
        async def run_once():
            response = await agent_loop.process_direct(message, session_id)
            console.print(f"\n{__logo__} {response}")
        
        asyncio.run(run_once())
    else:
        # Interactive mode
        console.print(f"{__logo__} Interactive mode (Ctrl+C to exit)\n")
        
        async def run_interactive():
            while True:
                try:
                    user_input = console.input("[bold blue]You:[/bold blue] ")
                    if not user_input.strip():
                        continue
                    
                    response = await agent_loop.process_direct(user_input, session_id)
                    console.print(f"\n{__logo__} {response}\n")
                except KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break
        
        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Feishu
    fe = config.channels.feishu
    fe_config = f"app_id: {fe.app_id}" if fe.app_id else "[dim]not configured[/dim]"
    table.add_row("Feishu", "✓" if fe.enabled else "✗", fe_config)

    # Discord
    dc = config.channels.discord
    dc_config = f"token: {dc.token[:10]}..." if dc.token else "[dim]not configured[/dim]"
    table.add_row("Discord", "✓" if dc.enabled else "✗", dc_config)

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id}" if qq.app_id else "[dim]not configured[/dim]"
    table.add_row("QQ", "✓" if qq.enabled else "✗", qq_config)

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id}" if dt.client_id else "[dim]not configured[/dim]"
    table.add_row("DingTalk", "✓" if dt.enabled else "✗", dt_config)

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess
    
    # User's bridge location
    user_bridge = Path.home() / ".nanobot" / "bridge"
    
    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge
    
    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)
    
    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)
    
    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge
    
    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)
    
    console.print(f"{__logo__} Setting up bridge...")
    
    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))
    
    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)
    
    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess
    
    bridge_dir = _get_bridge_dir()
    
    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")
    
    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Session Commands
# ============================================================================

sessions_app = typer.Typer(help="Manage chat sessions")
app.add_typer(sessions_app, name="sessions")


@sessions_app.command("list")
def sessions_list():
    """List chat sessions."""
    from nanobot.config.loader import load_config
    from nanobot.session.manager import SessionManager

    config = load_config()
    manager = SessionManager(workspace=config.workspace_path)
    sessions = manager.list_sessions()

    if not sessions:
        console.print("No chat sessions found.")
        return

    table = Table(title="Chat Sessions")
    table.add_column("Session", style="cyan")
    table.add_column("Messages", style="green")
    table.add_column("Updated At")
    table.add_column("Created At")

    for item in sessions:
        table.add_row(
            item["key"],
            str(item.get("message_count", 0)),
            item.get("updated_at", ""),
            item.get("created_at", ""),
        )

    console.print(table)


@sessions_app.command("delete")
def sessions_delete(
    session_key: str = typer.Argument(..., help="Session key, e.g. cli:default"),
):
    """Delete a chat session."""
    from nanobot.config.loader import load_config
    from nanobot.session.manager import SessionManager

    if not typer.confirm(f"Delete session '{session_key}' and all messages?"):
        console.print("Cancelled.")
        return

    config = load_config()
    manager = SessionManager(workspace=config.workspace_path)
    deleted = manager.delete(session_key)

    if deleted:
        console.print(f"[green]✓[/green] Deleted session {session_key}")
    else:
        console.print(f"[yellow]Session not found:[/yellow] {session_key}")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from nanobot.config.loader import get_db_path
    from nanobot.cron.service import CronService
    
    service = CronService(get_db_path())
    
    jobs = service.list_jobs(include_disabled=all)
    
    if not jobs:
        console.print("No scheduled jobs.")
        return
    
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")
    
    import time
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"
        
        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            next_run = next_time
        
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
        
        table.add_row(job.id, job.name, sched, status, next_run)
    
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from nanobot.config.loader import get_db_path
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule
    
    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    
    service = CronService(get_db_path())
    
    job = service.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver,
        to=to,
        channel=channel,
    )
    
    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from nanobot.config.loader import get_db_path
    from nanobot.cron.service import CronService
    
    service = CronService(get_db_path())
    
    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from nanobot.config.loader import get_db_path
    from nanobot.cron.service import CronService
    
    service = CronService(get_db_path())
    
    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from nanobot.config.loader import get_db_path
    from nanobot.cron.service import CronService
    
    service = CronService(get_db_path())
    
    async def run():
        return await service.run_job(job_id, force=force)
    
    if asyncio.run(run()):
        console.print(f"[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import load_config, get_db_path, get_config_repository

    db_path = get_db_path()
    repo = get_config_repository()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Database: {db_path} {'[green]✓[/green]' if db_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if repo.has_config():
        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys
        has_openrouter = bool(config.providers.openrouter.api_key)
        has_anthropic = bool(config.providers.anthropic.api_key)
        has_openai = bool(config.providers.openai.api_key)
        has_gemini = bool(config.providers.gemini.api_key)
        has_vllm = bool(config.providers.vllm.api_base)

        console.print(f"OpenRouter API: {'[green]✓[/green]' if has_openrouter else '[dim]not set[/dim]'}")
        console.print(f"Anthropic API: {'[green]✓[/green]' if has_anthropic else '[dim]not set[/dim]'}")
        console.print(f"OpenAI API: {'[green]✓[/green]' if has_openai else '[dim]not set[/dim]'}")
        console.print(f"Gemini API: {'[green]✓[/green]' if has_gemini else '[dim]not set[/dim]'}")
        vllm_status = f"[green]✓ {config.providers.vllm.api_base}[/green]" if has_vllm else "[dim]not set[/dim]"
        console.print(f"vLLM/Local: {vllm_status}")


if __name__ == "__main__":
    app()
