"""
Lum3on ComfyUI Bridge Manager — GUI Application
Cross-platform (macOS, Windows, Linux) using PySide6 (Qt).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QGroupBox, QFrame,
    QSizePolicy, QHeaderView,
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QColor, QPalette

from .config import (
    APP_NAME, APP_VERSION,
    load_config, save_config,
    find_houdini_pref_dir, find_comfyui_path,
)
from .installer import (
    validate_comfyui, validate_houdini_prefs,
    run_full_install,
)
from .comfy_api import (
    check_server, find_missing_nodes,
    extract_workflow_node_types, extract_node_pack_info,
)


# ---- Colors ----------------------------------------------------------------

DARK_STYLE = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QGroupBox {
    background-color: #2a2a3e;
    border: 1px solid #363650;
    border-radius: 8px;
    margin-top: 12px;
    padding: 16px;
    padding-top: 28px;
    font-weight: bold;
    font-size: 13px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: #f5a623;
}
QLineEdit {
    background-color: #363650;
    border: 1px solid #45456a;
    border-radius: 4px;
    padding: 8px 12px;
    color: #cdd6f4;
    font-size: 12px;
    selection-background-color: #f5a623;
}
QLineEdit:focus {
    border-color: #f5a623;
}
QPushButton {
    background-color: #363650;
    border: none;
    border-radius: 4px;
    padding: 8px 20px;
    color: #cdd6f4;
    font-weight: bold;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #45456a;
}
QPushButton:pressed {
    background-color: #52527a;
}
QPushButton[primary="true"] {
    background-color: #f5a623;
    color: #1e1e2e;
}
QPushButton[primary="true"]:hover {
    background-color: #ffc04d;
}
QTabWidget::pane {
    border: none;
    background-color: #1e1e2e;
}
QTabBar::tab {
    background-color: transparent;
    color: #6c7086;
    padding: 10px 24px;
    font-weight: bold;
    font-size: 13px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #f5a623;
    border-bottom: 2px solid #f5a623;
}
QTabBar::tab:hover {
    color: #cdd6f4;
}
QTreeWidget {
    background-color: #363650;
    border: 1px solid #45456a;
    border-radius: 4px;
    color: #cdd6f4;
    font-size: 12px;
    outline: none;
}
QTreeWidget::item {
    padding: 4px 8px;
}
QTreeWidget::item:selected {
    background-color: #f5a623;
    color: #1e1e2e;
}
QHeaderView::section {
    background-color: #2a2a3e;
    color: #6c7086;
    border: none;
    padding: 6px 12px;
    font-weight: bold;
    font-size: 11px;
}
QLabel[status="ok"] { color: #a6e3a1; }
QLabel[status="error"] { color: #f38ba8; }
QLabel[status="warn"] { color: #fab387; }
QLabel[status="info"] { color: #6c7086; }
"""


# ---- Thread-safe signal helper --------------------------------------------

class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


def run_in_thread(func, on_result=None, on_error=None):
    signals = WorkerSignals()
    if on_result:
        signals.result.connect(on_result)
    if on_error:
        signals.error.connect(on_error)

    def _worker():
        try:
            r = func()
            signals.result.emit(r)
        except Exception as e:
            signals.error.emit(str(e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ---- Widgets ---------------------------------------------------------------

class PathPicker(QWidget):
    def __init__(self, label: str, default: str = "", pick_dir: bool = True):
        super().__init__()
        self.pick_dir = pick_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        lbl = QLabel(label)
        lbl.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.entry = QLineEdit(default)
        self.entry.setMinimumHeight(36)
        row.addWidget(self.entry)

        btn = QPushButton("Browse")
        btn.clicked.connect(self._browse)
        btn.setMinimumHeight(36)
        row.addWidget(btn)

        layout.addLayout(row)

    def _browse(self):
        if self.pick_dir:
            path = QFileDialog.getExistingDirectory(self, "Select Directory")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select File", "", "JSON Files (*.json);;All Files (*)"
            )
        if path:
            self.entry.setText(path)

    def get(self) -> str:
        return self.entry.text().strip()


class StatusLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("font-size: 11px;")
        self.setProperty("status", "info")

    def _update_style(self, status: str):
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_ok(self, text: str):
        self.setText(f"✓ {text}")
        self._update_style("ok")

    def set_error(self, text: str):
        self.setText(f"✗ {text}")
        self._update_style("error")

    def set_warn(self, text: str):
        self.setText(f"⚠ {text}")
        self._update_style("warn")

    def set_info(self, text: str):
        self.setText(text)
        self._update_style("info")


# ============================================================================
# MAIN WINDOW
# ============================================================================

class ManagerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} Manager")
        self.setMinimumSize(750, 650)
        self.resize(850, 720)

        self.config_data = load_config()
        self.bridge_dir = Path(__file__).resolve().parent.parent.parent

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 16, 24, 24)
        main_layout.setSpacing(0)

        # Title
        title_row = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #f5a623;")
        title_row.addWidget(title)

        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet("font-size: 11px; color: #6c7086; padding-top: 6px;")
        title_row.addWidget(version)
        title_row.addStretch()
        main_layout.addLayout(title_row)

        main_layout.addSpacing(12)

        # Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._build_setup_tab()
        self._build_workflows_tab()

    # ---- Setup Tab ---------------------------------------------------------

    def _build_setup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # ComfyUI
        grp1 = QGroupBox("ComfyUI Installation")
        g1 = QVBoxLayout(grp1)

        default_comfy = self.config_data.get("comfyui_path", "")
        if not default_comfy:
            found = find_comfyui_path()
            if found:
                default_comfy = str(found)

        self.comfyui_picker = PathPicker("ComfyUI Directory", default_comfy)
        g1.addWidget(self.comfyui_picker)
        self.comfyui_status = StatusLabel()
        g1.addWidget(self.comfyui_status)

        if default_comfy:
            ok, msg = validate_comfyui(Path(default_comfy))
            self.comfyui_status.set_ok(msg) if ok else self.comfyui_status.set_warn(msg)

        layout.addWidget(grp1)

        # Houdini
        grp2 = QGroupBox("Houdini Preferences")
        g2 = QVBoxLayout(grp2)

        default_hou = self.config_data.get("houdini_pref_dir", "")
        if not default_hou:
            found = find_houdini_pref_dir()
            if found:
                default_hou = str(found)

        self.houdini_picker = PathPicker("Houdini User Preferences Directory", default_hou)
        g2.addWidget(self.houdini_picker)
        self.houdini_status = StatusLabel()
        g2.addWidget(self.houdini_status)

        if default_hou:
            ok, msg = validate_houdini_prefs(Path(default_hou))
            if ok:
                self.houdini_status.set_ok(msg)

        layout.addWidget(grp2)

        # Server
        grp3 = QGroupBox("ComfyUI Server")
        g3 = QVBoxLayout(grp3)

        server_row = QHBoxLayout()
        lbl = QLabel("URL")
        lbl.setStyleSheet("color: #6c7086; font-size: 11px;")
        server_row.addWidget(lbl)

        self.server_url = QLineEdit(
            self.config_data.get("server_url", "http://127.0.0.1:8188")
        )
        self.server_url.setMinimumHeight(36)
        self.server_url.setFixedWidth(300)
        server_row.addWidget(self.server_url)

        test_btn = QPushButton("Test Connection")
        test_btn.setMinimumHeight(36)
        test_btn.clicked.connect(self._test_connection)
        server_row.addWidget(test_btn)
        server_row.addStretch()

        g3.addLayout(server_row)
        self.server_status = StatusLabel()
        g3.addWidget(self.server_status)

        layout.addWidget(grp3)

        # Install button
        btn_row = QHBoxLayout()
        self.install_status = StatusLabel()
        btn_row.addWidget(self.install_status)

        install_btn = QPushButton("Install / Update")
        install_btn.setProperty("primary", True)
        install_btn.setMinimumHeight(44)
        install_btn.setMinimumWidth(180)
        install_btn.clicked.connect(self._run_install)
        btn_row.addWidget(install_btn)

        layout.addLayout(btn_row)
        layout.addStretch()

        self.tabs.addTab(tab, "Setup")

    def _test_connection(self):
        url = self.server_url.text().strip().rstrip("/")
        self.server_status.set_info("Connecting...")

        def _check():
            return check_server(url)

        run_in_thread(
            _check,
            on_result=lambda ok: (
                self.server_status.set_ok("Connected to ComfyUI") if ok
                else self.server_status.set_error("Cannot reach server")
            ),
            on_error=lambda e: self.server_status.set_error(f"Error: {e}"),
        )

    def _run_install(self):
        comfy_path = Path(self.comfyui_picker.get())
        hou_path = Path(self.houdini_picker.get())

        if not self.comfyui_picker.get():
            self.install_status.set_error("Set ComfyUI path first")
            return
        if not self.houdini_picker.get():
            self.install_status.set_error("Set Houdini prefs path first")
            return

        self.install_status.set_info("Installing...")

        results = run_full_install(self.bridge_dir, comfy_path, hou_path)

        # Save config
        self.config_data["comfyui_path"] = str(comfy_path)
        self.config_data["houdini_pref_dir"] = str(hou_path)
        self.config_data["server_url"] = self.server_url.text().strip()
        save_config(self.config_data)

        all_ok = all(r[1] for r in results)
        summary = "\n".join(
            f"{'✓' if ok else '✗'} {name}: {msg}" for name, ok, msg in results
        )

        if all_ok:
            self.install_status.set_ok("Installation complete!")
            self.comfyui_status.set_ok("Configured")
            self.houdini_status.set_ok("Package installed")
        else:
            self.install_status.set_error("Some steps failed")

        QMessageBox.information(self, "Installation Results", summary)

    # ---- Workflows Tab -----------------------------------------------------

    def _build_workflows_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Workflow directory
        grp1 = QGroupBox("Workflow Import")
        g1 = QVBoxLayout(grp1)

        desc = QLabel(
            "Select a folder with ComfyUI workflow JSON files. "
            "The manager will scan them and check which custom nodes are needed."
        )
        desc.setStyleSheet("color: #6c7086; font-size: 11px;")
        desc.setWordWrap(True)
        g1.addWidget(desc)

        self.wf_picker = PathPicker(
            "Workflows Directory",
            self.config_data.get("workflows_dir", ""),
        )
        g1.addWidget(self.wf_picker)

        btn_row = QHBoxLayout()
        scan_btn = QPushButton("Scan Workflows")
        scan_btn.setProperty("primary", True)
        scan_btn.setMinimumHeight(40)
        scan_btn.clicked.connect(self._scan_workflows)
        btn_row.addWidget(scan_btn)

        self.scan_status = StatusLabel()
        btn_row.addWidget(self.scan_status)
        btn_row.addStretch()

        g1.addLayout(btn_row)
        layout.addWidget(grp1)

        # Results
        grp2 = QGroupBox("Required Custom Nodes")
        g2 = QVBoxLayout(grp2)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Status", "Node Type", "Package"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.resizeSection(0, 120)
        header.resizeSection(1, 320)
        g2.addWidget(self.tree)

        layout.addWidget(grp2)

        self.tabs.addTab(tab, "Workflows")

    def _scan_workflows(self):
        wf_dir = self.wf_picker.get()
        if not wf_dir:
            self.scan_status.set_error("Select a workflows directory first")
            return

        wf_path = Path(wf_dir)
        if not wf_path.is_dir():
            self.scan_status.set_error("Directory not found")
            return

        self.config_data["workflows_dir"] = wf_dir
        save_config(self.config_data)

        wf_files = list(set(
            list(wf_path.glob("*.json")) + list(wf_path.glob("**/*.json"))
        ))

        if not wf_files:
            self.scan_status.set_error("No JSON files found")
            return

        self.scan_status.set_info(f"Scanning {len(wf_files)} workflow(s)...")

        self.tree.clear()

        # Extract types
        all_types: set[str] = set()
        all_packs: dict[str, str | None] = {}
        for wf in wf_files:
            try:
                all_types.update(extract_workflow_node_types(wf))
                all_packs.update(extract_node_pack_info(wf))
            except Exception:
                continue

        if not all_types:
            self.scan_status.set_error("No node types found")
            return

        url = self.server_url.text().strip().rstrip("/")

        def _check():
            if not check_server(url):
                return None  # server offline
            return find_missing_nodes(url, wf_files)

        def _on_result(missing):
            if missing is None:
                for t in sorted(all_types):
                    item = QTreeWidgetItem([
                        "? (server offline)", t, all_packs.get(t, "—") or "—"
                    ])
                    self.tree.addTopLevelItem(item)
                self.scan_status.set_warn(
                    f"{len(all_types)} types found. Start ComfyUI to check status."
                )
            else:
                missing_count = 0
                for t in sorted(all_types):
                    pack = all_packs.get(t, "—") or "—"
                    if t in missing:
                        status = "✗ MISSING"
                        missing_count += 1
                    else:
                        status = "✓ Installed"
                    item = QTreeWidgetItem([status, t, pack])
                    self.tree.addTopLevelItem(item)

                if missing_count == 0:
                    self.scan_status.set_ok(
                        f"All {len(all_types)} node types installed!"
                    )
                else:
                    self.scan_status.set_warn(
                        f"{missing_count} missing out of {len(all_types)}. "
                        f"Install via ComfyUI Manager."
                    )

        run_in_thread(_check, on_result=_on_result,
                      on_error=lambda e: self.scan_status.set_error(str(e)))


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)

    window = ManagerWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
