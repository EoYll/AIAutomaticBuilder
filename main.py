#!/usr/bin/env python
"""兼容入口：``python main.py`` 等价于 ``python -m rpa_builder.gui``。

未安装（editable install）时也能直接运行：把 src 加入模块搜索路径。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from rpa_builder.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
