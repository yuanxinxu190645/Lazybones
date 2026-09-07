# F:\MOFCNM\src\main.py

import sys
import os
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def _get_resource_base() -> Path:
    """只读资源根目录（prompts、Readme）：程序安装目录"""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        return Path(__file__).parent.parent


def _get_writable_base() -> Path:
    r"""可写数据根目录（config、project）：AppData\Roaming\Lazybones"""
    if getattr(sys, 'frozen', False):
        appdata = Path(os.environ.get("APPDATA", Path.home()))
        writable = appdata / "Lazybones"
        writable.mkdir(parents=True, exist_ok=True)
        return writable
    else:
        return Path(__file__).parent.parent


def check_dependencies():
    missing = []
    required = {
        "fitz": "pymupdf",
        "pdfplumber": "pdfplumber",
        "docx": "python-docx",
        "openai": "openai",
        "openpyxl": "openpyxl",
        "yaml": "pyyaml",
    }
    for module, pkg in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pkg)

    if missing:
        msg = (
            "缺少以下依赖库，请在终端运行：" + chr(10) + chr(10) +
            "pip install " + " ".join(missing) +
            " -i https://mirrors.aliyun.com/pypi/simple"
        )
        print(msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("依赖缺失", msg)
        except Exception:
            pass
        sys.exit(1)


def check_project_structure():
    resource_base = _get_resource_base()
    writable_base = _get_writable_base()

    # 检查提示词目录（只读，在安装目录）
    prompts_dir = resource_base / "prompts"
    if not prompts_dir.exists() or not list(prompts_dir.glob("*.yaml")):
        msg = "提示词文件未找到。请确保 " + str(prompts_dir) + " 下至少有一个 .yaml 文件。"
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("初始化失败", msg)
        except Exception:
            print(msg)
        sys.exit(1)

    # 创建配置目录（可写，在 AppData）
    config_dir = writable_base / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    settings_file = config_dir / "settings.json"
    if not settings_file.exists():
        import json
        default_settings = {
            "provider": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "",
            "concurrent": 3,
            "theme": "classic",
            "font_scale": 100,
            "ui_density": "comfortable",
            "manage_page_size": 100,
            "auto_update_check": True,
            "performance_mode": "balanced",
            "custom_cpu_workers": 2,
            "custom_ai_workers": 3,
            "custom_active_documents": 3,
            "memory_limit_percent": 75,
            "ocr_dpi": 190,
            "gpu_mode": "auto",
            "gpu_ocr_enabled": True,
            "max_chars": 80000,
            "lang_zh": True,
            "lang_en": True,
            "auto_confirm_clean_pairs": True,
            "auto_open_review": True,
            "review_lang": "zh",
            "review_schema_filter": "全部",
            "last_import_dir": "",
            "pending_files": [],
            "project_dir": str(writable_base / "project").replace("\\", "/"),
            "schema_version": "",
            "ai_timeout": 600,
            "ai_max_retries": 3,
            "max_input_tokens": 800000,
        }
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, ensure_ascii=False, indent=2)
        print("已创建默认配置文件：" + str(settings_file))

    # 创建项目数据目录
    project_dir = writable_base / "project"
    project_dir.mkdir(parents=True, exist_ok=True)


def main():
    check_dependencies()
    check_project_structure()

    try:
        from ui import App
        app = App()

        try:
            icon_path = _get_resource_base() / "icon.ico"
            if icon_path.exists():
                app.iconbitmap(str(icon_path))
        except Exception:
            pass

        app.update_idletasks()
        w = app.winfo_width()
        h = app.winfo_height()
        x = (app.winfo_screenwidth() - w) // 2
        y = (app.winfo_screenheight() - h) // 2
        app.geometry("+" + str(x) + "+" + str(y))
        app.mainloop()

    except Exception as e:
        err_msg = "程序启动失败：" + type(e).__name__ + ": " + str(e)
        err_msg += chr(10) + chr(10) + traceback.format_exc()
        print(err_msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动错误", err_msg)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
