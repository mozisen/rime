from __future__ import annotations

import os
import subprocess
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


APP_VERSION = "1.0.1"
APP_TITLE = f"极点五笔词库工具 {APP_VERSION}"
USER_DICT_NAME = "wubi86_jidian_user.dict.yaml"
MAIN_DICT_NAME = "wubi86_jidian.dict.yaml"


@dataclass(frozen=True)
class DictionaryEntry:
    line_index: int
    text: str
    code: str
    weight: str
    extra_fields: tuple[str, ...] = ()


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_dictionary_directory() -> Path:
    candidates = [
        application_directory(),
        Path.cwd(),
        Path(os.environ.get("APPDATA", "")) / "Rime",
        application_directory().parent,
    ]
    for directory in candidates:
        if (
            directory
            and (directory / USER_DICT_NAME).is_file()
            and (directory / MAIN_DICT_NAME).is_file()
        ):
            return directory.resolve()
    return application_directory()


def find_weasel_deployer() -> Path | None:
    candidates: list[Path] = []
    try:
        import winreg

        registry_locations = (
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Rime\Weasel", 0),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\WOW6432Node\Rime\Weasel",
                0,
            ),
        )
        for hive, key_name, access in registry_locations:
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | access) as key:
                    root, _ = winreg.QueryValueEx(key, "WeaselRoot")
                    candidates.append(Path(root) / "WeaselDeployer.exe")
            except OSError:
                continue
    except ImportError:
        pass

    for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(environment_name)
        if not program_files:
            continue
        rime_root = Path(program_files) / "Rime"
        if rime_root.is_dir():
            candidates.extend(
                sorted(
                    rime_root.glob("weasel-*/WeaselDeployer.exe"),
                    reverse=True,
                )
            )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def deploy_rime(deployer: Path, timeout: int = 300) -> None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(deployer), "/deploy"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        timeout=timeout,
    )
    if result.returncode != 0:
        details = result.stderr.decode(errors="replace").strip()
        suffix = f"\n{details}" if details else ""
        raise RuntimeError(f"部署程序返回错误代码 {result.returncode}。{suffix}")


def read_text_preserving_encoding(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法识别文件编码：{path.name}")


def parse_entry_line(line: str, line_index: int) -> DictionaryEntry | None:
    content = line.rstrip("\r\n")
    if not content or content.lstrip().startswith("#"):
        return None
    parts = content.split("\t")
    if len(parts) < 3:
        return None
    return DictionaryEntry(line_index, parts[0], parts[1], parts[2], tuple(parts[3:]))


def find_entries(path: Path, target: str) -> list[DictionaryEntry]:
    content, _ = read_text_preserving_encoding(path)
    results: list[DictionaryEntry] = []
    for index, line in enumerate(content.splitlines(keepends=True)):
        entry = parse_entry_line(line, index)
        if entry and entry.text == target:
            results.append(entry)
    return results


def validate_fields(text: str, code: str, weight: str) -> tuple[str, str, str]:
    values = (text.strip(), code.strip(), weight.strip())
    if not all(values):
        raise ValueError("词条、编码和权重都不能为空。")
    if any("\t" in value or "\r" in value or "\n" in value for value in values):
        raise ValueError("输入内容不能包含 Tab 或换行。")
    if not values[2].isdigit():
        raise ValueError("权重必须是非负整数。")
    return values


def append_entry(path: Path, text: str, code: str, weight: str) -> None:
    text, code, weight = validate_fields(text, code, weight)
    _, encoding = read_text_preserving_encoding(path)
    data = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    prefix = b"" if not data or data.endswith((b"\n", b"\r")) else newline
    line = f"{text}\t{code}\t{weight}".encode(
        "utf-8" if encoding == "utf-8-sig" else encoding
    )
    with path.open("ab") as handle:
        handle.write(prefix + line + newline)


def update_entry(
    path: Path, line_index: int, original_text: str, text: str, code: str, weight: str
) -> Path:
    text, code, weight = validate_fields(text, code, weight)
    content, encoding = read_text_preserving_encoding(path)
    lines = content.splitlines(keepends=True)
    if line_index < 0 or line_index >= len(lines):
        raise RuntimeError("词条位置已变化，请重新搜索后再保存。")
    current = parse_entry_line(lines[line_index], line_index)
    if current is None or current.text != original_text:
        raise RuntimeError("词库内容已变化，请重新搜索后再保存。")

    old_line = lines[line_index]
    ending = "\r\n" if old_line.endswith("\r\n") else "\n" if old_line.endswith("\n") else ""
    fields = [text, code, weight, *current.extra_fields]
    lines[line_index] = "\t".join(fields) + ending

    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text("".join(lines), encoding=encoding, newline="")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return backup


class DictionaryTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("780x590")
        self.minsize(720, 540)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.dict_dir = find_dictionary_directory()
        self.user_dict = self.dict_dir / USER_DICT_NAME
        self.main_dict = self.dict_dir / MAIN_DICT_NAME
        self.matches: list[DictionaryEntry] = []
        self.selected_entry: DictionaryEntry | None = None

        self._configure_style()
        self._build_ui()
        self._set_status()
        self.after(100, self._validate_files)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Hint.TLabel", foreground="#5f6368")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=30)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.deploy_button = ttk.Button(
            header,
            text="重新部署 Rime",
            command=self._start_deploy,
            style="Accent.TButton",
        )
        self.deploy_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(18, 0))
        ttk.Label(
            header,
            text="添加自定义词条，或查找并调整主词库中的编码与权重。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, pady=(16, 0))
        add_tab = ttk.Frame(notebook, padding=22)
        edit_tab = ttk.Frame(notebook, padding=22)
        notebook.add(add_tab, text="  造词  ")
        notebook.add(edit_tab, text="  调整词序  ")

        self._build_add_tab(add_tab)
        self._build_edit_tab(edit_tab)

        self.status_var = tk.StringVar()
        ttk.Separator(outer).pack(fill="x", pady=(14, 8))
        ttk.Label(outer, textvariable=self.status_var, style="Hint.TLabel").pack(anchor="w")

    def _labeled_entry(
        self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=9)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(16, 0), pady=9)
        return entry

    def _build_add_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(1, weight=1)
        ttk.Label(
            tab,
            text=f"新词条将追加到 {USER_DICT_NAME} 文件末尾。",
            style="Hint.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        self.add_text = tk.StringVar()
        self.add_code = tk.StringVar()
        self.add_weight = tk.StringVar(value="100")
        first = self._labeled_entry(tab, 1, "词条", self.add_text)
        self._labeled_entry(tab, 2, "编码", self.add_code)
        weight_entry = self._labeled_entry(tab, 3, "权重", self.add_weight)
        weight_entry.bind("<Return>", lambda _event: self._append())
        ttk.Button(
            tab, text="添加到自定义词库", command=self._append, style="Accent.TButton"
        ).grid(row=4, column=1, sticky="e", pady=(18, 0))
        first.focus_set()

    def _build_edit_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

        self.search_text = tk.StringVar()
        ttk.Label(tab, text="要查找的词条").grid(row=0, column=0, sticky="w")
        search_entry = ttk.Entry(tab, textvariable=self.search_text)
        search_entry.grid(row=0, column=1, sticky="ew", padx=(16, 10))
        search_entry.bind("<Return>", lambda _event: self._search())
        ttk.Button(tab, text="查找", command=self._search).grid(row=0, column=2)

        ttk.Label(
            tab,
            text="如有多个同名词条，请选择需要修改的一项。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 8))

        columns = ("text", "code", "weight", "line")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", height=6)
        self.tree.heading("text", text="词条")
        self.tree.heading("code", text="编码")
        self.tree.heading("weight", text="权重")
        self.tree.heading("line", text="所在行")
        self.tree.column("text", width=250)
        self.tree.column("code", width=130, anchor="center")
        self.tree.column("weight", width=100, anchor="center")
        self.tree.column("line", width=90, anchor="center")
        self.tree.grid(row=2, column=0, columnspan=3, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._select_match)

        editor = ttk.LabelFrame(tab, text="修改选中词条", padding=14)
        editor.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        editor.columnconfigure(1, weight=1)
        self.edit_text = tk.StringVar()
        self.edit_code = tk.StringVar()
        self.edit_weight = tk.StringVar()
        self._labeled_entry(editor, 0, "词条", self.edit_text)
        self._labeled_entry(editor, 1, "编码", self.edit_code)
        edit_weight_entry = self._labeled_entry(editor, 2, "权重", self.edit_weight)
        edit_weight_entry.bind("<Return>", lambda _event: self._save_edit())
        self.save_button = ttk.Button(
            editor, text="保存修改", command=self._save_edit, style="Accent.TButton"
        )
        self.save_button.grid(row=3, column=1, sticky="e", pady=(10, 0))
        self.save_button.state(["disabled"])

    def _validate_files(self) -> bool:
        missing = [
            name
            for name, path in (
                (USER_DICT_NAME, self.user_dict),
                (MAIN_DICT_NAME, self.main_dict),
            )
            if not path.is_file()
        ]
        if missing:
            messagebox.showerror(
                "未找到词库",
                "请把本工具放到 Rime 配置目录后再运行。\n\n缺少："
                + "、".join(missing),
            )
            return False
        return True

    def _set_status(self, message: str | None = None) -> None:
        if not hasattr(self, "status_var"):
            return
        default = f"词库目录：{self.dict_dir}"
        self.status_var.set(message or default)

    def _append(self) -> None:
        if not self._validate_files():
            return
        try:
            text, code, weight = validate_fields(
                self.add_text.get(), self.add_code.get(), self.add_weight.get()
            )
            append_entry(self.user_dict, text, code, weight)
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("添加失败", str(exc))
            return
        self.add_text.set("")
        self.add_code.set("")
        self._set_status(f"已添加：{text}    {code}    {weight}")
        messagebox.showinfo("添加成功", f"已将“{text}”添加到自定义词库。")

    def _search(self) -> None:
        if not self._validate_files():
            return
        target = self.search_text.get().strip()
        if not target:
            messagebox.showwarning("请输入词条", "请先输入要查找的词条。")
            return
        try:
            self.matches = find_entries(self.main_dict, target)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("查找失败", str(exc))
            return

        self.selected_entry = None
        self.save_button.state(["disabled"])
        self.edit_text.set("")
        self.edit_code.set("")
        self.edit_weight.set("")
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self.matches):
            self.tree.insert(
                "", "end", iid=str(index),
                values=(entry.text, entry.code, entry.weight, entry.line_index + 1),
            )
        if not self.matches:
            self._set_status(f"主词库中没有找到：{target}")
            messagebox.showinfo("没有找到", f"主词库中没有词条“{target}”。")
        else:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self._set_status(f"找到 {len(self.matches)} 条匹配记录。")

    def _select_match(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.selected_entry = self.matches[int(selection[0])]
        self.edit_text.set(self.selected_entry.text)
        self.edit_code.set(self.selected_entry.code)
        self.edit_weight.set(self.selected_entry.weight)
        self.save_button.state(["!disabled"])

    def _save_edit(self) -> None:
        if self.selected_entry is None:
            messagebox.showwarning("请选择词条", "请先从查找结果中选择一项。")
            return
        try:
            text, code, weight = validate_fields(
                self.edit_text.get(), self.edit_code.get(), self.edit_weight.get()
            )
            backup = update_entry(
                self.main_dict,
                self.selected_entry.line_index,
                self.selected_entry.text,
                text,
                code,
                weight,
            )
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.search_text.set(text)
        self._search()
        self._set_status(f"已保存修改；备份文件：{backup.name}")
        messagebox.showinfo(
            "保存成功",
            f"已更新“{text}”。\n\n原文件已备份为：{backup.name}\n"
            "请点击“重新部署 Rime”使修改生效。",
        )

    def _start_deploy(self) -> None:
        deployer = find_weasel_deployer()
        if deployer is None:
            messagebox.showerror(
                "未找到部署程序",
                "没有找到 WeaselDeployer.exe。\n"
                "请确认已经安装 Windows 版小狼毫输入法。",
            )
            return
        self.deploy_button.state(["disabled"])
        self.deploy_button.configure(text="正在部署…")
        self._set_status("正在重新部署 Rime，请稍候…")
        threading.Thread(
            target=self._deploy_worker,
            args=(deployer,),
            daemon=True,
        ).start()

    def _deploy_worker(self, deployer: Path) -> None:
        try:
            deploy_rime(deployer)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.after(0, self._finish_deploy, False, str(exc))
        else:
            self.after(0, self._finish_deploy, True, "")

    def _finish_deploy(self, success: bool, details: str) -> None:
        self.deploy_button.state(["!disabled"])
        self.deploy_button.configure(text="重新部署 Rime")
        if success:
            self._set_status("Rime 重新部署完成，词库修改已生效。")
            messagebox.showinfo("部署完成", "Rime 已重新部署，词库修改已生效。")
        else:
            self._set_status("Rime 重新部署失败。")
            messagebox.showerror("部署失败", details)


def main() -> None:
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    app = DictionaryTool()
    app.mainloop()


if __name__ == "__main__":
    main()
