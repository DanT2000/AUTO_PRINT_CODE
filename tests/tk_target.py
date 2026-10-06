# Тестовое окно-приёмник. mode=plain|ide. Через idle секунд без ввода пишет текст в out и выходит.
import sys, tkinter as tk
mode, out = sys.argv[1], sys.argv[2]
root = tk.Tk(); root.title("TARGET"); root.geometry("600x400+100+100")
t = tk.Text(root, undo=False); t.pack(fill="both", expand=True)
last = [0]
def touch(*_): last[0] = 0
if mode == "ide":
    PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"'}
    def key(e):
        touch()
        ch = e.char
        nxt = t.get("insert")
        if ch in PAIRS.values() and nxt == ch and not t.tag_ranges("sel"):
            t.mark_set("insert", "insert+1c"); return "break"   # overtype
        if ch in PAIRS and not t.tag_ranges("sel"):
            t.insert("insert", ch + PAIRS[ch]); t.mark_set("insert", "insert-1c"); return "break"
    def ret(e):
        touch()
        line = t.get("insert linestart", "insert")
        ind = line[:len(line) - len(line.lstrip(" "))]
        if line.rstrip().endswith((":", "{", "(")): ind += "    "
        t.insert("insert", "\n" + ind); t.see("insert"); return "break"
    t.bind("<Key>", key); t.bind("<Return>", ret)
else:
    t.bind("<Key>", touch)
def tick():
    last[0] += 1
    if last[0] > 25:  # 2.5 c тишины
        open(out, "w", encoding="utf-8").write(t.get("1.0", "end-1c")); root.destroy(); return
    root.after(100, tick)
root.after(300, lambda: (root.lift(), root.focus_force(), t.focus_set()))
root.after(100, tick)
root.mainloop()
