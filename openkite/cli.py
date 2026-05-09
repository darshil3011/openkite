"""CloudOps OpenKite — natural-language AWS agent (ReAct).

Single CLI verb: ``openkite ask "<question>"``. Streams every tool call to the
console as it happens. Pauses for confirmation when a write tool runs.
"""

from __future__ import annotations

import sys
import uuid

import typer
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from openkite.agent import build_agent

app = typer.Typer(add_completion=False, help="Natural-language AWS operations agent.")
console = Console()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _print_tool_call(name: str, args: dict) -> None:
    pretty = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    console.print(f"[dim cyan]→ {name}({pretty})[/dim cyan]")


def _print_tool_result(name: str, content: str) -> None:
    snippet = content if len(content) <= 400 else content[:400] + "…"
    console.print(f"[dim]   {snippet}[/dim]")


def _drain(stream, on_assistant_text=None) -> dict:
    """Consume the agent stream and surface tool calls as they happen."""
    last_state: dict = {}
    for chunk in stream:
        for node, payload in chunk.items():
            for msg in payload.get("messages") or []:
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    _print_tool_call(tc["name"], tc.get("args") or {})
                if msg.__class__.__name__ == "ToolMessage":
                    _print_tool_result(msg.name, str(msg.content))
                elif msg.__class__.__name__ == "AIMessage" and msg.content and not tool_calls:
                    if on_assistant_text:
                        on_assistant_text(msg.content)
            last_state.setdefault("_node", node)
    return last_state


def _resume_through_interrupts(graph, config) -> None:
    """Loop: while the graph is paused on an interrupt, ask the user and resume."""
    while True:
        snap = graph.get_state(config)
        if not snap.tasks or not snap.tasks[0].interrupts:
            return
        interrupt_value = snap.tasks[0].interrupts[0].value
        if isinstance(interrupt_value, dict) and interrupt_value.get("type") == "confirm_action":
            msg = interrupt_value.get("message", "Confirm action?")
        else:
            msg = str(interrupt_value)
        reply = typer.prompt(f"\n[confirm] {msg}  (yes/no)")
        _drain(graph.stream(Command(resume=reply), config=config, stream_mode="updates"),
               on_assistant_text=lambda t: console.print(Markdown(t)))


# ── Commands ───────────────────────────────────────────────────────────────────


@app.command()
def ask(
    query: str = typer.Argument(..., help="What you want to know or do."),
    thread: str = typer.Option(None, "--thread", help="Reuse a session thread for follow-ups."),
):
    """Ask the agent anything. Tool calls stream live; writes pause to confirm."""
    graph = build_agent()
    thread_id = thread or f"openkite-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    console.print(Panel(query, title="You", border_style="cyan"))

    final_text = []
    _drain(
        graph.stream(
            {"messages": [{"role": "user", "content": query}]},
            config=config,
            stream_mode="updates",
        ),
        on_assistant_text=final_text.append,
    )
    _resume_through_interrupts(graph, config)

    if final_text:
        console.print(Panel(Markdown(final_text[-1]), title="OpenKite", border_style="green"))
    console.print(f"[dim]thread: {thread_id}[/dim]")


@app.command()
def tools():
    """List every tool the agent can call."""
    from openkite.tools import ALL_TOOLS

    table = Table(title="OpenKite toolbox", header_style="bold cyan", expand=True)
    table.add_column("Tool", style="green", no_wrap=True)
    table.add_column("Args", style="dim")
    table.add_column("Description")

    for t in ALL_TOOLS:
        args = ", ".join(t.args.keys()) if hasattr(t, "args") else ""
        desc = (t.description or "").split("\n")[0]
        table.add_row(t.name, args, desc)
    console.print(table)


@app.command()
def providers():
    """List every supported LLM provider, default model, and required env var."""
    from openkite.llm import PROVIDERS

    table = Table(title="OpenKite LLM providers", header_style="bold cyan", expand=True)
    table.add_column("Provider", style="green", no_wrap=True)
    table.add_column("Default model", style="cyan")
    table.add_column("API key env var", style="yellow")
    table.add_column("Install", style="dim")

    for prov in PROVIDERS.values():
        table.add_row(
            prov.name,
            prov.default_model,
            prov.env_key or "(none — local)",
            f"pip install 'cloudops-openkite[{prov.extra}]'",
        )
    console.print(table)
    console.print(
        "\n[dim]Set [bold]OPENKITE_PROVIDER[/bold] and [bold]OPENKITE_MODEL[/bold] "
        "(or use the combined form [bold]OPENKITE_MODEL=provider:model[/bold]) "
        "plus the provider's API key env var.[/dim]"
    )


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
