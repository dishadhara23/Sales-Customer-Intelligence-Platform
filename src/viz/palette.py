"""The one palette every surface in this project draws from.

Values are not chosen by eye. Each set below was checked with the data-viz
validator (lightness band, chroma floor, colour-vision-deficiency separation,
normal-vision separation, contrast against the surface) and only passing sets
are recorded here. The validator output is quoted next to each set so the claim
is auditable rather than asserted.

Rules this encodes:

* **Categorical** (identity) is capped at three slots, because forms where every
  pair can appear side by side — maps, scatter — cannot clear the CVD floor with
  more. A fourth series folds into "Other" or becomes a small multiple.
* **Sequential** (magnitude) is one hue, light to dark. Never a rainbow.
* **Diverging** (polarity) is two opposed hues with a neutral grey midpoint.
* **Ordinal** (ranked stages) uses widely-spaced steps of the single hue so
  adjacent stages are visibly different.
"""

from __future__ import annotations

# --- surfaces & ink --------------------------------------------------------
SURFACE_LIGHT = "#fcfcfb"
SURFACE_DARK = "#1a1a19"
SURFACE_RAISED_LIGHT = "#f4f3f0"
SURFACE_RAISED_DARK = "#232322"

TEXT_PRIMARY_LIGHT = "#0b0b0b"
TEXT_PRIMARY_DARK = "#ffffff"
TEXT_SECONDARY_LIGHT = "#52514e"
TEXT_SECONDARY_DARK = "#c3c2b7"
TEXT_MUTED_LIGHT = "#75736d"
TEXT_MUTED_DARK = "#8f8d84"

GRID_LIGHT = "#e6e4df"
GRID_DARK = "#33322f"

# De-emphasis grey for the "one series is the point, rest are context" form.
CONTEXT_LIGHT = "#b6b3ac"
CONTEXT_DARK = "#5a5852"

# --- categorical (identity) ------------------------------------------------
# Validated all-pairs in BOTH modes:
#   light: CVD worst 9.2 (deutan), normal-vision worst 24.0  -> PASS
#   dark:  CVD worst 9.4 (deutan), normal-vision worst 20.9  -> PASS
# Light-mode aqua sits at 2.74:1 on the light surface, below 3:1, so the
# "relief rule" applies wherever it is used: ship visible direct labels or a
# table view. Every chart here that uses slot 3 is directly labelled.
CATEGORICAL_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a")
CATEGORICAL_DARK = ("#3987e5", "#d95926", "#199e70")

# --- sequential (magnitude): single blue hue, light -> dark ----------------
SEQUENTIAL_BLUE = (
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
)

# --- ordinal (ranked stages): widely spaced steps of the same hue ---------
# Validated --ordinal in both modes: monotone lightness, all adjacent gaps
# >= 0.06, light end clears 2:1 against the surface.
ORDINAL_LIGHT = ("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281")
ORDINAL_DARK = ("#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4")

# --- diverging (polarity): blue <-> red with a neutral midpoint -----------
DIVERGING_LIGHT = ("#1c5cab", "#3987e5", "#9ec5f4", "#f0efec", "#f2a3a2", "#e34948", "#b32a2a")
DIVERGING_DARK = ("#3987e5", "#6da7ec", "#9ec5f4", "#383835", "#f2a3a2", "#e66767", "#c73f3e")
DIVERGING_MID_LIGHT = "#f0efec"
DIVERGING_MID_DARK = "#383835"

# --- status (reserved; never reused as "series 4") ------------------------
STATUS = {
    "good": "#008300",
    "warning": "#eda100",
    "serious": "#eb6834",
    "critical": "#e34948",
}


def sequential_scale(reverse: bool = False) -> list[list]:
    """Plotly-style normalised colour scale from the sequential blue ramp."""
    ramp = list(reversed(SEQUENTIAL_BLUE)) if reverse else list(SEQUENTIAL_BLUE)
    last = len(ramp) - 1
    return [[i / last, c] for i, c in enumerate(ramp)]


def plotly_layout(dark: bool = False) -> dict:
    """Shared Plotly layout: recessive grid and axes, text in ink not series colour."""
    surface = SURFACE_DARK if dark else SURFACE_LIGHT
    primary = TEXT_PRIMARY_DARK if dark else TEXT_PRIMARY_LIGHT
    secondary = TEXT_SECONDARY_DARK if dark else TEXT_SECONDARY_LIGHT
    grid = GRID_DARK if dark else GRID_LIGHT
    axis = {
        "gridcolor": grid,
        "linecolor": grid,
        "zerolinecolor": grid,
        "tickfont": {"color": secondary, "size": 12},
        "title": {"font": {"color": secondary, "size": 12}},
    }
    return {
        "paper_bgcolor": surface,
        "plot_bgcolor": surface,
        "font": {
            "family": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "color": primary,
            "size": 13,
        },
        # An empty text is required: a title dict without one renders the
        # literal string "undefined" above the chart in Plotly.
        "title": {"text": "", "font": {"size": 16, "color": primary},
                  "x": 0, "xanchor": "left"},
        "xaxis": axis,
        "yaxis": dict(axis),
        "margin": {"l": 60, "r": 24, "t": 56, "b": 48},
        "hoverlabel": {"bgcolor": surface, "font": {"color": primary, "size": 12}},
        "legend": {
            "orientation": "h",
            "yanchor": "bottom", "y": 1.02,
            "xanchor": "left", "x": 0,
            "font": {"color": secondary, "size": 12},
            "bgcolor": "rgba(0,0,0,0)",
        },
    }
