"""Preview the real application with disposable settings and sample papers.

Run with .venv/Scripts/python scripts/preview_ui.py, or add --check for layout
and theme-switch checks. No personal configuration or database is loaded.
"""

import argparse
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import schema_loader
import ui
from database import insert_paper
from tkinter import ttk


def check_layout(app):
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args[1]))
    assert [app.main_notebook.tab(tab, "text") for tab in app.main_notebook.tabs()] == ["数据库建立", "AI 资料包"]
    assert [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()] == ["文献抽取", "内容审核", "数据管理"]
    app._select_database_tab(app.tab_review)
    assert app._review_tab_is_active()
    app.main_notebook.select(app.tab_ai_bundle)
    assert not app._review_tab_is_active()
    app._show_settings()
    assert not app._review_tab_is_active()
    app._show_workspace()
    assert app.main_notebook.select() == str(app.tab_ai_bundle)
    app._select_database_tab(app.tab_review)
    assert app._review_tab_is_active()
    for theme in ("classic", "codex_dark", "codex_light", "speed", "classic"):
        app.theme_manager.apply({"theme": theme})
        app.update()
        if theme == "speed":
            assert "Lazybones." not in str(app.theme_manager.style.layout("TButton"))
        app.review_detail.tag_raise("identity_block")
        app.theme_manager.apply_widget(app.review_detail)
        assert app.review_detail.tag_names()[-1] == "sel"
        for tab in app.notebook.tabs():
            app._select_database_tab(tab)
            app.update()
        app.main_notebook.select(app.tab_ai_bundle)
        app.update()
        app._show_settings()
        app.update()
        app._show_workspace()
        assert not errors, errors
    for scale in (100, 125):
        app.theme_manager.apply({"theme": "classic", "font_scale": scale})
        app.geometry("1280x720")
        app._select_database_tab(app.tab_manage)
        app.update()
        for bar in (app.manage_action_bar, app.manage_page_bar):
            assert bar.winfo_ismapped(), "Hidden action bar"
            assert bar.winfo_rooty() + bar.winfo_height() <= app.winfo_rooty() + app.winfo_height()
            for button in bar.winfo_children():
                assert button.winfo_ismapped(), f"Hidden control: {button}"
                assert button.winfo_x() + button.winfo_width() <= bar.winfo_width(), f"Clipped control: {button}"
                assert button.winfo_width() >= button.winfo_reqwidth(), f"Truncated control: {button}"
        app._select_database_tab(app.tab_review)
        app.update()
        for row in app.review_action_bar.winfo_children():
            for button in row.winfo_children():
                if isinstance(button, (ttk.Button, ttk.Menubutton)):
                    assert button.winfo_ismapped(), f"Hidden review action: {button.cget('text')}"
                    assert button.winfo_x() + button.winfo_width() <= row.winfo_width(), f"Clipped review action: {button.cget('text')}"
                    assert button.winfo_width() >= button.winfo_reqwidth(), f"Truncated review action: {button.cget('text')}"
        app._select_database_tab(app.tab_extract)
        app.update()
        def check_children(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Combobox)):
                    assert child.winfo_ismapped(), f"Hidden extract control: {child}"
                    assert child.winfo_width() >= child.winfo_reqwidth(), f"Clipped extract control: {child}"
                check_children(child)
        check_children(app.tab_extract)
    assert not errors, errors
    print("PASS: five theme transitions, five pages, text selection, speed controls, 720p toolbars at 100/125%", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--theme", default="classic")
    parser.add_argument("--bundle-check", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lazybones-ui-preview-") as temp:
        settings = {
            "project_dir": str(Path(temp) / "project"),
            "current_db": "main", "theme": args.theme,
            "schema_version": "v1.0_basic", "auto_update_check": False,
            "gpu_mode": "off", "performance_mode": "eco",
            "api_key": "", "provider": "DeepSeek", "manage_page_size": 100,
        }
        with patch.object(schema_loader, "get_config_path", return_value=Path(temp) / "settings.json"), \
                patch.object(ui, "load_settings", return_value=settings), \
                patch.object(ui, "list_prompt_versions", return_value=["v1.0_basic"]):
            app = ui.App()
            app.title("Lazybones · 外观预览（示例数据）")
            for index in range(1, 25):
                insert_paper(app.db_path, f"2026_Research_notes_{index:02}__zh", {
                    "Title": f"Research notes — sample paper {index}",
                    "Authors_Full": "Lin Chen; Wei Zhang",
                    "Year": "2026", "Journal_Full": "Example Journal",
                    "DOI": f"10.0000/example.{index}",
                }, "v1.0_basic", "示例模型", "zh")
            app._refresh_manage_tab()
            app._log("工作台已就绪。将文献拖入左侧队列即可开始。")
            app._log("此窗口仅包含示例数据，用于检查外观。")
            if args.bundle_check:
                import fitz
                source = Path(temp) / "Example.pdf"
                with fitz.open() as document:
                    page = document.new_page()
                    page.insert_text((50, 60), "Sample document for end-to-end UI export, 298 K, 0.025 mol/L.")
                    document.save(source)
                workspace = app.tab_ai_bundle
                app.main_notebook.select(workspace)
                workspace.add_files([source])
                workspace.mode.set("文本优先")
                workspace.ocr.set(False)
                workspace.output.set(str(Path(temp) / "bundles"))
                errors = []
                app.report_callback_exception = lambda *items: errors.append(str(items[1]))
                workspace._start()
                done = ui.tk.BooleanVar(value=False)
                def finished():
                    if workspace._busy:
                        app.after(100, finished)
                    else:
                        done.set(True)
                app.after(100, finished)
                timeout = app.after(30000, lambda: done.set(True))
                try:
                    app.wait_variable(done)
                    assert not errors, errors
                    assert workspace.result, workspace.status.get()
                    assert workspace.result["summary"]["completed"] == 1, workspace.result
                    assert Path(workspace.result["zip"]).is_file()
                    assert workspace.tree.set("0", "status") == "完成·请核对"
                    print("PASS: AI bundle page -> independent worker -> ZIP -> visible completion status", flush=True)
                finally:
                    app.after_cancel(timeout)
                    if workspace.process and workspace.process.is_alive():
                        workspace.cancel.set()
                        workspace.process.join(2)
                    app.destroy()
            elif args.check:
                try:
                    check_layout(app)
                finally:
                    app.destroy()
            else:
                app.mainloop()


if __name__ == "__main__":
    main()
