"""Centralized appearance themes for the Lazybones Tk desktop app."""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # Keep the application usable in a partially installed env.
    Image = ImageDraw = ImageTk = None


THEME_LABELS = {
    "speed": "极速",
    "codex_dark": "深色",
    "codex_light": "浅色",
    "system": "跟随 Windows",
    "classic": "经典",
}
THEME_IDS_BY_LABEL = {label: key for key, label in THEME_LABELS.items()}

DENSITY_LABELS = {"compact": "紧凑", "comfortable": "舒适"}
DENSITY_IDS_BY_LABEL = {
    label: key for key, label in DENSITY_LABELS.items()
}


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface_alt: str
    input_bg: str
    hover: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    selection: str
    success: str
    warning: str
    danger: str
    link: str
    highlight: str
    log_bg: str
    code_bg: str


PALETTES = {
    "codex_dark": Palette(
        dark=True,
        bg="#181B1F", surface="#21252B", surface_alt="#292E35",
        input_bg="#252A31", hover="#343B43", border="#404852",
        text="#E8ECEF", muted="#A6AFB8", accent="#82BCA7",
        accent_hover="#9BCCB9", accent_text="#132B23",
        selection="#324F48", success="#81C5A2", warning="#D6A85F",
        danger="#E4777F", link="#83AEFF", highlight="#44362F",
        log_bg="#1C2025", code_bg="#1C2025",
    ),
    "codex_light": Palette(
        dark=False,
        bg="#F7F7F5", surface="#FFFFFF", surface_alt="#F1F1EE",
        input_bg="#FFFFFF", hover="#EAEAE6", border="#DEDED8",
        text="#20201E", muted="#6B6B66", accent="#C75C3A",
        accent_hover="#B84E2F", accent_text="#FFFFFF",
        selection="#F2DED5", success="#287A4B", warning="#936A19",
        danger="#B83A45", link="#315FA8", highlight="#F8E8C9",
        log_bg="#FAFAF8", code_bg="#F1F1EE",
    ),
    "classic": Palette(
        dark=False,
        bg="#F2F4F3", surface="#FFFFFF", surface_alt="#F3F6F5",
        input_bg="#FAFCFB", hover="#E8EEEB", border="#D4DED9",
        text="#24352E", muted="#66796F", accent="#34765E",
        accent_hover="#285D4A", accent_text="#FFFFFF",
        selection="#DDEDE5", success="#287548", warning="#8A5A00",
        danger="#B43D48", link="#28649A", highlight="#FAF0D8",
        log_bg="#FAFCFB", code_bg="#F3F6F5",
    ),
}
# 极速皮肤沿用经典配色，但使用 clam 原生元素，不创建圆角位图。
# 单独保留主题 ID，确保可以持久化并在运行时可靠切换。
PALETTES["speed"] = PALETTES["classic"]


def windows_uses_dark_theme() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value = int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
        return value == 0
    except (ImportError, FileNotFoundError, OSError, TypeError, ValueError):
        return False


def resolve_theme_id(theme_id: str) -> str:
    if theme_id == "system":
        return "codex_dark" if windows_uses_dark_theme() else "codex_light"
    return theme_id if theme_id in PALETTES else "classic"


def normalize_appearance(settings: dict[str, Any]) -> dict[str, Any]:
    theme_id = str(settings.get("theme", "classic"))
    if theme_id not in THEME_LABELS:
        theme_id = "classic"
    density = str(settings.get("ui_density", "comfortable"))
    if density not in DENSITY_LABELS:
        density = "comfortable"
    try:
        font_scale = int(settings.get("font_scale", 100))
    except (TypeError, ValueError):
        font_scale = 100
    font_scale = min(140, max(80, font_scale))
    return {
        "theme": theme_id,
        "ui_density": density,
        "font_scale": font_scale,
    }


class ThemeManager:
    """Applies one palette to ttk, classic Tk, Treeview and Text tags."""

    def __init__(self, root: tk.Misc, settings: dict[str, Any]):
        self.root = root
        self.style = ttk.Style(root)
        self.native_theme = self.style.theme_use()
        self._clam_button_layout = None
        self._clam_tab_layout = None
        try:
            self.style.theme_use("clam")
            self._clam_button_layout = self.style.layout("TButton")
            self._clam_tab_layout = self.style.layout("TNotebook.Tab")
        except tk.TclError:
            pass
        self.requested_theme = "classic"
        self.resolved_theme = "classic"
        self.palette = PALETTES["classic"]
        self.font_scale = 100
        self.density = "comfortable"
        self._image_refs: dict[str, tuple[Any, ...]] = {}
        self._system_dark = windows_uses_dark_theme()
        self.apply(settings)

    def apply(self, settings: dict[str, Any]):
        appearance = normalize_appearance(settings)
        self.requested_theme = appearance["theme"]
        self.resolved_theme = resolve_theme_id(self.requested_theme)
        self.palette = PALETTES[self.resolved_theme]
        self.font_scale = appearance["font_scale"]
        self.density = appearance["ui_density"]
        self._system_dark = windows_uses_dark_theme()
        self._configure_ttk()
        try:
            self.root.configure(bg=self.palette.bg)
        except tk.TclError:
            pass
        self.apply_tree(self.root)

    def poll_system_change(self, settings: dict[str, Any]) -> bool:
        if self.requested_theme != "system":
            return False
        current = windows_uses_dark_theme()
        if current == self._system_dark:
            return False
        self.apply(settings)
        return True

    def _font(self, size: int = 9, weight: str = "normal",
              family: str = "Microsoft YaHei UI") -> tuple:
        scaled = max(8, round(size * self.font_scale / 100))
        return family, scaled, weight

    @property
    def signature(self) -> tuple[str, int, str]:
        """Current appearance identity used to avoid redundant repainting."""
        return self.resolved_theme, self.font_scale, self.density

    def widget_is_current(self, widget: tk.Misc) -> bool:
        return (getattr(widget, "_lazybones_theme_signature", None)
                == self.signature)

    @staticmethod
    def _mix_hex(first: str, second: str, ratio: float) -> str:
        """Blend two hex colours; ratio is the weight of ``second``."""
        ratio = min(1.0, max(0.0, ratio))
        left = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
        right = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
        mixed = tuple(round(a * (1 - ratio) + b * ratio)
                      for a, b in zip(left, right))
        return "#" + "".join(f"{value:02x}" for value in mixed)

    def _rounded_image(self, fill: str, border: str, radius: int):
        if Image is None or ImageDraw is None or ImageTk is None:
            return None
        scale = 4
        size = 32
        canvas = Image.new("RGBA", (size * scale, size * scale),
                           (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (1 * scale, 1 * scale, (size - 1) * scale,
             (size - 1) * scale),
            radius=radius * scale, fill=fill, outline=border,
            width=1 * scale)
        canvas = canvas.resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(canvas, master=self.root)

    def _rounded_element(self, role: str, normal: str, hover: str,
                         pressed: str, disabled: str, border: str,
                         radius: int, selected: str | None = None) -> str | None:
        """Create a reusable nine-slice ttk image element."""
        if ImageTk is None:
            return None
        key = (f"Lazybones.{self.resolved_theme}.{self.density}."
               f"{self.font_scale}.{role}")
        if key in self.style.element_names():
            return key
        images = tuple(
            self._rounded_image(color, border, radius)
            for color in (normal, hover, pressed, disabled, selected)
            if color is not None)
        if any(image is None for image in images):
            return None
        normal_image, hover_image, pressed_image, disabled_image = images[:4]
        state_images: list[Any] = [
            ("disabled", disabled_image),
            ("pressed", pressed_image),
        ]
        if selected is not None:
            state_images.append(("selected", images[4]))
        state_images.append(("active", hover_image))
        self.style.element_create(
            key, "image", normal_image, *state_images,
            border=(10, 10, 10, 10), sticky="nsew")
        self._image_refs[key] = images
        return key

    def _configure_rounded_controls(self, pad_y: int):
        p = self.palette
        radius = 6 if self.density == "compact" else 8
        soft_border = self._mix_hex(p.border, p.bg, 0.42)
        variants = {
            "button": ("TButton", p.surface, p.hover, p.selection,
                       p.surface),
            "accent": ("Accent.TButton", p.accent, p.accent_hover,
                       p.accent_hover, p.surface_alt),
            "danger": ("Danger.TButton", p.surface, p.highlight,
                       p.selection, p.surface),
        }
        for role, (style_name, normal, hover, pressed, disabled) in variants.items():
            element = self._rounded_element(
                role, normal, hover, pressed, disabled,
                p.accent if role == "accent" else soft_border, radius)
            if element:
                self.style.layout(style_name, [
                    (element, {"sticky": "nsew", "children": [
                        ("Button.padding", {"sticky": "nsew", "children": [
                            ("Button.focus", {"sticky": "nsew", "children": [
                                ("Button.label", {"sticky": "nsew"})
                            ]})
                        ]})
                    ]})
                ])

        tab_element = self._rounded_element(
            "tab", p.bg, p.hover, p.hover, p.bg, soft_border, radius,
            selected=p.surface)
        if tab_element:
            self.style.layout("TNotebook.Tab", [
                (tab_element, {"sticky": "nsew", "children": [
                    ("Notebook.padding", {"side": "top", "sticky": "nsew",
                                          "children": [
                        ("Notebook.label", {"side": "top", "sticky": ""})
                    ]})
                ]})
            ])

    def _configure_ttk(self):
        p = self.palette
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        # 切换主题时先撤销上一套圆角图片布局。极速皮肤直接停在这里的
        # clam 原生布局；其他皮肤稍后再挂载圆角九宫格元素。
        if self._clam_button_layout:
            for style_name in ("TButton", "Accent.TButton",
                               "Danger.TButton"):
                try:
                    self.style.layout(style_name, self._clam_button_layout)
                except tk.TclError:
                    pass
        if self._clam_tab_layout:
            try:
                self.style.layout("TNotebook.Tab", self._clam_tab_layout)
            except tk.TclError:
                pass
        lightweight = self.resolved_theme == "speed"
        compact = self.density == "compact"
        pad_y = (2 if compact else 4) if lightweight else (3 if compact else 6)
        tab_pad_y = ((3 if compact else 5) if lightweight
                     else (4 if compact else 7))
        base_row_height = ((21 if compact else 25) if lightweight
                           else (26 if compact else 32))
        row_height = round(base_row_height * self.font_scale / 100)
        base_font = self._font(9)
        soft_border = self._mix_hex(p.border, p.bg, 0.52)

        self.style.configure(".", background=p.bg, foreground=p.text,
                             font=base_font, bordercolor=p.border,
                             lightcolor=p.border, darkcolor=p.border)
        self.style.configure("TFrame", background=p.surface)
        self.style.configure("Shell.TFrame", background=p.bg)
        self.style.configure("Card.TFrame", background=p.surface)
        self.style.configure("TLabel", background=p.surface, foreground=p.text)
        self.style.configure("Muted.TLabel", foreground=p.muted)
        self.style.configure("Brand.TLabel", background=p.bg,
                             foreground=p.text, font=self._font(18, "bold"))
        self.style.configure("Shell.TLabel", background=p.bg,
                             foreground=p.muted)
        self.style.configure("PageTitle.TLabel", foreground=p.text,
                             font=self._font(12, "bold"))
        self.style.configure("TLabelframe", background=p.surface,
                             bordercolor=soft_border, lightcolor=soft_border,
                             darkcolor=soft_border, borderwidth=1,
                             relief="solid")
        self.style.configure("TLabelframe.Label", background=p.surface,
                             foreground=p.text, font=self._font(9, "bold"))
        self.style.configure("TButton", background=p.surface,
                             foreground=p.text, bordercolor=p.border,
                             padding=(10, pad_y), relief="flat", width=0,
                             focuscolor=p.accent, focusthickness=1)
        self.style.map(
            "TButton",
            background=[("disabled", p.surface), ("active", p.hover),
                        ("pressed", p.selection)],
            foreground=[("disabled", p.muted), ("active", p.text)])
        self.style.configure("Accent.TButton", background=p.accent,
                             foreground=p.accent_text,
                             bordercolor=p.accent,
                             padding=(10, pad_y), relief="flat")
        self.style.map(
            "Accent.TButton",
            background=[("disabled", p.surface_alt),
                        ("active", p.accent_hover),
                        ("pressed", p.accent_hover)],
            foreground=[("disabled", p.muted)])
        self.style.configure("Danger.TButton", background=p.surface,
                             foreground=p.danger, padding=(9, pad_y))
        self.style.map("Danger.TButton",
                       background=[("active", p.highlight)],
                       foreground=[("disabled", p.muted), ("active", p.danger)])
        self.style.configure("TMenubutton", background=p.surface,
                             foreground=p.text, padding=(10, pad_y),
                             borderwidth=1, relief="solid", arrowcolor=p.muted)
        self.style.map("TMenubutton", background=[("active", p.hover)])
        if not lightweight:
            self._configure_rounded_controls(pad_y)
        for name in ("TCheckbutton", "TRadiobutton"):
            self.style.configure(name, background=p.surface, foreground=p.text,
                                 padding=(3, pad_y // 2))
            self.style.map(
                name,
                background=[("active", p.surface)],
                foreground=[("disabled", p.muted), ("active", p.text)],
                indicatorcolor=[("selected", p.accent),
                                ("!selected", p.input_bg)])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            self.style.configure(name, fieldbackground=p.input_bg,
                                 foreground=p.text,
                                 insertcolor=p.text,
                                 bordercolor=p.border,
                                 arrowcolor=p.muted,
                                 padding=(6, pad_y))
            self.style.map(
                name,
                fieldbackground=[("readonly", p.input_bg),
                                 ("disabled", p.surface_alt)],
                foreground=[("readonly", p.text),
                            ("disabled", p.muted)],
                bordercolor=[("focus", p.accent)])
        self.root.option_add("*TCombobox*Listbox.background", p.surface)
        self.root.option_add("*TCombobox*Listbox.foreground", p.text)
        self.root.option_add("*TCombobox*Listbox.selectBackground", p.selection)
        self.root.option_add("*TCombobox*Listbox.selectForeground", p.text)

        self.style.configure("TNotebook", background=p.bg,
                             bordercolor=p.bg, lightcolor=p.bg,
                             darkcolor=p.bg, borderwidth=0,
                             tabmargins=(0, 0, 0, 0))
        self.style.configure("TNotebook.Tab", background=p.bg,
                             foreground=p.muted,
                             padding=(16, tab_pad_y), borderwidth=0,
                             bordercolor=p.bg, lightcolor=p.bg,
                             darkcolor=p.bg, focuscolor=p.bg,
                             relief="flat")
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", p.surface), ("active", p.hover)],
            foreground=[("selected", p.accent), ("active", p.text)],
            expand=[("selected", (0, 0, 0, 1))])
        self.style.configure("Treeview", background=p.surface,
                             fieldbackground=p.surface, foreground=p.text,
                             bordercolor=soft_border, rowheight=row_height,
                             relief="flat", borderwidth=0)
        self.style.map("Treeview",
                       background=[("selected", p.selection)],
                       foreground=[("selected", p.text)])
        self.style.configure("Treeview.Heading", background=p.surface_alt,
                             foreground=p.text, bordercolor=soft_border,
                             font=self._font(9, "bold"),
                             padding=(6, pad_y))
        self.style.map("Treeview.Heading",
                       background=[("active", p.hover)])
        self.style.configure("TProgressbar", background=p.accent,
                             troughcolor=p.surface_alt,
                             bordercolor=p.border, lightcolor=p.accent,
                             darkcolor=p.accent)
        self.style.configure("TSeparator", background=p.border)
        self.style.configure("TPanedwindow", background=p.surface_alt)
        for name in ("TScrollbar", "Vertical.TScrollbar",
                     "Horizontal.TScrollbar"):
            self.style.configure(name, background=p.surface_alt,
                                 troughcolor=p.bg, bordercolor=p.bg,
                                 arrowcolor=p.muted)
            self.style.map(name, background=[("active", p.hover)])

    def apply_tree(self, widget: tk.Misc):
        self.apply_widget(widget)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self.apply_tree(child)

    def apply_widget(self, widget: tk.Misc):
        p = self.palette
        try:
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                widget.configure(bg=p.bg)
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=p.surface)
            elif isinstance(widget, tk.Label):
                role = getattr(widget, "_theme_role", "")
                foreground = {
                    "muted": p.muted, "success": p.success,
                    "warning": p.warning, "danger": p.danger,
                    "accent": p.accent,
                }.get(role, p.text)
                background = p.bg if role == "status" else p.surface
                widget.configure(bg=background, fg=foreground)
                self._scale_widget_font(widget)
            elif isinstance(widget, tk.Button):
                accent = getattr(widget, "_theme_role", "") == "accent"
                widget.configure(
                    bg=p.accent if accent else p.surface_alt,
                    fg=p.accent_text if accent else p.text,
                    activebackground=(p.accent_hover if accent else p.hover),
                    activeforeground=p.accent_text if accent else p.text,
                    highlightbackground=p.border, highlightcolor=p.accent,
                    relief=tk.FLAT)
                self._scale_widget_font(widget)
            elif isinstance(widget, tk.Listbox):
                widget.configure(bg=p.surface, fg=p.text,
                                 selectbackground=p.selection,
                                 selectforeground=p.text,
                                 relief=tk.FLAT, borderwidth=0,
                                 highlightthickness=1,
                                 highlightbackground=p.border,
                                 highlightcolor=p.accent)
                self._scale_widget_font(widget)
            elif isinstance(widget, tk.Text):
                role = getattr(widget, "_theme_role", "")
                background = p.log_bg if role == "log" else p.surface
                selection_text = "#171717" if p.dark else "#FFFFFF"
                widget.configure(bg=background, fg=p.text,
                                 insertbackground=p.text,
                                 selectbackground=p.link,
                                 selectforeground=selection_text,
                                 relief=tk.FLAT, borderwidth=0,
                                 highlightthickness=1, padx=10, pady=8,
                                 highlightbackground=p.border,
                                 highlightcolor=p.accent)
                self._scale_widget_font(widget)
                self._apply_text_tags(widget)
                # Content tags can carry their own background colour.  Keep the
                # built-in selection tag above them so dragged text is visible.
                widget.tag_configure(
                    tk.SEL, background=p.link, foreground=selection_text)
                widget.tag_raise(tk.SEL)
            elif isinstance(widget, tk.Entry):
                widget.configure(bg=p.input_bg, fg=p.text,
                                 insertbackground=p.text,
                                 selectbackground=p.selection,
                                 selectforeground=p.text,
                                 highlightbackground=p.border,
                                 highlightcolor=p.accent)
                self._scale_widget_font(widget)
            elif isinstance(widget, tk.Canvas):
                widget.configure(bg=p.surface, highlightbackground=p.border)
            elif isinstance(widget, tk.Menu):
                widget.configure(bg=p.surface, fg=p.text,
                                 activebackground=p.selection,
                                 activeforeground=p.text)
            if isinstance(widget, ttk.Treeview):
                self._apply_tree_tags(widget)
        except (tk.TclError, AttributeError):
            pass
        try:
            widget._lazybones_theme_signature = self.signature
        except (tk.TclError, AttributeError):
            pass

    def _scale_widget_font(self, widget: tk.Misc):
        try:
            base = getattr(widget, "_theme_base_font", None)
            if base is None:
                actual = tkfont.Font(font=widget.cget("font")).actual()
                base = {
                    "family": actual["family"],
                    "size": abs(int(actual["size"] or 9)),
                    "weight": actual["weight"],
                    "slant": actual["slant"],
                    "underline": actual["underline"],
                    "overstrike": actual["overstrike"],
                }
                widget._theme_base_font = base
            size = max(8, round(base["size"] * self.font_scale / 100))
            widget.configure(font=(base["family"], size, base["weight"],
                                   base["slant"]))
        except (tk.TclError, TypeError, ValueError):
            pass

    def _apply_tree_tags(self, tree: ttk.Treeview):
        p = self.palette
        tag_colors = {
            "checked": p.selection,
            "ok": p.selection,
            "recommended": p.selection,
            "no_si": p.highlight,
            "same": p.highlight,
            "conflict": p.highlight if p.dark else "#F9DEDE",
            "no_data": p.surface_alt,
            "seq_odd": p.surface,
            "seq_even": p.surface_alt,
        }
        for tag, color in tag_colors.items():
            try:
                tree.tag_configure(tag, background=color, foreground=p.text)
            except tk.TclError:
                pass

    def _apply_text_tags(self, text: tk.Text):
        p = self.palette
        foregrounds = {
            "time": p.success, "ok": p.success, "error": p.danger,
            "warn": p.warning, "info": p.link, "ai": p.accent,
            "title": p.accent, "detail": p.muted,
            "identity_label": p.accent, "section": p.success,
            "field": p.link, "evidence": p.muted, "na": p.muted,
            "doi_link": p.link, "user": p.link, "sys": p.muted,
            "thinking": p.muted, "thinking_header": p.text,
            "md_h1": p.accent, "md_h2": p.link, "md_h3": p.link,
            "md_bold": p.text, "md_italic": p.text,
            "md_code_inline": p.danger, "md_code_block": p.text,
            "md_quote": p.muted, "md_list": p.text,
            "md_table_header": p.text, "md_table_cell": p.text,
            "md_hr": p.border,
        }
        backgrounds = {
            "identity_block": p.surface_alt,
            "ai_tag": p.highlight,
            "thinking": p.surface_alt,
            "md_code_inline": p.code_bg,
            "md_code_block": p.code_bg,
            "md_quote": p.surface_alt,
            "md_table_header": p.surface_alt,
            "md_table_cell": p.surface,
        }
        existing = set(text.tag_names())
        for tag, color in foregrounds.items():
            if tag in existing:
                try:
                    text.tag_configure(tag, foreground=color)
                except tk.TclError:
                    pass
        for tag, color in backgrounds.items():
            if tag in existing:
                try:
                    text.tag_configure(tag, background=color)
                except tk.TclError:
                    pass
        base_fonts = getattr(text, "_theme_tag_base_fonts", {})
        for tag in existing:
            try:
                if tag not in base_fonts:
                    tag_font = text.tag_cget(tag, "font")
                    if not tag_font:
                        continue
                    actual = tkfont.Font(font=tag_font).actual()
                    base_fonts[tag] = {
                        "family": actual["family"],
                        "size": abs(int(actual["size"] or 9)),
                        "weight": actual["weight"],
                        "slant": actual["slant"],
                    }
                base = base_fonts[tag]
                size = max(
                    8, round(base["size"] * self.font_scale / 100))
                text.tag_configure(
                    tag, font=(base["family"], size,
                               base["weight"], base["slant"]))
            except (tk.TclError, TypeError, ValueError):
                continue
        text._theme_tag_base_fonts = base_fonts
