"""Modern Rich Terminal UI components for FaceChain."""

from __future__ import annotations

import os
import sys
from typing import Any, List, Optional

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from rich.align import Align
    from rich.box import DOUBLE_EDGE, HEAVY, ROUNDED, SIMPLE
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    _RICH_AVAILABLE = True
    console = Console(force_terminal=True, legacy_windows=False)
except ImportError:
    _RICH_AVAILABLE = False
    console = None


def print_banner(engine: str = "auto", ledger_backend: str = "local") -> None:
    """Print a modern, branded CLI header banner."""
    if not _RICH_AVAILABLE:
        print("\n=== FACECHAIN: Biometric Evidence & Web Likeness Search ===")
        print(f"Backend: {ledger_backend.upper()} | Engine: {engine.upper()}\n")
        return

    backend_color = "cyan" if ledger_backend == "local" else "magenta"
    title = Text("🛡️  FACECHAIN", style="bold cyan")
    subtitle = Text("Biometric Identity Verification & Cryptographic Web Likeness Chain", style="dim white")
    
    info_line = Text()
    info_line.append(" ENGINE: ", style="bold white on blue")
    info_line.append(f" {engine.upper()} ", style="bold white on dark_blue")
    info_line.append("   ")
    info_line.append(" LEDGER: ", style="bold white on dark_green")
    info_line.append(f" {ledger_backend.upper()} ", style=f"bold white on {backend_color}")
    info_line.append("   ")
    info_line.append(" PRIVACY: ", style="bold white on grey27")
    info_line.append(" ZERO-BIOMETRICS ON CHAIN ", style="bold green on grey23")

    header_content = Group(
        Align.center(title),
        Align.center(subtitle),
        Align.center(Text("")),
        Align.center(info_line),
    )

    console.print(
        Panel(
            header_content,
            border_style="bright_cyan",
            box=ROUNDED,
            padding=(1, 2),
        )
    )


def print_face_card(face: Any) -> None:
    """Print the Stage 1 Face Detection result card."""
    if not _RICH_AVAILABLE:
        print(f"FACE DETECTED: quality={face.quality:.3f}, subject_hash={face.subject_hash}")
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    q_color = "green" if face.quality >= 0.6 else ("yellow" if face.quality >= 0.4 else "red")
    grid.add_row("Primary Face :", f"Detected ({face.num_faces_detected} in image)")
    grid.add_row("Quality Score:", f"[{q_color}]{face.quality:.1%}[/{q_color}] (det: {face.det_score:.2f})")
    grid.add_row("Subject Hash :", f"[bold yellow]{face.subject_hash}[/]")
    grid.add_row("Storage Mode :", "[green]RAM only[/] (512-d ArcFace vector never leaves process)")

    console.print(
        Panel(
            grid,
            title="[bold cyan]👤 STAGE 1: FACE DETECTION & BIOMETRIC ENCODING[/]",
            border_style="cyan",
            box=ROUNDED,
        )
    )


def format_confidence_bar(score: float, width: int = 10) -> str:
    """Return a visual block bar representation of a match score."""
    clamped = max(0.0, min(1.0, score))
    filled = int(round(clamped * width))
    empty = width - filled
    bar = "█" * filled + "░" * empty
    if score >= 0.85:
        color = "bright_green"
    elif score >= 0.65:
        color = "green"
    elif score >= 0.50:
        color = "yellow"
    else:
        color = "red"
    return f"[{color}]{bar} {score:.1%}[/{color}]"


def get_platform_badge(source: str, url: str) -> str:
    """Return an icon + colored badge for web platforms."""
    combined = (source + " " + url).lower()
    if "youtube." in combined:
        return "[bold red]▶ YouTube[/]"
    elif "reddit." in combined:
        return "[bold bright_red]🟠 Reddit[/]"
    elif "twitter." in combined or "x.com" in combined:
        return "[bold bright_white]𝕏 Twitter/X[/]"
    elif "wikipedia." in combined or "wikimedia." in combined:
        return "[bold bright_cyan]🌐 Wikipedia[/]"
    elif "linkedin." in combined:
        return "[bold bright_blue]💼 LinkedIn[/]"
    elif "instagram." in combined:
        return "[bold magenta]📷 Instagram[/]"
    elif "facebook." in combined:
        return "[bold blue]👥 Facebook[/]"
    elif "flickr." in combined:
        return "[bold hot_pink]🌸 Flickr[/]"
    elif "amazon." in combined:
        return "[bold yellow]📦 Amazon[/]"
    elif "loc.gov" in combined or "archives.gov" in combined or "whitehouse." in combined:
        return "[bold bright_magenta]🏛️ Gov/Archive[/]"
    else:
        return f"[dim white]{source[:14]}[/]"


def print_matches_table(matches: list[Any], threshold: float, total_checked: int) -> None:
    """Print the verified matches in a rich table."""
    if not _RICH_AVAILABLE:
        print(f"\nVERIFIED MATCHES ({len(matches)} found, {total_checked} checked):")
        for i, m in enumerate(matches[:8], 1):
            print(f" #{i:02d} [{m.similarity:.3f}] {m.candidate.source}: {m.candidate.page_link}")
        return

    table = Table(
        title=f"\n[bold green]🔍 VERIFIED WEB MATCHES ({len(matches)} matches from {total_checked} candidates checked)[/]",
        box=ROUNDED,
        header_style="bold cyan",
        border_style="green",
        show_lines=False,
    )
    table.add_column("#", justify="center", style="bold dim", width=4)
    table.add_column("Platform", justify="left", width=16)
    table.add_column("Similarity", justify="left", width=22)
    table.add_column("Source Page URL", justify="left", overflow="fold")

    for i, match in enumerate(matches[:8], 1):
        badge = get_platform_badge(match.candidate.source, match.candidate.page_link)
        conf_bar = format_confidence_bar(match.similarity, width=10)
        table.add_row(
            f"{i:02d}",
            badge,
            conf_bar,
            f"[link={match.candidate.page_link}]{match.candidate.page_link}[/link]",
        )

    console.print(table)


def print_no_match_card(best_similarity: float, threshold: float) -> None:
    """Print a clean no-match notice."""
    if not _RICH_AVAILABLE:
        print(f"\n[NO MATCH] Best similarity: {best_similarity:.3f} (below threshold {threshold}).")
        return

    msg = (
        f"[bold yellow]No unauthorized likeness matches found on the open web.[/]\n\n"
        f"• Best Candidate Similarity : [bold]{best_similarity:.1%}[/]\n"
        f"• Acceptance Threshold      : [bold]{threshold:.1%}[/]\n\n"
        f"[dim]No evidence anchored — clean exit logged to audit trail.[/]"
    )
    console.print(Panel(msg, title="[bold yellow]🛡️ NO MATCH DETECTED[/]", border_style="yellow", box=ROUNDED))


def print_anchored_certificate(record: Any, receipt: Any, evidence_path: str) -> None:
    """Print an official cryptographic proof certificate card."""
    if not _RICH_AVAILABLE:
        print("\n=== EVIDENCE ANCHORED ===")
        print(f"Record ID   : {record.record_id}")
        print(f"Bundle Hash : {record.bundle_hash}")
        print(f"Anchor Tx   : {receipt.tx_ref}")
        print(f"Evidence    : {evidence_path}\n")
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right", width=16)
    grid.add_column(style="white")

    status_tag = f"[bold green]✓ {receipt.status.value.upper()}[/]" if receipt.status.value == "confirmed" else f"[bold yellow]{receipt.status.value.upper()}[/]"
    backend_tag = f"[bold magenta]{receipt.backend.upper()}[/]"

    grid.add_row("Record ID       :", f"[bold white]{record.record_id}[/]")
    grid.add_row("Bundle Hash     :", f"[bold green]{record.bundle_hash}[/]")
    grid.add_row("Image SHA-256   :", f"[yellow]{record.image_sha256}[/]")
    grid.add_row("Perceptual Hash :", f"[cyan]{record.image_phash}[/] (pHash)")
    grid.add_row("Match Confidence:", format_confidence_bar(record.match_confidence, width=12))
    grid.add_row("Ledger Status   :", f"{status_tag} on {backend_tag} (Tx: [dim]{receipt.tx_ref[:20]}...[/dim])")
    grid.add_row("Evidence Bundle :", f"[bold underline white]{evidence_path}[/]")

    console.print(
        Panel(
            grid,
            title="[bold green]⛓️  CRYPTOGRAPHIC EVIDENCE ANCHORED TO LEDGER[/]",
            border_style="bright_green",
            box=ROUNDED,
            padding=(1, 2),
        )
    )
