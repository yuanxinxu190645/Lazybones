import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ui_rendering import CanvasFormLayout, stabilize_scrolled_text


class CanvasStub:
    def __init__(self):
        self.bindings = {}
        self.jobs = {}
        self.next_job = 0
        self.regions = []
        self.widths = []
        self.scrolls = []

    def bind(self, event, callback, **kwargs):
        self.bindings[event] = callback

    def configure(self, **kwargs):
        self.regions.append(kwargs["scrollregion"])

    def itemconfigure(self, item, **kwargs):
        self.widths.append(kwargs["width"])

    def after(self, delay, callback):
        self.next_job += 1
        self.jobs[self.next_job] = callback
        return self.next_job

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def yview(self, *args):
        self.scrolls.append(args)

    def settle(self):
        jobs, self.jobs = self.jobs, {}
        for callback in jobs.values():
            callback()


class CanvasRenderingTests(unittest.TestCase):
    def setUp(self):
        self.canvas = CanvasStub()
        self.inner = CanvasStub()
        self.layout = CanvasFormLayout(self.canvas, self.inner, 1)

    def resize(self, width, height=700):
        self.canvas.bindings["<Configure>"](SimpleNamespace(width=width, height=height))

    def test_scroll_motion_does_not_recalculate_content_region(self):
        for y in range(100):
            self.inner.bindings["<Configure>"](
                SimpleNamespace(width=800, height=2000, x=0, y=-y))
        self.assertEqual(self.canvas.regions, [(0, 0, 800, 2000)])
        self.inner.bindings["<Configure>"](SimpleNamespace(width=800, height=2400))
        self.assertEqual(self.canvas.regions[-1], (0, 0, 800, 2400))

    def test_resize_burst_applies_only_final_width(self):
        self.resize(800)
        for width in range(801, 1001):
            self.resize(width)
        self.assertEqual(self.canvas.widths, [800])
        self.assertEqual(len(self.canvas.jobs), 1)
        self.canvas.settle()
        self.assertEqual(self.canvas.widths, [800, 1000])

    def test_height_only_change_never_reflows_width(self):
        self.resize(800)
        for height in range(500, 800):
            self.resize(800, height)
        self.assertFalse(self.canvas.jobs)
        self.assertEqual(self.canvas.widths, [800])

    def test_return_to_original_width_cancels_pending_reflow(self):
        self.resize(800)
        self.resize(900)
        self.resize(800)
        self.canvas.settle()
        self.assertEqual(self.canvas.widths, [800])

    def test_drag_burst_keeps_final_thumb_position(self):
        for fraction in ("0.1", "0.2", "0.6", "0.8"):
            self.layout.yview("moveto", fraction)
        self.assertEqual(len(self.canvas.jobs), 1)
        self.canvas.settle()
        self.assertEqual(self.canvas.scrolls, [("moveto", "0.8")])

    def test_arrow_scroll_follows_pending_drag_without_losing_either(self):
        self.layout.yview("moveto", "0.8")
        self.layout.yview("scroll", "1", "units")
        self.assertEqual(self.canvas.scrolls,
                         [("moveto", "0.8"), ("scroll", "1", "units")])
        self.assertFalse(self.canvas.jobs)

    def test_closing_window_cancels_pending_work(self):
        self.resize(800)
        self.resize(900)
        self.layout.yview("moveto", "0.8")
        self.canvas.bindings["<Destroy>"](SimpleNamespace(widget=self.canvas))
        self.assertFalse(self.canvas.jobs)


class TextLayoutTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.geometry("640x400")
        self.text = ScrolledText(self.root, wrap=tk.WORD)
        self.text.pack(fill=tk.BOTH, expand=True)
        stabilize_scrolled_text(self.text)
        self.root.update()

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def settle(self):
        done = tk.BooleanVar(master=self.root, value=False)
        self.root.after(220, lambda: done.set(True))
        self.root.wait_variable(done)
        self.root.update_idletasks()

    def test_resize_preserves_content_font_selection_and_scrollbar(self):
        content = "Sample research paragraph for wrapping and copying.\n" * 100
        self.text.insert("1.0", content)
        self.text.tag_add("sel", "1.0", "1.6")
        font = self.text.cget("font")
        for geometry in ("460x350", "900x480", "600x400"):
            self.root.geometry(geometry)
            self.root.update()
            self.settle()
            self.assertEqual(self.text.winfo_width(),
                             self.text.frame.winfo_width() - self.text.vbar.winfo_reqwidth())
            self.assertEqual(self.text.cget("font"), font)
            self.assertEqual(self.text.get("1.0", "end-1c"), content)
            self.assertEqual(tuple(map(str, self.text.tag_ranges("sel"))), ("1.0", "1.6"))
            self.assertTrue(self.text.vbar.winfo_ismapped())
        self.text.yview_moveto(1.0)
        self.root.update()
        self.assertAlmostEqual(self.text.yview()[1], 1.0)

    def test_destroy_with_pending_resize_has_no_late_callback(self):
        self.root.geometry("450x300")
        self.root.update_idletasks()
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        # ScrolledText.destroy destroys its Text child; its frame lives until root.
        self.text.destroy()
        self.settle()
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
