from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any


COLORS = {
    "D-FS-M": "#1f77b4",
    "J-FS-M": "#2ca02c",
    "S-FS-v2-M": "#9467bd",
    "MP-FS-M": "#7f7f7f",
    "MP-FS+": "#d62728",
    "Gold-MP": "#ffbf00",
}


def _svg(
    title: str,
    groups: list[str],
    series: list[tuple[str, list[float]]],
    *,
    width: int = 1100,
    height: int = 640,
) -> str:
    left, top, right, bottom = 88, 76, 28, 122
    plot_w = width - left - right
    plot_h = height - top - bottom
    group_w = plot_w / max(len(groups), 1)
    bar_w = min(34.0, group_w * 0.72 / max(len(series), 1))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#444;stroke-width:1}.grid{stroke:#ddd;stroke-width:1}.label{font-size:13px}.small{font-size:11px}.title{font-size:22px;font-weight:700}.legend{font-size:13px}</style>',
        f'<text class="title" x="{width / 2}" y="34" text-anchor="middle">{escape(title)}</text>',
    ]
    for tick in range(0, 11, 2):
        value = tick / 10
        y = top + plot_h * (1 - value)
        parts.append(f'<line class="grid" x1="{left}" x2="{left + plot_w}" y1="{y:.2f}" y2="{y:.2f}"/>')
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 4:.2f}" text-anchor="end">{value:.1f}</text>')
    parts.extend(
        [
            f'<line class="axis" x1="{left}" x2="{left}" y1="{top}" y2="{top + plot_h}"/>',
            f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}"/>',
        ]
    )
    for group_index, group in enumerate(groups):
        center = left + group_w * (group_index + 0.5)
        total_bar_w = bar_w * len(series)
        for series_index, (name, values) in enumerate(series):
            value = values[group_index]
            x = center - total_bar_w / 2 + series_index * bar_w
            y = top + plot_h * (1 - value)
            color = COLORS.get(
                name,
                ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728")[
                    series_index % 4
                ],
            )
            parts.append(
                f'<rect x="{x + 1:.2f}" y="{y:.2f}" width="{bar_w - 2:.2f}" height="{plot_h * value:.2f}" fill="{color}"/>'
            )
            if len(groups) <= 7:
                parts.append(
                    f'<text class="small" x="{x + bar_w / 2:.2f}" y="{max(y - 5, 65):.2f}" text-anchor="middle">{value:.3f}</text>'
                )
        parts.append(
            f'<text class="label" x="{center:.2f}" y="{top + plot_h + 22}" text-anchor="middle">{escape(group)}</text>'
        )
    legend_y = height - 52
    legend_total = len(series) * 155
    legend_x = max(left, (width - legend_total) / 2)
    for index, (name, _values) in enumerate(series):
        x = legend_x + index * 155
        color = COLORS.get(
            name,
            ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728")[index % 4],
        )
        parts.append(f'<rect x="{x:.2f}" y="{legend_y - 12}" width="16" height="16" fill="{color}"/>')
        parts.append(f'<text class="legend" x="{x + 22:.2f}" y="{legend_y + 1}">{escape(name)}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def _horizontal_counts(title: str, counts: list[tuple[str, int]]) -> str:
    width, height = 1100, max(520, 110 + 42 * len(counts))
    left, top, right = 330, 70, 80
    plot_w = width - left - right
    maximum = max((count for _name, count in counts), default=1)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#222}.title{font-size:22px;font-weight:700}.label{font-size:13px}.count{font-size:13px;font-weight:700}</style>',
        f'<text class="title" x="{width / 2}" y="34" text-anchor="middle">{escape(title)}</text>',
    ]
    for index, (name, count) in enumerate(counts):
        y = top + index * 42
        bar = plot_w * count / maximum
        parts.append(f'<text class="label" x="{left - 12}" y="{y + 19}" text-anchor="end">{escape(name)}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar:.2f}" height="25" fill="#d62728"/>')
        parts.append(f'<text class="count" x="{left + bar + 8:.2f}" y="{y + 18}">{count}</text>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def build_figures(
    methods: dict[str, dict[str, Any]],
    taxonomy: list[dict[str, Any]],
    output_dir: Path,
) -> list[Path]:
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    method_order = [
        "D-FS-M",
        "J-FS-M",
        "S-FS-v2-M",
        "MP-FS-M",
        "MP-FS+",
    ]
    main = _svg(
        "Final target-state accuracy and method-specific admission coverage",
        method_order,
        [
            ("Target state", [methods[name]["target_state_accuracy"] for name in method_order]),
            (
                "Admission coverage",
                [methods[name]["method_specific_admission_coverage"] for name in method_order],
            ),
        ],
    )
    main_path = figure_dir / "main_accuracy_coverage.svg"
    main_path.write_text(main, encoding="utf-8", newline="\n")

    formats = ["csv_or_mixed", "free_text", "json", "key_value", "markdown"]
    slice_methods = ["D-FS-M", "J-FS-M", "MP-FS-M", "MP-FS+"]
    format_svg = _svg(
        "Target-state accuracy by input format",
        [value.replace("_", " ") for value in formats],
        [
            (
                method,
                [methods[method]["slices"][f"input_format:{value}"]["target_state_accuracy"] for value in formats],
            )
            for method in slice_methods
        ],
    )
    format_path = figure_dir / "input_format_accuracy.svg"
    format_path.write_text(format_svg, encoding="utf-8", newline="\n")

    error_counts = Counter()
    for row in taxonomy:
        if row["method_id"] == "MP-FS+" and row["error_category"] != "correct":
            error_counts[str(row["error_category"])] += int(row["count"])
    taxonomy_path = figure_dir / "mp_fs_plus_error_taxonomy.svg"
    taxonomy_path.write_text(
        _horizontal_counts(
            "MP-FS+ error taxonomy (correct rows excluded)",
            error_counts.most_common(),
        ),
        encoding="utf-8",
        newline="\n",
    )
    return [main_path, format_path, taxonomy_path]
