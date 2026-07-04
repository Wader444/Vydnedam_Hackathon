import argparse
import sys
from rich.console import Console
from rich.panel import Panel

import parser_engine
import graph_engine
import ai_cli_orchestrator

console = Console()

def main():
    parser = argparse.ArgumentParser(description="ImpactGraph - Architectural Change & Impact Reviewer")
    parser.add_argument(
        "--baseline",
        default="HEAD",
        help="Baseline commit/revision to compare against (default: HEAD)"
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Target repository directory to analyze (default: current directory)"
    )
    args = parser.parse_args()

    console.print("[bold cyan]ImpactGraph Architectural PR Impact Analysis[/bold cyan]\n")

    # 1. Parse repository structure
    with console.status("[bold green]Parsing repository code structure (AST)..."):
        ast_data = parser_engine.parse_directory(args.dir)
    console.print(f"  [bold green]✔[/bold green] Parsed {len(ast_data.get('files', []))} files, {len(ast_data.get('classes', []))} classes, {len(ast_data.get('functions', []))} functions.")

    # 2. Synchronize to Graph database
    if graph_engine.neo4j_online:
        with console.status("[bold green]Updating Neo4j Graph Database..."):
            graph_engine.init_db()
            graph_engine.populate_graph(ast_data)
        console.print("  [bold green]✔[/bold green] Neo4j Graph Database synchronized and constraints enforced.")
    else:
        console.print("  [bold yellow]⚠[/bold yellow] Neo4j is offline. Using local in-memory BFS fallback.")

    # 3. Detect code changes via git diff
    with console.status("[bold green]Scraping git diff and mapping line changes..."):
        modified_functions_by_file = parser_engine.get_modified_functions(args.baseline)
    
    if not modified_functions_by_file:
        console.print("\n[bold green]No modified functions found between working copy and baseline.[/bold green]")
        return

    # Count total changes
    total_funcs = sum(len(funcs) for funcs in modified_functions_by_file.values())
    console.print(f"  [bold green]✔[/bold green] Detected {total_funcs} modified function(s) across {len(modified_functions_by_file)} file(s).\n")

    # 4. Process each modified function
    for filepath, funcs in modified_functions_by_file.items():
        console.print(f"[bold white underline]File: {filepath}[/bold white underline]")
        for func in funcs:
            func_name = func["name"]
            diff_content = func["diff"]

            console.print(f"\n[bold yellow]Analyzing modified function: [magenta]{func_name}[/magenta][/bold yellow]")

            # a. Trace downstream dependents
            with console.status("[bold green]Tracing call graph dependents..."):
                dependents = graph_engine.trace_downstream_deps(func_name)

            # b. Query AI for architectural risk evaluation
            with console.status("[bold green]Querying AI for architectural risk evaluation..."):
                risk_level, analysis_text = ai_cli_orchestrator.analyze_impact_with_ai(
                    modified_function=func_name,
                    diff_content=diff_content,
                    downstream_deps=dependents
                )

            # c. Render color-coded outputs
            ai_cli_orchestrator.render_dependency_tree(func_name, dependents, risk_level)
            ai_cli_orchestrator.render_risk_panel(risk_level, analysis_text)
            console.print("-" * 80)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Analysis cancelled by user.[/bold red]")
        sys.exit(1)
