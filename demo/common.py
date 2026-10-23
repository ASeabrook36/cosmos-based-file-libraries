"""Shared setup for the demo pages."""

from __future__ import annotations

import streamlit as st

from fli_demo.client import IndexClient, IndexError_
from fli_demo.registry import Registry


@st.cache_resource
def client() -> IndexClient:
    return IndexClient.from_env()


@st.cache_resource
def registry() -> Registry:
    return Registry()


def guard(fn, *args, **kwargs):
    """Call the index; show a refusal instead of a stack trace."""
    try:
        return fn(*args, **kwargs)
    except IndexError_ as exc:
        st.error(str(exc))
    except Exception as exc:  # connection refused, bad key
        st.error(f"Index API unreachable or misconfigured: {type(exc).__name__}")
    return None
