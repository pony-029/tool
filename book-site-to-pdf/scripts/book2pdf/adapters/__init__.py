# -*- coding: utf-8 -*-
"""站点适配器层：把各站的差异消解成同一份 BookIR。"""
from __future__ import annotations

from .base import (FetchError, HttpFetcher, SiteAdapter, SiteProbe,
                   extract_headings, find_container, find_container_span,
                   probe_site, strip_tags, url_to_local)
from .docusaurus import DocusaurusAdapter
from .generic import GenericAdapter
from .gitbook import GitBookAdapter
from .mdbook import MdBookAdapter
from .mkdocs import MkDocsAdapter
from .multipage import MultiPageAdapter
from .registry import ADAPTERS, MIN_CONFIDENCE, detect, score_all

__all__ = [
    "FetchError", "HttpFetcher", "SiteAdapter", "SiteProbe", "probe_site",
    "extract_headings", "find_container", "find_container_span", "strip_tags",
    "url_to_local", "MdBookAdapter", "MkDocsAdapter", "DocusaurusAdapter",
    "GitBookAdapter", "GenericAdapter", "MultiPageAdapter",
    "ADAPTERS", "MIN_CONFIDENCE", "detect", "score_all",
]
