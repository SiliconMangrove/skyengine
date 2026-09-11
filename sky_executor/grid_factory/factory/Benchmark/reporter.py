"""
Visualization and Reporting Module for Benchmark Results

Generate charts, tables, and detailed reports from evaluation results.
"""

import json
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

try:
    import matplotlib.pyplot as plt
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

logger = logging.getLogger(__name__)


class BenchmarkReporter:
    """Generate reports from benchmark results."""

    def __init__(self, results_dir: str):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(
        self,
        results_by_algo: Dict[str, List[Any]],
        output_name: str = "report",
    ) -> str:
        """
        Generate comprehensive HTML/Markdown report.

        Args:
            results_by_algo: dict of algorithm_name -> list of metrics
            output_name: base name for output file

        Returns:
            path to generated report
        """
        report_lines = [
            "# Benchmark Report",
            "",
            f"Generated: {Path(output_name).stem}",
            "",
            "## Summary",
            "",
        ]

        # Summary table
        report_lines.extend(self._generate_summary_table(results_by_algo))
        report_lines.extend(["", "## Detailed Results", ""])

        # Detailed results per algorithm
        for algo_name, metrics_list in results_by_algo.items():
            report_lines.extend(self._generate_algo_section(algo_name, metrics_list))

        # Save report
        report_path = self.results_dir / f"{output_name}.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))

        logger.info(f"✓ Report saved to {report_path}")
        return str(report_path)

    def _generate_summary_table(self, results_by_algo: Dict[str, List[Any]]) -> List[str]:
        """Generate summary comparison table."""
        lines = [
            "| Algorithm | Avg Makespan | Avg Flowtime | Avg AGV Util | Success Rate |",
            "|-----------|--------------|--------------|--------------|--------------|",
        ]

        for algo_name in sorted(results_by_algo.keys()):
            metrics_list = results_by_algo[algo_name]
            successful = [m for m in metrics_list if m.success]

            if not successful:
                lines.append(f"| {algo_name} | FAILED | - | - | 0% |")
                continue

            makespans = [m.total_makespan for m in successful]
            flowtimes = [m.total_flowtime for m in successful]
            utils = [m.agv_utilization for m in successful]
            success_rate = len(successful) / len(metrics_list)

            line = (
                f"| {algo_name} | "
                f"{np.mean(makespans):.1f} | "
                f"{np.mean(flowtimes):.1f} | "
                f"{np.mean(utils):.1%} | "
                f"{success_rate:.0%} |"
            )
            lines.append(line)

        return lines

    def _generate_algo_section(self, algo_name: str, metrics_list: List[Any]) -> List[str]:
        """Generate section for one algorithm."""
        lines = [f"### {algo_name}", ""]

        if not metrics_list:
            lines.extend(["No results.", ""])
            return lines

        successful = [m for m in metrics_list if m.success]
        failed = [m for m in metrics_list if not m.success]

        # Success rate
        success_rate = len(successful) / len(metrics_list) if metrics_list else 0
        lines.append(f"**Success Rate**: {success_rate:.0%} ({len(successful)}/{len(metrics_list)})")
        lines.append("")

        # Statistics
        if successful:
            lines.append("**Statistics**:")
            makespans = [m.total_makespan for m in successful]
            flowtimes = [m.total_flowtime for m in successful]
            utils = [m.agv_utilization for m in successful]

            lines.append(f"- Makespan: mean={np.mean(makespans):.1f}, min={np.min(makespans):.1f}, max={np.max(makespans):.1f}")
            lines.append(f"- Flowtime: mean={np.mean(flowtimes):.1f}, min={np.min(flowtimes):.1f}, max={np.max(flowtimes):.1f}")
            lines.append(f"- AGV Utilization: mean={np.mean(utils):.1%}")
            lines.append("")

        # Failed runs
        if failed:
            lines.append("**Failed Runs**:")
            for m in failed:
                lines.append(f"- {m.scenario_name}: {m.error_msg}")
            lines.append("")

        return lines

    def plot_comparison(
        self,
        results_by_algo: Dict[str, List[Any]],
        output_name: str = "comparison",
        metrics: List[str] = None,
    ) -> Optional[str]:
        """
        Generate comparison plots.

        Args:
            results_by_algo: dict of algorithm_name -> list of metrics
            output_name: base name for output file
            metrics: list of metrics to plot (e.g., ['makespan', 'flowtime'])

        Returns:
            path to saved plot or None if matplotlib unavailable
        """
        if not HAS_MATPLOTLIB:
            logger.warning("matplotlib not available, skipping plot generation")
            return None

        if metrics is None:
            metrics = ["total_makespan", "total_flowtime", "agv_utilization"]

        num_metrics = len(metrics)
        fig, axes = plt.subplots(1, num_metrics, figsize=(5*num_metrics, 5))
        if num_metrics == 1:
            axes = [axes]

        algo_names = list(results_by_algo.keys())

        for idx, metric in enumerate(metrics):
            ax = axes[idx]
            data = []
            labels = []

            for algo_name in algo_names:
                metrics_list = results_by_algo[algo_name]
                successful = [m for m in metrics_list if m.success]

                if successful:
                    values = [getattr(m, metric, 0) for m in successful]
                    data.append(values)
                    labels.append(algo_name)

            if data:
                ax.boxplot(data, labels=labels)
                ax.set_title(metric.replace("_", " ").title())
                ax.set_ylabel("Value")
                ax.grid(alpha=0.3)

        plt.tight_layout()

        plot_path = self.results_dir / f"{output_name}.png"
        plt.savefig(plot_path, dpi=150)
        plt.close()

        logger.info(f"✓ Plot saved to {plot_path}")
        return str(plot_path)


class ExcelExporter:
    """Export results to Excel format (if openpyxl available)."""

    @staticmethod
    def export(
        results_by_algo: Dict[str, List[Any]],
        output_path: str,
    ) -> bool:
        """
        Export to Excel with summary and detailed sheets.

        Args:
            results_by_algo: benchmark results
            output_path: output file path

        Returns:
            True if successful, False otherwise
        """
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            logger.warning("openpyxl not available, cannot export to Excel")
            return False

        wb = openpyxl.Workbook()

        # Summary sheet
        ws_summary = wb.active
        ws_summary.title = "Summary"
        ws_summary.append(["Algorithm", "Avg Makespan", "Avg Flowtime", "Avg AGV Util", "Success Rate"])

        for algo_name in sorted(results_by_algo.keys()):
            metrics_list = results_by_algo[algo_name]
            successful = [m for m in metrics_list if m.success]

            if successful:
                makespans = [m.total_makespan for m in successful]
                flowtimes = [m.total_flowtime for m in successful]
                utils = [m.agv_utilization for m in successful]
                success_rate = len(successful) / len(metrics_list)

                ws_summary.append([
                    algo_name,
                    np.mean(makespans),
                    np.mean(flowtimes),
                    np.mean(utils),
                    success_rate,
                ])

        # Detailed sheet
        ws_detail = wb.create_sheet("Detailed Results")
        headers = [
            "Algorithm", "Scenario", "Makespan", "Flowtime", "AGV Util",
            "Machine Util", "Solution Time", "Episode Length", "Success"
        ]
        ws_detail.append(headers)

        for algo_name in sorted(results_by_algo.keys()):
            for metrics in results_by_algo[algo_name]:
                ws_detail.append([
                    metrics.algorithm_name,
                    metrics.scenario_name,
                    metrics.total_makespan,
                    metrics.total_flowtime,
                    metrics.agv_utilization,
                    metrics.machine_utilization,
                    metrics.solution_time,
                    metrics.episode_length,
                    "Yes" if metrics.success else "No",
                ])

        wb.save(output_path)
        logger.info(f"✓ Exported to {output_path}")
        return True
