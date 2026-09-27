#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""book2pdf 薄壳入口（真正的实现见 ./book2pdf/ 包）。

用法：
    python book2pdf.py --base https://host/book/ --out out/书.pdf
    python book2pdf.py --base https://host/book/ --out out/书.pdf \
        --mode translated --target-lang en --provider local \
        --base-url http://127.0.0.1:15721/v1 --model deepseek-chat
    python book2pdf.py --base https://host/book/ --out out/书.pdf --provider null

依赖：
    pip install playwright pymupdf
    # 浏览器用系统自带 Edge（channel="msedge"），无需 playwright install
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from book2pdf.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
