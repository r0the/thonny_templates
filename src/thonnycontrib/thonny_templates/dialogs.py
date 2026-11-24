from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from thonny import get_runner, get_workbench, ui_utils
from thonny.common import InlineCommand
from thonny.running import InlineCommandDialog
from thonny.ui_utils import get_hyperlink_cursor

from .download import (
    Cancelled,
    download_zip,
    find_main_file,
    install_zip,
    sanitize_folder_name,
    unique_folder_name,
)
from .manifest import ManifestError, ManifestSource, Template, fetch_manifest, parse_sources
from .packages import missing_distributions

logger = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 100
_PROGRESS_MAXIMUM = 1000


def show_template_dialog() -> None:
    dialog = TemplateDialog(get_workbench())
    dialog.show()


class TemplateDialog(tk.Toplevel):
    def __init__(self, workbench):
        super().__init__(workbench)
        self._workbench = workbench
        self._templates = []
        self._queue = queue.Queue()
        self._cancel_event = threading.Event()
        self._busy = False
        self._installing = False
        self._close_after_cancel = False
        self._poll_job = None
        self._package_check_job = None
        self._package_check_active = False
        self._packages_to_check = []
        self._pending_result = None
        self._target_dir = _stored_target_dir(workbench)
        self._sources = parse_sources(workbench.get_option("templates.sources"))
        self._source = self._initial_source()

        self.title("Projektvorlagen")
        self.minsize(560, 420)
        self._build_ui()
        self._place_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda event: self._on_close())

        self._start_manifest_load()

    def show(self) -> None:
        self.transient(self._workbench)
        self.wait_visibility()
        self.grab_set()
        self._listbox.focus_set()
        self.wait_window(self)

    def destroy(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._cancel_package_check()
        super().destroy()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(
            outer,
            text=(
                "Wählen Sie eine Vorlage aus. Sie wird heruntergeladen, entpackt "
                "und in Thonny geöffnet."
            ),
            wraplength=600,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        source_frame = ttk.Frame(outer)
        source_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(source_frame, text="Quelle:").pack(side="left")
        self._source_var = tk.StringVar(value=self._source.name)
        self._source_combo = ttk.Combobox(
            source_frame,
            textvariable=self._source_var,
            values=[source.name for source in self._sources],
            state="readonly",
            width=32,
        )
        self._source_combo.pack(side="left", padx=(6, 0))
        self._source_combo.bind("<<ComboboxSelected>>", self._on_source_selected)

        list_frame = ttk.Frame(outer)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self._listbox = tk.Listbox(list_frame, height=8, exportselection=False)
        self._listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self._listbox.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=scrollbar.set)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", self._on_double_click)
        self._listbox.bind("<Return>", self._on_double_click)

        self._project_var = tk.StringVar()
        self._project_font = tkfont.nametofont("TkDefaultFont").copy()
        self._project_font.configure(underline=1)
        self._project_label = ttk.Label(
            outer,
            textvariable=self._project_var,
            style="Url.TLabel",
            cursor=get_hyperlink_cursor(),
            font=self._project_font,
            wraplength=600,
            justify="left",
        )
        self._project_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self._project_label.bind("<Button-1>", self._on_open_project_page)

        target_frame = ttk.Frame(outer)
        target_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        target_frame.columnconfigure(1, weight=1)

        ttk.Label(target_frame, text="Zielordner:").grid(row=0, column=0, sticky="w")
        self._target_var = tk.StringVar(
            value=self._target_dir or "Wird beim ersten Download gewählt"
        )
        self._target_entry = ttk.Entry(
            target_frame, textvariable=self._target_var, state="readonly"
        )
        self._target_entry.grid(row=0, column=1, sticky="ew", padx=6)
        self._change_button = ttk.Button(
            target_frame, text="Ändern…", command=self._choose_target_dir
        )
        self._change_button.grid(row=0, column=2, sticky="e")

        status_frame = ttk.Frame(outer)
        status_frame.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        status_frame.columnconfigure(0, weight=1)

        self._status_var = tk.StringVar()
        self._status_label = ttk.Label(
            status_frame, textvariable=self._status_var, wraplength=480, justify="left"
        )
        self._status_label.grid(row=0, column=0, sticky="w")
        self._retry_button = ttk.Button(
            status_frame, text="Erneut versuchen", command=self._start_manifest_load
        )
        self._retry_button.grid(row=0, column=1, sticky="e", padx=(6, 0))
        self._retry_button.grid_remove()

        self._progress = ttk.Progressbar(outer, mode="indeterminate")
        self._progress.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        self._progress.grid_remove()

        buttons = ttk.Frame(outer)
        buttons.grid(row=7, column=0, sticky="e", pady=(12, 0))

        self._download_button = ttk.Button(
            buttons, text="Herunterladen und öffnen", command=self._start_download
        )
        self._download_button.pack(side="right")
        self._cancel_button = ttk.Button(
            buttons, text="Schliessen", command=self._on_close
        )
        self._cancel_button.pack(side="right", padx=(0, 6))

        self._download_button.configure(state="disabled")

    def _place_window(self) -> None:
        self.update_idletasks()
        width, height = 640, 460
        x = self._workbench.winfo_rootx() + max(
            0, (self._workbench.winfo_width() - width) // 2
        )
        y = self._workbench.winfo_rooty() + max(
            0, (self._workbench.winfo_height() - height) // 3
        )
        self.geometry("%dx%d+%d+%d" % (width, height, x, y))

    def _initial_source(self) -> ManifestSource:
        saved = self._workbench.get_option("templates.source") or ""
        for source in self._sources:
            if source.url == saved:
                return source
        return self._sources[0]

    def _selected_source(self) -> Optional[ManifestSource]:
        index = self._source_combo.current()
        if 0 <= index < len(self._sources):
            return self._sources[index]
        return None

    def _on_source_selected(self, event=None) -> None:
        source = self._selected_source()
        if source is None:
            return

        self._source = source
        self._workbench.set_option("templates.source", source.url)
        self._start_manifest_load()

    def _start_manifest_load(self) -> None:
        if self._busy:
            return

        self._installing = False
        self._close_after_cancel = False
        self._templates = []
        self._listbox.configure(state="normal")
        self._listbox.delete(0, "end")
        self._listbox.configure(state="disabled")
        self._project_var.set("")
        self._retry_button.grid_remove()
        self._set_busy(True)
        self._status_var.set("Vorlagen werden geladen…")
        self._show_indeterminate_progress()

        url = self._source.url
        threading.Thread(target=self._load_manifest_worker, args=(url,), daemon=True).start()
        self._schedule_poll()

    def _load_manifest_worker(self, url: str) -> None:
        try:
            templates = fetch_manifest(url)
        except ManifestError as error:
            self._queue.put(("manifest_error", str(error)))
        except Exception as error:  # noqa: BLE001 - unerwartete Fehler anzeigen
            logger.exception("Vorlagenliste konnte nicht geladen werden")
            self._queue.put(
                ("manifest_error", "Die Vorlagenliste konnte nicht geladen werden.\n\n%s" % error)
            )
        else:
            self._queue.put(("templates", templates))

    def _schedule_poll(self) -> None:
        if self._poll_job is None:
            self._poll_job = self.after(_POLL_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass

        try:
            if self._busy and self.winfo_exists():
                self._poll_job = self.after(_POLL_INTERVAL_MS, self._poll)
        except tk.TclError:
            self._poll_job = None

    def _handle_message(self, kind: str, payload) -> None:
        if kind == "templates":
            self._show_templates(payload)
        elif kind == "manifest_error":
            self._hide_progress()
            self._set_busy(False)
            self._status_var.set(payload)
            self._retry_button.grid(row=0, column=1, sticky="e", padx=(6, 0))
        elif kind == "phase":
            self._status_var.set(payload)
            self._show_indeterminate_progress()
        elif kind == "progress":
            received, total = payload
            self._update_progress(received, total)
        elif kind == "done":
            self._handle_done(*payload)
        elif kind == "error":
            self._handle_error(payload)
        elif kind == "cancelled":
            self._handle_cancelled()

    def _show_templates(self, templates) -> None:
        self._templates = templates
        self._hide_progress()
        self._set_busy(False)
        self._status_var.set("")

        self._listbox.configure(state="normal")
        self._listbox.delete(0, "end")
        for template in templates:
            self._listbox.insert("end", template.title)

        if templates:
            self._listbox.selection_set(0)
            self._listbox.activate(0)
            self._on_select()
        self._listbox.focus_set()

    def _on_select(self, event=None) -> None:
        template = self._selected_template()
        self._project_var.set("Projektseite: %s" % template.project_url if template else "")
        self._update_download_button()

    def _on_open_project_page(self, event=None) -> None:
        template = self._selected_template()
        if template is not None:
            webbrowser.open(template.project_url)

    def _on_double_click(self, event=None) -> None:
        if not self._busy and self._selected_template() is not None:
            self._start_download()

    def _selected_template(self) -> Optional[Template]:
        selection = self._listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self._templates):
            return self._templates[index]
        return None

    def _update_download_button(self) -> None:
        enabled = not self._busy and self._selected_template() is not None
        self._download_button.configure(state="normal" if enabled else "disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._listbox.configure(state="disabled")
            self._change_button.configure(state="disabled")
            self._source_combo.configure(state="disabled")
            self._download_button.configure(state="disabled")
            self._cancel_button.configure(
                text="Abbrechen" if self._installing else "Schliessen", state="normal"
            )
        else:
            self._listbox.configure(state="normal")
            self._change_button.configure(state="normal")
            self._source_combo.configure(state="readonly")
            self._cancel_button.configure(text="Schliessen", state="normal")
            self._update_download_button()

    def _show_indeterminate_progress(self) -> None:
        self._progress.grid()
        if str(self._progress.cget("mode")) != "indeterminate":
            self._progress.configure(mode="indeterminate")
        self._progress.stop()
        self._progress.start(12)

    def _update_progress(self, received: int, total: Optional[int]) -> None:
        if not total:
            self._show_indeterminate_progress()
            return

        self._progress.grid()
        if str(self._progress.cget("mode")) != "determinate":
            self._progress.stop()
            self._progress.configure(
                mode="determinate", maximum=_PROGRESS_MAXIMUM, value=0
            )
        self._progress["value"] = min(_PROGRESS_MAXIMUM, received * _PROGRESS_MAXIMUM // total)

    def _hide_progress(self) -> None:
        self._progress.stop()
        self._progress.grid_remove()

    def _choose_target_dir(self) -> Optional[str]:
        initial = self._default_target_dir()
        path = filedialog.askdirectory(
            parent=self,
            title="Zielordner für Projektvorlagen",
            initialdir=initial,
            mustexist=False,
        )
        if not path:
            return None

        path = os.path.abspath(os.path.expanduser(path))
        self._target_dir = path
        self._target_var.set(path)
        self._workbench.set_option("templates.target_dir", path)
        return path

    def _default_target_dir(self) -> str:
        if self._target_dir:
            return self._target_dir

        home = Path.home()
        informatik = home / "Documents" / "Informatik"
        if informatik.is_dir():
            return str(informatik)
        if home.is_dir():
            return str(home)
        return ""

    def _ensure_target_dir(self) -> Optional[str]:
        if self._target_dir and Path(self._target_dir).is_dir():
            return self._target_dir
        return self._choose_target_dir()

    def _start_download(self) -> None:
        template = self._selected_template()
        if self._busy or template is None:
            return

        target_dir = self._ensure_target_dir()
        if not target_dir:
            return

        folder_name = sanitize_folder_name(template.id)
        destination = Path(target_dir) / folder_name
        overwrite = False

        if destination.exists():
            choice = ConflictDialog.ask(self, destination)
            if choice is None:
                return
            if choice == "overwrite":
                overwrite = True
            else:
                new_name = simpledialog.askstring(
                    "Anderer Name",
                    "Name des neuen Ordners:",
                    initialvalue=unique_folder_name(target_dir, folder_name),
                    parent=self,
                )
                if not new_name:
                    return
                folder_name = sanitize_folder_name(new_name)
                if (Path(target_dir) / folder_name).exists():
                    messagebox.showwarning(
                        "Ordner existiert bereits",
                        "Der Ordner «%s» existiert bereits. Bitte wählen Sie einen anderen Namen."
                        % folder_name,
                        parent=self,
                    )
                    return

        self._run_install(template, target_dir, folder_name, overwrite)

    def _run_install(
        self, template: Template, target_dir: str, folder_name: str, overwrite: bool
    ) -> None:
        self._cancel_event.clear()
        self._close_after_cancel = False
        self._installing = True
        self._set_busy(True)
        self._status_var.set("Vorlage wird heruntergeladen…")
        self._show_indeterminate_progress()

        threading.Thread(
            target=self._install_worker,
            args=(template, target_dir, folder_name, overwrite),
            daemon=True,
        ).start()
        self._schedule_poll()

    def _install_worker(
        self, template: Template, target_dir: str, folder_name: str, overwrite: bool
    ) -> None:
        zip_path = None
        try:
            handle, zip_path = tempfile.mkstemp(prefix="thonny-vorlage-", suffix=".zip")
            os.close(handle)

            download_zip(
                template.url,
                zip_path,
                on_progress=self._report_progress,
                should_cancel=self._cancel_event.is_set,
            )

            self._queue.put(("phase", "Vorlage wird entpackt…"))
            destination = install_zip(
                zip_path,
                target_dir,
                folder_name,
                overwrite=overwrite,
                on_progress=self._report_progress,
                should_cancel=self._cancel_event.is_set,
            )
            main_file = find_main_file(destination, template.main)
            self._queue.put(("done", (template, destination, main_file)))
        except Cancelled:
            self._queue.put(("cancelled", None))
        except Exception as error:  # noqa: BLE001 - Fehler im Dialog anzeigen
            logger.exception("Vorlage konnte nicht installiert werden")
            self._queue.put(("error", error))
        finally:
            if zip_path:
                try:
                    os.unlink(zip_path)
                except OSError:
                    pass

    def _report_progress(self, received: int, total: Optional[int]) -> None:
        self._queue.put(("progress", (received, total)))

    def _handle_done(
        self, template: Template, destination: Path, main_file: Optional[Path]
    ) -> None:
        self._installing = False
        self._pending_result = (destination, main_file)

        if not template.packages:
            self._finish_install()
            return

        self._status_var.set("Pakete werden geprüft…")
        self._show_indeterminate_progress()
        self._check_packages(template.packages)

    def _check_packages(self, packages) -> None:
        runner = get_runner()
        if runner is None or runner.get_backend_proxy() is None:
            messagebox.showwarning(
                "Projektvorlagen",
                "Die benötigten Pakete können erst geprüft werden, wenn die "
                "Python-Konsole bereit ist. Bitte installieren Sie sie bei Bedarf "
                "über «Werkzeuge → Verwalte Pakete…».",
                parent=self,
            )
            self._finish_install()
            return

        if not runner.is_waiting_toplevel_command():
            if runner.is_running():
                message = (
                    "Bitte beenden Sie zuerst das laufende Programm. Anschliessend "
                    "können die benötigten Pakete installiert werden."
                )
            else:
                message = (
                    "Die Python-Konsole ist noch nicht bereit. Die benötigten Pakete "
                    "können erst danach geprüft und installiert werden."
                )
            messagebox.showinfo("Projektvorlagen", message, parent=self)
            self._finish_install()
            return

        self._packages_to_check = list(packages)
        self._workbench.bind(
            "get_active_distributions_response", self._on_distributions, True
        )
        self._package_check_active = True
        self._package_check_job = self.after(5000, self._on_package_check_timeout)
        try:
            runner.send_command(InlineCommand("get_active_distributions"))
        except Exception:  # noqa: BLE001 - Prüfung darf das Öffnen nicht verhindern
            logger.exception("Installierte Pakete konnten nicht abgefragt werden")
            self._cancel_package_check()
            self._finish_install()

    def _on_distributions(self, msg) -> None:
        self._cancel_package_check()
        if "error" in msg:
            self._install_packages(self._packages_to_check)
            return

        missing = missing_distributions(
            self._packages_to_check, getattr(msg, "distributions", {}).keys()
        )
        if missing:
            self._install_packages(missing)
        else:
            self._finish_install()

    def _on_package_check_timeout(self) -> None:
        self._package_check_job = None
        self._cancel_package_check()
        messagebox.showwarning(
            "Projektvorlagen",
            "Die installierten Pakete konnten nicht geprüft werden. Bitte "
            "installieren Sie sie bei Bedarf über «Werkzeuge → Verwalte Pakete…».",
            parent=self,
        )
        self._finish_install()

    def _cancel_package_check(self) -> None:
        if self._package_check_job is not None:
            try:
                self.after_cancel(self._package_check_job)
            except tk.TclError:
                pass
            self._package_check_job = None

        if not self._package_check_active:
            return
        self._package_check_active = False

        try:
            self._workbench.unbind(
                "get_active_distributions_response", self._on_distributions
            )
        except Exception:  # noqa: BLE001 - beim Beenden kann die Workbench fehlen
            pass

    def _install_packages(self, packages) -> None:
        if not packages:
            self._finish_install()
            return

        self._status_var.set("Pakete werden installiert…")
        command = InlineCommand("install_distributions", args=list(packages))
        dialog = InlineCommandDialog(
            self,
            command,
            title="Pakete installieren",
            instructions="Folgende Pakete werden für die Vorlage installiert: "
            + ", ".join(packages),
            autostart=True,
        )
        ui_utils.show_dialog(dialog)

        if dialog.returncode:
            messagebox.showwarning(
                "Projektvorlagen",
                "Die Pakete konnten nicht installiert werden. Die Vorlage wurde "
                "trotzdem entpackt.\n\nBitte installieren Sie die Pakete bei Bedarf "
                "über «Werkzeuge → Verwalte Pakete…».",
                parent=self,
            )
        self._finish_install()

    def _finish_install(self) -> None:
        if self._pending_result is None:
            return

        destination, main_file = self._pending_result
        self._pending_result = None
        self._packages_to_check = []

        self._hide_progress()
        self.destroy()
        self._reveal_project_folder(destination)

        if main_file is not None:
            self._workbench.get_editor_notebook().show_file(str(main_file))
        else:
            messagebox.showinfo(
                "Projektvorlagen",
                "Die Vorlage wurde nach\n%s\nentpackt. Eine Startdatei wurde nicht gefunden."
                % destination,
                parent=self._workbench,
            )

    def _reveal_project_folder(self, folder: Path) -> None:
        try:
            self._workbench.show_view("FilesView")
            browser = self._workbench.get_view("FilesView")
            browser.local_files.request_focus_into(str(folder))
        except Exception:  # noqa: BLE001 - Öffnen der Startdatei nicht verhindern
            logger.exception("Projektordner konnte nicht in der Dateiansicht geöffnet werden")

    def _handle_error(self, error: Exception) -> None:
        self._installing = False
        self._hide_progress()
        if self._close_after_cancel:
            self.destroy()
            return

        self._set_busy(False)
        self._status_var.set("")
        messagebox.showerror(
            "Projektvorlagen",
            "Die Vorlage konnte nicht installiert werden.\n\n%s" % error,
            parent=self,
        )

    def _handle_cancelled(self) -> None:
        self._installing = False
        self._hide_progress()
        if self._close_after_cancel:
            self.destroy()
            return

        self._set_busy(False)
        self._status_var.set("Abgebrochen.")

    def _on_cancel(self) -> None:
        if not self._installing:
            self.destroy()
            return

        self._cancel_event.set()
        self._status_var.set("Wird abgebrochen…")
        self._cancel_button.configure(state="disabled")

    def _on_close(self) -> None:
        if not self._installing:
            self.destroy()
            return

        self._close_after_cancel = True
        self._cancel_event.set()
        self._status_var.set("Wird abgebrochen…")
        self._cancel_button.configure(state="disabled")


class ConflictDialog(tk.Toplevel):
    def __init__(self, parent, destination: Path):
        super().__init__(parent)
        self.result = None
        self._parent = parent
        self.title("Ordner existiert bereits")
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        message = (
            "Der Ordner «%s» existiert bereits.\n\n"
            "Beim Überschreiben wird sein gesamter Inhalt gelöscht."
            % destination.name
        )
        ttk.Label(frame, text=message, wraplength=420, justify="left").pack(
            anchor="w", pady=(0, 12)
        )

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e")
        self._cancel_button = ttk.Button(
            buttons, text="Abbrechen", command=self._choose_cancel
        )
        self._cancel_button.pack(side="right")
        ttk.Button(
            buttons, text="Anderer Name…", command=self._choose_rename
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            buttons, text="Überschreiben", command=self._choose_overwrite
        ).pack(side="right", padx=(0, 6))

        self.protocol("WM_DELETE_WINDOW", self._choose_cancel)
        self.bind("<Escape>", lambda event: self._choose_cancel())
        self._place_window()

    def show(self) -> Optional[str]:
        self.transient(self._parent)
        self.wait_visibility()
        self.grab_set()
        self._cancel_button.focus_set()
        self.wait_window(self)
        return self.result

    def _place_window(self) -> None:
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = self._parent.winfo_rootx() + max(
            0, (self._parent.winfo_width() - width) // 2
        )
        y = self._parent.winfo_rooty() + max(
            0, (self._parent.winfo_height() - height) // 3
        )
        self.geometry("+%d+%d" % (x, y))

    def _choose_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _choose_rename(self) -> None:
        self.result = "rename"
        self.destroy()

    def _choose_overwrite(self) -> None:
        self.result = "overwrite"
        self.destroy()

    @classmethod
    def ask(cls, parent, destination: Path) -> Optional[str]:
        dialog = cls(parent, destination)
        return dialog.show()


def _stored_target_dir(workbench) -> str:
    value = workbench.get_option("templates.target_dir") or ""
    return os.path.expanduser(value)
