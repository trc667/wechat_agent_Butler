# -*- coding: utf-8 -*-
"""pytest 根配置：把项目根目录加入 sys.path，便于 tests 里 import 业务模块。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
