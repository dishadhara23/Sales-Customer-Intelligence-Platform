"""Matplotlib styling bound to the validated palette.

The notebook uses matplotlib rather than Plotly for one practical reason:
matplotlib output embeds as PNG and therefore renders on GitHub, where the
notebook is most likely to be read. Plotly figures show as blank cells there.
"""

from __future__ import annotations

import matplotlib as mpl
from matplotlib.colors import LinearSegmentedColormap

from src.viz.palette import (
    CATEGORICAL_LIGHT,
    CONTEXT_LIGHT,
    GRID_LIGHT,
    SEQUENTIAL_BLUE,
    SURFACE_LIGHT,
    TEXT_PRIMARY_LIGHT,
    TEXT_SECONDARY_LIGHT,
)

BLUES = LinearSegmentedColormap.from_list("viz_blues", list(SEQUENTIAL_BLUE))
ACCENT = CATEGORICAL_LIGHT[0]
CONTEXT = CONTEXT_LIGHT


def apply() -> None:
    """Install the project chart style. Call once per notebook/session."""
    mpl.rcParams.update(
        {
            "figure.facecolor": SURFACE_LIGHT,
            "axes.facecolor": SURFACE_LIGHT,
            "savefig.facecolor": SURFACE_LIGHT,
            "figure.dpi": 110,
            "savefig.dpi": 110,
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "font.size": 10,
            "text.color": TEXT_PRIMARY_LIGHT,
            # Recessive axes: the data should be the darkest thing on the page.
            "axes.edgecolor": GRID_LIGHT,
            "axes.labelcolor": TEXT_SECONDARY_LIGHT,
            "axes.titlesize": 13,
            "axes.titleweight": "600",
            "axes.titlecolor": TEXT_PRIMARY_LIGHT,
            "axes.titlelocation": "left",
            "axes.titlepad": 12,
            "axes.labelsize": 10,
            "axes.grid": True,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": GRID_LIGHT,
            "grid.linewidth": 0.8,
            "xtick.color": TEXT_SECONDARY_LIGHT,
            "ytick.color": TEXT_SECONDARY_LIGHT,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "legend.frameon": False,
            "legend.fontsize": 9,
            "lines.linewidth": 2.0,      # 2px lines per the mark spec
            "lines.markersize": 5,
            "lines.solid_capstyle": "round",
            "figure.autolayout": False,
        }
    )


def brl(value: float, decimals: int = 0) -> str:
    """Format a BRL amount in the configured display currency."""
    from src.viz.money import fmt

    return fmt(value, decimals)


def compact_brl(value: float) -> str:
    """Short form (1.2M / 340k) in the configured display currency."""
    from src.viz.money import compact

    return compact(value)
