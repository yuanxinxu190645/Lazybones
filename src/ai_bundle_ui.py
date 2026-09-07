"""Independent AI-bundle workspace; no extraction/review/database operations."""

from dataclasses import asdict
import multiprocessing as mp
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ai_bundle import BundleOptions, MODE_LABELS, bundle_worker
from schema_loader import save_settings
from ui_rendering import stabilize_scrolled_text


def human_size(size):
    return f"{size / 1024 / 1024:.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"


class AIBundlePage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.files = []
        self.result = None
        self.process = None
        self.messages = None
        self.cancel = None
        self._poll_job = None
        self._busy = False
        preferences = app.settings.get("ai_bundle", {})
        if not isinstance(preferences, dict):
            preferences = {}
        ttk.Label(self, text="AI 资料包", style="PageTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(self, text="全文转换 · 来源可追溯 · 按预算分包", style="Muted.TLabel").pack(anchor=tk.W, pady=(3, 8))
        notice = ttk.Label(self, text="文件变小 ≠ 上下文无限。图表需连同图片上传；ZIP 主要用于保存和搬运。",
                           style="Muted.TLabel")
        notice.pack(anchor=tk.W, pady=(0, 8))

        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, pady=(0, 6))
        self.add_button = ttk.Button(controls, text="添加 PDF / Word", command=self._choose_files)
        self.add_button.pack(side=tk.LEFT)
        self.folder_button = ttk.Button(controls, text="添加文件夹", command=self._choose_folder)
        self.folder_button.pack(side=tk.LEFT, padx=6)
        self.remove_button = ttk.Button(controls, text="移除选中", command=self._remove_selected)
        self.remove_button.pack(side=tk.LEFT)
        self.count = tk.StringVar(value="拖入文件或文件夹，独立于现有文献队列")
        ttk.Label(controls, textvariable=self.count, style="Muted.TLabel").pack(side=tk.LEFT, padx=12)

        options = ttk.LabelFrame(self, text="转换与分包", padding=8)
        options.pack(fill=tk.X, pady=(0, 8))
        row = ttk.Frame(options)
        row.pack(fill=tk.X)
        ttk.Label(row, text="转换模式").pack(side=tk.LEFT)
        self.mode = tk.StringVar(value=MODE_LABELS.get(preferences.get("mode"), MODE_LABELS["visual"]))
        self.mode_combo = ttk.Combobox(row, textvariable=self.mode, state="readonly", width=20,
                                      values=list(MODE_LABELS.values()))
        self.mode_combo.pack(side=tk.LEFT, padx=6)
        ttk.Button(row, text="说明", command=self._explain_mode).pack(side=tk.LEFT)
        ttk.Label(row, text="每包预算").pack(side=tk.LEFT, padx=(16, 4))
        saved_tokens = str(preferences.get("part_tokens", 16000))
        self.tokens = tk.StringVar(value=saved_tokens if saved_tokens.isdigit() else "16000")
        self.tokens_combo = ttk.Combobox(row, textvariable=self.tokens,
                                        values=["8000", "16000", "32000", "64000"], width=9)
        self.tokens_combo.pack(side=tk.LEFT)
        ttk.Label(row, text="tokens（保守分包）").pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="说明", command=self._explain_budget).pack(side=tk.LEFT)
        self.ocr = tk.BooleanVar(value=bool(preferences.get("ocr", True)))
        self.ocr_check = ttk.Checkbutton(row, text="扫描页 OCR", variable=self.ocr)
        self.ocr_check.pack(side=tk.LEFT, padx=12)

        path_row = ttk.Frame(options)
        path_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(path_row, text="输出目录").pack(side=tk.LEFT)
        default_output = Path(app.settings.get("project_dir", ".")) / "exports" / "ai_bundles"
        self.output = tk.StringVar(value=preferences.get("output_dir", str(default_output)))
        self.output_entry = ttk.Entry(path_row, textvariable=self.output)
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.browse_button = ttk.Button(path_row, text="选择目录", command=self._choose_output)
        self.browse_button.pack(side=tk.LEFT)

        actions = ttk.Frame(self)
        actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        self.start_button = ttk.Button(actions, text="生成 AI 资料包", style="Accent.TButton", command=self._start)
        self.start_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(actions, text="停止", state=tk.DISABLED, command=self._cancel)
        self.cancel_button.pack(side=tk.LEFT, padx=6)
        self.open_button = ttk.Button(actions, text="打开结果目录", state=tk.DISABLED, command=self._open_result)
        self.open_button.pack(side=tk.LEFT, padx=6)
        self.copy_button = ttk.Button(actions, text="复制分析提示词", state=tk.DISABLED, command=self._copy_prompt)
        self.copy_button.pack(side=tk.LEFT, padx=6)
        self.progress_bar = ttk.Progressbar(actions, mode="indeterminate", length=110)
        self.progress_bar.pack(side=tk.RIGHT)

        self.status = tk.StringVar(value="仅本地转换，不调用大模型，不自动上传。支持 PDF / DOCX；旧版 DOC 请先另存为 DOCX。")
        ttk.Label(self, textvariable=self.status, style="Muted.TLabel").pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.summary = tk.StringVar(value="完成后将显示原文件体积、Markdown 体积、ZIP 体积与文本 token 估算。")
        ttk.Label(self, textvariable=self.summary).pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pane)
        right = ttk.LabelFrame(pane, text="结果预览 / 转换进度", padding=6)
        pane.add(left, weight=3)
        pane.add(right, weight=4)
        self.tree = ttk.Treeview(left, columns=("name", "size", "status"), show="headings", selectmode="extended")
        for name, label, width in (("name", "原文件", 260), ("size", "体积", 80), ("status", "状态", 100)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=60, stretch=name == "name")
        scroll = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._preview)
        self.preview = scrolledtext.ScrolledText(right, wrap=tk.WORD, state=tk.DISABLED, width=55)
        self.preview.pack(fill=tk.BOTH, expand=True)
        stabilize_scrolled_text(self.preview)
        self._set_preview("使用方法\n\n1. 拖入 PDF / DOCX。\n2. 选择转换模式和分包预算。\n3. 生成后先查看质量报告，再把全文或分段文件交给 AI。\n\n图文保留会附带每一页 PDF 的图片，体积可能变大，但方便核对图表、公式与阅读顺序。\n\n不生成摘要，不主动删除参考文献，不按原抽取流程的字数上限截断。")
        try:
            from tkinterdnd2 import DND_FILES
            for widget in (self, self.tree, self.preview):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._drop)
        except tk.TclError:
            pass
        self.bind("<Destroy>", self._destroy, add="+")

    def _explain_mode(self):
        messagebox.showinfo("转换模式", "文本优先：保留可提取文字和表格，文件通常更小，但不含图片。\n\n图文保留：额外保存每页 PDF 的 PNG，以及 Word 中的原始图片；公式保留原结构用于核对。图片不会因 Markdown 链接而自动被 AI 读取，需一起上传。\n\n两种模式均不摘要、不主动截断全文，但复杂排版和 OCR 仍可能出错。", parent=self)

    def _explain_budget(self):
        messagebox.showinfo("分包预算", "这是每个分段的保守预算，另外预留 25% 空间给问题和回答。文本 token 数只是估算，图片占用未计入。\n\n分包保留全部已提取文字；超长表格可能跨段，完整版本仍在每篇全文中。\n\n在同一会话把所有分段都发过去，仍可能超过总上下文。大量文献应分批分析，或放进支持检索的资料库。", parent=self)

    def _choose_files(self):
        self.add_files(filedialog.askopenfilenames(parent=self, filetypes=[("文献", "*.pdf *.docx"), ("所有文件", "*.*")]))

    def _choose_folder(self):
        directory = filedialog.askdirectory(parent=self)
        if directory:
            self.add_files([directory])

    def _drop(self, event):
        self.add_files(self.tk.splitlist(event.data))
        return "break"

    def add_files(self, paths):
        if self._busy:
            return
        known = {str(path).casefold() for path in self.files}
        rejected = 0
        for value in paths:
            path = Path(value).resolve()
            candidates = sorted(path.glob("*")) if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in {".pdf", ".docx"}:
                    rejected += 1
                    continue
                if str(candidate).casefold() not in known:
                    known.add(str(candidate).casefold())
                    self.files.append(candidate)
        self.result = None
        self._refresh_files()
        self.open_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        if rejected:
            self.status.set(f"跳过 {rejected} 个不支持的文件；旧版 DOC 请先另存为 DOCX。文件夹仅读取当前层。")

    def _refresh_files(self):
        self.tree.delete(*self.tree.get_children())
        for index, path in enumerate(self.files):
            size = path.stat().st_size if path.exists() else 0
            self.tree.insert("", tk.END, iid=str(index), values=(path.name, human_size(size), "待转换"))
        self.count.set(f"共 {len(self.files)} 个文件 · 文件夹仅扫描当前层")

    def _remove_selected(self):
        if self._busy:
            return
        indices = {int(item) for item in self.tree.selection()}
        self.files = [path for index, path in enumerate(self.files) if index not in indices]
        self.result = None
        self._refresh_files()
        self.open_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)

    def _choose_output(self):
        directory = filedialog.askdirectory(parent=self)
        if directory:
            self.output.set(directory)

    def _set_preview(self, text):
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", text)
        self.preview.configure(state=tk.DISABLED)

    def _preview(self, event=None):
        if not self.result or self._busy or not self.tree.selection():
            return
        index = int(self.tree.selection()[0])
        records = self.result["documents"]
        if index >= len(records):
            return
        record = records[index]
        if record.get("markdown"):
            file = Path(self.result["directory"]) / record["markdown"]
            with file.open(encoding="utf-8") as stream:
                text = stream.read(16001)
            suffix = "\n\n［界面仅预览前 16,000 字，导出文件保留全文。］" if len(text) > 16000 else ""
            self._set_preview(text[:16000] + suffix)
        else:
            self._set_preview(record.get("error") or f"状态：{record['status']}\n重复来源：{record.get('duplicate_of', '无')}")

    def _set_busy(self, busy):
        self._busy = busy
        for control in (self.add_button, self.folder_button, self.remove_button, self.start_button,
                        self.browse_button, self.output_entry, self.ocr_check):
            control.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.mode_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.tokens_combo.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        if busy:
            self.progress_bar.start(60)
        else:
            self.progress_bar.stop()

    def _start(self):
        if self._busy:
            return
        if not self.files:
            messagebox.showinfo("添加文献", "请先添加 PDF / DOCX 文件。", parent=self)
            return
        try:
            modes = {label: key for key, label in MODE_LABELS.items()}
            options = BundleOptions(mode=modes[self.mode.get()], part_tokens=int(self.tokens.get()), ocr=self.ocr.get())
            options.validate()
            if not self.output.get().strip():
                raise ValueError("请选择输出目录")
            output = str(Path(self.output.get()).resolve())
        except (ValueError, KeyError) as exc:
            messagebox.showerror("参数无效", str(exc), parent=self)
            return
        self.app.settings["ai_bundle"] = {**asdict(options), "output_dir": output}
        save_settings(self.app.settings)
        context = mp.get_context("spawn")
        self.messages = context.Queue()
        self.cancel = context.Event()
        self.result = None
        self.open_button.configure(state=tk.DISABLED)
        self.copy_button.configure(state=tk.DISABLED)
        self._set_preview("正在启动独立转换进程……\n每次生成一个新的输出目录，原文件不变。")
        self._set_busy(True)
        self.status.set("转换中；可继续使用其他功能页。停止会在当前处理步骤完成后生效。")
        self.process = context.Process(target=bundle_worker,
                                       args=([str(path) for path in self.files], output, asdict(options),
                                             self.app.performance_manager.budget.to_dict(), self.messages, self.cancel),
                                       daemon=True)
        try:
            self.process.start()
        except Exception as exc:
            self._set_busy(False)
            self.messages.close()
            messagebox.showerror("无法启动转换", str(exc), parent=self)
            return
        self._poll_job = self.after(120, self._poll)

    def _poll(self):
        self._poll_job = None
        last_progress = None
        terminal = False
        try:
            for _ in range(100):
                kind, value = self.messages.get_nowait()
                if kind == "progress":
                    last_progress = value
                elif kind == "done":
                    terminal = True
                    self._finished(value)
                    break
                elif kind == "error":
                    terminal = True
                    self._set_busy(False)
                    self.status.set("生成失败；已有目录若标为 processing，表示任务未完整完成。")
                    self._set_preview(value)
                    break
        except queue.Empty:
            pass
        if terminal:
            self.process.join(timeout=0.1)
            self.messages.close()
            return
        if last_progress:
            self._set_preview(last_progress)
        if not self.process.is_alive():
            self._set_busy(False)
            self.status.set("转换进程已退出；请检查输出目录中的 manifest.json 和质量报告。")
            self.messages.close()
            return
        self._poll_job = self.after(120, self._poll)

    def _finished(self, result):
        self.result = result
        self._set_busy(False)
        summary = result["summary"]
        zip_size = Path(result["zip"]).stat().st_size if result.get("zip") else 0
        self.summary.set(f"原文件 {human_size(summary['source_bytes'])} → Markdown {human_size(summary['markdown_bytes'])}"
                         f" · ZIP {human_size(zip_size)} · 文本约 {summary['estimated_text_tokens']:,} tokens · {summary['parts']} 个分段")
        self.status.set(f"{'已停止' if result['cancelled'] else '已生成'}：完成 {summary['completed']}，失败 {summary['failed']}，"
                        f"缺少可读文字 {summary.get('needs_review', 0)}，重复 {summary['duplicates']}。图像 token 未计入，请先看质量报告。")
        labels = {"completed": "完成·请核对", "needs_review": "内容需核对", "failed": "失败",
                  "duplicate": "完全重复", "cancelled": "已停止", "not_processed": "未处理"}
        for index, record in enumerate(result["documents"]):
            self.tree.set(str(index), "status", labels.get(record["status"], record["status"]))
        report = Path(result["directory"]) / "先读我_质量报告.md"
        with report.open(encoding="utf-8") as stream:
            self._set_preview(stream.read(16000))
        self.open_button.configure(state=tk.NORMAL)
        self.copy_button.configure(state=tk.NORMAL)

    def _cancel(self):
        if self.cancel is not None:
            self.cancel.set()
            self.cancel_button.configure(state=tk.DISABLED)
            self.status.set("正在停止；等当前页面/表格处理结束，已完成文件会保留。")

    def _open_result(self):
        if self.result:
            os.startfile(self.result["directory"])

    def _copy_prompt(self):
        if self.result:
            text = (Path(self.result["directory"]) / "分析提示词.txt").read_text(encoding="utf-8")
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("分析提示词已复制；请填写你的分析任务，并按质量报告准备附件。")

    def _destroy(self, event):
        if event.widget is self:
            if self.cancel is not None:
                self.cancel.set()
            if self._poll_job is not None:
                self.after_cancel(self._poll_job)
