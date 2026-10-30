"""Small player-facing launcher; work runs in a worker, UI stays responsive."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import extracteur as core

TITLES = {"azuchi1": "Stage: Azuchi 1", "azuchi2": "Stage: Azuchi 2", "crimson1": "Stage: Crimson 1",
          "crimson2": "Stage: Crimson 2", "danger-zone": "Stage: Danger Zone", "fireworks": "Stage: Fireworks",
          "titan": "Stage: Titan", "stage-menu": "Stage select menu: PS4 thumbnails and layout",
          "softengine-lr": "PS4 LR breast motion data (DOA5Extra)",
          "stage-data": "Stage mod data: ExtraStages.d5sd and CrimsonVFX.d5sd (needed by the stage mods)",
          "costumes-v2": "Costumes v2: full pack for StatefulCostumes",
          "costumes-classiques": "Classic costumes: 17-costume archive",
          "costumes-particules": "Costume particles: modified common.lnk"}

def main():
    app = tk.Tk()
    app.title("DOA5LR PS4 Extractor 0.5.2")
    app.geometry("850x820")
    app.minsize(700, 550)
    frame = ttk.Frame(app, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Extract from your own PS4 backup", font=("Segoe UI", 17, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Add your extracted PS4 folders: base game, update 1.19 and DLC.\n"
              "Also add your PC game folder (costumes v2, costume particles, stage sounds).\n"
              "Everything is made from your own files and stays on your computer.").pack(anchor="w", pady=8)
    sources = tk.Listbox(frame, height=4)
    sources.pack(fill="x")
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=6)
    def add():
        path = filedialog.askdirectory(title="PS4 base game, PS4 update 1.19, PS4 DLC or PC game folder")
        if path and path not in sources.get(0, "end"):
            sources.insert("end", path)
    ttk.Button(row, text="Add a source folder", command=add).pack(side="left")
    ttk.Button(row, text="Remove", command=lambda: [sources.delete(i) for i in reversed(sources.curselection())]).pack(side="left", padx=6)
    key_file = tk.StringVar()
    key_row = ttk.Frame(frame)
    key_row.pack(fill="x", pady=4)
    def choose_key():
        path = filedialog.askopenfilename(title="Your own key file")
        if path:
            key_file.set(path)
    ttk.Button(key_row, text="Key file...", command=choose_key).pack(side="left")
    ttk.Entry(key_row, textvariable=key_file, state="readonly").pack(side="left", fill="x", expand=True, padx=8)
    options = ttk.LabelFrame(frame, text="Content")
    options.pack(fill="x", pady=6)
    selections = {}
    for group in sorted({j["group"] for j in core.read_recipe()["jobs"]}):
        flag = tk.BooleanVar(value=True)
        selections[group] = flag
        ttk.Checkbutton(options, text=TITLES.get(group, group.upper()), variable=flag).pack(anchor="w", padx=8)
    events = queue.Queue()
    log = tk.Text(frame, height=10, wrap="word", state="disabled")
    actions = ttk.Frame(frame)
    actions.pack(fill="x", pady=8)
    log.pack(fill="both", expand=True)
    busy = False
    def append(text):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")
    def run(fn):
        nonlocal busy
        if busy:
            return
        busy = True
        def worker():
            try:
                fn()
                events.put(("done", "Done."))
            except Exception as e:
                events.put(("error", core.error_text(e)))
        threading.Thread(target=worker, daemon=False).start()
    def build():
        roots = list(sources.get(0, "end"))
        groups = [g for g, v in selections.items() if v.get()]
        if not roots or not groups:
            messagebox.showerror("Selection", "Add a source folder and choose some content.")
            return
        texconv = None
        if any(g.startswith("costumes-") and g != "costumes-particules" for g in groups):
            bundled = core.HOME / "tools" / "texconv.exe"
            texconv = str(bundled) if bundled.is_file() else filedialog.askopenfilename(
                title="Select texconv.exe (DirectXTex)", filetypes=[("texconv.exe", "texconv.exe")])
            if not texconv:
                return
        folder = filedialog.askdirectory(title="Folder for the ZIPs")
        if not folder:
            return
        parts = core.split_groups(groups)
        existing = [name for kind, name, _ in core.ZIP_SET if kind in parts and (Path(folder) / name).exists()]
        replace = False
        if existing:
            replace = messagebox.askyesno("ZIPs already there", "Replace these ZIPs?\n" + "\n".join(existing))
            if not replace:
                return
        selected_key = key_file.get() or None
        run(lambda: core.build_set(roots, groups, folder, texconv, lambda t: events.put(("log", t)),
                                   key_file=selected_key, replace=replace))
    def verify():
        p = filedialog.askopenfilename(filetypes=[("ZIP archive", "*.zip")])
        if p:
            run(lambda: core.verify(p, lambda t: events.put(("log", t))))
    def install():
        p = filedialog.askopenfilename(title="ZIP to import (stages or costume particles)", filetypes=[("ZIP archive", "*.zip")])
        if not p:
            return
        game = filedialog.askdirectory(title="Folder that contains game.exe")
        if game:
            run(lambda: core.install(p, game, log=lambda t: events.put(("log", t))))
    def restore():
        p = filedialog.askopenfilename(title="state.json of the PS4 import backup",
                                      filetypes=[("Backup journal", "state.json")])
        if p:
            run(lambda: core.restore(p))
    ttk.Button(actions, text="Create the ZIPs", command=build).pack(side="left")
    ttk.Button(actions, text="Verify a ZIP", command=verify).pack(side="left", padx=8)
    ttk.Button(actions, text="Import", command=install).pack(side="left")
    ttk.Button(actions, text="Restore", command=restore).pack(side="left", padx=8)
    ttk.Label(frame, text="One click makes up to 3 ZIPs: DOA5MissingContent.zip (stages), DOA5LR-PS4-Costumes.zip "
              "and DOA5LR-PS4-Costume-Particles.zip.\nPut them next to DOA5Installer.exe, or use its Add zips "
              "button: each ZIP goes to its card (PS4 content, PS4 costumes, PS4 particles).\n"
              "Stage sounds take a few minutes. Other modules are needed: see README.",
              wraplength=790).pack(anchor="w", pady=4)
    def poll():
        nonlocal busy
        while not events.empty():
            kind, text = events.get()
            append(("ERROR: " if kind == "error" else "") + text)
            if kind != "log":
                busy = False
        app.after(150, poll)
    def close():
        if busy:
            messagebox.showinfo("Working", "Wait for the current task to finish before closing.")
        else:
            app.destroy()
    app.protocol("WM_DELETE_WINDOW", close)
    poll()
    app.mainloop()

if __name__ == "__main__":
    main()
