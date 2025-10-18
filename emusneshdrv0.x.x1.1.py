#!/usr/bin/env python3
"""
EmuSNES v0.1 — Super Nintendo Entertainment System Emulator
Based on SNES9x v0.1 Original Architecture

© 2025 Flames Co. Labs / Samsoft Interactive
Developed for educational and preservation purposes only.
Super FX™ is a trademark of Nintendo.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import os
import struct


# --- EmuSNES Core Engine ---
class EmuSNESCore:
    SNES_CLOCK_SPEED = 3579545
    SNES_REFRESH_RATE = 60.0988475
    CYCLES_PER_SCANLINE = 1364
    SCANLINES_PER_FRAME = 262

    def __init__(self, screen_canvas, status_callback):
        self.screen_canvas = screen_canvas
        self.status_callback = status_callback

        self.memory = self.MemoryMap()
        self.cpu = self.CPU65C816(self.memory)
        self.ppu = self.PPU(self.memory, screen_canvas)
        self.apu = self.APU(self.memory)
        self.dsp = self.DSP()

        self.running = False
        self.paused = False
        self.frame_skip = 0
        self.fps_counter = 0
        self.last_fps_time = time.time()

        self.rom_name = ""
        self.rom_type = "LoROM"
        self.rom_size = 0

    def load_rom(self, rom_path):
        try:
            with open(rom_path, "rb") as f:
                rom_data = f.read()
            if len(rom_data) % 1024 == 512:
                self.status_callback("Detected copier header, removing…")
                rom_data = rom_data[512:]
            self.rom_size = len(rom_data)
            self.rom_name = os.path.basename(rom_path)
            self.rom_type = "LoROM" if self.rom_size <= 0x200000 else "HiROM"
            self.memory.load_rom(rom_data, self.rom_type)
            self._parse_header(rom_data)
            self.status_callback(f"Loaded: {self.rom_name} ({self.rom_type}, {self.rom_size//1024} KB)")
            return True
        except Exception as e:
            self.status_callback(f"ROM Load Error: {e}")
            return False

    def _parse_header(self, rom_data):
        header_offset = 0x7FC0 if self.rom_type == "LoROM" else 0xFFC0
        if header_offset + 32 <= len(rom_data):
            title_bytes = rom_data[header_offset:header_offset+21]
            self.rom_name = title_bytes.decode('ascii', errors='ignore').strip()

    def reset(self):
        self.cpu.reset()
        self.ppu.reset()
        self.apu.reset()
        self.dsp.reset()
        self.memory.reset()
        self.status_callback("System Reset")

    def run_frame(self):
        if not self.running or self.paused:
            return
        start_time = time.time()
        cycles_target = self.CYCLES_PER_SCANLINE * self.SCANLINES_PER_FRAME
        cycles_executed = 0
        while cycles_executed < cycles_target and self.running:
            cycles = self.cpu.step()
            if cycles == 0:
                self.running = False
                break
            cycles_executed += cycles
            if cycles_executed % self.CYCLES_PER_SCANLINE == 0:
                scanline = cycles_executed // self.CYCLES_PER_SCANLINE
                self.ppu.update_scanline(scanline)
            if cycles_executed % 64 == 0:
                self.apu.update()
        self.ppu.render_frame()
        self.fps_counter += 1
        now = time.time()
        if now - self.last_fps_time >= 1.0:
            fps = self.fps_counter / (now - self.last_fps_time)
            self.status_callback(f"FPS: {fps:.1f} | {self.rom_name}")
            self.fps_counter = 0
            self.last_fps_time = now
        frame_time = time.time() - start_time
        target = 1.0 / self.SNES_REFRESH_RATE
        if frame_time < target:
            time.sleep(target - frame_time)

    # --- CPU 65C816 subset ---
    class CPU65C816:
        def __init__(self, memory):
            self.memory = memory
            self.a = self.x = self.y = 0
            self.s = 0x01FF
            self.d = self.db = self.pb = 0
            self.pc = 0
            self.p = 0x34
            self.e = 1

        def reset(self):
            self.e = 1
            self.pb = 0
            low = self.memory.read(0xFFFC)
            high = self.memory.read(0xFFFD)
            self.pc = (high << 8) | low
            self.s = 0x01FF
            self.p = 0x34

        def step(self):
            opcode = self.memory.read((self.pb << 16) | self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            cycles = 2
            if opcode == 0xEA: cycles = 2        # NOP
            elif opcode == 0xA9:                 # LDA #imm
                self.a = self.memory.read((self.pb << 16) | self.pc)
                self.pc = (self.pc + 1) & 0xFFFF
                self._set_nz(self.a)
            elif opcode == 0xE8:                 # INX
                self.x = (self.x + 1) & 0xFF; self._set_nz(self.x)
            elif opcode == 0xC8:                 # INY
                self.y = (self.y + 1) & 0xFF; self._set_nz(self.y)
            return cycles

        def _set_nz(self, v):
            self.p = (self.p | 0x02) if v == 0 else (self.p & ~0x02)
            self.p = (self.p | 0x80) if v & 0x80 else (self.p & ~0x80)

    # --- Memory Map ---
    class MemoryMap:
        def __init__(self):
            self.wram = bytearray(128 * 1024)
            self.vram = bytearray(64 * 1024)
            self.oam = bytearray(544)
            self.cgram = bytearray(512)
            self.rom = bytearray(4 * 1024 * 1024)
            self.rom_type = "LoROM"

        def reset(self):
            self.wram[:] = b"\x00" * len(self.wram)
            self.vram[:] = b"\x00" * len(self.vram)

        def load_rom(self, rom_data, rom_type):
            self.rom_type = rom_type
            size = min(len(rom_data), len(self.rom))
            self.rom[:size] = rom_data[:size]

        def read(self, addr):
            bank = (addr >> 16) & 0xFF
            ofs = addr & 0xFFFF
            if bank < 0x40 and ofs < 0x2000:
                return self.wram[ofs & 0x1FFF]
            if ofs >= 0x8000:
                rom_addr = ((bank & 0x7F) << 15) | (ofs & 0x7FFF)
                return self.rom[rom_addr % len(self.rom)]
            return 0

        def write(self, addr, val):
            bank = (addr >> 16) & 0xFF
            ofs = addr & 0xFFFF
            if bank < 0x40 and ofs < 0x2000:
                self.wram[ofs & 0x1FFF] = val & 0xFF

    # --- PPU ---
    class PPU:
        def __init__(self, memory, canvas):
            self.memory = memory
            self.screen = canvas
            self.frame_buffer = [[0]*256 for _ in range(224)]
            self.brightness = 15

        def reset(self):
            self.frame_buffer = [[0]*256 for _ in range(224)]

        def update_scanline(self, y):
            if y < 224:
                for x in range(256):
                    c = (self.memory.vram[(y*32 + x//8) % len(self.memory.vram)] + x) % 32
                    self.frame_buffer[y][x] = c

        def render_frame(self):
            self.screen.delete("all")
            for y in range(224):
                for x in range(0,256,4):
                    c = int(self.frame_buffer[y][x] * ((self.brightness+1)/16.0) * 8)
                    color = f'#{c:02x}{c:02x}{c:02x}'
                    self.screen.create_rectangle(x*2,y*2,(x+4)*2,(y+1)*2,fill=color,outline="")

    # --- APU & DSP stubs ---
    class APU:
        def __init__(self, mem): self.enabled = True
        def reset(self): self.enabled = True
        def update(self): pass

    class DSP:
        def __init__(self): self.volume = 127
        def reset(self): self.volume = 127


# --- GUI Application ---
class EmuSNESApp:
    def __init__(self, master):
        self.master = master
        self.rom_loaded = False  # <-- FIXED FLAG
        master.title("EmuSNES v0.1 — Flames Co. Labs © 2025")
        master.geometry("600x400")
        master.resizable(False, False)
        master.configure(bg="#c0c0c0")

        self.status_frame = tk.Frame(master, bg="#808080", height=25)
        self.status_frame.pack(side=tk.TOP, fill=tk.X)
        self.status_frame.pack_propagate(False)
        self.status_label = tk.Label(
            self.status_frame,
            text="EmuSNES v0.1 Ready",
            bg="#808080", fg="white",
            font=("MS Sans Serif", 8),
            anchor=tk.W
        )
        self.status_label.pack(side=tk.LEFT, padx=5, pady=2)

        self.menu_bar = tk.Menu(master); master.config(menu=self.menu_bar)
        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open ROM…", command=self.load_rom, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.exit_emulator, accelerator="Alt+F4")

        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About EmuSNES…", command=self.show_about)

        self.screen_frame = tk.Frame(master, bg="black", width=512, height=448)
        self.screen_frame.pack(expand=True, fill=tk.BOTH)
        self.screen = tk.Canvas(self.screen_frame, width=512, height=448, bg="black", highlightthickness=0)
        self.screen.pack(expand=True)

        self.screen.create_text(256, 180, text="EmuSNES v0.1", fill="white", font=("Arial", 24,"bold"))
        self.screen.create_text(256, 220, text="Super Nintendo Entertainment System Emulator",
                                fill="#808080", font=("Arial", 10))
        self.screen.create_text(256, 260, text="© 2025 Flames Co. Labs / Samsoft Interactive",
                                fill="#606060", font=("Arial", 8))
        self.screen.create_text(256, 290, text="Super FX™ is a trademark of Nintendo",
                                fill="#606060", font=("Arial", 8))
        self.screen.create_text(256, 330, text="File > Open ROM… to begin",
                                fill="white", font=("Arial", 11))

        self.emulator = EmuSNESCore(self.screen, self.update_status)
        self.emulation_thread = None

        master.bind("<Control-o>", lambda e: self.load_rom())
        master.protocol("WM_DELETE_WINDOW", self.exit_emulator)

    def update_status(self, msg):
        self.status_label.config(text=f"EmuSNES v0.1 — {msg}")

    def load_rom(self):
        if self.emulator.running:
            self.stop_emulation()
        fp = filedialog.askopenfilename(title="Open SNES ROM",
            filetypes=[("SNES ROM Files","*.smc *.sfc *.swc *.fig"),("All Files","*.*")])
        if fp and self.emulator.load_rom(fp):
            self.rom_loaded = True
            self.emulator.reset()
            self.start_emulation()

    def start_emulation(self):
        if self.emulator.running: return
        self.emulator.running = True
        self.emulation_thread = threading.Thread(target=self.emulation_loop, daemon=True)
        self.emulation_thread.start()

    def stop_emulation(self):
        self.emulator.running = False
        if self.emulation_thread:
            self.emulation_thread.join(timeout=1.0)

    def emulation_loop(self):
        while self.emulator.running:
            self.emulator.run_frame()

    def exit_emulator(self):
        self.stop_emulation()
        self.master.quit()

    def show_about(self):
        msg = (
            "EmuSNES v0.1 — Flames Co. Labs © 2025\n"
            "Super Nintendo Entertainment System Emulator\n\n"
            "Based on SNES9x v0.1 architecture.\n"
            "Super FX™ is a trademark of Nintendo.\n\n"
            "Educational build for historical preservation only.\n"
            "Please support original developers and publishers."
        )
        messagebox.showinfo("About EmuSNES", msg)


if __name__ == "__main__":
    root = tk.Tk()
    app = EmuSNESApp(root)
    root.mainloop()
