"""Small event coalescers for native Tk scrollable form layouts."""

import tkinter as tk


class CanvasFormLayout:
    """Keep form text stable during resize and ignore position-only events.

    Canvas scrolling moves its child window, which also emits Configure events.
    Only changes to the child's size require a new scroll region. Width reflow
    waits briefly for resizing to settle; scrollbar drag requests are coalesced
    without delaying wheel/button scroll commands.
    """

    def __init__(self, canvas, inner, window_id, resize_delay=150):
        self.canvas = canvas
        self.inner = inner
        self.window_id = window_id
        self.resize_delay = resize_delay
        self._resize_job = None
        self._scroll_job = None
        self._width = None
        self._pending_width = None
        self._size = None
        self._scroll_target = None
        canvas.bind("<Configure>", self._on_viewport, add="+")
        inner.bind("<Configure>", self._on_content, add="+")
        canvas.bind("<Destroy>", self._on_destroy, add="+")

    def _on_content(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        self.canvas.configure(scrollregion=(0, 0, *size))

    def _on_viewport(self, event):
        width = event.width
        if width == self._pending_width:
            return
        self._pending_width = width
        if self._resize_job is not None:
            self.canvas.after_cancel(self._resize_job)
            self._resize_job = None
        if self._width is None:
            self._apply_width()
        elif width != self._width:
            self._resize_job = self.canvas.after(self.resize_delay, self._apply_width)

    def _apply_width(self):
        self._resize_job = None
        if self._width != self._pending_width:
            self._width = self._pending_width
            self.canvas.itemconfigure(self.window_id, width=self._width)

    def yview(self, *args):
        if len(args) == 2 and args[0] == "moveto":
            self._scroll_target = args
            if self._scroll_job is None:
                self._scroll_job = self.canvas.after(16, self._flush_scroll)
        else:
            # Preserve the ordering of a pending drag followed by an arrow click.
            if self._scroll_job is not None:
                self.canvas.after_cancel(self._scroll_job)
                self._flush_scroll()
            self.canvas.yview(*args)

    def _flush_scroll(self):
        self._scroll_job = None
        target, self._scroll_target = self._scroll_target, None
        if target is not None:
            self.canvas.yview(*target)

    def _on_destroy(self, event):
        if event.widget is not self.canvas:
            return
        for job in (self._resize_job, self._scroll_job):
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except tk.TclError:
                    pass
        self._resize_job = self._scroll_job = None


def bind_canvas_form(canvas, inner, window_id, scrollbar):
    layout = CanvasFormLayout(canvas, inner, window_id)
    scrollbar.configure(command=layout.yview)
    canvas.configure(yscrollincrement=20)
    # Keep the controller alive and available for layout regression checks.
    canvas._form_layout = layout
    return layout


class StableTextLayout:
    """Delay line wrapping during horizontal resizing, keeping native text.

    ScrolledText delegates pack/place methods to its outer frame. The explicit
    Tk mixin calls below position only the Text child; the surrounding frame and
    scrollbar still follow the window immediately. No text is raster-scaled.
    """

    def __init__(self, text):
        self.text = text
        self.frame = text.frame
        self._job = None
        self._closed = False
        self._width = None
        self._pending_width = None
        self.frame.configure(width=text.winfo_reqwidth() + text.vbar.winfo_reqwidth(),
                             height=text.winfo_reqheight())
        self.frame.pack_propagate(False)
        tk.Pack.pack_forget(text)
        tk.Place.place_configure(text, x=0, y=0, width=text.winfo_reqwidth(), relheight=1)
        # A temporarily wider Text must not cover the scrollbar while shrinking.
        text.vbar.lift()
        self.frame.bind("<Configure>", self._on_resize, add="+")
        text.vbar.bind("<Configure>", self._on_resize, add="+")
        text.bind("<Destroy>", self._on_destroy, add="+")

    def _on_resize(self, event):
        if self._closed:
            return
        width = max(1, self.frame.winfo_width() - self.text.vbar.winfo_reqwidth())
        if width == self._pending_width:
            return
        self._pending_width = width
        if self._job is not None:
            self.text.after_cancel(self._job)
            self._job = None
        if self._width is None:
            self._apply_width()
        elif width != self._width:
            self._job = self.text.after(150, self._apply_width)

    def _apply_width(self):
        self._job = None
        self._width = self._pending_width
        tk.Place.place_configure(self.text, width=self._width)

    def _on_destroy(self, event):
        if event.widget is self.text:
            self._closed = True
            if self._job is not None:
                self.text.after_cancel(self._job)
                self._job = None


def stabilize_scrolled_text(text):
    text._stable_layout = StableTextLayout(text)
