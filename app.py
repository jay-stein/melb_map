"""Melbourne suburbs quirk map — Dash app.

Run:
    uv run python app.py
Then open http://localhost:8050.

This first pass uses placeholder data so the map renders before the Reddit/LLM
pipeline is wired up. Real data lives in data/suburbs.json once generated.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

ROOT = Path(__file__).resolve().parent
BOUNDARIES_PATH = ROOT / "data" / "boundaries.geojson"
SUBURBS_PATH = ROOT / "data" / "suburbs.json"

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
                "nickname": entry.get("nickname", ""),
                "category": entry.get("primary_category", "unknown"),
                "tags": entry.get("tags", []),
                "vibe": entry.get("vibe", ""),
                "lore": entry.get("lore", []),
                "history": entry.get("history", ""),
                "history_source": entry.get("history_source"),
                "history_source_url": entry.get("history_source_url", ""),
                "history_source_author": entry.get("history_source_author", ""),
                "top_quote": entry.get("top_quote", ""),
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
            "nickname": "",
            "category": cat,
            "tags": [f"placeholder {cat} tag {i+1}" for i in range(3)],
            "vibe": f"Placeholder vibe for {s}. Real summary will arrive once the Reddit pipeline runs.",
            "lore": [],
            "history": "",
            "history_source": None,
            "history_source_url": "",
            "history_source_author": "",
            "top_quote": "",
            "quotes": [],
            "mascot_name": "",
            "mascot_tagline": "",
            "mascot_description": "",
        })
    return pd.DataFrame(rows)


def compute_centroids(geojson: dict) -> dict[str, tuple[float, float]]:
    """Return {suburb_name: (lat, lon)} using each polygon's representative
    point (always inside the polygon — centroid can fall outside for
    non-convex shapes)."""
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    centroids: dict[str, tuple[float, float]] = {}
    for _, row in gdf.iterrows():
        pt = row.geometry.representative_point()
        centroids[row["suburb"]] = (pt.y, pt.x)  # (lat, lon)
    return centroids


def build_figure(df: pd.DataFrame, geojson: dict):
    df = df.copy()
    def fmt_hover(lst):
        if not lst:
            return "<i>no quirks gathered yet</i>"
        return "<br>".join(f"• {t}" for t in lst[:3])
    df["hover_tags"] = df["tags"].apply(fmt_hover)
    df["display_name"] = df.apply(
        lambda r: f"{r['suburb']} ({r['nickname']})" if r.get("nickname") else r["suburb"],
        axis=1,
    )

    fig = px.choropleth_map(
        df,
        geojson=geojson,
        locations="suburb",
        featureidkey="properties.suburb",
        color="category",
        color_discrete_map=CATEGORY_COLORS,
        category_orders={"category": CATEGORIES + ["unknown"]},
        custom_data=["display_name", "hover_tags"],
        center={"lat": -37.83, "lon": 144.97},
        zoom=10.5,
        map_style="carto-positron",
        opacity=0.45,
    )
    fig.update_traces(
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>",
        marker_line_width=0.5,
        marker_line_color="white",
    )

    # Suburb text labels at each polygon's centroid. Name on top, nickname
    # underneath in brackets if one exists. hoverinfo='skip' so labels never
    # steal hover from the underlying choropleth.
    centroids = compute_centroids(geojson)
    label_rows = []
    for _, r in df.iterrows():
        latlon = centroids.get(r["suburb"])
        if not latlon:
            continue
        lat, lon = latlon
        nickname = r.get("nickname") or ""
        text = f"<b>{r['suburb']}</b><br>({nickname})" if nickname else f"<b>{r['suburb']}</b>"
        label_rows.append({"lat": lat, "lon": lon, "text": text})
    if label_rows:
        labels_df = pd.DataFrame(label_rows)
        fig.add_trace(go.Scattermap(
            lat=labels_df["lat"],
            lon=labels_df["lon"],
            mode="text",
            text=labels_df["text"],
            textfont={"color": "#212121", "size": 10, "family": "-apple-system, sans-serif"},
            hoverinfo="skip",
            showlegend=False,
        ))

    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        legend={"title": "vibe", "orientation": "v", "x": 0.01, "y": 0.99},
        uirevision="static",
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
    nickname = r.get("nickname") or ""

    def suburb_heading() -> "html.H3":
        if nickname:
            return html.H3(
                [
                    suburb,
                    html.Span(
                        f" ({nickname})",
                        style={"fontWeight": 400, "color": "#757575", "fontSize": "16px"},
                    ),
                ],
                style={"marginBottom": 4},
            )
        return html.H3(suburb, style={"marginBottom": 4})

    if not r["tags"] and not r["vibe"]:
        return html.Div([
            suburb_heading(),
            html.P(
                "No quirks gathered for this suburb yet — run the scrape + summarise pipeline to populate it.",
                style={"color": "#757575", "fontStyle": "italic"},
            ),
        ])

    children: list = [
        suburb_heading(),
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
    top_quote = (r.get("top_quote") or "").strip()
    quotes_list = list(r["quotes"]) if r["quotes"] is not None else []
    if top_quote or quotes_list:
        children.append(
            html.H4("from r/melbourne", style={"marginBottom": 4, "marginTop": "16px"}),
        )
    if top_quote:
        children.append(
            html.Div(
                f"“{top_quote}”",
                style={
                    "borderLeft": "4px solid #7E57C2",
                    "padding": "14px 18px",
                    "margin": "8px 0 12px 0",
                    "background": "white",
                    "borderRadius": "0 8px 8px 0",
                    "color": "#212121",
                    "fontSize": "16px",
                    "fontStyle": "italic",
                    "lineHeight": 1.45,
                    "boxShadow": "0 1px 2px rgba(0,0,0,0.04)",
                },
            ),
        )
    # Dedupe top_quote from the supporting list (case-insensitive substring)
    if top_quote:
        tq_lower = top_quote.lower()
        quotes_list = [q for q in quotes_list if tq_lower not in q.lower() and q.lower() not in tq_lower]
    if quotes_list:
        children.append(
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
                for q in quotes_list
            ]),
        )
    return html.Div(children)


if __name__ == "__main__":
    app.run(debug=True, port=8050)
