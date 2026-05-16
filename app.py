"""Melbourne suburbs quirk map — Dash app.

Run:
    uv run python app.py
Then open http://localhost:8050.

This first pass uses placeholder data so the map renders before the Reddit/LLM
pipeline is wired up. Real data lives in data/suburbs.json once generated.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, dcc, html

ROOT = Path(__file__).resolve().parent
BOUNDARIES_PATH = ROOT / "data" / "boundaries.geojson"
SUBURBS_PATH = ROOT / "data" / "suburbs.json"
FLAGS_DIR = ROOT / "assets" / "flags"

# Tuning for flag overlays. Flag rectangle is sized as a fraction of polygon
# bbox width but capped so tiny suburbs aren't drowned by their flag and big
# ones don't get an absurd one.
FLAG_BBOX_FRAC = 0.55
FLAG_LON_MIN = 0.012  # ~1 km wide at Melbourne's latitude
FLAG_LON_MAX = 0.030
FLAG_ASPECT = 1.5  # 3:2 visual; we'll skew height by cos(lat) to compensate Mercator

CATEGORIES = [
    "hipster", "posh", "student", "family",
    "nightlife", "industrial", "sleepy", "multicultural",
]
CATEGORY_COLORS = {
    "hipster": "#7E57C2",
    "posh": "#D4AF37",
    "student": "#26A69A",
    "family": "#66BB6A",
    "nightlife": "#EC407A",
    "industrial": "#8D6E63",
    "sleepy": "#90A4AE",
    "multicultural": "#FFA726",
    "unknown": "#BDBDBD",
}


def load_boundaries() -> dict:
    with BOUNDARIES_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_suburbs_data(geojson: dict) -> pd.DataFrame:
    """Load real suburbs.json if present, else placeholder data."""
    suburbs = [feat["properties"]["suburb"] for feat in geojson["features"]]

    if SUBURBS_PATH.exists():
        with SUBURBS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        rows = []
        for s in suburbs:
            entry = data.get(s, {})
            mascot = entry.get("mascot") or {}
            rows.append({
                "suburb": s,
                "category": entry.get("primary_category", "unknown"),
                "tags": entry.get("tags", []),
                "vibe": entry.get("vibe", ""),
                "lore": entry.get("lore", []),
                "history": entry.get("history", ""),
                "history_source": entry.get("history_source"),
                "history_source_url": entry.get("history_source_url", ""),
                "history_source_author": entry.get("history_source_author", ""),
                "quotes": entry.get("quotes", []),
                "mascot_name": mascot.get("name", ""),
                "mascot_tagline": mascot.get("tagline", ""),
                "mascot_description": mascot.get("description", ""),
            })
        return pd.DataFrame(rows)

    # Placeholder: deterministic random per suburb so it stays stable across reloads
    rng = random.Random(42)
    rows = []
    for s in suburbs:
        cat = rng.choice(CATEGORIES)
        rows.append({
            "suburb": s,
            "category": cat,
            "tags": [f"placeholder {cat} tag {i+1}" for i in range(3)],
            "vibe": f"Placeholder vibe for {s}. Real summary will arrive once the Reddit pipeline runs.",
            "lore": [],
            "history": "",
            "history_source": None,
            "history_source_url": "",
            "history_source_author": "",
            "quotes": [],
            "mascot_name": "",
            "mascot_tagline": "",
            "mascot_description": "",
        })
    return pd.DataFrame(rows)


def compute_flag_layers(geojson: dict) -> list[dict]:
    """For every suburb that has a flag PNG in assets/flags/, compute a
    map.layers entry that pins the flag image at the polygon's representative
    point with a sensible width."""
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    layers = []
    for _, row in gdf.iterrows():
        suburb = row["suburb"]
        safe = suburb.replace(" ", "_").replace("/", "_")
        for ext in ("png", "jpg"):
            flag_path = FLAGS_DIR / f"{safe}.{ext}"
            if flag_path.exists():
                break
        else:
            continue

        geom = row.geometry
        # Representative point is always inside the polygon (centroid can fall
        # outside for non-convex shapes)
        pt = geom.representative_point()
        cx, cy = pt.x, pt.y

        # Pick a flag width based on bbox, clamped to sensible range
        minx, miny, maxx, maxy = geom.bounds
        bbox_w = maxx - minx
        flag_w = max(FLAG_LON_MIN, min(FLAG_LON_MAX, bbox_w * FLAG_BBOX_FRAC))
        # Compensate Mercator latitude stretch so visual aspect stays ~3:2
        flag_h = (flag_w / FLAG_ASPECT) / math.cos(math.radians(cy))

        # Coordinates: NW, NE, SE, SW (lon, lat)
        coords = [
            [cx - flag_w / 2, cy + flag_h / 2],
            [cx + flag_w / 2, cy + flag_h / 2],
            [cx + flag_w / 2, cy - flag_h / 2],
            [cx - flag_w / 2, cy - flag_h / 2],
        ]
        layers.append({
            "sourcetype": "image",
            "source": f"/assets/flags/{flag_path.name}",
            "coordinates": coords,
        })
    return layers


def build_figure(df: pd.DataFrame, geojson: dict):
    df = df.copy()
    def fmt_hover(lst):
        if not lst:
            return "<i>no quirks gathered yet</i>"
        return "<br>".join(f"• {t}" for t in lst[:3])
    df["hover_tags"] = df["tags"].apply(fmt_hover)

    fig = px.choropleth_map(
        df,
        geojson=geojson,
        locations="suburb",
        featureidkey="properties.suburb",
        color="category",
        color_discrete_map=CATEGORY_COLORS,
        category_orders={"category": CATEGORIES + ["unknown"]},
        custom_data=["suburb", "hover_tags"],
        center={"lat": -37.83, "lon": 144.97},
        zoom=10.5,
        map_style="carto-positron",
        opacity=0.30,  # lowered so flags pop on top
    )
    fig.update_traces(
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
        marker_line_width=0.5,
        marker_line_color="white",
    )
    flag_layers = compute_flag_layers(geojson)
    print(f"[app] {len(flag_layers)} flag layer(s) added to map")
    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        legend={"title": "vibe", "orientation": "v", "x": 0.01, "y": 0.99},
        uirevision="static",
        map={"layers": flag_layers} if flag_layers else {},
    )
    return fig


geojson = load_boundaries()
df = load_suburbs_data(geojson)
fig = build_figure(df, geojson)

app = Dash(__name__, title="Melbourne Suburb Quirks")

app.layout = html.Div(
    style={
        "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        "display": "flex",
        "height": "100vh",
        "margin": 0,
    },
    children=[
        html.Div(
            style={"flex": "1 1 70%", "position": "relative"},
            children=[
                dcc.Graph(
                    id="map",
                    figure=fig,
                    style={"height": "calc(100vh - 28px)"},
                    config={"scrollZoom": True, "displayModeBar": False},
                ),
                html.Footer(
                    [
                        "Sources: suburb character & quotes from ",
                        html.A("r/melbourne", href="https://www.reddit.com/r/melbourne/",
                               target="_blank", rel="noopener", style={"color": "#616161"}),
                        " and ",
                        html.A("MELBZ", href="https://melbz.com.au/",
                               target="_blank", rel="noopener", style={"color": "#616161"}),
                        " · history from ",
                        html.A("eMelbourne", href="https://www.emelbourne.net.au/",
                               target="_blank", rel="noopener", style={"color": "#616161"}),
                        " and ",
                        html.A("Wikipedia", href="https://en.wikipedia.org/",
                               target="_blank", rel="noopener", style={"color": "#616161"}),
                        " · boundaries from ",
                        html.A("ABS SAL 2021",
                               href="https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3",
                               target="_blank", rel="noopener", style={"color": "#616161"}),
                        " · summaries by DeepSeek",
                    ],
                    style={
                        "position": "absolute",
                        "bottom": 0, "left": 0, "right": 0,
                        "padding": "6px 12px",
                        "background": "rgba(255,255,255,0.85)",
                        "fontSize": "11px",
                        "color": "#757575",
                        "borderTop": "1px solid #E0E0E0",
                        "lineHeight": 1.4,
                    },
                ),
            ],
        ),
        html.Div(
            id="side-panel",
            style={
                "flex": "0 0 30%",
                "padding": "24px",
                "overflowY": "auto",
                "background": "#FAFAFA",
                "borderLeft": "1px solid #E0E0E0",
            },
            children=[
                html.H2("Melbourne suburb quirks", style={"marginTop": 0}),
                html.P(
                    "Hover a suburb for a peek. Click for the full breakdown.",
                    style={"color": "#616161"},
                ),
                html.P(
                    f"{df['tags'].apply(bool).sum()} of {len(df)} suburbs have quirks gathered. "
                    "Sourced from r/melbourne, summarised by an LLM.",
                    style={"color": "#9E9E9E", "fontSize": "12px", "marginBottom": "16px"},
                ),
                html.Div(id="suburb-detail"),
            ],
        ),
    ],
)


@app.callback(Output("suburb-detail", "children"), Input("map", "clickData"))
def update_panel(click_data):
    if not click_data:
        return html.P(
            "Click a suburb on the map →",
            style={"color": "#9E9E9E", "fontStyle": "italic"},
        )
    suburb = click_data["points"][0]["location"]
    row = df[df["suburb"] == suburb]
    if row.empty:
        return html.P(f"No data for {suburb}.")
    r = row.iloc[0]

    if not r["tags"] and not r["vibe"]:
        return html.Div([
            html.H3(suburb, style={"marginBottom": 4}),
            html.P(
                "No quirks gathered for this suburb yet — run the scrape + summarise pipeline to populate it.",
                style={"color": "#757575", "fontStyle": "italic"},
            ),
        ])

    children: list = [
        html.H3(suburb, style={"marginBottom": 4}),
        html.Span(
            r["category"],
            style={
                "display": "inline-block",
                "padding": "2px 10px",
                "borderRadius": "12px",
                "fontSize": "12px",
                "background": CATEGORY_COLORS.get(r["category"], "#BDBDBD"),
                "color": "white",
                "marginBottom": "12px",
            },
        ),
    ]

    # Mascot section — show image (if file exists) + name + tagline + description
    safe_suburb = suburb.replace(" ", "_").replace("/", "_")
    mascot_paths = [
        ROOT / "assets" / "mascots" / f"{safe_suburb}.jpg",
        ROOT / "assets" / "mascots" / f"{safe_suburb}.png",
    ]
    mascot_img_src = None
    for p in mascot_paths:
        if p.exists():
            mascot_img_src = f"/assets/mascots/{p.name}"
            break

    if mascot_img_src or r["mascot_name"]:
        mascot_block = []
        if mascot_img_src:
            mascot_block.append(
                html.Img(
                    src=mascot_img_src,
                    style={
                        "width": "100%",
                        "borderRadius": "8px",
                        "marginBottom": "8px",
                        "background": "white",
                    },
                ),
            )
        if r["mascot_name"]:
            mascot_block.append(html.Div(
                r["mascot_name"],
                style={"fontWeight": 600, "fontSize": "16px"},
            ))
        if r["mascot_tagline"]:
            mascot_block.append(html.Div(
                f"“{r['mascot_tagline']}”",
                style={"fontStyle": "italic", "color": "#616161", "fontSize": "13px",
                       "marginBottom": "8px"},
            ))
        if r["mascot_description"]:
            mascot_block.append(html.P(
                r["mascot_description"],
                style={"fontSize": "13px", "lineHeight": 1.5, "color": "#424242"},
            ))
        children += [
            html.Div(
                mascot_block,
                style={
                    "marginTop": "16px",
                    "padding": "12px",
                    "background": "white",
                    "borderRadius": "8px",
                    "border": "1px solid #E0E0E0",
                },
            ),
        ]
    if r["vibe"]:
        children.append(html.P(r["vibe"], style={"fontSize": "15px", "lineHeight": 1.5}))
    if r["tags"]:
        children += [
            html.H4("tags", style={"marginBottom": 4, "marginTop": "16px"}),
            html.Div(
                [
                    html.Span(
                        t,
                        style={
                            "display": "inline-block",
                            "padding": "3px 10px",
                            "margin": "3px 4px 3px 0",
                            "borderRadius": "10px",
                            "background": "#ECEFF1",
                            "color": "#37474F",
                            "fontSize": "13px",
                        },
                    )
                    for t in r["tags"]
                ],
            ),
        ]
    if r.get("lore"):
        children += [
            html.H4("lore", style={"marginBottom": 4, "marginTop": "16px"}),
            html.Ul(
                [html.Li(item, style={"marginBottom": "6px"}) for item in r["lore"]],
                style={"fontSize": "14px", "lineHeight": 1.5, "paddingLeft": "20px"},
            ),
        ]
    if r.get("history"):
        history_children = [
            html.P(
                r["history"],
                style={"fontSize": "14px", "lineHeight": 1.5, "color": "#37474F",
                       "marginBottom": "4px"},
            ),
        ]
        if r.get("history_source"):
            label_bits = []
            if r["history_source"] == "emelbourne":
                src_text = "eMelbourne"
                if r.get("history_source_author"):
                    src_text += f" — {r['history_source_author']}"
            elif r["history_source"] == "wikipedia":
                src_text = "Wikipedia"
            else:
                src_text = ""
            if src_text:
                label_bits.append(html.Span(f"— {src_text}", style={"marginRight": "4px"}))
            if r.get("history_source_url"):
                label_bits.append(html.A(
                    "[source]",
                    href=r["history_source_url"],
                    target="_blank",
                    rel="noopener",
                    style={"color": "#9E9E9E", "textDecoration": "none"},
                ))
            if label_bits:
                history_children.append(html.Div(
                    label_bits,
                    style={"fontSize": "11px", "color": "#9E9E9E",
                           "fontStyle": "italic", "marginBottom": "8px"},
                ))
        children += [
            html.H4("history", style={"marginBottom": 4, "marginTop": "16px"}),
            *history_children,
        ]
    if r["quotes"]:
        children += [
            html.H4("from r/melbourne", style={"marginBottom": 4, "marginTop": "16px"}),
            html.Div([
                html.Blockquote(
                    q,
                    style={
                        "borderLeft": "3px solid #BDBDBD",
                        "paddingLeft": "12px",
                        "margin": "8px 0",
                        "color": "#424242",
                        "fontSize": "14px",
                        "fontStyle": "italic",
                    },
                )
                for q in r["quotes"]
            ]),
        ]
    return html.Div(children)


if __name__ == "__main__":
    app.run(debug=True, port=8050)
