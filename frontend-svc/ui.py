"""Accenture-branded UI/UX layer for the Streamlit app.

Implements the feasible subset of the requested patterns via a single CSS theme
plus small HTML helpers: sticky glassmorphic top nav, breadcrumbs, hero/fold,
pills/chips, badges/dot indicators, tooltips, skeleton loader, FAB, and (via
native Streamlit) toasts, modal dialogs, kebab popover, and the sidebar-as-drawer.

All dynamic text inserted into HTML is escaped (html.escape) to avoid XSS.
"""
import html as _html

import streamlit as st

PURPLE = "#A100FF"
DARK = "#1A1A1A"
GREY = "#505050"
LGREY = "#F2F2F2"
GREEN = "#1F9D55"
ORANGE = "#E8830C"
RED = "#C00000"

SEV_COLOR = {"Gap": RED, "Partial": ORANGE, "Covered": GREEN,
             "High": RED, "Medium": ORANGE, "Low": GREY, "None": GREEN}


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def inject_theme():
    st.markdown(
        f"""
        <style>
          /* ---------- Typography & base ---------- */
          html, body, [class*="css"], .stMarkdown, .stButton>button,
          input, textarea, select, [data-baseweb="tab"] {{ font-family: Arial, Helvetica, sans-serif !important; }}
          .stApp {{ background: #FFFFFF; }}

          /* ---------- White space / gutters ---------- */
          .block-container {{ padding-top: 1.2rem; padding-bottom: 5rem; max-width: 1200px; }}

          /* ---------- Sticky glassmorphic top nav ---------- */
          .agx-topbar {{
            position: sticky; top: 0; z-index: 1000;
            display: flex; align-items: center; justify-content: space-between;
            padding: 10px 18px; margin: -8px 0 12px 0;
            background: rgba(255,255,255,0.72);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            border-bottom: 2px solid {PURPLE};
          }}
          .agx-wordmark {{ font-weight: 700; color: {PURPLE}; font-size: 15px; letter-spacing:.2px; }}
          .agx-crumbs {{ color: {GREY}; font-size: 12.5px; }}
          .agx-crumbs b {{ color: {DARK}; }}
          .agx-crumbs .sep {{ color: {PURPLE}; padding: 0 6px; }}

          /* ---------- Hero (above the fold) ---------- */
          .agx-hero h1 {{ color: {PURPLE}; font-size: 30px; margin: 0 0 4px 0; }}
          .agx-hero p {{ color: {GREY}; font-size: 13px; margin: 0; }}

          /* ---------- Pills / chips ---------- */
          .agx-chip {{ display:inline-block; padding: 3px 11px; border-radius: 999px;
            font-size: 12px; font-weight: 600; margin: 3px 6px 3px 0; color:#fff; }}
          .agx-chip.ghost {{ background: {LGREY}; color: {DARK}; border: 1px solid #E0D3F5; }}

          /* ---------- Badge / dot ---------- */
          .agx-badge {{ display:inline-block; min-width: 18px; text-align:center; padding: 1px 7px;
            border-radius: 999px; background: {RED}; color:#fff; font-size: 11px; font-weight:700; }}
          .agx-dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }}

          /* ---------- Tabs -> Accenture style ---------- */
          [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid #E6E6E6; }}
          [data-baseweb="tab"] {{ font-weight: 600; color: {GREY}; }}
          [data-baseweb="tab"][aria-selected="true"] {{ color: {PURPLE}; }}
          [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{ background-color: {PURPLE} !important; }}

          /* ---------- Buttons ---------- */
          .stButton>button[kind="primary"], .stDownloadButton>button[kind="primary"] {{
            background: {PURPLE}; border-color: {PURPLE}; border-radius: 8px; font-weight: 600; }}
          .stButton>button[kind="secondary"] {{ border-radius: 8px; }}

          /* ---------- Sidebar (drawer) ---------- */
          [data-testid="stSidebar"] {{ background: #FBF9FE; border-right: 1px solid #ECE3F8; }}

          /* ---------- Alerts radius ---------- */
          [data-testid="stAlert"] {{ border-radius: 10px; }}

          /* ---------- Skeleton loader ---------- */
          .agx-skel {{ height: 16px; border-radius: 6px; margin: 10px 0;
            background: linear-gradient(90deg,#eee 25%,#f5f5f5 37%,#eee 63%);
            background-size: 400% 100%; animation: agxsk 1.3s ease infinite; }}
          @keyframes agxsk {{ 0%{{background-position:100% 50%}} 100%{{background-position:0 50%}} }}

          /* ---------- FAB ---------- */
          .agx-fab {{ position: fixed; right: 26px; bottom: 26px; z-index: 1001;
            width: 54px; height: 54px; border-radius: 50%; background: {PURPLE}; color:#fff;
            display:flex; align-items:center; justify-content:center; font-size: 24px;
            box-shadow: 0 4px 14px rgba(161,0,255,.4); text-decoration:none; }}
          .agx-fab:hover {{ filter: brightness(1.08); }}

          /* ---------- Scrollbar ---------- */
          ::-webkit-scrollbar-thumb {{ background: #D9C2F5; border-radius: 6px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def top_nav(crumbs: list[str], right_html: str = ""):
    trail = f' <span class="sep">›</span> '.join(
        (f"<b>{esc(c)}</b>" if i == len(crumbs) - 1 else esc(c)) for i, c in enumerate(crumbs))
    st.markdown(
        f"""<div class="agx-topbar">
              <div><span class="agx-wordmark">accenture &gt;</span>
                   &nbsp;&nbsp;<span class="agx-crumbs">{trail}</span></div>
              <div>{right_html}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str, chips: list[tuple] | None = None):
    chip_html = "".join(chip(label, color, ghost) for (label, color, ghost) in (chips or []))
    st.markdown(
        f"""<div class="agx-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p>
            <div style="margin-top:8px">{chip_html}</div></div>""",
        unsafe_allow_html=True,
    )


def chip(label: str, color: str = PURPLE, ghost: bool = False) -> str:
    if ghost:
        return f'<span class="agx-chip ghost">{esc(label)}</span>'
    return f'<span class="agx-chip" style="background:{color}">{esc(label)}</span>'


def badge(text) -> str:
    return f'<span class="agx-badge">{esc(text)}</span>'


def dot(color: str) -> str:
    return f'<span class="agx-dot" style="background:{color}"></span>'


def skeleton(lines: int = 5):
    """Return a placeholder container rendering animated skeleton bars."""
    ph = st.empty()
    bars = "".join(
        f'<div class="agx-skel" style="width:{w}%"></div>'
        for w in ([90, 70, 80, 60, 75][:lines] + [70] * max(0, lines - 5)))
    ph.markdown(bars, unsafe_allow_html=True)
    return ph


def fab(href: str = "#agx-upload", symbol: str = "↑", tip: str = "Go to upload"):
    st.markdown(f'<a class="agx-fab" href="{esc(href)}" title="{esc(tip)}">{esc(symbol)}</a>',
                unsafe_allow_html=True)
