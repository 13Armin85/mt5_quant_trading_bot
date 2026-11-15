"""NoaTech's shared visual identity and safe display primitives."""
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).parent / "assets"


def stylesheet():
    return (ASSETS / "noatech.css").read_text(encoding="utf-8-sig")


@lru_cache(maxsize=1)
def mark():
    return (ASSETS / "noatech-mark.svg").read_text(encoding="utf-8")


def apply_theme():
    st.markdown("<style>" + stylesheet() + "</style>", unsafe_allow_html=True)


def e(value):
    return escape(str(value if value is not None else ""))


def fa(value):
    return str(value).translate(str.maketrans("0123456789.,", "۰۱۲۳۴۵۶۷۸۹٫٬"))


def icon(name, size=20):
    paths = {
        "link": '<path d="m10 13 4-4M8.5 15.5l-1 1a4.25 4.25 0 0 1-6-6l4-4a4.25 4.25 0 0 1 6 0m0 2 1-1a4.25 4.25 0 0 1 6 6l-4 4a4.25 4.25 0 0 1-6 0" transform="translate(2 -1)"/>',
        "shield": '<path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6z"/><path d="m8.5 11.5 2.5 2.5 4.5-5"/>',
        "activity": '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
        "wallet": '<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 8V6a3 3 0 0 1 3-3h12M21 11h-6v5h6"/><path d="M17 13.5h.1"/>',
        "layers": '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
        "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
        "spark": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
        "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c-5 5-5 13 0 18 5-5 5-13 0-18"/>',
        "check": '<path d="m5 12 4 4L19 6"/>',
    }
    return f'<svg class="nv-icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths.get(name, paths["activity"])}</svg>'


def brand(subtitle="میزکار معاملات"):
    return f'<div class="nv-brand">{mark()}<div><strong>نواتک</strong><span>NOATECH</span></div><small>{e(subtitle)}</small></div>'


def badge(label, tone="muted"):
    tone = tone if tone in {"good", "warn", "bad", "accent", "muted"} else "muted"
    return f'<span class="nv-badge nv-{tone}"><i></i>{e(label)}</span>'


def section(title, subtitle="", eyebrow=""):
    st.markdown(f'<div class="nv-section"><div><span class="nv-eyebrow">{e(eyebrow)}</span><h3>{e(title)}</h3><p>{e(subtitle)}</p></div></div>', unsafe_allow_html=True)


def empty_state(title, description, symbol="activity"):
    st.markdown(f'<div class="nv-empty"><div>{icon(symbol,26)}</div><h4>{e(title)}</h4><p>{e(description)}</p></div>', unsafe_allow_html=True)
